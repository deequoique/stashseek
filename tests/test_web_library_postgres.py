from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.channels.types import UserScope
from app.db import get_engine
from app.models import (
    AppUser,
    Base,
    BrowserCapture,
    ContentItem,
    ContentItemStage,
    IngestDispatch,
    Segment,
)
from app.ingest.stages import mark_stage
from app.ingest.submission import IngestQuotaPolicy
from app.retrieval.search import bm25_search, vector_search
from app.web.library import ContentLibraryService, LibraryConflict, LibraryNotFound


@pytest.fixture
def library_factory():
    engine = get_engine()
    schema = f"test_library_{uuid4().hex}"
    tables = [
        AppUser.__table__,
        ContentItem.__table__,
        IngestDispatch.__table__,
        BrowserCapture.__table__,
        ContentItemStage.__table__,
        Segment.__table__,
    ]
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            Base.metadata.create_all(connection, tables=tables, checkfirst=False)
    except Exception as exc:
        try:
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            pytest.skip(f"isolated PostgreSQL schema unavailable: {type(exc).__name__}")

    def factory():
        db = Session(bind=engine, expire_on_commit=False)
        db.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        return db

    try:
        yield factory
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def add_user(factory):
    with factory() as db:
        user = AppUser()
        db.add(user)
        db.commit()
        return UserScope(user.id)


def add_item(factory, scope, *, platform_id, state="pending"):
    with factory() as db:
        item = ContentItem(
            public_id=uuid4().hex,
            user_id=scope.app_user_id,
            platform="youtube",
            platform_id=platform_id,
            kind="video",
            url=f"https://www.youtube.com/watch?v={platform_id}",
            title=platform_id,
            state=state,
        )
        db.add(item)
        db.flush()
        db.add(IngestDispatch(
            public_id=uuid4().hex,
            item_id=item.id,
            request_key=uuid4().hex,
            attempt=1,
            state="failed" if state == "failed" else "pending",
            error_code="queue_unavailable" if state == "failed" else None,
        ))
        db.commit()
        return item.public_id


def test_library_is_tenant_scoped_and_archive_excludes_agent_retrieval(library_factory):
    scope_a = add_user(library_factory)
    scope_b = add_user(library_factory)
    public_a = add_item(library_factory, scope_a, platform_id="dQw4w9WgXcQ", state="ready")
    public_b = add_item(library_factory, scope_b, platform_id="9bZkp7q19f0", state="ready")
    service = ContentLibraryService(library_factory, lambda _dispatch_id: "task")

    assert service.get_item(scope_a, public_a).public_id == public_a
    with pytest.raises(LibraryNotFound):
        service.get_item(scope_a, public_b)

    service.archive(scope_a, public_a)
    with library_factory() as db:
        assert vector_search(db, [0.01] * 1536, user_id=scope_a.app_user_id) == []
        assert bm25_search(db, "anything", user_id=scope_a.app_user_id) == []


def test_latest_dispatch_uses_attempt_then_id_and_pending_failed_projects_failed(library_factory):
    scope = add_user(library_factory)
    public_id = add_item(library_factory, scope, platform_id="M7lc1UVf-VE")
    with library_factory() as db:
        item = db.scalar(select(ContentItem).where(ContentItem.public_id == public_id))
        first = db.scalar(select(IngestDispatch).where(IngestDispatch.item_id == item.id))
        first.state = "enqueued"
        db.add(IngestDispatch(
            public_id=uuid4().hex,
            item_id=item.id,
            request_key=uuid4().hex,
            attempt=2,
            state="failed",
            error_code="queue_unavailable",
        ))
        db.commit()

    detail = ContentLibraryService(library_factory, lambda _dispatch_id: "task").get_item(scope, public_id)

    assert detail.lifecycle == "failed"
    assert detail.error_code == "queue_unavailable"


def _add_old_dispatch(db, item_id):
    # BrowserCapture.dispatch_id is unique; attempt 0 stays older than the
    # item's own dispatch, so it never becomes the "latest" one.
    old = IngestDispatch(
        public_id=uuid4().hex,
        item_id=item_id,
        request_key=uuid4().hex,
        attempt=0,
        state="failed",
        error_code="queue_unavailable",
    )
    db.add(old)
    db.flush()
    return old.id


