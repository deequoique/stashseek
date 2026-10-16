"""Tenant-scoped content library and lifecycle projection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import inspect
import re
import time
from typing import Any, Literal, Protocol
from uuid import uuid4

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.ingest.submission import (
    MIN_REMAINING_PUBLISH_BUDGET_SECONDS,
    IngestQuotaExceeded,
    IngestQuotaPolicy,
)
from app.models import ContentItem, IngestDispatch
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
        "source_changed",
        "source_unavailable",
    }
)
Lifecycle = Literal[
    "archived", "ready", "needs_action", "failed", "processing", "queued"
]


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


@dataclass(frozen=True)
class LibraryPage:
    items: tuple[LibraryItemDTO, ...]
    total: int
    page: int
    page_size: int
    is_true_first_empty: bool


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


def project_lifecycle(item: Any, latest: Any | None) -> LifecycleProjection:
    if item.archived_at is not None:
        return LifecycleProjection("archived", None, ("restore",))
    if item.state == "ready":
        state: Lifecycle = "ready"
        error = None
    elif item.state in {"needs_extension", "needs_asr", "no_text"}:
        state = "needs_action"
        error = None
    elif (latest is not None and latest.state == "failed") or item.state == "failed":
        state = "failed"
        error = _safe_error(
            latest.error_code if latest is not None and latest.state == "failed" else item.fail_reason
        )
    elif (latest is not None and latest.state == "running") or item.state in {
        "fetching",
        "chunking",
        "embedding",
    }:
        state = "processing"
        error = None
    elif item.state == "pending" and latest is not None and latest.state in {
        "pending",
        "enqueued",
    }:
        state = "queued"
        error = None
    else:
        state = "failed"
        error = "missing_dispatch"

    actions = ["edit_why_saved", "archive"]
    if state == "failed" and (
        latest is None or latest.state not in ACTIVE_DISPATCH_STATES
    ):
        actions.append("retry")
    if getattr(item, "url", None):
        actions.append("open_source")
    return LifecycleProjection(state, error, tuple(actions))


class ContentLibraryService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        publisher: Callable[..., str | None],
        *,
        quota_policy: IngestQuotaPolicy | None = None,
        save_enabled: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._quota_policy = quota_policy or IngestQuotaPolicy()
        self._save_enabled = bool(save_enabled)
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
            projected = [self._dto(value, latest_by_item.get(value.id)) for value in items]
            if lifecycle is not None:
                projected = [value for value in projected if value.lifecycle == lifecycle]
            total = len(projected)
            start = (page - 1) * page_size
            return LibraryPage(
                tuple(projected[start : start + page_size]),
                total,
                page,
                page_size,
                true_total == 0,
            )

    def get_item(self, scope: UserScopeLike, item_public_id: str) -> LibraryItemDTO:
        with self._session_factory() as db:
            item = self._owned_item(db, scope, item_public_id)
            latest = self._latest_dispatch(db, item.id, scope.app_user_id)
            return self._dto(item, latest)

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
                )
            latest = self._latest_dispatch(db, item.id, scope.app_user_id)
            projection = project_lifecycle(item, latest)
            if item.archived_at is not None or projection.state != "failed" or (
                latest is not None and latest.state in ACTIVE_DISPATCH_STATES
            ):
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

    def _dto(self, item: ContentItem, latest: IngestDispatch | None) -> LibraryItemDTO:
        projection = project_lifecycle(item, latest)
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
            url=item_source_url(item.public_id) if has_verified_bilibili_source(item) else item.url,
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
