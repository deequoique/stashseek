from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.channels.types import TenantContext
from app.connectors.base import TransientFetchError
from app.db import get_engine
from app.ingest.submission import (
    IngestSubmissionService,
    ItemReference,
    PreparedBatch,
    PreparedItem,
)
from app.ingest.tasks import (
    IngestCompletionPublisher,
    _claim_completion_event,
    _complete_dispatch,
    _mark_completion_enqueued,
    _mark_dispatch_failed,
    process_dispatch,
)
from app.models import (
    AppUser,
    Base,
    ChannelIdentity,
    ContentItem,
    ConversationThread,
    IngestCompletionEvent,
    IngestDispatch,
)


@pytest.fixture
def submission_factory(monkeypatch):
    engine = get_engine()
    schema = f"test_save_{uuid4().hex}"
    tables = [
        AppUser.__table__,
        ChannelIdentity.__table__,
        ConversationThread.__table__,
        ContentItem.__table__,
        IngestDispatch.__table__,
        IngestCompletionEvent.__table__,
    ]
    monkeypatch.setattr(
        "app.ingest.tasks.publish_ingest_completion_event",
        lambda _event_id: "completion-task-id",
    )
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(
                text(f'SET LOCAL search_path TO "{schema}", public')
            )
            Base.metadata.create_all(
                connection,
                tables=tables,
                checkfirst=False,
            )
    except Exception as exc:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                )
        finally:
            pytest.skip(
                "isolated PostgreSQL schema unavailable: "
                f"{type(exc).__name__}"
            )

    def factory():
        db = Session(bind=engine, expire_on_commit=False)
        db.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
        return db

    try:
        yield factory
    finally:
        with engine.begin() as connection:
            connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )


def _tenant(factory, number):
    with factory() as db:
        user = AppUser()
        db.add(user)
        db.commit()
        return TenantContext(
            user.id,
            number,
            "telegram",
            "account",
            f"external-{number}",
        )


def test_same_request_replays_queued_and_tenants_remain_isolated(
    submission_factory,
):
    tenant_a = _tenant(submission_factory, 1)
    tenant_b = _tenant(submission_factory, 2)
    published = []
    service = IngestSubmissionService(
        submission_factory,
        lambda dispatch_id: published.append(dispatch_id)
        or f"task-{dispatch_id}",
    )
    url = "https://youtu.be/dQw4w9WgXcQ"

    first = service.submit_urls(
        tenant_a, [url], why_saved=None, request_key="a:first"
    )
    replay = service.submit_urls(
        tenant_a, [url], why_saved=None, request_key="a:first"
    )
    other_request = service.submit_urls(
        tenant_a, [url], why_saved=None, request_key="a:second"
    )
    other_tenant = service.submit_urls(
        tenant_b, [url], why_saved=None, request_key="b:first"
    )

    assert first.results[0].status == "queued"
    assert replay.results[0].status == "queued"
    assert other_request.results[0].status == "already_exists"
    assert other_tenant.results[0].status == "queued"
    assert len(published) == 2
    with submission_factory() as db:
        items = list(
            db.scalars(select(ContentItem).order_by(ContentItem.user_id))
        )
    assert [item.user_id for item in items] == [
        tenant_a.app_user_id,
        tenant_b.app_user_id,
    ]


def test_pending_same_request_never_claims_publish_succeeded(
    submission_factory,
):
    tenant = _tenant(submission_factory, 3)
    publisher_entered = Event()
    release_publisher = Event()

    def publisher(dispatch_id):
        publisher_entered.set()
        assert release_publisher.wait(timeout=5)
        return f"task-{dispatch_id}"

    service = IngestSubmissionService(submission_factory, publisher)
    url = "https://youtu.be/9bZkp7q19f0"

    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(
            service.submit_urls,
            tenant,
            [url],
            why_saved=None,
            request_key="pending:request",
        )
        assert publisher_entered.wait(timeout=5)
        while_pending = service.submit_urls(
            tenant,
            [url],
            why_saved=None,
            request_key="pending:request",
        )
        release_publisher.set()
        first = first_future.result(timeout=5)

    after_publish = service.submit_urls(
        tenant,
        [url],
        why_saved=None,
        request_key="pending:request",
    )

    assert while_pending.results[0].status == "already_exists"
    assert first.results[0].status == "queued"
    assert after_publish.results[0].status == "queued"