def _add_capture(db, *, item_id, user_id, dispatch_id, caption_status, state="ready"):
    available = caption_status == "available"
    db.add(BrowserCapture(
        public_id=uuid4().hex,
        app_user_id=user_id,
        item_id=item_id,
        dispatch_id=dispatch_id,
        request_key=uuid4().hex,
        body_hash="0" * 64,
        protocol_version="capture.v1",
        client_version="0.2.0",
        caption_status=caption_status,
        caption_source="official_cc" if available else None,
        language="en" if available else None,
        capture_metadata={},
        state=state,
    ))


def test_stage_reports_transcribing_for_a_caption_less_capture_and_the_dispatch_time(
    library_factory,
):
    scope = add_user(library_factory)
    public_id = add_item(library_factory, scope, platform_id="dQw4w9WgXcQ", state="fetching")
    with library_factory() as db:
        item = db.scalar(select(ContentItem).where(ContentItem.public_id == public_id))
        dispatch = db.scalar(select(IngestDispatch).where(IngestDispatch.item_id == item.id))
        dispatch.state = "running"
        _add_capture(
            db,
            item_id=item.id,
            user_id=scope.app_user_id,
            dispatch_id=dispatch.id,
            caption_status="unavailable",
        )
        db.commit()
        dispatch_updated_at = dispatch.updated_at
    service = ContentLibraryService(library_factory, lambda _dispatch_id: "task")

    detail = service.get_item(scope, public_id)
    page = service.list_items(scope)

    assert detail.lifecycle == "processing"
    assert detail.stage == "transcribing"
    assert detail.stage_updated_at == dispatch_updated_at
    assert [value.stage for value in page.items] == ["transcribing"]


def test_stage_ignores_failed_or_foreign_captures_and_available_captions(library_factory):
    scope = add_user(library_factory)
    other = add_user(library_factory)
    public_id = add_item(library_factory, scope, platform_id="9bZkp7q19f0", state="fetching")
    with library_factory() as db:
        item = db.scalar(select(ContentItem).where(ContentItem.public_id == public_id))
        dispatch = db.scalar(select(IngestDispatch).where(IngestDispatch.item_id == item.id))
        dispatch.state = "running"
        # A caption-less capture that is not ready, and one owned by another
        # tenant, must not mark the item as transcribing ...
        _add_capture(db, item_id=item.id, user_id=scope.app_user_id,
                     dispatch_id=dispatch.id, caption_status="unavailable", state="failed")
        db.commit()
    service = ContentLibraryService(library_factory, lambda _dispatch_id: "task")
    assert service.get_item(scope, public_id).stage == "fetching"

    with library_factory() as db:
        item = db.scalar(select(ContentItem).where(ContentItem.public_id == public_id))
        dispatch = db.scalar(select(IngestDispatch).where(IngestDispatch.item_id == item.id))
        _add_capture(db, item_id=item.id, user_id=other.app_user_id,
                     dispatch_id=_add_old_dispatch(db, item.id),
                     caption_status="unavailable")
        db.commit()
    assert service.get_item(scope, public_id).stage == "fetching"


# ---------------------------------------------------------------------------
# Ingest status model on real rows
# ---------------------------------------------------------------------------

