"""Operator backfill: re-dispatch public ``needs_asr`` items through ASR.

Items saved before ASR was enabled ended in ``needs_asr``.  This module gives
the operator a bounded, dry-run-capable way to run them through the normal
dispatch path again: a fresh ``IngestDispatch`` is created and published to the
``ingest`` queue exactly like a library retry, the worker finds no caption
track and hands the same dispatch to the ``asr`` queue.

Only counts and internal item IDs leave this module; URLs, titles and error
text never do.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import exists, select

from app.db import get_session_factory
from app.models import BrowserCapture, ContentItem, IngestDispatch

DEFAULT_BACKFILL_LIMIT = 20
MAX_BACKFILL_LIMIT = 500
_ACTIVE_DISPATCH_STATES = frozenset({"pending", "enqueued", "running"})
_ASR_PLATFORMS = ("youtube", "bilibili")


@dataclass(frozen=True)
class BackfillResult:
    """Item IDs only; ``queued``/``failed`` stay empty for a dry run."""

    selected: tuple[int, ...]
    queued: tuple[int, ...]
    skipped: tuple[int, ...]
    failed: tuple[int, ...]
    dry_run: bool


def select_backfill_item_ids(
    db, *, limit: int, user_id: int | None = None
) -> list[int]:
    """Oldest-first public ``needs_asr`` items that did not come from a capture."""

    statement = (
        select(ContentItem.id)
        .where(
            ContentItem.state == "needs_asr",
            ContentItem.platform.in_(_ASR_PLATFORMS),
            ContentItem.deleted_at.is_(None),
            ContentItem.purge_claimed_at.is_(None),
            ContentItem.archived_at.is_(None),
            ~exists().where(BrowserCapture.item_id == ContentItem.id),
        )
        .order_by(ContentItem.id)
        .limit(limit)
    )
    if user_id is not None:
        statement = statement.where(ContentItem.user_id == user_id)
    return [int(item_id) for item_id in db.scalars(statement)]


def backfill_needs_asr(
    *,
    limit: int = DEFAULT_BACKFILL_LIMIT,
    user_id: int | None = None,
    dry_run: bool = False,
    session_factory=None,
    publisher: Callable[[int], str | None] | None = None,
) -> BackfillResult:
    if isinstance(limit, bool) or not 1 <= int(limit) <= MAX_BACKFILL_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_BACKFILL_LIMIT}")
    factory = session_factory or get_session_factory()
    with factory() as db:
        item_ids = select_backfill_item_ids(db, limit=int(limit), user_id=user_id)
    if dry_run:
        return BackfillResult(tuple(item_ids), (), (), (), True)

    if publisher is None:
        # Resolved lazily: importing the Celery app is not needed for a dry run.
        from app.ingest.tasks import publish_ingest_dispatch as publisher

    queued: list[int] = []
    skipped: list[int] = []
    failed: list[int] = []
    for item_id in item_ids:
        dispatch_id = _admit(item_id, factory)
        if dispatch_id is None:
            skipped.append(item_id)
            continue
        try:
            task_id = publisher(dispatch_id)
        except Exception:
            # Broker text is never recorded; the item returns to needs_asr so
            # the operator can simply run the command again.
            _publish_failed(dispatch_id, factory)
            failed.append(item_id)
        else:
            _mark_enqueued(dispatch_id, task_id, factory)
            queued.append(item_id)
    return BackfillResult(
        tuple(item_ids), tuple(queued), tuple(skipped), tuple(failed), False
    )


def _admit(item_id: int, factory) -> int | None:
    """Create one pending dispatch under the item row lock, or ``None``."""

    with factory() as db:
        item = db.scalar(
            select(ContentItem).where(ContentItem.id == item_id).with_for_update()
        )
        if (
            item is None
            or item.state != "needs_asr"
            or item.deleted_at is not None
            or item.purge_claimed_at is not None
            or item.archived_at is not None
        ):
            return None
        latest = db.scalar(
            select(IngestDispatch)
            .where(IngestDispatch.item_id == item.id)
            .order_by(IngestDispatch.attempt.desc(), IngestDispatch.id.desc())
            .limit(1)
        )
        if latest is not None and latest.state in _ACTIVE_DISPATCH_STATES:
            return None
        item.state = "pending"
        item.fail_reason = None
        dispatch = IngestDispatch(
            public_id=uuid4().hex,
            item_id=item.id,
            request_key=f"asr-backfill-{uuid4().hex}",
            attempt=(latest.attempt + 1) if latest is not None else 1,
            state="pending",
        )
        db.add(dispatch)
        db.flush()
        dispatch_id = dispatch.id
        db.commit()
        return dispatch_id


def _mark_enqueued(dispatch_id: int, task_id: str | None, factory) -> None:
    try:
        with factory() as db:
            # Lock and re-check: a fast worker may already have claimed the
            # dispatch and must never be overwritten with ``enqueued``.
            dispatch = db.scalar(
                select(IngestDispatch)
                .where(IngestDispatch.id == dispatch_id)
                .with_for_update()
            )
            if dispatch is None or dispatch.state != "pending":
                return
            dispatch.state = "enqueued"
            dispatch.task_id = task_id
            dispatch.updated_at = datetime.now(UTC)
            db.commit()
    except Exception:
        # Publishing already succeeded; the worker claims a pending dispatch.
        return


def _publish_failed(dispatch_id: int, factory) -> None:
    try:
        with factory() as db:
            dispatch = db.scalar(
                select(IngestDispatch)
                .where(IngestDispatch.id == dispatch_id)
                .with_for_update()
            )
            if dispatch is None or dispatch.state != "pending":
                return
            item = db.scalar(
                select(ContentItem)
                .where(ContentItem.id == dispatch.item_id)
                .with_for_update()
            )
            dispatch.state = "failed"
            dispatch.error_code = "queue_unavailable"
            dispatch.updated_at = datetime.now(UTC)
            if item is not None and item.state == "pending":
                item.state = "needs_asr"
            db.commit()
    except Exception:
        return