def test_failed_publish_is_stable_for_same_request_and_retryable_by_new_request(
    submission_factory,
):
    tenant = _tenant(submission_factory, 4)
    calls = []

    def publisher(dispatch_id):
        calls.append(dispatch_id)
        if len(calls) == 1:
            raise RuntimeError("private broker failure")
        return f"task-{dispatch_id}"

    service = IngestSubmissionService(submission_factory, publisher)
    url = "https://youtu.be/M7lc1UVf-VE"

    failed = service.submit_urls(
        tenant, [url], why_saved=None, request_key="request:one"
    )
    replay = service.submit_urls(
        tenant, [url], why_saved=None, request_key="request:one"
    )
    recovered = service.submit_urls(
        tenant, [url], why_saved=None, request_key="request:two"
    )
    active = service.submit_urls(
        tenant, [url], why_saved=None, request_key="request:three"
    )

    assert failed.results[0].status == "queue_unavailable"
    assert replay.results[0].status == "queue_unavailable"
    assert recovered.results[0].status == "queued"
    assert active.results[0].status == "already_exists"
    assert len(calls) == 2
    with submission_factory() as db:
        dispatches = list(
            db.scalars(
                select(IngestDispatch).order_by(IngestDispatch.attempt)
            )
        )
    assert [dispatch.attempt for dispatch in dispatches] == [1, 2]
    assert [dispatch.state for dispatch in dispatches] == [
        "failed",
        "enqueued",
    ]


def test_concurrent_duplicate_submission_creates_one_item_and_dispatch(
    submission_factory,
):
    tenant = _tenant(submission_factory, 5)
    barrier = Barrier(6)
    published = []
    published_lock = Lock()

    def publisher(dispatch_id):
        with published_lock:
            published.append(dispatch_id)
        return f"task-{dispatch_id}"

    service = IngestSubmissionService(submission_factory, publisher)

    def submit(_index):
        barrier.wait(timeout=5)
        return service.submit_urls(
            tenant,
            ["https://youtu.be/aqz-KE-bpKQ"],
            why_saved=None,
            request_key="concurrent:request",
        ).results[0].status

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(submit, range(6)))

    assert set(statuses) <= {"queued", "already_exists"}
    assert "queued" in statuses
    assert "create_failed" not in statuses
    assert len(published) == 1
    with submission_factory() as db:
        assert len(list(db.scalars(select(ContentItem)))) == 1
        assert len(list(db.scalars(select(IngestDispatch)))) == 1


def test_concurrent_distinct_submissions_enforce_one_active_item_per_tenant(
    submission_factory,
):
    tenant = _tenant(submission_factory, 51)
    barrier = Barrier(2)
    published = []
    published_lock = Lock()

    def publisher(dispatch_id):
        with published_lock:
            published.append(dispatch_id)
        return f"task-{dispatch_id}"

    service = IngestSubmissionService(
        submission_factory,
        publisher,
        max_active_per_tenant=1,
    )

    def submit(index):
        barrier.wait(timeout=5)
        return service.submit_urls(
            tenant,
            [f"https://youtu.be/quotaC0000{index}"],
            why_saved=None,
            request_key=f"quota:active:{index}",
        ).results[0].status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, (1, 2)))

    assert sorted(statuses) == ["queued", "quota_exceeded"]
    assert len(published) == 1
    with submission_factory() as db:
        assert len(list(db.scalars(select(ContentItem)))) == 1
        assert len(list(db.scalars(select(IngestDispatch)))) == 1


