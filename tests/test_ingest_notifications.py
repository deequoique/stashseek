import json
import logging
from pathlib import Path
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from sqlalchemy.dialects import postgresql

from app.config import Settings
from app.ingest.notification_repair_index import InMemoryNotificationRepairIndex
from app.ingest.notifications import (
    DeliveryClaim,
    HANDLER_KEY,
    IngestNotificationPoller,
    LangBotOutboundClient,
    NotificationSweepResult,
    NotificationTransportError,
    _notification_diagnostic,
    mark_repair_due_best_effort,
    render_completion_notification,
)
from app.ingest.tasks import (
    _notification_full_sweep_interval_from_env,
    _notification_interval_from_env,
    celery_app,
)


@pytest.mark.parametrize(
    ("outcome", "item_state", "needle"),
    [
        ("completed", "ready", "加入知识库，可以开始提问"),
        ("completed", "needs_extension", "浏览器扩展补充文本"),
        ("completed", "needs_asr", "语音识别"),
        ("failed", "failed", "解析失败"),
    ],
)
def test_notification_renderer_has_four_fixed_snapshots(
    outcome, item_state, needle
):
    message = render_completion_notification(
        outcome,
        item_state,
        "  私密\n标题\x00 " + ("x" * 200),
    )
    assert needle in message
    assert "\x00" not in message
    assert len(message) <= 320


def test_notification_schedule_is_maintenance_and_has_one_owner():
    schedule = celery_app.conf.beat_schedule
    assert schedule["deliver-pending-ingest-notifications"]["task"] == (
        "app.ingest.tasks.deliver_pending_ingest_notifications_task"
    )
    assert schedule["deliver-pending-ingest-notifications"]["options"] == {
        "queue": "maintenance"
    }
    assert "publish-pending-ingest-completion-events" not in schedule
    assert celery_app.conf.task_routes[
        "app.ingest.tasks.deliver_pending_ingest_notifications_task"
    ]["queue"] == "maintenance"


@pytest.mark.parametrize("value", ["0", "-1", "not-an-int"])
def test_notification_interval_fails_closed(monkeypatch, value):
    monkeypatch.setenv("INGEST_NOTIFICATION_INTERVAL_SECONDS", value)
    with pytest.raises(ValueError, match="positive integer"):
        _notification_interval_from_env()


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def getcode(self):
        return self.status


class _Opener:
    def __init__(self, response=None):
        self.response = response or _Response()
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_langbot_client_uses_exact_private_target_and_plain_chain():
    opener = _Opener()
    client = LangBotOutboundClient(
        "http://127.0.0.1:5300",
        "dedicated-key",
        timeout_seconds=3,
        opener=opener,
    )
    client.send_message(
        bot_uuid="bot/uuid",
        conversation_id="-100/topic-7",
        text="已完成",
        timeout_seconds=0.25,
    )
    assert opener.request.full_url.endswith(
        "/api/v1/platform/bots/bot%2Fuuid/send_message"
    )
    assert opener.request.get_header("X-api-key") == "dedicated-key"
    assert opener.timeout == 0.25
    assert json.loads(opener.request.data) == {
        "target_type": "person",
        "target_id": "-100/topic-7",
        "message_chain": {"root": [{"type": "Plain", "text": "已完成"}]},
    }


@pytest.mark.parametrize(
    ("status", "error_code", "retryable"),
    [
        (400, "outbound_contract_invalid", False),
        (401, "outbound_auth_rejected", False),
        (404, "outbound_target_not_found", False),
        (429, "outbound_rate_limited", True),
        (503, "outbound_server_error", True),
    ],
)
def test_langbot_client_classifies_status_without_response_body(
    status, error_code, retryable
):
    opener = _Opener(HTTPError("http://127.0.0.1", status, "private body", {}, None))
    client = LangBotOutboundClient(
        "http://127.0.0.1:5300", "key", opener=opener
    )
    with pytest.raises(NotificationTransportError) as caught:
        client.send_message(bot_uuid="bot", conversation_id="chat", text="x")
    assert caught.value.error_code == error_code
    assert caught.value.retryable is retryable
    assert "private body" not in repr(caught.value)


