"""Operator reindex: Chinese ``fts`` backfill and per-item title rows.

Two idempotent jobs for rows that predate the Chinese bigram index and the
title row (``app.ingest.title_row``):

* ``zh_fts`` rewrites ``segment.fts`` for Chinese items whose body segments
  still have ``fts IS NULL``.  Pure SQL work: no embedding API call.
* ``title_rows`` inserts a missing title row, or refreshes one whose text no
  longer matches the item's current title and description, embedding it first.
  Running the command again after a title or description edit updates the row.

Only counts and internal item IDs leave this module; titles, descriptions and
provider error text never do.  The work is deliberately outside the Alembic
migration unit (no embedding network access, and the one-time embedding calls
must not block a deployment).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update

from app.db import get_session_factory
from app.ingest.title_row import (
    TITLE_ROW_KIND,
    TITLE_ROW_SEQ,
    build_title_row,
    title_row_fts,
    title_row_text,
)
from app.models import ContentItem, Segment
from app.retrieval.zh_tokens import zh_tsvector

_ZH_BATCH_SIZE = 200


@dataclass(frozen=True)
class ReindexResult:
    """Counts and item IDs only; for a dry run the counts are what would change."""

    zh_segments_updated: int
    title_rows_inserted: int
    title_rows_updated: int
    title_rows_skipped: int
    failed_item_ids: tuple[int, ...]
    dry_run: bool


def _scope_statement(user_id: int | None):
    """Ready, live, unarchived items (optionally one tenant), oldest first."""

    statement = (
        select(
            ContentItem.id,
            ContentItem.title,
            ContentItem.description,
            ContentItem.platform_id,
            ContentItem.lang,
        )
        .where(
            ContentItem.state == "ready",
            ContentItem.deleted_at.is_(None),
            ContentItem.purge_claimed_at.is_(None),
            ContentItem.archived_at.is_(None),
        )
        .order_by(ContentItem.id)
    )
    if user_id is not None:
        statement = statement.where(ContentItem.user_id == user_id)
    return statement


def _zh_pending_statement(item_id: int, after_segment_id: int):
    """Next batch of body segments of one item that still lack ``fts``."""

    return (
        select(Segment.id, Segment.text)
        .where(
            Segment.item_id == item_id,
            Segment.fts.is_(None),
            Segment.boundary_kind != TITLE_ROW_KIND,
            Segment.id > after_segment_id,
        )
        .order_by(Segment.id)
        .limit(_ZH_BATCH_SIZE)
    )


def _title_row_statement(item_id: int):
    return select(Segment.id, Segment.text).where(
        Segment.item_id == item_id, Segment.seq == TITLE_ROW_SEQ
    )


def _is_zh(item) -> bool:
    return (item.lang or "").startswith("zh")


def _backfill_zh_fts(db, item_id: int, *, dry_run: bool) -> int:
    """Fill ``fts`` for one Chinese item's body segments; one commit per item."""

    updated = 0
    after = 0
    while True:
        rows = db.execute(_zh_pending_statement(item_id, after)).all()
        if not rows:
            break
        if not dry_run:
            for row in rows:
                db.execute(
                    update(Segment)
                    .where(Segment.id == row.id)
                    .values(fts=zh_tsvector(row.text))
                )
        updated += len(rows)
        after = rows[-1].id
    if updated and not dry_run:
        db.commit()
    return updated


def reindex(
    *,
    user_id: int | None = None,
    dry_run: bool = False,
    zh_fts: bool = True,
    title_rows: bool = True,
    embedder=None,
    session_factory=None,
) -> ReindexResult:
    factory = session_factory or get_session_factory()
    zh_updated = inserted = updated = skipped = 0
    failed: list[int] = []

    with factory() as db:
        items = list(db.execute(_scope_statement(user_id)).all())

        if zh_fts:
            for item in items:
                if not _is_zh(item):
                    continue
                try:
                    zh_updated += _backfill_zh_fts(db, item.id, dry_run=dry_run)
                except Exception:
                    # Provider/DB text is never recorded; re-running retries it.
                    db.rollback()
                    failed.append(item.id)

        if title_rows:
            for item in items:
                text = title_row_text(item)
                existing = db.execute(_title_row_statement(item.id)).first()
                if existing is not None and existing.text == text:
                    skipped += 1
                    continue
                if dry_run:
                    if existing is None:
                        inserted += 1
                    else:
                        updated += 1
                    continue
                if embedder is None:
                    # Resolved lazily: a dry run or a zh-fts-only run never
                    # needs the embedding provider.  A configuration error here
                    # is an operator problem, not a per-item failure.
                    from app.ingest.tasks import build_worker_embedder

                    embedder = build_worker_embedder()
                try:
                    vectors = embedder.embed([text])
                    if len(vectors) != 1:
                        raise ValueError("embedding count mismatch")
                    if existing is None:
                        db.add(build_title_row(item, text=text, embedding=vectors[0]))
                    else:
                        db.execute(
                            update(Segment)
                            .where(Segment.id == existing.id)
                            .values(
                                text=text,
                                embedding=vectors[0],
                                fts=title_row_fts(text, item.lang),
                            )
                        )
                    db.commit()
                except Exception:
                    db.rollback()
                    if item.id not in failed:
                        failed.append(item.id)
                else:
                    if existing is None:
                        inserted += 1
                    else:
                        updated += 1

    return ReindexResult(
        zh_segments_updated=zh_updated,
        title_rows_inserted=inserted,
        title_rows_updated=updated,
        title_rows_skipped=skipped,
        failed_item_ids=tuple(failed),
        dry_run=dry_run,
    )
