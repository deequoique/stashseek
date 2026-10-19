"""Ingest stage registry and the write API for post-body stages.

The ``body`` stage (read captions, transcribe when needed, build the index) is
projected from ``content_item.state`` and the latest dispatch and is never
stored.  Every later stage (classification, key points, a future movie
recognizer) keeps one ``content_item_stage`` row, written through
``mark_stage``.  A stage only appears in the API once it is listed in
``ENRICHMENT_STAGES``; a missing row then reads as ``pending``.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql import func

from app.ingest.reasons import is_known_reason
from app.models import ContentItemStage

BODY_STAGE = "body"
# Order and validity of stage keys.  The API never treats this as an enum.
KNOWN_STAGES = ("body", "classify", "summary", "movie")
# Post-body stages the API reports.  The enrichment task registers
# ("classify", "summary"); the movie stage follows in phase two.
ENRICHMENT_STAGES: tuple[str, ...] = ()
STAGE_STATUSES = ("pending", "running", "done", "failed", "skipped")


def stage_sort_key(stage: str) -> tuple[int, int]:
    """Known stages in registry order, anything else after them."""

    if stage in KNOWN_STAGES:
        return (0, KNOWN_STAGES.index(stage))
    return (1, 0)


def mark_stage(
    db,
    item_id: int,
    stage: str,
    status: str,
    *,
    error_code: str | None = None,
) -> None:
    """Upsert the ``content_item_stage`` row of ``(item_id, stage)``.

    ``stage`` must be a known stage other than ``body``.  ``error_code`` is
    required for, and only allowed with, ``failed``, and must be a code of the
    reason registry (later stages register theirs in ``app.ingest.reasons``).
    The caller owns the transaction: this issues the statement and does not
    commit.
    """

    if stage == BODY_STAGE or stage not in KNOWN_STAGES:
        raise ValueError("unknown_stage")
    if status not in STAGE_STATUSES:
        raise ValueError("invalid_stage_status")
    if status == "failed":
        if error_code is None:
            raise ValueError("stage_failure_requires_error_code")
        if not is_known_reason(error_code):
            raise ValueError("unknown_stage_error_code")
    elif error_code is not None:
        raise ValueError("stage_error_code_requires_failed_status")

    statement = insert(ContentItemStage).values(
        item_id=item_id, stage=stage, status=status, error_code=error_code
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[ContentItemStage.item_id, ContentItemStage.stage],
            set_={
                "status": statement.excluded.status,
                "error_code": statement.excluded.error_code,
                "updated_at": func.now(),
            },
        )
    )