def test_langbot_client_rejects_redirect_without_following_it():
    opener = _Opener(_Response())
    opener.response.status = 302
    client = LangBotOutboundClient(
        "http://127.0.0.1:5300", "key", opener=opener
    )

    with pytest.raises(NotificationTransportError) as caught:
        client.send_message(bot_uuid="bot", conversation_id="chat", text="x")

    assert caught.value.error_code == "redirect_rejected"
    assert caught.value.retryable is False


class _ClaimSession:
    bind = None

    def __init__(self):
        self.statement = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def scalar(self, _statement):
        return datetime(2026, 8, 9, tzinfo=UTC)

    def scalars(self, statement):
        self.statement = statement
        return ()

    def commit(self):
        return None


class _FailureSession(_ClaimSession):
    def __init__(self, row):
        super().__init__()
        self.row = row
        self.scalar_calls = 0

    def scalar(self, _statement):
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return datetime(2026, 8, 9, tzinfo=UTC)
        return self.row


def test_claim_query_locks_only_event_side_of_outer_join():
    session = _ClaimSession()
    settings = SimpleNamespace(
        ingest_notification_claim_timeout_seconds=300,
        ingest_notification_batch_size=20,
    )
    poller = IngestNotificationPoller(lambda: session)

    assert poller._claim_batch(
        now=datetime(2026, 8, 9, tzinfo=UTC),
        settings=settings,
    ) == []

    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LEFT OUTER JOIN ingest_completion_delivery" in sql
    assert "FOR UPDATE OF ingest_completion_event SKIP LOCKED" in sql


def test_ack_failure_syncs_retry_exhausted_error_code():
    row = SimpleNamespace(
        status="claimed",
        disposition=None,
        claim_token="token",
        claimed_at=datetime(2026, 8, 9, tzinfo=UTC),
        attempts=2,
        next_attempt_at=None,
        last_error_code=None,
        completed_at=None,
        updated_at=None,
    )
    session = _FailureSession(row)
    settings = SimpleNamespace(
        ingest_notification_max_attempts=2,
        ingest_notification_retry_base_seconds=5,
        ingest_notification_retry_max_seconds=30,
    )
    poller = IngestNotificationPoller(lambda: session)

    acknowledged, exhausted = poller._ack_failure(
        DeliveryClaim(11, 22, "token", 2),
        error_code="outbound_server_error",
        settings=settings,
        now=datetime(2026, 8, 9, tzinfo=UTC),
    )

    assert (acknowledged, exhausted) == (True, True)
    assert row.status == "failed"
    assert row.disposition == "retry_exhausted"
    assert row.last_error_code == "retry_exhausted"
    assert row.next_attempt_at is None


class _ReleaseSession(_ClaimSession):
    def __init__(self, row):
        super().__init__()
        self.row = row

    def scalars(self, statement):
        self.statement = statement
        return (self.row,)


def test_deferred_claim_is_token_fenced_and_immediately_eligible():
    row = SimpleNamespace(
        status="claimed",
        disposition=None,
        claim_token="token",
        claimed_at=datetime(2026, 8, 9, tzinfo=UTC),
        next_attempt_at=None,
        last_error_code=None,
        completed_at=None,
        updated_at=None,
    )
    session = _ReleaseSession(row)
    poller = IngestNotificationPoller(lambda: session)

    released = poller._release_deferred_claims(
        (DeliveryClaim(11, 22, "token", 1),)
    )

    assert released == 1
    assert row.status == "failed"
    assert row.claim_token is None
    assert row.claimed_at is None
    assert row.last_error_code == "notification_deferred"
    assert row.next_attempt_at == datetime(2026, 8, 9, tzinfo=UTC)


