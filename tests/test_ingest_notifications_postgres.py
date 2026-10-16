"""PostgreSQL integration coverage for source-channel notifications.

These tests create all tables they touch in a per-test schema.  They never use
the shared schema and the outbound client is not exercised, so a live database
is the only external dependency.
"""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import time
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.channels.types import TenantContext
from app.db import get_engine
from app.ingest.notification_repair_index import InMemoryNotificationRepairIndex
from app.ingest.notifications import (
    IngestNotificationPoller,
    NotificationSweepResult,
    NotificationTransportError,
)
from app.ingest.submission import IngestSubmissionService
from app.models import (
    AppUser,
    Base,
    ChannelIdentity,
    ContentItem,
    ConversationThread,
    IngestCompletionDelivery,
    IngestCompletionEvent,
    IngestDispatch,
)


@pytest.fixture
def notification_factory():
    """Return a Session factory bound to one disposable PostgreSQL schema."""

    engine = get_engine()
    schema = f"test_notification_{uuid4().hex}"
    tables = [
        AppUser.__table__,
        ChannelIdentity.__table__,
        ConversationThread.__table__,
        ContentItem.__table__,
        IngestDispatch.__table__,
        IngestCompletionEvent.__table__,
        IngestCompletionDelivery.__table__,
    ]
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


def _source_context(
    factory,
    *,
    channel: str,
    suffix: str,
    app_user_id: int | None = None,
):
    with factory() as db:
        user = AppUser(id=app_user_id) if app_user_id is not None else AppUser()
        db.add(user)
        db.flush()
        identity = ChannelIdentity(
            app_user_id=user.id,
            channel=channel,
            account_id=f"bot-{suffix}",
            external_user_id=f"user-{suffix}",
        )
        db.add(identity)
        db.flush()
        thread = ConversationThread(
            public_id=uuid4().hex,
            app_user_id=user.id,
            channel_identity_id=identity.id,
            channel=channel,
            account_id=identity.account_id,
            external_conversation_id=f"chat-{suffix}",
        )
        alternate = ConversationThread(
            public_id=uuid4().hex,
            app_user_id=user.id,
            channel_identity_id=identity.id,
            channel=channel,
            account_id=identity.account_id,
            external_conversation_id=f"chat-{suffix}-alternate",
        )
        db.add_all([thread, alternate])
        db.commit()
        return (
            TenantContext(
                user.id,
                identity.id,
                channel,
                identity.account_id,
                identity.external_user_id,
            ),
            thread.id,
            alternate.id,
            user.id,
        )


def test_source_thread_capture_replay_and_invalid_target_fail_closed(
    notification_factory,
):
    """Admission snapshots the trusted route and never retargets a replay."""

    tenant, thread_id, alternate_id, _ = _source_context(
        notification_factory,
        channel="telegram",
        suffix=uuid4().hex,
    )
    other_tenant, other_thread_id, _, _ = _source_context(
        notification_factory,
        channel="wechat",
        suffix=uuid4().hex,
    )
    del other_tenant
    published: list[int] = []
    service = IngestSubmissionService(
        notification_factory,
        lambda dispatch_id: published.append(dispatch_id) or "task-id",
    )
    url = "https://youtu.be/dQw4w9WgXcQ"

    first = service.submit_urls(
        tenant,
        [url],
        why_saved=None,
        request_key="source-replay",
        source_thread_id=thread_id,
    )
    replay = service.submit_urls(
        tenant,
        [url],
        why_saved=None,
        request_key="source-replay",
        # This is another valid thread owned by the same identity.  A replay
        # must return the original dispatch instead of rewriting its target.
        source_thread_id=alternate_id,
    )
    assert first.results[0].status == "queued"
    assert replay.results[0].status == "queued"
    assert len(published) == 1

    with notification_factory() as db:
        dispatch = db.scalar(select(IngestDispatch))
        assert dispatch is not None
        assert dispatch.source_thread_id == thread_id

    # A thread belonging to another tenant and an unknown ID are both safe
    # no-target admissions; neither can cause a later channel to receive it.
    cross_tenant = service.submit_urls(
        tenant,
        ["https://youtu.be/9bZkp7q19f0"],
        why_saved=None,
        request_key="cross-tenant-source",
        source_thread_id=other_thread_id,
    )
    invalid = service.submit_urls(
        tenant,
        ["https://youtu.be/M7lc1UVf-VE"],
        why_saved=None,
        request_key="invalid-source",
        source_thread_id=999_999_999,
    )
    assert cross_tenant.results[0].status == "queued"
    assert invalid.results[0].status == "queued"
    with notification_factory() as db:
        rows = list(
            db.scalars(
                select(IngestDispatch)
                .where(IngestDispatch.request_key.in_(
                    ["cross-tenant-source", "invalid-source"])
                )
                .order_by(IngestDispatch.request_key)
            )
        )
        assert [row.source_thread_id for row in rows] == [None, None]