def test_concurrent_distinct_tenants_enforce_one_active_item_globally(
    submission_factory,
):
    tenant_a = _tenant(submission_factory, 54)
    tenant_b = _tenant(submission_factory, 55)
    barrier = Barrier(2)
    published = []
    published_lock = Lock()

    def publisher(dispatch_id):
        with published_lock:
            published.append(dispatch_id)
        return f"task-{dispatch_id}"

    service = IngestSubmissionService(
        submission_factory,
        publisher,
        max_active_global=1,
        max_active_per_tenant=10,
        daily_new_item_limit=10,
        max_items_per_tenant=10,
    )

    def submit(index):
        tenant = tenant_a if index == 1 else tenant_b
        barrier.wait(timeout=5)
        return service.submit_urls(
            tenant,
            [f"https://youtu.be/quotaG0000{index}"],
            why_saved=None,
            request_key=f"quota:global:{index}",
        ).results[0].status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(submit, (1, 2)))

    assert sorted(statuses) == ["queued", "quota_exceeded"]
    assert len(published) == 1
    with submission_factory() as db:
        assert len(list(db.scalars(select(ContentItem)))) == 1
        assert len(list(db.scalars(select(IngestDispatch)))) == 1


def test_daily_new_item_limit_rejects_the_next_distinct_item(
    submission_factory,
):
    tenant = _tenant(submission_factory, 52)
    published = []
    service = IngestSubmissionService(
        submission_factory,
        lambda dispatch_id: published.append(dispatch_id)
        or f"task-{dispatch_id}",
        daily_new_item_limit=1,
    )

    first = service.submit_urls(
        tenant,
        ["https://youtu.be/quotaD00001"],
        why_saved=None,
        request_key="quota:daily:first",
    )
    second = service.submit_urls(
        tenant,
        ["https://youtu.be/quotaD00002"],
        why_saved=None,
        request_key="quota:daily:second",
    )

    assert first.results[0].status == "queued"
    assert second.results[0].status == "quota_exceeded"
    assert len(published) == 1
    with submission_factory() as db:
        assert len(list(db.scalars(select(ContentItem)))) == 1
        assert len(list(db.scalars(select(IngestDispatch)))) == 1


def test_daily_dispatch_limit_counts_failed_item_retries(
    submission_factory,
):
    tenant = _tenant(submission_factory, 54)
    published = []

    def unavailable(dispatch_id):
        published.append(dispatch_id)
        raise RuntimeError("private broker failure")

    service = IngestSubmissionService(
        submission_factory,
        unavailable,
        daily_dispatch_limit_per_tenant=1,
    )
    url = "https://youtu.be/quotaR00001"

    first = service.submit_urls(
        tenant,
        [url],
        why_saved=None,
        request_key="quota:retry:first",
    )
    second = service.submit_urls(
        tenant,
        [url],
        why_saved=None,
        request_key="quota:retry:second",
    )

    assert first.results[0].status == "queue_unavailable"
    assert second.results[0].status == "quota_exceeded"
    assert len(published) == 1
    with submission_factory() as db:
        assert len(list(db.scalars(select(ContentItem)))) == 1
        assert len(list(db.scalars(select(IngestDispatch)))) == 1


def test_total_item_limit_counts_existing_items_before_creating_a_new_one(
    submission_factory,
):
    tenant = _tenant(submission_factory, 53)
    with submission_factory() as db:
        db.add(
            ContentItem(
                public_id=uuid4().hex,
                user_id=tenant.app_user_id,
                platform="youtube",
                platform_id="quotaT00001",
                kind="video",
                url="https://www.youtube.com/watch?v=quotaT00001",
                state="ready",
            )
        )
        db.commit()
    published = []
    service = IngestSubmissionService(
        submission_factory,
        lambda dispatch_id: published.append(dispatch_id)
        or f"task-{dispatch_id}",
        max_items_per_tenant=1,
    )

    result = service.submit_urls(
        tenant,
        ["https://youtu.be/quotaT00002"],
        why_saved=None,
        request_key="quota:total:second",
    )

    assert result.results[0].status == "quota_exceeded"
    assert published == []
    with submission_factory() as db:
        assert len(list(db.scalars(select(ContentItem)))) == 1
        assert list(db.scalars(select(IngestDispatch))) == []


def test_fast_worker_transition_is_not_overwritten_by_publish_ack(
    submission_factory,
):
    tenant = _tenant(submission_factory, 6)

    def publisher(dispatch_id):
        with submission_factory() as db:
            dispatch = db.get(IngestDispatch, dispatch_id)
            dispatch.state = "running"
            db.commit()
        return f"task-{dispatch_id}"

    service = IngestSubmissionService(submission_factory, publisher)
    result = service.submit_urls(
        tenant,
        ["https://youtu.be/ScMzIvxBSi4"],
        why_saved=None,
        request_key="fast-worker",
    )

    assert result.results[0].status == "queued"
    with submission_factory() as db:
        dispatch = db.scalar(select(IngestDispatch))
    assert dispatch.state == "running"
    assert dispatch.task_id is None