def _seed(
    factory,
    scope,
    *,
    platform_id,
    state,
    fail_reason=None,
    dispatch_state="completed",
    error_code=None,
    asr_handoff_at=None,
    archived=False,
    deleted=False,
    platform="youtube",
):
    with factory() as db:
        item = ContentItem(
            public_id=uuid4().hex,
            user_id=scope.app_user_id,
            platform=platform,
            platform_id=platform_id,
            kind="video",
            url=f"https://example.test/{platform_id}",
            title=platform_id,
            state=state,
            fail_reason=fail_reason,
            archived_at=datetime.now(UTC) if archived else None,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        db.add(item)
        db.flush()
        dispatch = IngestDispatch(
            public_id=uuid4().hex,
            item_id=item.id,
            request_key=uuid4().hex,
            attempt=1,
            state=dispatch_state,
            error_code=error_code,
            asr_handoff_at=asr_handoff_at,
        )
        db.add(dispatch)
        db.flush()
        values = (item.public_id, item.id, dispatch.id)
        db.commit()
        return values


def test_enrichment_rows_are_batched_and_never_change_the_lifecycle(library_factory):
    scope = add_user(library_factory)
    ready_pid, ready_id, _ = _seed(
        library_factory, scope, platform_id="ready-enriched", state="ready"
    )
    fresh_pid, _, _ = _seed(
        library_factory, scope, platform_id="ready-fresh", state="ready"
    )
    failed_pid, failed_id, _ = _seed(
        library_factory,
        scope,
        platform_id="needs-asr",
        state="needs_asr",
        fail_reason="asr_disabled",
    )
    with library_factory() as db:
        mark_stage(db, ready_id, "classify", "done")
        mark_stage(db, ready_id, "summary", "failed", error_code="asr_provider_failed")
        db.commit()
    service = ContentLibraryService(
        library_factory,
        lambda _dispatch_id: "task",
        enrichment_stages=("classify", "summary"),
    )
    engine = get_engine()
    stage_reads = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        if "content_item_stage" in statement and statement.lstrip().upper().startswith(
            "SELECT"
        ):
            stage_reads.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        page = service.list_items(scope)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    # One batched read for the whole page, not one per item.
    assert len(stage_reads) == 1
    by_id = {value.public_id: value for value in page.items}

    enriched = by_id[ready_pid]
    assert (enriched.lifecycle, enriched.body_searchable, enriched.error_code) == (
        "ready",
        True,
        None,
    )
    assert [(v.key, v.status) for v in enriched.stages] == [
        ("body", "done"),
        ("classify", "done"),
        ("summary", "failed"),
    ]
    assert enriched.stages[2].reason.code == "asr_provider_failed"
    assert enriched.stages[2].reason.category == "asr_failed"
    assert enriched.reason is None
    assert "retry" not in enriched.available_actions

    fresh = by_id[fresh_pid]
    assert [(v.key, v.status) for v in fresh.stages] == [
        ("body", "done"),
        ("classify", "pending"),
        ("summary", "pending"),
    ]

    unfinished = by_id[failed_pid]
    assert (unfinished.lifecycle, unfinished.body_searchable) == ("needs_action", False)
    assert [(v.key, v.status) for v in unfinished.stages] == [
        ("body", "failed"),
        ("classify", "skipped"),
        ("summary", "skipped"),
    ]
    assert unfinished.reason.code == "asr_disabled"
    assert "retry" in unfinished.available_actions

    # The detail view reads the same rows.
    detail = service.get_item(scope, ready_pid)
    assert [(v.key, v.status) for v in detail.stages] == [
        ("body", "done"),
        ("classify", "done"),
        ("summary", "failed"),
    ]


def test_enrichment_rows_are_ignored_while_no_stage_is_enabled(library_factory):
    scope = add_user(library_factory)
    pid, item_id, _ = _seed(library_factory, scope, platform_id="rows-ignored", state="ready")
    with library_factory() as db:
        mark_stage(db, item_id, "classify", "done")
        db.commit()

    detail = ContentLibraryService(library_factory, lambda _id: "task").get_item(scope, pid)

    assert [v.key for v in detail.stages] == ["body"]


def test_availability_counts_are_tenant_scoped_and_skip_archived_and_deleted(
    library_factory,
):
    scope = add_user(library_factory)
    other = add_user(library_factory)
    seed = lambda **kw: _seed(library_factory, scope, **kw)  # noqa: E731
    seed(platform_id="a-ready", state="ready")
    seed(platform_id="a-failed", state="failed", dispatch_state="failed",
         error_code="asr_no_speech")
    seed(platform_id="a-needs-asr", state="needs_asr", fail_reason="asr_disabled")
    seed(platform_id="a-needs-ext", state="needs_extension")
    seed(platform_id="a-queued", state="pending", dispatch_state="enqueued")
    seed(platform_id="a-running", state="fetching", dispatch_state="running")
    seed(platform_id="a-archived-failed", state="failed", dispatch_state="failed",
         archived=True)
    seed(platform_id="a-deleted-failed", state="failed", dispatch_state="failed",
         deleted=True)
    _seed(library_factory, other, platform_id="b-failed-1", state="failed",
          dispatch_state="failed")
    _seed(library_factory, other, platform_id="b-failed-2", state="needs_asr")
    service = ContentLibraryService(library_factory, lambda _dispatch_id: "task")

    counts = service.availability_counts(scope)
    other_counts = service.availability_counts(other)

    assert (counts.unsearchable_total, counts.processing_total) == (3, 2)
    assert (other_counts.unsearchable_total, other_counts.processing_total) == (2, 0)
    # The list reports the same whole-library counts for every filter.
    for kwargs in (
        {},
        {"include_archived": True},
        {"lifecycle": "ready"},
        {"lifecycle": "archived"},
        {"search": "a-failed"},
    ):
        page = service.list_items(scope, **kwargs)
        assert (page.unsearchable_total, page.processing_total) == (3, 2), kwargs


def test_url_import_asr_marker_reports_transcribing_without_a_capture(library_factory):
    scope = add_user(library_factory)
    marked_pid, _, _ = _seed(
        library_factory, scope, platform_id="url-asr", state="fetching",
        dispatch_state="enqueued", asr_handoff_at=datetime.now(UTC),
        platform="bilibili",
    )
    plain_pid, _, _ = _seed(
        library_factory, scope, platform_id="url-plain", state="fetching",
        dispatch_state="running", platform="bilibili",
    )
    service = ContentLibraryService(library_factory, lambda _dispatch_id: "task")

    marked = service.get_item(scope, marked_pid)
    plain = service.get_item(scope, plain_pid)
    listed = {value.public_id: value for value in service.list_items(scope).items}

    assert marked.stages[0].detail == "transcribing"
    assert plain.stages[0].detail == "fetching"
    assert listed[marked_pid].stages[0].detail == "transcribing"
    # The legacy ``stage`` field keeps its meaning for older clients.
    assert marked.stage == "fetching"
    assert marked.lifecycle == plain.lifecycle == "processing"


def test_retry_restarts_an_unfinished_item_once_and_replays_idempotently(library_factory):
    scope = add_user(library_factory)
    pid, item_id, _ = _seed(
        library_factory, scope, platform_id="retry-needs-asr", state="needs_asr",
        fail_reason="asr_disabled",
    )
    published = []
    service = ContentLibraryService(
        library_factory, lambda dispatch_id: published.append(dispatch_id) or "task"
    )

    first = service.retry(scope, pid, request_key="retry-1")
    replay = service.retry(scope, pid, request_key="retry-1")

    assert first.lifecycle == replay.lifecycle == "queued"
    assert first.reason is None and first.body_searchable is False
    assert len(published) == 1
    with library_factory() as db:
        item = db.get(ContentItem, item_id)
        attempts = sorted(
            db.scalars(
                select(IngestDispatch.attempt).where(IngestDispatch.item_id == item_id)
            )
        )
    assert (item.state, item.fail_reason) == ("pending", None)
    assert attempts == [1, 2]

    # One dispatch at a time: a second request while it is in flight is refused.
    with pytest.raises(LibraryConflict) as caught:
        service.retry(scope, pid, request_key="retry-2")
    assert caught.value.error_code == "retry_unavailable"
    assert len(published) == 1


def test_retry_refuses_login_required_ready_archived_and_foreign_items(library_factory):
    scope = add_user(library_factory)
    other = add_user(library_factory)
    published = []
    service = ContentLibraryService(
        library_factory, lambda dispatch_id: published.append(dispatch_id) or "task"
    )
    refused = {
        "login": _seed(library_factory, scope, platform_id="login", state="needs_extension",
                       fail_reason="bilibili_login_required", platform="bilibili")[0],
        "ready": _seed(library_factory, scope, platform_id="ready-retry", state="ready")[0],
        "archived": _seed(library_factory, scope, platform_id="archived-retry",
                          state="needs_asr", archived=True)[0],
    }
    foreign = _seed(library_factory, other, platform_id="foreign", state="needs_asr")[0]

    for name, pid in refused.items():
        with pytest.raises(LibraryConflict) as caught:
            service.retry(scope, pid, request_key=f"retry-{name}")
        assert caught.value.error_code == "retry_unavailable", name
    with pytest.raises(LibraryNotFound):
        service.retry(scope, foreign, request_key="retry-foreign")

    assert published == []
    with library_factory() as db:
        assert db.scalar(select(IngestDispatch).where(IngestDispatch.attempt > 1)) is None


def test_retry_over_quota_is_rejected_and_keeps_the_reason(library_factory):
    scope = add_user(library_factory)
    pid, item_id, _ = _seed(
        library_factory, scope, platform_id="quota-retry", state="needs_asr",
        fail_reason="asr_disabled",
    )
    published = []
    service = ContentLibraryService(
        library_factory,
        lambda dispatch_id: published.append(dispatch_id) or "task",
        quota_policy=IngestQuotaPolicy(daily_dispatch_limit_per_tenant=1),
    )

    with pytest.raises(LibraryConflict) as caught:
        service.retry(scope, pid, request_key="retry-quota")

    assert caught.value.error_code == "quota_exceeded"
    assert published == []
    with library_factory() as db:
        item = db.get(ContentItem, item_id)
        count = db.scalar(
            select(func.count(IngestDispatch.id)).where(IngestDispatch.item_id == item_id)
        )
    assert (item.state, item.fail_reason, count) == ("needs_asr", "asr_disabled", 1)