@pytest.mark.parametrize("disposition", ["terminal_failure", "retry_exhausted"])
def test_terminal_disposition_cannot_be_acknowledged_as_succeeded(disposition):
    poller = IngestNotificationPoller(
        lambda: (_ for _ in ()).throw(AssertionError("must not open session"))
    )

    with pytest.raises(ValueError, match="invalid_notification_success_disposition"):
        poller._ack_succeeded(
            DeliveryClaim(11, 22, "token", 1),
            disposition=disposition,
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )


class _DeferredReclaimSession(_ClaimSession):
    def __init__(self, event, row):
        super().__init__()
        self.event = event
        self.row = row
        self.scalar_calls = 0

    def scalar(self, _statement):
        self.statement = _statement
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return datetime(2026, 8, 9, tzinfo=UTC)
        return self.row

    def scalars(self, statement):
        self.statement = statement
        return (self.event,)


def test_deadline_deferred_reclaim_does_not_consume_retry_attempt():
    event = SimpleNamespace(id=11)
    row = SimpleNamespace(
        id=22,
        status="failed",
        disposition=None,
        claim_token=None,
        claimed_at=None,
        attempts=3,
        next_attempt_at=datetime(2026, 8, 9, tzinfo=UTC),
        last_error_code="notification_deferred",
        completed_at=None,
        updated_at=None,
    )
    session = _DeferredReclaimSession(event, row)
    settings = SimpleNamespace(
        ingest_notification_claim_timeout_seconds=300,
        ingest_notification_batch_size=20,
        ingest_notification_max_attempts=5,
    )
    poller = IngestNotificationPoller(
        lambda: session,
        token_factory=lambda: "next-token",
    )

    claims = poller._claim_batch(
        now=datetime(2026, 8, 9, tzinfo=UTC),
        settings=settings,
    )

    assert claims == [DeliveryClaim(11, 22, "next-token", 3)]
    assert row.status == "claimed"
    assert row.attempts == 3


class _BacklogSession(_ClaimSession):
    def __init__(self, oldest):
        super().__init__()
        self.oldest = oldest
        self.scalar_calls = 0

    def scalar(self, _statement):
        self.statement = _statement
        self.scalar_calls += 1
        if self.scalar_calls == 1:
            return datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
        return self.oldest


def test_oldest_eligible_backlog_age_uses_bounded_read_only_query():
    session = _BacklogSession(datetime(2026, 8, 8, 23, 59, 18, tzinfo=UTC))
    settings = SimpleNamespace(ingest_notification_claim_timeout_seconds=300)
    poller = IngestNotificationPoller(lambda: session)

    age = poller._oldest_eligible_backlog_age_seconds(
        settings=settings,
        budget_seconds=0.137,
    )

    assert age == 42
    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "LEFT OUTER JOIN ingest_completion_delivery" in sql
    assert "FOR UPDATE" not in sql


def test_oldest_eligible_backlog_age_reserves_zero_for_empty_queue():
    settings = SimpleNamespace(ingest_notification_claim_timeout_seconds=300)
    empty = IngestNotificationPoller(lambda: _BacklogSession(None))
    just_created = IngestNotificationPoller(
        lambda: _BacklogSession(datetime(2026, 8, 9, 0, 0, tzinfo=UTC))
    )

    assert empty._oldest_eligible_backlog_age_seconds(
        settings=settings,
        budget_seconds=0.1,
    ) == 0
    assert just_created._oldest_eligible_backlog_age_seconds(
        settings=settings,
        budget_seconds=0.1,
    ) == 1


