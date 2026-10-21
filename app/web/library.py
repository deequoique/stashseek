"""Tenant-scoped content library and lifecycle projection."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import inspect
import re
import time
from typing import Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.asr.types import ASR_ERROR_CODES
from app.ingest.reasons import (
    DEFAULT_NEEDS_ASR_REASON,
    DEFAULT_NEEDS_EXTENSION_REASON,
    LOGIN_REQUIRED,
    IngestReason,
    reason_for,
)
from app.ingest.stages import BODY_STAGE, ENRICHMENT_STAGES, stage_sort_key
from app.ingest.submission import (
    MIN_REMAINING_PUBLISH_BUDGET_SECONDS,
    IngestQuotaExceeded,
    IngestQuotaPolicy,
)
from app.models import BrowserCapture, ContentItem, ContentItemStage, IngestDispatch
from app.limits import normalize_why_saved
from app.web.source_projection import has_verified_bilibili_source, item_source_url


ACTIVE_DISPATCH_STATES = frozenset({"pending", "enqueued", "running"})
SAFE_ERROR_CODES = frozenset(
    {
        "queue_unavailable",
        "ingestion_failed",
        "transient_fetch_failed",
        "item_missing",
        "missing_dispatch",
        "transcript_unavailable",
        "transcript_invalid",
        "ingest_too_large",
        *sorted(ASR_ERROR_CODES),
        "source_changed",
        "source_unavailable",
    }
)
Lifecycle = Literal[
    "archived", "ready", "needs_action", "failed", "processing", "queued"
]
# Finer, read-only view of the same ingest state, for progress UIs.  It is
# derived from existing columns only; the public ``Lifecycle`` is unchanged.
Stage = Literal[
    "queued",
    "fetching",
    "transcribing",
    "chunking",
    "embedding",
    "ready",
    "needs_action",
    "failed",
    "archived",
]
StageStatus = Literal["pending", "running", "done", "failed", "skipped"]
# Only the ``body`` stage has a detail, and only while it is running.
BodyDetail = Literal["queued", "fetching", "transcribing", "indexing"]
# Platforms whose caption-less browser captures are handed to the ASR worker
# (mirrors ``_ASR_CAPTURE_PLATFORMS`` in ``app.ingest.tasks``; kept local so the
# web layer does not import the Celery task module).
_ASR_CAPTURE_PLATFORMS = frozenset({"youtube", "bilibili"})


class UserScopeLike(Protocol):
    app_user_id: int


class LibraryError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class LibraryNotFound(LibraryError):
    def __init__(self) -> None:
        super().__init__("not_found")


class LibraryConflict(LibraryError):
    pass


@dataclass(frozen=True)
class LifecycleProjection:
    state: Lifecycle
    error_code: str | None
    available_actions: tuple[str, ...]


@dataclass(frozen=True)
class StageProjection:
    stage: Stage
    updated_at: datetime | None


@dataclass(frozen=True)
class IngestStageDTO:
    """One stage of the ingest pipeline as shown to clients."""

    key: str
    status: StageStatus
    detail: BodyDetail | None = None
    reason: IngestReason | None = None


@dataclass(frozen=True)
class AvailabilityCounts:
    """Non-archived, non-deleted items by what a content search can reach."""

    unsearchable_total: int
    processing_total: int


@dataclass(frozen=True)
class LibraryItemDTO:
    public_id: str
    platform: str
    kind: str
    url: str
    title: str | None
    author: str | None
    published_at: datetime | None
    duration_sec: int | None
    lang: str | None
    description: str | None
    tags: tuple[str, ...]
    chapters: tuple[dict, ...]
    cover_url: str | None
    saved_at: datetime
    why_saved: str | None
    text_source: str
    lifecycle: Lifecycle
    error_code: str | None
    available_actions: tuple[str, ...]
    latest_dispatch_public_id: str | None
    stage: Stage = "queued"
    stage_updated_at: datetime | None = None
    # Body stage first, then the enabled enrichment stages.
    stages: tuple[IngestStageDTO, ...] = ()
    # Why the body is not searchable; equals the body stage's reason.
    reason: IngestReason | None = None
    # ``item.state == 'ready'``: the only state content retrieval returns.
    body_searchable: bool = False


@dataclass(frozen=True)
class LibraryPage:
    items: tuple[LibraryItemDTO, ...]
    total: int
    page: int
    page_size: int
    is_true_first_empty: bool
    unsearchable_total: int = 0
    processing_total: int = 0


@dataclass(frozen=True)
class DispatchDTO:
    public_id: str
    item_public_id: str
    attempt: int
    state: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime


def _safe_error(value: str | None, *, default: str = "ingestion_failed") -> str:
    return value if value in SAFE_ERROR_CODES else default


def _base_lifecycle(item: Any, latest: Any | None) -> tuple[Lifecycle, str | None]:
    """Lifecycle and public error code of the ingest pipeline, ignoring archival."""

    if item.state == "ready":
        return "ready", None
    if item.state in {"needs_extension", "needs_asr", "no_text"}:
        return "needs_action", None
    if (latest is not None and latest.state == "failed") or item.state == "failed":
        return "failed", _safe_error(
            latest.error_code if latest is not None and latest.state == "failed" else item.fail_reason
        )
    if (latest is not None and latest.state == "running") or item.state in {
        "fetching",
        "chunking",
        "embedding",
    }:
        return "processing", None
    if item.state == "pending" and latest is not None and latest.state in {
        "pending",
        "enqueued",
    }:
        return "queued", None
    return "failed", "missing_dispatch"


def _reason_of(item: Any, state: Lifecycle, error: str | None) -> IngestReason | None:
    if state == "failed":
        return reason_for(error)
    if state == "needs_action":
        code = getattr(item, "fail_reason", None)
        if not code:
            # Rows written before reasons were recorded.
            code = (
                DEFAULT_NEEDS_EXTENSION_REASON
                if item.state == "needs_extension"
                else DEFAULT_NEEDS_ASR_REASON
            )
        return reason_for(code)
    return None


def project_reason(item: Any, latest: Any | None) -> IngestReason | None:
    """Why the body is not searchable, from the reason registry.

    Only ``failed`` and ``needs_action`` items have a reason.  Archival is
    ignored, so an archived item keeps the reason it had.  Codes outside the
    registry are shown as ``ingestion_failed``.
    """

    return _reason_of(item, *_base_lifecycle(item, latest))


def project_lifecycle(item: Any, latest: Any | None) -> LifecycleProjection:
    if item.archived_at is not None:
        return LifecycleProjection("archived", None, ("restore",))
    state, error = _base_lifecycle(item, latest)

    actions = ["edit_why_saved", "archive"]
    if state in {"failed", "needs_action"} and (
        latest is None or latest.state not in ACTIVE_DISPATCH_STATES
    ):
        reason = _reason_of(item, state, error)
        # A server-side re-run cannot read login-only Bilibili subtitles; the
        # user re-collects from the video page instead.
        if reason is not None and reason.category != LOGIN_REQUIRED:
            actions.append("retry")
    if getattr(item, "url", None):
        actions.append("open_source")
    return LifecycleProjection(state, error, tuple(actions))


def project_body_stage(
    item: Any,
    latest: Any | None,
    *,
    asr_capture: bool = False,
) -> IngestStageDTO:
    """Project the ``body`` stage (captions, ASR, index) from the item state.

    Nothing is stored for it, so it cannot disagree with ``content_item.state``
    (the only state retrieval honours).  Archival is ignored.  ``detail`` is
    set only while running: ``transcribing`` when the dispatch was handed to
    the asr queue (``asr_handoff_at``) or, for items still in flight from
    before that column existed, when the latest ready browser capture found no
    caption track (``asr_capture``); ``indexing`` while chunking or embedding.
    """

    state, error = _base_lifecycle(item, latest)
    if state == "queued":
        return IngestStageDTO(BODY_STAGE, "pending", "queued")
    if state == "processing":
        detail: BodyDetail
        if item.state in {"chunking", "embedding"}:
            detail = "indexing"
        elif item.state == "fetching" and (
            asr_capture or getattr(latest, "asr_handoff_at", None) is not None
        ):
            detail = "transcribing"
        else:
            detail = "fetching"
        return IngestStageDTO(BODY_STAGE, "running", detail)
    if state == "ready":
        return IngestStageDTO(BODY_STAGE, "done")
    return IngestStageDTO(BODY_STAGE, "failed", None, _reason_of(item, state, error))


def project_stages(
    item: Any,
    latest: Any | None,
    rows: Iterable[Any],
    *,
    enabled: Sequence[str],
    asr_capture: bool = False,
) -> tuple[IngestStageDTO, ...]:
    """The body stage followed by every enabled enrichment stage.

    ``rows`` are the item's ``content_item_stage`` rows.  An enabled stage
    takes its stored row; with no row it is ``skipped`` when the body failed
    and ``pending`` otherwise (so existing ready items read "not started").
    Enrichment never changes the lifecycle or ``body_searchable``.
    """

    body = project_body_stage(item, latest, asr_capture=asr_capture)
    by_stage = {row.stage: row for row in rows}
    keys = sorted(
        (key for key in dict.fromkeys(enabled) if key != BODY_STAGE),
        key=stage_sort_key,
    )
    stages = [body]
    for key in keys:
        row = by_stage.get(key)
        if row is not None:
            reason = reason_for(row.error_code) if row.status == "failed" else None
            stages.append(IngestStageDTO(key, row.status, None, reason))
        elif body.status == "failed":
            stages.append(IngestStageDTO(key, "skipped"))
        else:
            stages.append(IngestStageDTO(key, "pending"))
    return tuple(stages)


def project_stage(
    item: Any,
    latest: Any | None,
    *,
    asr_capture: bool = False,
) -> StageProjection:
    """Project the current processing step of ``item`` for progress display.

    ``stage`` follows ``project_lifecycle`` and only subdivides ``processing``
    using the internal item state.  ``asr_capture`` is true when a ``fetching``
    item's latest ready browser capture found no caption track: the pipeline
    then hands the same dispatch to the ASR worker and the item stays
    ``fetching`` until it is chunked, so this is the only reliable ASR signal.
    An ASR run for a URL-submitted item (connector ``NeedsASR``) is reported as
    ``fetching`` here to keep the legacy field unchanged; the body stage
    detail of ``project_body_stage`` reads ``asr_handoff_at`` and reports it
    as ``transcribing``.

    ``updated_at`` is the best available time the item entered its stage.
    There is no per-stage timestamp, so it is the latest dispatch's
    ``updated_at`` (admission, enqueue, claim, ASR hand-off or completion;
    for ``chunking``/``embedding`` that is the claim time, an upper bound on
    the stage's age) or, for archived items, ``archived_at``.
    """

    lifecycle = project_lifecycle(item, latest).state
    if lifecycle == "archived":
        return StageProjection("archived", getattr(item, "archived_at", None))
    stage: Stage
    if lifecycle == "processing":
        if item.state == "chunking":
            stage = "chunking"
        elif item.state == "embedding":
            stage = "embedding"
        elif asr_capture and item.state == "fetching":
            stage = "transcribing"
        else:
            stage = "fetching"
    else:
        # queued, ready, needs_action and failed map one-to-one.
        stage = lifecycle
    return StageProjection(
        stage, getattr(latest, "updated_at", None) if latest is not None else None
    )


def _count_availability(states: Iterable[str]) -> AvailabilityCounts:
    unsearchable = processing = 0
    for state in states:
        if state in {"needs_action", "failed"}:
            unsearchable += 1
        elif state in {"queued", "processing"}:
            processing += 1
    return AvailabilityCounts(unsearchable, processing)


class ContentLibraryService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        publisher: Callable[..., str | None],
        *,
        quota_policy: IngestQuotaPolicy | None = None,
        save_enabled: bool = True,
        enrichment_stages: Sequence[str] | None = None,
        bilibili_public_enabled: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._quota_policy = quota_policy or IngestQuotaPolicy()
        self._save_enabled = bool(save_enabled)
        # Post-body stages the API reports; defaults to the registry value.
        self._enrichment_stages = tuple(
            ENRICHMENT_STAGES if enrichment_stages is None else enrichment_stages
        )
        self._bilibili_public_enabled = bool(bilibili_public_enabled)
        try:
            signature = inspect.signature(publisher)
            self._publisher_accepts_budget = (
                "remaining_budget_seconds" in signature.parameters
                or any(
                    value.kind is inspect.Parameter.VAR_KEYWORD
                    for value in signature.parameters.values()
                )
            )
        except (TypeError, ValueError):
            self._publisher_accepts_budget = False

    def list_items(
        self,
        scope: UserScopeLike,
        *,
        search: str | None = None,
        collection: str | None = None,
        lifecycle: str | None = None,
        include_archived: bool = False,
        sort: Literal["saved_desc", "saved_asc", "title_asc"] = "saved_desc",
        page: int = 1,
        page_size: int = 20,
    ) -> LibraryPage:
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        if lifecycle not in {
            None,
            "archived",
            "ready",
            "needs_action",
            "failed",
            "processing",
            "queued",
        }:
            raise LibraryConflict("invalid_lifecycle")
        if sort not in {"saved_desc", "saved_asc", "title_asc"}:
            raise LibraryConflict("invalid_sort")
        if collection is not None and not re.fullmatch(r"[\w-]{1,20}", collection):
            raise LibraryConflict("invalid_collection")

        with self._session_factory() as db:
            true_total = int(
                db.scalar(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.user_id == scope.app_user_id,
                        ContentItem.deleted_at.is_(None),
                    )
                )
                or 0
            )
            statement = select(ContentItem).where(
                ContentItem.user_id == scope.app_user_id,
                ContentItem.deleted_at.is_(None),
            )
            if lifecycle == "archived":
                statement = statement.where(ContentItem.archived_at.is_not(None))
            elif not include_archived:
                statement = statement.where(ContentItem.archived_at.is_(None))
            if search and search.strip():
                pattern = f"%{search.strip()}%"
                statement = statement.where(
                    or_(
                        ContentItem.title.ilike(pattern),
                        ContentItem.author.ilike(pattern),
                        ContentItem.why_saved.ilike(pattern),
                    )
                )
            if collection is not None:
                token_pattern = (
                    rf"(^|[[:space:]])#{re.escape(collection)}([[:space:]]|$)"
                )
                statement = statement.where(
                    ContentItem.why_saved.op("~*")(token_pattern)
                )
            if sort == "saved_asc":
                statement = statement.order_by(asc(ContentItem.saved_at), asc(ContentItem.id))
            elif sort == "title_asc":
                statement = statement.order_by(
                    asc(func.lower(func.coalesce(ContentItem.title, ""))),
                    asc(ContentItem.id),
                )
            else:
                statement = statement.order_by(desc(ContentItem.saved_at), desc(ContentItem.id))
            items = list(db.scalars(statement).all())
            latest_by_item = self._latest_for_items(
                db,
                [value.id for value in items],
                scope.app_user_id,
            )
            asr_ids = self._asr_capture_item_ids(db, items, scope.app_user_id)
            states = [
                (value, project_lifecycle(value, latest_by_item.get(value.id)).state)
                for value in items
            ]
            # The counts cover every non-archived item.  The loop above already
            # saw all of them unless a search, collection or archive filter
            # narrowed the query; only then is a second pass needed.
            if (search and search.strip()) or collection is not None or lifecycle == "archived":
                counts = self._availability_counts(db, scope.app_user_id)
            else:
                counts = _count_availability(state for _, state in states)
            if lifecycle is not None:
                states = [(value, state) for value, state in states if state == lifecycle]
            total = len(states)
            start = (page - 1) * page_size
            page_items = [value for value, _ in states[start : start + page_size]]
            stage_rows = self._stage_rows(
                db, [value.id for value in page_items], scope.app_user_id
            )
            return LibraryPage(
                tuple(
                    self._dto(
                        value,
                        latest_by_item.get(value.id),
                        asr_capture=value.id in asr_ids,
                        stage_rows=stage_rows.get(value.id, ()),
                    )
                    for value in page_items
                ),
                total,
                page,
                page_size,
                true_total == 0,
                counts.unsearchable_total,
                counts.processing_total,
            )

    def availability_counts(self, scope: UserScopeLike) -> AvailabilityCounts:
        """Non-archived, non-deleted items whose body is not searchable.

        ``unsearchable_total`` counts ``needs_action`` and ``failed`` items;
        ``processing_total`` counts ``queued`` and ``processing`` ones (searchable
        soon, but not yet).  Shared by the library list and content search.
        """

        with self._session_factory() as db:
            return self._availability_counts(db, scope.app_user_id)

    def _availability_counts(self, db, user_id: int) -> AvailabilityCounts:
        # Lifecycle is projected in Python (as in ``list_items``), so this loads
        # the user's whole library; move to a SQL aggregate with DB pagination.
        items = list(
            db.scalars(
                select(ContentItem).where(
                    ContentItem.user_id == user_id,
                    ContentItem.deleted_at.is_(None),
                    ContentItem.archived_at.is_(None),
                )
            ).all()
        )
        latest_by_item = self._latest_for_items(db, [value.id for value in items], user_id)
        return _count_availability(
            project_lifecycle(value, latest_by_item.get(value.id)).state
            for value in items
        )

    def get_item(self, scope: UserScopeLike, item_public_id: str) -> LibraryItemDTO:
        with self._session_factory() as db:
            item = self._owned_item(db, scope, item_public_id)
            latest = self._latest_dispatch(db, item.id, scope.app_user_id)
            asr_ids = self._asr_capture_item_ids(db, [item], scope.app_user_id)
            stage_rows = self._stage_rows(db, [item.id], scope.app_user_id)
            return self._dto(
                item,
                latest,
                asr_capture=item.id in asr_ids,
                stage_rows=stage_rows.get(item.id, ()),
            )

    def update_why_saved(
        self,
        scope: UserScopeLike,
        item_public_id: str,
        why_saved: str | None,
    ) -> LibraryItemDTO:
        try:
            normalized = normalize_why_saved(why_saved)
        except ValueError:
            raise LibraryConflict("why_saved_too_long")
        with self._session_factory() as db:
            item = self._owned_item(db, scope, item_public_id)
            if item.archived_at is not None:
                raise LibraryConflict("item_archived")
            item.why_saved = normalized
            db.commit()
        return self.get_item(scope, item_public_id)

    def archive(self, scope: UserScopeLike, item_public_id: str) -> LibraryItemDTO:
        with self._session_factory() as db:
            item = self._owned_item(db, scope, item_public_id)
            if item.archived_at is None:
                item.archived_at = datetime.now(UTC)
                db.commit()
        return self.get_item(scope, item_public_id)

    def restore(self, scope: UserScopeLike, item_public_id: str) -> LibraryItemDTO:
        with self._session_factory() as db:
            item = self._owned_item(db, scope, item_public_id)
            if item.archived_at is not None:
                item.archived_at = None
                db.commit()
        return self.get_item(scope, item_public_id)

    def retry(
        self,
        scope: UserScopeLike,
        item_public_id: str,
        *,
        request_key: str,
        publish_budget_seconds: float | None = None,
    ) -> LibraryItemDTO:
        if not self._save_enabled:
            raise LibraryConflict("save_disabled")
        if not request_key.strip():
            raise ValueError("request key is required")
        if publish_budget_seconds is not None and publish_budget_seconds <= 0:
            raise ValueError("publish budget must be positive")
        publish_deadline = (
            time.monotonic() + float(publish_budget_seconds)
            if publish_budget_seconds is not None
            else None
        )
        with self._session_factory() as db:
            if not self._quota_policy.acquire_locks(
                db, scope.app_user_id
            ):
                raise LibraryNotFound()
            item = self._owned_item(db, scope, item_public_id, lock=True)
            if item.archived_at is not None:
                raise LibraryConflict("retry_unavailable")
            replay = db.scalar(
                select(IngestDispatch)
                .join(ContentItem, ContentItem.id == IngestDispatch.item_id)
                .where(
                    IngestDispatch.item_id == item.id,
                    IngestDispatch.request_key == request_key,
                    ContentItem.user_id == scope.app_user_id,
                    ContentItem.deleted_at.is_(None),
                )
            )
            if replay is not None:
                return self._dto(
                    item,
                    self._latest_dispatch(db, item.id, scope.app_user_id),
                    asr_capture=item.id
                    in self._asr_capture_item_ids(db, [item], scope.app_user_id),
                    stage_rows=self._stage_rows(
                        db, [item.id], scope.app_user_id
                    ).get(item.id, ()),
                )
            latest = self._latest_dispatch(db, item.id, scope.app_user_id)
            # The same rule that offers ``retry`` in ``available_actions``:
            # failed or needs_action, nothing in flight, and not a reason a
            # server-side re-run cannot fix.  A ready item (even with a failed
            # enrichment stage) is rejected here.
            if item.archived_at is not None or "retry" not in project_lifecycle(
                item, latest
            ).available_actions:
                raise LibraryConflict("retry_unavailable")
            try:
                self._quota_policy.enforce(
                    db,
                    scope.app_user_id,
                    include_new_item_limits=False,
                )
            except IngestQuotaExceeded:
                raise LibraryConflict("quota_exceeded") from None
            item.state = "pending"
            item.fail_reason = None
            dispatch = IngestDispatch(
                public_id=uuid4().hex,
                item_id=item.id,
                request_key=request_key,
                attempt=(latest.attempt + 1) if latest is not None else 1,
                state="pending",
            )
            db.add(dispatch)
            db.flush()
            dispatch_id = dispatch.id
            db.commit()
        remaining_budget = (
            publish_deadline - time.monotonic()
            if publish_deadline is not None
            else None
        )
        try:
            if (
                remaining_budget is not None
                and remaining_budget <= MIN_REMAINING_PUBLISH_BUDGET_SECONDS
            ):
                raise TimeoutError("broker_publish_timeout")
            if self._publisher_accepts_budget and remaining_budget is not None:
                task_id = self._publisher(
                    dispatch_id,
                    remaining_budget_seconds=remaining_budget,
                )
            else:
                task_id = self._publisher(dispatch_id)
        except Exception:
            self._set_dispatch(
                dispatch_id,
                scope.app_user_id,
                "failed",
                error_code="queue_unavailable",
            )
        else:
            self._set_dispatch(
                dispatch_id,
                scope.app_user_id,
                "enqueued",
                task_id=task_id,
            )
        return self.get_item(scope, item_public_id)

    def get_dispatch(
        self, scope: UserScopeLike, dispatch_public_id: str
    ) -> DispatchDTO:
        with self._session_factory() as db:
            row = db.execute(
                select(IngestDispatch, ContentItem)
                .join(ContentItem, IngestDispatch.item_id == ContentItem.id)
                .where(
                    ContentItem.user_id == scope.app_user_id,
                    ContentItem.deleted_at.is_(None),
                    IngestDispatch.public_id == dispatch_public_id,
                )
            ).one_or_none()
            if row is None:
                raise LibraryNotFound()
            dispatch, item = row
            return DispatchDTO(
                dispatch.public_id,
                item.public_id,
                dispatch.attempt,
                dispatch.state,
                _safe_error(dispatch.error_code) if dispatch.error_code else None,
                dispatch.created_at,
                dispatch.updated_at,
            )

    @staticmethod
    def _owned_item(db, scope, public_id: str, *, lock: bool = False):
        statement = select(ContentItem).where(
            ContentItem.user_id == scope.app_user_id,
            ContentItem.public_id == public_id,
            ContentItem.deleted_at.is_(None),
        )
        if lock:
            statement = statement.with_for_update()
        item = db.scalar(statement)
        if item is None:
            raise LibraryNotFound()
        return item

    @staticmethod
    def _latest_dispatch(db, item_id: int, user_id: int):
        return db.scalar(
            select(IngestDispatch)
            .join(ContentItem, ContentItem.id == IngestDispatch.item_id)
            .where(
                IngestDispatch.item_id == item_id,
                ContentItem.user_id == user_id,
                ContentItem.deleted_at.is_(None),
            )
            .order_by(IngestDispatch.attempt.desc(), IngestDispatch.id.desc())
            .limit(1)
        )

    @staticmethod
    def _latest_for_items(
        db, item_ids: list[int], user_id: int
    ) -> dict[int, IngestDispatch]:
        if not item_ids:
            return {}
        dispatches = db.scalars(
            select(IngestDispatch)
            .join(ContentItem, ContentItem.id == IngestDispatch.item_id)
            .where(
                IngestDispatch.item_id.in_(item_ids),
                ContentItem.user_id == user_id,
                ContentItem.deleted_at.is_(None),
            )
            .order_by(
                IngestDispatch.item_id,
                IngestDispatch.attempt.desc(),
                IngestDispatch.id.desc(),
            )
        ).all()
        result: dict[int, IngestDispatch] = {}
        for dispatch in dispatches:
            result.setdefault(dispatch.item_id, dispatch)
        return result

    def _stage_rows(
        self, db, item_ids: list[int], user_id: int
    ) -> dict[int, list[ContentItemStage]]:
        """Enrichment stage rows of ``item_ids`` in one query, by item id.

        Skipped when no enrichment stage is enabled: stored rows are ignored
        then, so the common path pays nothing.
        """

        if not self._enrichment_stages or not item_ids:
            return {}
        rows = db.scalars(
            select(ContentItemStage)
            .join(ContentItem, ContentItem.id == ContentItemStage.item_id)
            .where(
                ContentItemStage.item_id.in_(item_ids),
                ContentItem.user_id == user_id,
                ContentItem.deleted_at.is_(None),
            )
        ).all()
        result: dict[int, list[ContentItemStage]] = {}
        for row in rows:
            result.setdefault(row.item_id, []).append(row)
        return result

    @staticmethod
    def _asr_capture_item_ids(db, items, user_id: int) -> frozenset[int]:
        """Ids of ``fetching`` items whose latest ready capture has no captions.

        Mirrors the capture lookup of ``process_item`` (tenant-matching, ``ready``,
        newest first). The query is skipped unless a candidate exists, so only
        items that are mid-fetch pay for it.
        """

        candidates = [
            value.id
            for value in items
            if value.state == "fetching"
            and value.platform in _ASR_CAPTURE_PLATFORMS
            and value.archived_at is None
        ]
        if not candidates:
            return frozenset()
        rows = db.execute(
            select(BrowserCapture.item_id, BrowserCapture.caption_status)
            .where(
                BrowserCapture.item_id.in_(candidates),
                BrowserCapture.app_user_id == user_id,
                BrowserCapture.state == "ready",
            )
            .order_by(
                BrowserCapture.item_id,
                BrowserCapture.created_at.desc(),
                BrowserCapture.id.desc(),
            )
        ).all()
        latest_status: dict[int, str] = {}
        for item_id, caption_status in rows:
            latest_status.setdefault(item_id, caption_status)
        return frozenset(
            item_id
            for item_id, caption_status in latest_status.items()
            if caption_status == "unavailable"
        )

    def _dto(
        self,
        item: ContentItem,
        latest: IngestDispatch | None,
        *,
        asr_capture: bool = False,
        stage_rows: Iterable[Any] = (),
    ) -> LibraryItemDTO:
        projection = project_lifecycle(item, latest)
        stage = project_stage(item, latest, asr_capture=asr_capture)
        stages = project_stages(
            item,
            latest,
            stage_rows,
            enabled=self._enrichment_stages,
            asr_capture=asr_capture,
        )
        available_actions = (
            tuple(
                action
                for action in projection.available_actions
                if action != "retry"
            )
            if not self._save_enabled
            else projection.available_actions
        )
        return LibraryItemDTO(
            public_id=item.public_id,
            platform=item.platform,
            kind=item.kind,
            url=item_source_url(item.public_id)
            if has_verified_bilibili_source(
                item, bilibili_public_enabled=self._bilibili_public_enabled
            )
            else item.url,
            title=item.title,
            author=item.author,
            published_at=item.published_at,
            duration_sec=item.duration_sec,
            lang=item.lang,
            description=item.description,
            tags=tuple(item.tags or ()),
            chapters=tuple(item.chapters or ()),
            cover_url=item.cover_url,
            saved_at=item.saved_at,
            why_saved=item.why_saved,
            text_source=item.text_source,
            lifecycle=projection.state,
            error_code=projection.error_code,
            available_actions=available_actions,
            latest_dispatch_public_id=latest.public_id if latest is not None else None,
            stage=stage.stage,
            stage_updated_at=stage.updated_at,
            stages=stages,
            reason=stages[0].reason,
            body_searchable=item.state == "ready",
        )

    def _set_dispatch(
        self,
        dispatch_id: int,
        user_id: int,
        state: str,
        *,
        task_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._session_factory() as db:
            row = db.execute(
                select(IngestDispatch, ContentItem)
                .join(ContentItem, ContentItem.id == IngestDispatch.item_id)
                .where(
                    IngestDispatch.id == dispatch_id,
                    ContentItem.user_id == user_id,
                )
                .with_for_update()
            ).one_or_none()
            if row is None:
                return
            dispatch, item = row
            if dispatch.state != "pending":
                return
            dispatch.state = state
            dispatch.task_id = task_id
            dispatch.error_code = error_code
            dispatch.updated_at = datetime.now(UTC)
            if (
                state == "failed"
                and error_code == "queue_unavailable"
                and item.state == "pending"
            ):
                item.state = "failed"
                item.fail_reason = "queue_unavailable"
            db.commit()