@pytest.mark.parametrize("channel", ["telegram", "wechat"])
def test_source_thread_capture_supports_telegram_and_wechat(
    notification_factory, channel
):
    tenant, thread_id, _, _ = _source_context(
        notification_factory,
        channel=channel,
        suffix=uuid4().hex,
    )
    service = IngestSubmissionService(
        notification_factory,
        lambda dispatch_id: f"task-{dispatch_id}",
    )
    result = service.submit_urls(
        tenant,
        ["https://youtu.be/ScMzIvxBSi4"],
        why_saved=None,
        request_key=f"source-{channel}",
        source_thread_id=thread_id,
    )
    assert result.results[0].status == "queued"
    with notification_factory() as db:
        dispatch = db.scalar(select(IngestDispatch))
        assert dispatch is not None
        assert dispatch.source_thread_id == thread_id


def _seed_event(factory, *, suffix: str | None = None):
    suffix = suffix or uuid4().hex
    with factory() as db:
        user = AppUser()
        db.add(user)
        db.flush()
        item = ContentItem(
            user_id=user.id,
            platform="youtube",
            platform_id=f"event-{suffix}",
            kind="video",
            url=f"https://youtu.be/{suffix[:11]}",
            state="ready",
        )
        db.add(item)
        db.flush()
        dispatch = IngestDispatch(
            public_id=uuid4().hex,
            item_id=item.id,
            request_key=f"event-{suffix}",
            attempt=1,
            state="completed",
        )
        db.add(dispatch)
        db.flush()
        event = IngestCompletionEvent(
            public_id=uuid4().hex,
            dispatch_id=dispatch.id,
            item_id=item.id,
            outcome="completed",
            item_state="ready",
            publish_state="pending",
        )
        db.add(event)
        db.commit()
        return event.id, item.id