def test_successful_tick_heartbeat_is_numeric_and_observation_failure_is_isolated(
    monkeypatch,
):
    diagnostics = []
    observation_budgets = []
    monkeypatch.setattr(
        "app.ingest.notifications._notification_diagnostic",
        lambda event, **values: diagnostics.append((event, values)),
    )

    class _HeartbeatPoller(IngestNotificationPoller):
        def _claim_batch(self, **_kwargs):
            return []

        def _oldest_eligible_backlog_age_seconds(self, **_kwargs):
            observation_budgets.append(_kwargs["budget_seconds"])
            return 42

    settings = SimpleNamespace(ingest_notification_max_duration_seconds=8.0)
    poller = _HeartbeatPoller(
        lambda: None,
        _NeverSendClient(),
        settings=settings,
        clock=lambda: 0.0,
    )
    result = poller.sweep_once()

    assert result.claimed == 0
    heartbeat = [
        entry
        for entry in diagnostics
        if entry[0] == "notification_poller_heartbeat"
    ]
    assert heartbeat == [
        (
            "notification_poller_heartbeat",
            {
                "heartbeat": 1,
                "claimed": 0,
                "succeeded": 0,
                "skipped": 0,
                "failed": 0,
                "deferred": 0,
                "duration_ms": 0,
                "observability_failed": 0,
                "oldest_eligible_backlog_age_seconds": 42,
            },
        )
    ]
    assert observation_budgets == [0.25]

    diagnostics.clear()

    class _BrokenObservationPoller(_HeartbeatPoller):
        def _oldest_eligible_backlog_age_seconds(self, **_kwargs):
            raise RuntimeError("private observation failure")

    broken = _BrokenObservationPoller(
        lambda: None,
        _NeverSendClient(),
        settings=settings,
        clock=lambda: 0.0,
    )
    assert broken.sweep_once().claimed == 0
    broken_heartbeat = [
        entry
        for entry in diagnostics
        if entry[0] == "notification_poller_heartbeat"
    ]
    assert broken_heartbeat[0][1]["observability_failed"] == 1
    assert "private observation failure" not in repr(diagnostics)


def test_notification_diagnostic_enforces_numeric_privacy_allowlist(caplog):
    sentinel = "private-title-and-target"
    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        _notification_diagnostic(
            "untrusted-event-name",
            heartbeat=1,
            claimed=4,
            observability_failed=True,
            oldest_eligible_backlog_age_seconds=2**40,
            title=sentinel,
            bot_uuid=sentinel,
        )

    payload = caplog.records[-1].diagnostic_payload
    assert payload == {
        "event": "notification_sweep",
        "heartbeat": 1,
        "claimed": 4,
        "oldest_eligible_backlog_age_seconds": 2_147_483_647,
    }
    assert sentinel not in repr(payload)


def test_operator_docs_do_not_restore_retired_completion_consumer():
    root = Path(__file__).parents[1]
    readme_zh = (root / "README.zh-CN.md").read_text(encoding="utf-8")
    troubleshooting = (root / "docs/how-to/troubleshoot.md").read_text(
        encoding="utf-8"
    )
    configuration = (root / "docs/reference/configuration.md").read_text(
        encoding="utf-8"
    )

    combined = "\n".join((readme_zh, troubleshooting, configuration))
    assert "future idempotent consumer only" not in combined
    assert "真实 consumer 部署前" not in combined
    assert "Completion publisher/consumer" not in combined
    assert "notification_poller_heartbeat" in combined
    assert "delivery ledger" in combined
    assert "`ingest-completion` queue 已退役" in combined


class _NeverSendClient:
    def send_message(self, **_kwargs):
        raise AssertionError("deadline-expired claim must not be sent")


def test_sweep_releases_all_unsent_claims_before_stale_timeout():
    clock_value = [0.0]
    claims = [
        DeliveryClaim(11, 21, "one", 1),
        DeliveryClaim(12, 22, "two", 1),
    ]

    class _DeadlinePoller(IngestNotificationPoller):
        def _claim_batch(self, **_kwargs):
            clock_value[0] = 7.5
            return claims

        def _release_deferred_claims(self, pending, **_kwargs):
            self.released = list(pending)
            return len(self.released)

    settings = SimpleNamespace(ingest_notification_max_duration_seconds=8.0)
    poller = _DeadlinePoller(
        lambda: None,
        _NeverSendClient(),
        settings=settings,
        clock=lambda: clock_value[0],
    )

    result = poller.sweep_once()

    assert result.claimed == 2
    assert result.deferred == 2
    assert poller.released == claims