def _worker_dispatch(factory, tenant, *, suffix, state, task_id):
    with factory() as db:
        item = ContentItem(
            user_id=tenant.app_user_id,
            platform="youtube",
            platform_id=f"worker{suffix:05d}",
            kind="video",
            url=(
                "https://www.youtube.com/watch?v="
                f"worker{suffix:05d}"
            ),
            state="pending",
        )
        db.add(item)
        db.flush()
        dispatch = IngestDispatch(
            public_id=uuid4().hex,
            item_id=item.id,
            request_key=f"worker:{suffix}",
            attempt=1,
            state=state,
            task_id=task_id,
        )
        db.add(dispatch)
        db.commit()
        return item.id, dispatch.id


def test_dispatch_worker_claim_retry_and_completion_are_conditional_pg(
    submission_factory,
):
    tenant = _tenant(submission_factory, 70)
    item_id, dispatch_id = _worker_dispatch(
        submission_factory,
        tenant,
        suffix=1,
        state="enqueued",
        task_id="task-current",
    )
    attempts = []

    def transient(current_item_id):
        attempts.append(current_item_id)
        raise TransientFetchError("private provider detail")

    with pytest.raises(TransientFetchError) as caught:
        process_dispatch(
            dispatch_id,
            task_id="task-current",
            processor=transient,
            session_factory=submission_factory,
        )
    assert str(caught.value) == "transient_fetch_failed"
    assert "private provider detail" not in repr(caught.value)
    with submission_factory() as db:
        dispatch = db.get(IngestDispatch, dispatch_id)
        item = db.get(ContentItem, item_id)
        event = db.scalar(
            select(IngestCompletionEvent).where(
                IngestCompletionEvent.dispatch_id == dispatch_id
            )
        )
        assert (dispatch.state, dispatch.task_id) == (
            "enqueued",
            "task-current",
        )
        assert event is None
        assert item.state == "pending"
        assert item.user_id == tenant.app_user_id

    assert process_dispatch(
        dispatch_id,
        task_id="task-stale",
        processor=lambda _item_id: pytest.fail(
            "stale task processed the item"
        ),
        session_factory=submission_factory,
    ) == "duplicate"
    assert attempts == [item_id]

    def complete_ready(current_item_id):
        assert current_item_id == item_id
        with submission_factory() as db:
            item = db.get(ContentItem, current_item_id)
            item.state = "ready"
            db.commit()
        return "ready"

    assert process_dispatch(
        dispatch_id,
        task_id="task-current",
        processor=complete_ready,
        session_factory=submission_factory,
    ) == "ready"
    assert process_dispatch(
        dispatch_id,
        task_id="task-current",
        processor=lambda _item_id: pytest.fail(
            "completed dispatch was processed twice"
        ),
        session_factory=submission_factory,
    ) == "duplicate"
    with submission_factory() as db:
        dispatch = db.get(IngestDispatch, dispatch_id)
        item = db.get(ContentItem, item_id)
        event = db.scalar(
            select(IngestCompletionEvent).where(
                IngestCompletionEvent.dispatch_id == dispatch_id
            )
        )
        assert dispatch.state == "completed"
        assert dispatch.error_code is None
        assert event is not None
        assert (event.outcome, event.item_state, event.error_code) == (
            "completed",
            "ready",
            None,
        )
        assert item.user_id == tenant.app_user_id