def _notification_settings(**overrides):
    values = {
        "ingest_notification_claim_timeout_seconds": 300,
        "ingest_notification_batch_size": 20,
        "ingest_notification_max_attempts": 2,
        "ingest_notification_retry_base_seconds": 5,
        "ingest_notification_retry_max_seconds": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_postgres_claim_duplicate_stale_reclaim_and_old_token_ack(
    notification_factory,
):
    event_id, _ = _seed_event(notification_factory)
    settings = _notification_settings()
    first_poller = IngestNotificationPoller(
        notification_factory,
        settings=settings,
        token_factory=lambda: "first-token",
    )
    first = first_poller._claim_batch(
        now=datetime.now(UTC), settings=settings
    )
    assert len(first) == 1
    first_claim = first[0]
    assert first_claim.event_id == event_id

    fresh = IngestNotificationPoller(
        notification_factory,
        settings=settings,
        token_factory=lambda: "fresh-token",
    )._claim_batch(now=datetime.now(UTC), settings=settings)
    assert fresh == []

    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        assert delivery is not None
        delivery.claimed_at = datetime.now(UTC) - timedelta(seconds=600)
        delivery.updated_at = delivery.claimed_at
        db.commit()

    reclaimed = IngestNotificationPoller(
        notification_factory,
        settings=settings,
        token_factory=lambda: "second-token",
    )._claim_batch(now=datetime.now(UTC), settings=settings)
    assert len(reclaimed) == 1
    second_claim = reclaimed[0]
    assert second_claim.claim_token == "second-token"
    assert second_claim.claim_token != first_claim.claim_token
    assert second_claim.attempts == first_claim.attempts + 1

    assert first_poller._ack_succeeded(
        first_claim, disposition="sent", now=datetime.now(UTC)
    ) is False
    assert first_poller._ack_succeeded(
        second_claim, disposition="sent", now=datetime.now(UTC)
    ) is True
    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        assert delivery is not None
        assert (delivery.status, delivery.claim_token, delivery.attempts) == (
            "succeeded",
            None,
            2,
        )


def test_postgres_failed_backoff_retry_ceiling_and_manual_redrive(
    notification_factory,
):
    event_id, _ = _seed_event(notification_factory)
    settings = _notification_settings()
    poller = IngestNotificationPoller(
        notification_factory,
        settings=settings,
        token_factory=lambda: "retry-token",
    )

    first = poller._claim_batch(now=datetime.now(UTC), settings=settings)[0]
    acknowledged, exhausted = poller._ack_failure(
        first,
        error_code="outbound_server_error",
        settings=settings,
        now=datetime.now(UTC),
    )
    assert (acknowledged, exhausted) == (True, False)
    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        assert delivery is not None
        assert delivery.status == "failed"
        assert delivery.next_attempt_at is not None
        assert delivery.last_error_code == "outbound_server_error"
        delivery.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    second = poller._claim_batch(now=datetime.now(UTC), settings=settings)[0]
    acknowledged, exhausted = poller._ack_failure(
        second,
        error_code="outbound_server_error",
        settings=settings,
        now=datetime.now(UTC),
    )
    assert (acknowledged, exhausted) == (True, True)
    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        assert delivery is not None
        assert (
            delivery.status,
            delivery.disposition,
            delivery.next_attempt_at,
            delivery.last_error_code,
        ) == ("failed", "retry_exhausted", None, "retry_exhausted")

    assert poller.redrive_failed(event_id) is True
    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        assert delivery is not None
        assert delivery.disposition is None
        assert delivery.last_error_code == "manual_redrive"
        assert delivery.next_attempt_at is not None
        assert delivery.attempts == 1

    redriven = poller._claim_batch(now=datetime.now(UTC), settings=settings)
    assert len(redriven) == 1
    assert redriven[0].attempts == 1


def test_postgres_event_item_delete_cascades_delivery(notification_factory):
    event_id, item_id = _seed_event(notification_factory)
    settings = _notification_settings()
    claim = IngestNotificationPoller(
        notification_factory,
        settings=settings,
        token_factory=lambda: "cascade-token",
    )._claim_batch(now=datetime.now(UTC), settings=settings)[0]
    assert claim.event_id == event_id

    with notification_factory() as db:
        db.delete(db.get(ContentItem, item_id))
        db.commit()

    # ``notification_factory`` installs the isolated schema with SET LOCAL;
    # committing resets that setting for the connection.  Verify the cascade
    # through a fresh session so the assertions cannot fall back to ``public``.
    with notification_factory() as db:
        assert db.get(IngestCompletionEvent, event_id) is None
        assert db.scalar(
            select(func.count()).select_from(IngestCompletionDelivery)
        ) == 0


def test_notification_migration_and_model_contracts_are_declared():
    """Keep migration DDL and ORM constraints aligned for the live checks."""

    dispatch = IngestDispatch.__table__
    source_column = dispatch.c.source_thread_id
    source_fks = {
        (fk.target_fullname, fk.ondelete)
        for fk in source_column.foreign_keys
    }
    assert source_fks == {("conversation_thread.id", "SET NULL")}
    assert "ix_ingest_dispatch_source_thread" in {
        index.name for index in dispatch.indexes
    }

    delivery = IngestCompletionDelivery.__table__
    assert {
        "uq_ingest_completion_delivery_event_handler",
        "ck_ingest_completion_delivery_status",
        "ck_ingest_completion_delivery_attempts",
        "ck_ingest_completion_delivery_error_code",
        "ck_ingest_completion_delivery_state_contract",
        "ix_ingest_completion_delivery_claim",
        "ix_ingest_completion_delivery_event",
    } <= {
        constraint.name for constraint in delivery.constraints
    } | {index.name for index in delivery.indexes}
    migration = Path(
        "migrations/versions/a1b2c3d4e5f6_ingest_notification_delivery.py"
    ).read_text()
    for needle in (
        'revision: str = "a1b2c3d4e5f6"',
        'down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"',
        '"source_thread_id"',
        '"fk_ingest_dispatch_source_thread"',
        '"ix_ingest_dispatch_source_thread"',
        '"ingest_completion_delivery"',
        '"uq_ingest_completion_delivery_event_handler"',
        '"ck_ingest_completion_delivery_state_contract"',
        '"ix_ingest_completion_delivery_claim"',
    ):
        assert needle in migration


# --------------------------------------------------------------------------
# Redis-gated repair against the real ledger (Neon scale-to-zero)
# --------------------------------------------------------------------------


class _CountingFactory:
    """Counts PostgreSQL sessions opened through the isolated-schema factory."""

    def __init__(self, factory):
        self._factory = factory
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self._factory()


class _RecordingClient:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    def send_message(self, **kwargs):
        if self.error is not None:
            raise self.error
        self.sent.append(kwargs)


def _repair_settings(**overrides):
    values = {
        "ingest_notification_max_duration_seconds": 8.0,
        "langbot_outbound_timeout_seconds": 3.0,
    }
    values.update(overrides)
    return _notification_settings(**values)


def _seed_deliverable_event(factory):
    """A completed event whose source thread resolves to a sendable target."""

    suffix = uuid4().hex
    _, thread_id, _, user_id = _source_context(
        factory, channel="telegram", suffix=suffix
    )
    with factory() as db:
        item = ContentItem(
            user_id=user_id,
            platform="youtube",
            platform_id=f"repair-{suffix}",
            kind="video",
            url=f"https://youtu.be/{suffix[:11]}",
            title="repair target",
            state="ready",
        )
        db.add(item)
        db.flush()
        dispatch = IngestDispatch(
            public_id=uuid4().hex,
            item_id=item.id,
            request_key=f"repair-{suffix}",
            attempt=1,
            state="completed",
            source_thread_id=thread_id,
        )
        db.add(dispatch)
        db.flush()
        event = IngestCompletionEvent(
            public_id=uuid4().hex,
            dispatch_id=dispatch.id,
            item_id=item.id,
            outcome="completed",
            item_state="ready",
            publish_state="pending",
        )
        db.add(event)
        db.commit()
        return event.id


def _baselined_index(*due):
    index = InMemoryNotificationRepairIndex()
    index.set_baseline()
    for event_id, at in due:
        index.mark_due(event_id, at)
    return index


def _ledger(factory, event_id, **values):
    now = datetime.now(UTC)
    row = {
        "event_id": event_id,
        "handler_key": "source-channel.notification.v1",
        "attempts": 1,
        "created_at": now,
        "updated_at": now,
    }
    row.update(values)
    with factory() as db:
        db.add(IngestCompletionDelivery(**row))
        db.commit()


def test_postgres_lost_targeted_task_is_repaired_after_claim_timeout_then_idle(
    notification_factory,
):
    event_id = _seed_deliverable_event(notification_factory)
    base = 1_800_000_000.0
    clock = [base]
    # What the post-commit hook records before enqueueing the targeted task.
    index = _baselined_index((event_id, int(base) + 300))
    counting = _CountingFactory(notification_factory)
    client = _RecordingClient()
    poller = IngestNotificationPoller(
        counting,
        client,
        settings=_repair_settings(),
        repair_index=index,
        epoch_clock=lambda: clock[0],
    )

    # The targeted task never ran, but the claim timeout has not elapsed yet.
    clock[0] = base + 299
    assert poller.repair_tick().as_dict()["claimed"] == 0
    assert counting.calls == 0 and client.sent == []

    clock[0] = base + 301
    result = poller.repair_tick()
    assert (result.claimed, result.succeeded) == (1, 1)
    assert len(client.sent) == 1
    assert index.score(event_id) is None  # terminal: removed from the index
    with notification_factory() as db:
        assert db.scalar(select(IngestCompletionDelivery.status)) == "succeeded"

    # Terminal state reached: later ticks never reach PostgreSQL again.
    counting.calls = 0
    clock[0] = base + 10_000
    for _ in range(3):
        poller.repair_tick()
    assert counting.calls == 0
    assert len(client.sent) == 1


def test_postgres_crash_after_claim_is_recovered_once_the_claim_is_stale(
    notification_factory,
):
    event_id = _seed_deliverable_event(notification_factory)
    base = 1_800_000_000.0
    clock = [base]
    index = _baselined_index((event_id, int(base) + 300))
    settings = _repair_settings()
    # The targeted worker claims the event, then dies before it can ACK.
    claim = IngestNotificationPoller(
        notification_factory, settings=settings, repair_index=index
    )._claim_event(event_id, now=datetime.now(UTC), settings=settings)
    assert claim is not None
    assert index.score(event_id) == int(base) + 300  # a crash never edits it
    with notification_factory() as db:
        claimed_at = db.scalar(select(IngestCompletionDelivery.claimed_at))
    client = _RecordingClient()
    poller = IngestNotificationPoller(
        notification_factory,
        client,
        settings=settings,
        repair_index=index,
        epoch_clock=lambda: clock[0],
    )

    # The index says due, but the (fresh) claim is not stale yet: nothing is
    # sent and the member is rescheduled to the stale-claim time.
    clock[0] = base + 301
    assert poller.repair_tick().claimed == 0
    assert client.sent == []
    assert index.score(event_id) == math.ceil(
        (claimed_at + timedelta(seconds=300)).timestamp()
    )

    # The claim goes stale in PostgreSQL time and the tick passes that point.
    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        delivery.claimed_at = datetime.now(UTC) - timedelta(seconds=301)
        db.commit()
    clock[0] = index.score(event_id) + 1
    result = poller.repair_tick()

    assert (result.claimed, result.succeeded) == (1, 1)
    assert len(client.sent) == 1
    assert index.score(event_id) is None


def test_postgres_retryable_failure_is_indexed_at_next_attempt_and_gates_the_tick(
    notification_factory,
):
    event_id = _seed_deliverable_event(notification_factory)
    index = _baselined_index((event_id, int(time.time()) + 300))
    counting = _CountingFactory(notification_factory)
    failing = IngestNotificationPoller(
        counting,
        _RecordingClient(
            NotificationTransportError("outbound_server_error", retryable=True)
        ),
        settings=_repair_settings(),
        repair_index=index,
    )

    assert failing.deliver_event(event_id).failed == 1

    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        assert (delivery.status, delivery.disposition) == ("failed", None)
        next_attempt = delivery.next_attempt_at
    due_at = index.score(event_id)
    assert due_at == math.ceil(next_attempt.timestamp())

    # Strictly before the retry time: zero database access.
    counting.calls = 0
    client = _RecordingClient()
    retrying = IngestNotificationPoller(
        counting,
        client,
        settings=_repair_settings(),
        repair_index=index,
        epoch_clock=lambda: due_at - 1,
    )
    retrying.repair_tick()
    assert counting.calls == 0

    # Time passes: the ledger and the index both say "eligible now".
    past = datetime.now(UTC) - timedelta(seconds=2)
    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        delivery.next_attempt_at = past
        db.commit()
    assert index.reconcile(event_id, due_at, int(past.timestamp())) is True
    ticking = IngestNotificationPoller(
        counting,
        client,
        settings=_repair_settings(),
        repair_index=index,
    )
    result = ticking.repair_tick()

    assert counting.calls > 0
    assert (result.claimed, result.succeeded) == (1, 1)
    assert len(client.sent) == 1
    assert index.score(event_id) is None


def test_postgres_terminal_failure_removes_the_member_and_ticks_stay_idle(
    notification_factory,
):
    event_id = _seed_deliverable_event(notification_factory)
    index = _baselined_index((event_id, int(time.time()) + 300))
    counting = _CountingFactory(notification_factory)
    poller = IngestNotificationPoller(
        counting,
        _RecordingClient(
            NotificationTransportError("outbound_auth_rejected", retryable=False)
        ),
        settings=_repair_settings(),
        repair_index=index,
    )

    poller.deliver_event(event_id)

    with notification_factory() as db:
        delivery = db.scalar(select(IngestCompletionDelivery))
        assert (delivery.status, delivery.disposition) == (
            "failed",
            "terminal_failure",
        )
    assert index.score(event_id) is None
    counting.calls = 0
    poller.repair_tick()
    assert counting.calls == 0


def test_postgres_redis_failures_never_change_the_delivery_result(
    notification_factory,
):
    event_id = _seed_deliverable_event(notification_factory)

    class _BrokenIndex(InMemoryNotificationRepairIndex):
        def score(self, event_id):
            raise ConnectionError("redis down")

        def reconcile(self, *_args):
            raise ConnectionError("redis down")

        def mark_due(self, *_args):
            raise ConnectionError("redis down")

    client = _RecordingClient()
    poller = IngestNotificationPoller(
        notification_factory,
        client,
        settings=_repair_settings(),
        repair_index=_BrokenIndex(),
    )

    result = poller.deliver_event(event_id)

    assert (result.claimed, result.succeeded, result.failed) == (1, 1, 0)
    assert len(client.sent) == 1
    with notification_factory() as db:
        assert db.scalar(select(IngestCompletionDelivery.status)) == "succeeded"


def test_postgres_missing_baseline_runs_one_full_sweep_then_goes_idle(
    notification_factory,
):
    # Fresh/lost Redis: no baseline and no member for the pending event.
    event_id = _seed_deliverable_event(notification_factory)
    index = InMemoryNotificationRepairIndex()
    counting = _CountingFactory(notification_factory)
    client = _RecordingClient()
    poller = IngestNotificationPoller(
        counting, client, settings=_repair_settings(), repair_index=index
    )

    result = poller.repair_tick()

    assert (result.claimed, result.succeeded) == (1, 1)  # ungated, found anyway
    assert len(client.sent) == 1
    assert index.has_baseline() is True
    assert index.members(10) == []

    counting.calls = 0
    poller.repair_tick()
    assert counting.calls == 0
    assert event_id  # the event existed and was delivered exactly once
    assert len(client.sent) == 1


def test_postgres_full_repair_rebuilds_the_index_from_ledger_state(
    notification_factory,
):
    pending = _seed_deliverable_event(notification_factory)  # no ledger row
    claimed = _seed_deliverable_event(notification_factory)
    retryable = _seed_deliverable_event(notification_factory)
    terminal = _seed_deliverable_event(notification_factory)
    succeeded = _seed_deliverable_event(notification_factory)
    now = datetime.now(UTC)
    claimed_at = now - timedelta(seconds=30)
    retry_at = now + timedelta(seconds=120)
    _ledger(
        notification_factory, claimed, status="claimed", claim_token="t",
        claimed_at=claimed_at,
    )
    _ledger(
        notification_factory, retryable, status="failed",
        last_error_code="outbound_server_error", next_attempt_at=retry_at,
    )
    _ledger(
        notification_factory, terminal, status="failed",
        disposition="terminal_failure", last_error_code="outbound_auth_rejected",
    )
    _ledger(
        notification_factory, succeeded, status="succeeded", disposition="sent",
        completed_at=now,
    )
    ghost = 999_999_999
    # Stale members: terminal/succeeded rows and an event that no longer exists.
    index = InMemoryNotificationRepairIndex()
    for stale in (terminal, succeeded, ghost):
        index.mark_due(stale, 1)
    poller = IngestNotificationPoller(
        notification_factory,
        _RecordingClient(),
        settings=_repair_settings(),
        repair_index=index,
    )
    # Isolate the rebuild from the (separately tested) claim/send sweep.
    poller.sweep_once = lambda **_kwargs: NotificationSweepResult()

    poller.full_repair()

    members = dict(index.members(50))
    assert set(members) == {pending, claimed, retryable}
    assert abs(members[pending] - int(now.timestamp())) <= 5
    assert members[claimed] == math.ceil((claimed_at + timedelta(seconds=300)).timestamp())
    assert members[retryable] == math.ceil(retry_at.timestamp())
    assert index.has_baseline() is True


def test_postgres_full_repair_withholds_the_baseline_when_the_backlog_is_truncated(
    notification_factory, monkeypatch
):
    for _ in range(3):
        _seed_deliverable_event(notification_factory)
    monkeypatch.setattr("app.ingest.notifications._MAX_REBUILD_EVENTS", 2)
    index = InMemoryNotificationRepairIndex()
    poller = IngestNotificationPoller(
        notification_factory,
        _RecordingClient(),
        settings=_repair_settings(),
        repair_index=index,
    )
    poller.sweep_once = lambda **_kwargs: NotificationSweepResult()

    poller.full_repair()

    assert len(index.members(10)) == 2
    assert index.has_baseline() is False  # next tick runs another full repair

    # A baseline written earlier must not keep gating a backlog the index
    # cannot describe: the truncated rebuild clears it.
    index.set_baseline()
    poller.full_repair()
    assert index.has_baseline() is False


def test_postgres_redrive_marks_the_event_due_for_the_gated_tick(
    notification_factory,
):
    event_id = _seed_deliverable_event(notification_factory)
    _ledger(
        notification_factory, event_id, status="failed",
        disposition="retry_exhausted", last_error_code="retry_exhausted",
    )
    index = _baselined_index()
    poller = IngestNotificationPoller(
        notification_factory,
        settings=_repair_settings(),
        repair_index=index,
        epoch_clock=lambda: 1_800_000_123.0,
    )

    assert poller.redrive_failed(event_id) is True
    assert index.score(event_id) == 1_800_000_123

    class _BrokenIndex(InMemoryNotificationRepairIndex):
        def mark_due(self, *_args):
            raise ConnectionError("redis down")

    resilient = IngestNotificationPoller(
        notification_factory, settings=_repair_settings(), repair_index=_BrokenIndex()
    )
    assert resilient.redrive_failed(event_id) is True