def test_targeted_event_delivery_does_not_run_global_candidate_scan(monkeypatch):
    settings = SimpleNamespace(ingest_notification_max_duration_seconds=8.0)
    poller = IngestNotificationPoller(
        lambda: (_ for _ in ()).throw(AssertionError("database scan is not allowed")),
        _NeverSendClient(),
        settings=settings,
        clock=lambda: 0.0,
    )
    monkeypatch.setattr(
        poller,
        "_claim_batch",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("targeted delivery must not call _claim_batch")
        ),
    )
    monkeypatch.setattr(poller, "_claim_event", lambda _event_id, **_kwargs: None)

    result = poller.deliver_event(41)

    assert result.as_dict() == {
        "claimed": 0,
        "succeeded": 0,
        "skipped": 0,
        "failed": 0,
        "deferred": 0,
        "duration_ms": 0,
    }


def test_langbot_non_loopback_http_is_rejected():
    with pytest.raises(ValueError, match="HTTPS"):
        LangBotOutboundClient("http://langbot.internal:5300", "key")


# --------------------------------------------------------------------------
# Redis-gated repair tick (Neon scale-to-zero)
# --------------------------------------------------------------------------

_ZERO_RESULT = {
    "claimed": 0,
    "succeeded": 0,
    "skipped": 0,
    "failed": 0,
    "deferred": 0,
    "duration_ms": 0,
}