def test_stale_task_failure_cannot_overwrite_current_delivery_pg(
    submission_factory,
):
    tenant = _tenant(submission_factory, 71)
    item_id, dispatch_id = _worker_dispatch(
        submission_factory,
        tenant,
        suffix=2,
        state="running",
        task_id="task-current",
    )

    _mark_dispatch_failed(
        dispatch_id,
        RuntimeError("private stale failure"),
        task_id="task-stale",
        session_factory=submission_factory,
    )
    with submission_factory() as db:
        dispatch = db.get(IngestDispatch, dispatch_id)
        item = db.get(ContentItem, item_id)
        assert dispatch.state == "running"
        assert dispatch.error_code is None
        assert item.state == "pending"
        assert item.fail_reason is None

    _mark_dispatch_failed(
        dispatch_id,
        RuntimeError("private current failure"),
        task_id="task-current",
        session_factory=submission_factory,
    )
    _mark_dispatch_failed(
        dispatch_id,
        RuntimeError("private repeated failure hook"),
        task_id="task-current",
        session_factory=submission_factory,
    )
    with submission_factory() as db:
        dispatch = db.get(IngestDispatch, dispatch_id)
        item = db.get(ContentItem, item_id)
        events = list(
            db.scalars(
                select(IngestCompletionEvent).where(
                    IngestCompletionEvent.dispatch_id == dispatch_id
                )
            )
        )
        assert len(events) == 1
        event = events[0]
        assert dispatch.state == "failed"
        assert dispatch.error_code == "ingestion_failed"
        assert event is not None
        assert (event.outcome, event.item_state, event.error_code) == (
            "failed",
            "failed",
            "ingestion_failed",
        )
        assert item.state == "failed"
        assert item.fail_reason == "ingestion_failed"
        assert item.user_id == tenant.app_user_id


def test_generic_worker_failure_exposes_and_persists_only_safe_code_pg(
    submission_factory,
):
    tenant = _tenant(submission_factory, 72)
    item_id, dispatch_id = _worker_dispatch(
        submission_factory,
        tenant,
        suffix=3,
        state="enqueued",
        task_id="task-current",
    )

    def failed_processor(_item_id):
        raise RuntimeError("private URL/provider payload")

    with pytest.raises(RuntimeError) as caught:
        process_dispatch(
            dispatch_id,
            task_id="task-current",
            processor=failed_processor,
            session_factory=submission_factory,
        )

    assert str(caught.value) == "ingestion_failed"
    assert "private URL/provider payload" not in repr(caught.value)
    with submission_factory() as db:
        dispatch = db.get(IngestDispatch, dispatch_id)
        item = db.get(ContentItem, item_id)
        event = db.scalar(
            select(IngestCompletionEvent).where(
                IngestCompletionEvent.dispatch_id == dispatch_id
            )
        )
        assert dispatch.state == "failed"
        assert dispatch.task_id == "task-current"
        assert dispatch.error_code == "ingestion_failed"
        assert event is not None
        assert (event.outcome, event.item_state, event.error_code) == (
            "failed",
            "failed",
            "ingestion_failed",
        )
        assert item.state == "failed"
        assert item.fail_reason == "ingestion_failed"
        assert item.user_id == tenant.app_user_id


def test_terminal_dispatch_and_completion_event_roll_back_together_pg(
    submission_factory,
    monkeypatch,
):
    tenant = _tenant(submission_factory, 73)
    item_id, dispatch_id = _worker_dispatch(
        submission_factory,
        tenant,
        suffix=4,
        state="running",
        task_id="task-current",
    )
    with submission_factory() as db:
        item = db.get(ContentItem, item_id)
        item.state = "ready"
        db.commit()

    def fail_event_insert(*_args, **_kwargs):
        raise RuntimeError("simulated_event_insert_failure")

    monkeypatch.setattr(
        "app.ingest.tasks._ensure_completion_event", fail_event_insert
    )

    with pytest.raises(RuntimeError, match="simulated_event_insert_failure"):
        _complete_dispatch(
            dispatch_id,
            "task-current",
            process_state="ready",
            session_factory=submission_factory,
        )

    with submission_factory() as db:
        dispatch = db.get(IngestDispatch, dispatch_id)
        event = db.scalar(
            select(IngestCompletionEvent).where(
                IngestCompletionEvent.dispatch_id == dispatch_id
            )
        )
        assert dispatch.state == "running"
        assert dispatch.error_code is None
        assert event is None