class _NoDatabase:
    """Session factory that fails the test if PostgreSQL is ever opened."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("idle repair tick must not open a PostgreSQL session")


def _capture_diagnostics(monkeypatch):
    diagnostics = []
    monkeypatch.setattr(
        "app.ingest.notifications._notification_diagnostic",
        lambda event, **values: diagnostics.append((event, values)),
    )
    return diagnostics


def _gated_poller(index, factory=None, **kwargs):
    return IngestNotificationPoller(
        factory or _NoDatabase(),
        _NeverSendClient(),
        settings=SimpleNamespace(
            ingest_notification_max_duration_seconds=8.0,
            ingest_notification_claim_timeout_seconds=300,
        ),
        clock=lambda: 0.0,
        repair_index=index,
        epoch_clock=lambda: 1_000.0,
        **kwargs,
    )


def test_notification_repair_schedule_has_a_daily_full_repair_entry():
    schedule = celery_app.conf.beat_schedule
    entry = schedule["repair-all-ingest-notifications"]
    assert entry["task"] == "app.ingest.tasks.repair_all_ingest_notifications_task"
    assert entry["options"] == {"queue": "maintenance"}
    assert entry["schedule"] > 0
    assert celery_app.conf.task_routes[
        "app.ingest.tasks.repair_all_ingest_notifications_task"
    ]["queue"] == "maintenance"
    # The gated tick keeps its historical task name for rollback compatibility.
    assert schedule["deliver-pending-ingest-notifications"]["task"] == (
        "app.ingest.tasks.deliver_pending_ingest_notifications_task"
    )


@pytest.mark.parametrize("value", ["0", "-1", "not-an-int"])
def test_notification_full_sweep_interval_fails_closed(monkeypatch, value):
    monkeypatch.setenv("INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS", value)
    with pytest.raises(ValueError, match="positive integer"):
        _notification_full_sweep_interval_from_env()


def test_notification_full_sweep_interval_defaults_to_one_day(monkeypatch):
    monkeypatch.delenv("INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS", raising=False)
    assert _notification_full_sweep_interval_from_env() == 86400


def test_idle_repair_tick_issues_zero_postgres_statements_and_reports_idle(
    monkeypatch,
):
    diagnostics = _capture_diagnostics(monkeypatch)
    index = InMemoryNotificationRepairIndex()
    index.set_baseline()
    index.mark_due(7, 5_000)  # indexed, but not due until after "now"
    factory = _NoDatabase()
    poller = _gated_poller(index, factory)

    result = poller.repair_tick()

    assert factory.calls == 0
    assert result.as_dict() == _ZERO_RESULT
    assert diagnostics == [
        (
            "notification_poller_heartbeat",
            {
                "heartbeat": 1,
                "claimed": 0,
                "succeeded": 0,
                "skipped": 0,
                "failed": 0,
                "deferred": 0,
                "duration_ms": 0,
                "observability_failed": 0,
                "idle_skipped": 1,
            },
        )
    ]
    # No backlog query ran, so the age field must not claim "empty queue".
    assert "oldest_eligible_backlog_age_seconds" not in diagnostics[0][1]


def test_idle_repair_tick_with_an_empty_index_is_also_database_free(monkeypatch):
    diagnostics = _capture_diagnostics(monkeypatch)
    index = InMemoryNotificationRepairIndex()
    index.set_baseline()
    factory = _NoDatabase()

    _gated_poller(index, factory).repair_tick()
    _gated_poller(index, factory).repair_tick()

    assert factory.calls == 0
    assert [entry[1]["idle_skipped"] for entry in diagnostics] == [1, 1]


def test_repair_tick_without_an_index_is_the_original_ungated_sweep(monkeypatch):
    poller = _gated_poller(None)
    calls = []
    monkeypatch.setattr(
        poller,
        "sweep_once",
        lambda **kwargs: calls.append(kwargs) or NotificationSweepResult(claimed=3),
    )

    assert poller.repair_tick().claimed == 3
    assert calls == [{}]


def test_repair_tick_fails_open_when_redis_cannot_be_read(monkeypatch):
    diagnostics = _capture_diagnostics(monkeypatch)
    secret = "redis://:private-password@cache.internal:6379/0"

    class _BrokenIndex(InMemoryNotificationRepairIndex):
        def has_baseline(self):
            raise ConnectionError(secret)

    poller = _gated_poller(_BrokenIndex())
    swept = []
    monkeypatch.setattr(
        poller,
        "sweep_once",
        lambda **kwargs: swept.append(kwargs) or NotificationSweepResult(claimed=2),
    )

    result = poller.repair_tick()

    assert result.claimed == 2 and swept == [{}]
    assert diagnostics == [
        ("notification_repair_gate", {"repair_gate_unavailable": 1})
    ]
    assert secret not in repr(diagnostics)


def test_repair_tick_without_a_baseline_runs_a_full_repair(monkeypatch):
    diagnostics = _capture_diagnostics(monkeypatch)
    index = InMemoryNotificationRepairIndex()  # fresh or lost Redis
    poller = _gated_poller(index)
    monkeypatch.setattr(
        poller, "full_repair", lambda: NotificationSweepResult(claimed=5)
    )
    monkeypatch.setattr(
        poller,
        "sweep_once",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("gate bypassed")),
    )

    assert poller.repair_tick().claimed == 5
    assert diagnostics == [("notification_repair_gate", {"baseline_missing": 1})]


def test_repair_tick_with_due_members_sweeps_then_reconciles_due_and_claimed(
    monkeypatch,
):
    index = InMemoryNotificationRepairIndex()
    index.set_baseline()
    index.mark_due(1, 900)   # due
    index.mark_due(2, 5_000)  # not due; claimed by the sweep anyway
    poller = _gated_poller(index)
    reconciled = []

    def fake_sweep(*, claimed_event_ids=None):
        claimed_event_ids.extend([2, 3])
        return NotificationSweepResult(claimed=2)

    monkeypatch.setattr(poller, "sweep_once", fake_sweep)
    monkeypatch.setattr(
        poller,
        "_reconcile_index",
        lambda expected, *, settings: reconciled.append(dict(expected)) or True,
    )

    assert poller.repair_tick().claimed == 2
    # Observed scores: due member, claimed-but-not-due member, unindexed claim.
    assert reconciled == [{1: 900, 2: 5_000, 3: None}]


def test_repair_index_write_failure_never_raises_and_leaks_nothing(monkeypatch):
    diagnostics = _capture_diagnostics(monkeypatch)
    sentinel = "private redis endpoint and password"

    class _BrokenWrites(InMemoryNotificationRepairIndex):
        def mark_due(self, event_id, at):
            raise ConnectionError(sentinel)

    assert mark_repair_due_best_effort(_BrokenWrites(), 41, 1_000) is False
    assert mark_repair_due_best_effort(None, 41, 1_000) is False
    assert diagnostics == [
        ("notification_repair_index_write_failed", {"event_id": 41})
    ]
    assert sentinel not in repr(diagnostics)

    healthy = InMemoryNotificationRepairIndex()
    assert mark_repair_due_best_effort(healthy, 41, 1_000) is True
    assert healthy.score(41) == 1_000


def test_deliver_event_reconcile_does_not_overwrite_a_concurrent_mark_due(
    monkeypatch,
):
    """The CAS must lose to a mark_due that lands during delivery."""

    index = InMemoryNotificationRepairIndex()
    index.set_baseline()
    index.mark_due(41, 2_000)
    poller = _gated_poller(index, lambda: _ClaimSession())
    monkeypatch.setattr(
        "app.ingest.notifications._repair_due_map",
        lambda *_args, **_kwargs: {41: None},  # ledger says "terminal"
    )

    def claim_then_concurrent_redrive(_event_id, **_kwargs):
        index.mark_due(41, 500)  # operator redrive lands mid-delivery
        return None

    monkeypatch.setattr(poller, "_claim_event", claim_then_concurrent_redrive)

    poller.deliver_event(41)

    assert index.score(41) == 500


def test_reconcile_sql_is_bounded_by_the_callers_remaining_budget(monkeypatch):
    index = InMemoryNotificationRepairIndex()
    index.mark_due(41, 2_000)
    timeouts = []
    monkeypatch.setattr(
        "app.ingest.notifications._set_notification_statement_timeout",
        lambda _db, remaining: timeouts.append(remaining),
    )
    monkeypatch.setattr(
        "app.ingest.notifications._repair_due_map",
        lambda *_args, **_kwargs: {41: None},
    )
    exhausted = _NoDatabase()

    # An exhausted deadline skips the reconcile: no session, member stays due.
    assert _gated_poller(index, exhausted)._reconcile_index(
        {41: 2_000}, settings=SimpleNamespace(), budget_seconds=0
    ) is False
    assert exhausted.calls == 0 and index.score(41) == 2_000

    poller = _gated_poller(index, lambda: _ClaimSession())
    settings = SimpleNamespace(ingest_notification_max_duration_seconds=8.0)
    assert poller._reconcile_index(
        {41: 2_000}, settings=settings, budget_seconds=1.5
    ) is True
    assert timeouts == [1.5] and index.score(41) is None

    # The repair tick has no caller deadline: it uses the full sweep budget.
    index.mark_due(42, 2_000)
    assert poller._reconcile_index({42: 2_000}, settings=settings) is True
    assert timeouts[-1] == 8.0


def test_deliver_event_without_an_index_never_touches_the_repair_path(monkeypatch):
    poller = _gated_poller(None, _NoDatabase())
    monkeypatch.setattr(poller, "_claim_event", lambda _event_id, **_kwargs: None)
    assert poller.deliver_event(41).as_dict() == _ZERO_RESULT


def test_repair_diagnostics_stay_numeric_and_allow_listed(caplog):
    sentinel = "private-title-and-target"
    with caplog.at_level(logging.INFO, logger="notebook_agent.runtime"):
        _notification_diagnostic(
            "notification_repair_gate",
            repair_gate_unavailable=1,
            baseline_missing=1,
            idle_skipped=1,
            event_id=41,
            rebuilt=3,
            removed=2,
            truncated=0,
            index_failed=True,
            redis_url=sentinel,
        )

    assert caplog.records[-1].diagnostic_payload == {
        "event": "notification_repair_gate",
        "repair_gate_unavailable": 1,
        "baseline_missing": 1,
        "idle_skipped": 1,
        "event_id": 41,
        "rebuilt": 3,
        "removed": 2,
        "truncated": 0,
    }
    assert sentinel not in repr(caplog.records[-1].diagnostic_payload)