def test_late_failure_after_item_ready_converges_to_completed_event_pg(
    submission_factory,
):
    tenant = _tenant(submission_factory, 74)
    item_id, dispatch_id = _worker_dispatch(
        submission_factory,
        tenant,
        suffix=5,
        state="running",
        task_id="task-current",
    )
    with submission_factory() as db:
        item = db.get(ContentItem, item_id)
        item.state = "ready"
        db.commit()

    _mark_dispatch_failed(
        dispatch_id,
        RuntimeError("late worker failure after ready commit"),
        task_id="task-current",
        session_factory=submission_factory,
    )

    with submission_factory() as db:
        dispatch = db.get(IngestDispatch, dispatch_id)
        item = db.get(ContentItem, item_id)
        events = list(
            db.scalars(
                select(IngestCompletionEvent).where(
                    IngestCompletionEvent.dispatch_id == dispatch_id
                )
            )
        )
        assert (dispatch.state, dispatch.error_code) == ("completed", None)
        assert (item.state, item.fail_reason) == ("ready", None)
        assert len(events) == 1
        assert (events[0].outcome, events[0].item_state, events[0].error_code) == (
            "completed",
            "ready",
            None,
        )


@pytest.mark.parametrize(
    ("item_state", "suffix", "tenant_number"),
    [
        ("ready", 10, 80),
        ("needs_extension", 11, 81),
        ("needs_asr", 12, 82),
    ],
)
def test_normal_terminal_states_create_completed_snapshots_pg(
    submission_factory,
    item_state,
    suffix,
    tenant_number,
):
    tenant = _tenant(submission_factory, tenant_number)
    item_id, dispatch_id = _worker_dispatch(
        submission_factory,
        tenant,
        suffix=suffix,
        state="running",
        task_id="task-current",
    )
    with submission_factory() as db:
        item = db.get(ContentItem, item_id)
        item.state = item_state
        db.commit()

    _complete_dispatch(
        dispatch_id,
        "task-current",
        process_state=item_state,
        session_factory=submission_factory,
    )

    with submission_factory() as db:
        event = db.scalar(
            select(IngestCompletionEvent).where(
                IngestCompletionEvent.dispatch_id == dispatch_id
            )
        )
        assert event is not None
        assert (event.outcome, event.item_state, event.error_code) == (
            "completed",
            item_state,
            None,
        )


def _pending_completion_event(
    submission_factory,
    *,
    tenant_number,
    suffix,
    item_state="ready",
):
    tenant = _tenant(submission_factory, tenant_number)
    item_id, dispatch_id = _worker_dispatch(
        submission_factory,
        tenant,
        suffix=suffix,
        state="running",
        task_id="task-current",
    )
    with submission_factory() as db:
        item = db.get(ContentItem, item_id)
        item.state = item_state
        db.commit()
    event_id = _complete_dispatch(
        dispatch_id,
        "task-current",
        process_state=item_state,
        session_factory=submission_factory,
    )
    assert event_id is not None
    return event_id


def test_completion_sweep_recovers_stale_claim_and_isolates_peer_failure_pg(
    submission_factory,
):
    failed_event_id = _pending_completion_event(
        submission_factory,
        tenant_number=83,
        suffix=13,
    )
    stale_event_id = _pending_completion_event(
        submission_factory,
        tenant_number=84,
        suffix=14,
        item_state="needs_asr",
    )
    with submission_factory() as db:
        stale = db.get(IngestCompletionEvent, stale_event_id)
        stale.publish_state = "claimed"
        stale.claim_token = "abandoned-claim"
        stale.claimed_at = datetime.now(UTC) - timedelta(seconds=600)
        db.commit()

    published = []

    def publish(event_id):
        published.append(event_id)
        if event_id == failed_event_id:
            raise RuntimeError("one broker failure")
        return f"completion-task-{event_id}"

    result = IngestCompletionPublisher(
        submission_factory,
        publisher=publish,
        batch_size=20,
        claim_timeout_seconds=300,
        max_duration_seconds=5,
    ).sweep_once()

    assert result.claimed == 2
    assert (result.enqueued, result.failed) == (1, 1)
    assert published == [failed_event_id, stale_event_id]
    with submission_factory() as db:
        failed_event = db.get(IngestCompletionEvent, failed_event_id)
        stale_event = db.get(IngestCompletionEvent, stale_event_id)
        assert failed_event.publish_state == "pending"
        assert stale_event.publish_state == "enqueued"
        assert stale_event.publish_task_id == f"completion-task-{stale_event_id}"


def test_completion_ack_crash_republishes_same_stable_event_id_pg(
    submission_factory,
    monkeypatch,
):
    event_id = _pending_completion_event(
        submission_factory,
        tenant_number=85,
        suffix=15,
    )
    published = []

    def publish(current_event_id):
        published.append(current_event_id)
        return f"completion-task-{current_event_id}"

    def crash_before_ack(*_args, **_kwargs):
        raise RuntimeError("simulated_ack_crash")

    monkeypatch.setattr(
        "app.ingest.tasks._mark_completion_enqueued", crash_before_ack
    )
    first = IngestCompletionPublisher(
        submission_factory,
        publisher=publish,
        max_duration_seconds=5,
    ).sweep_once()
    assert (first.enqueued, first.failed) == (0, 1)

    monkeypatch.setattr(
        "app.ingest.tasks._mark_completion_enqueued",
        _mark_completion_enqueued,
    )
    second = IngestCompletionPublisher(
        submission_factory,
        publisher=publish,
        max_duration_seconds=5,
    ).sweep_once()

    assert (second.enqueued, second.failed) == (1, 0)
    assert published == [event_id, event_id]
    with submission_factory() as db:
        event = db.get(IngestCompletionEvent, event_id)
        assert event.publish_state == "enqueued"


def test_physical_item_purge_cascades_completion_event_before_late_publish_pg(
    submission_factory,
):
    event_id = _pending_completion_event(
        submission_factory,
        tenant_number=86,
        suffix=16,
    )
    with submission_factory() as db:
        event = db.get(IngestCompletionEvent, event_id)
        item_id = event.item_id
        db.delete(db.get(ContentItem, item_id))
        db.commit()

    assert (
        _claim_completion_event(
            event_id,
            session_factory=submission_factory,
            claim_timeout_seconds=300,
        )
        is None
    )


def test_favorites_imported_first_part_is_not_duplicated_by_a_plain_link(
    submission_factory,
):
    tenant = _tenant(submission_factory, 101)
    published = []
    service = IngestSubmissionService(
        submission_factory,
        lambda dispatch_id: published.append(dispatch_id)
        or f"task-{dispatch_id}",
    )
    bv = "BV1xx411c7mD"
    part = PreparedBatch((
        PreparedItem(0, reference=ItemReference(
            "bilibili", f"{bv}_cid123",
            f"https://www.bilibili.com/video/{bv}?p=1",
            bilibili_bvid=bv, bilibili_cid="123", bilibili_page=1,
        )),
    ))

    first = service.submit_prepared(
        tenant, part, why_saved=None, request_key="parts:first",
    )
    second = service.submit_urls(
        tenant, [f"https://www.bilibili.com/video/{bv}"],
        why_saved=None, request_key="link:second",
    )

    assert first.results[0].status == "queued"
    assert second.results[0].status == "already_exists"
    with submission_factory() as db:
        items = list(db.scalars(
            select(ContentItem).where(ContentItem.user_id == tenant.app_user_id)
        ))
    assert len(items) == 1


def test_trashed_legacy_item_does_not_block_part_admission(submission_factory):
    tenant = _tenant(submission_factory, 102)
    bv = "BV1xx411c7mD"
    with submission_factory() as db:
        db.add(ContentItem(
            public_id=uuid4().hex, user_id=tenant.app_user_id,
            platform="bilibili", platform_id=bv, kind="video",
            url=f"https://www.bilibili.com/video/{bv}",
            state="ready", deleted_at=datetime.now(UTC),
        ))
        db.commit()
    service = IngestSubmissionService(
        submission_factory, lambda dispatch_id: f"task-{dispatch_id}",
    )
    part = PreparedBatch((
        PreparedItem(0, reference=ItemReference(
            "bilibili", f"{bv}_cid123",
            f"https://www.bilibili.com/video/{bv}?p=1",
            bilibili_bvid=bv, bilibili_cid="123", bilibili_page=1,
        )),
    ))

    result = service.submit_prepared(
        tenant, part, why_saved=None, request_key="parts:trashed-legacy",
    )

    assert result.results[0].status == "queued"
