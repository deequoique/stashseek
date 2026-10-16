from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from kombu import Connection, Queue

from app.config import Settings
from app.ingest.notification_repair_index import InMemoryNotificationRepairIndex
from app.ingest.tasks import (
    COMPLETION_QUEUE,
    COMPLETION_TASK_NAME,
    NOTIFICATION_QUEUE,
    NOTIFICATION_TASK_NAME,
    _COMPLETION_QUEUES,
    _completion_interval_from_env,
    _enqueue_ingest_notification_best_effort,
    _notification_repair_index,
    _set_completion_statement_timeout,
    _terminal_item_state,
    celery_app,
    deliver_ingest_notification_task,
    deliver_pending_ingest_notifications_task,
    enqueue_ingest_notification,
    publish_ingest_completion_event,
    repair_all_ingest_notifications_task,
)
from app.models import IngestCompletionEvent


def test_completion_queue_is_durable_and_not_registered_as_a_consumer():
    queue = next(item for item in _COMPLETION_QUEUES if item.name == COMPLETION_QUEUE)
    assert isinstance(queue, Queue)
    assert queue.durable is True
    assert queue.auto_delete is False
    assert COMPLETION_TASK_NAME not in celery_app.tasks
    assert celery_app.conf.task_routes[COMPLETION_TASK_NAME]["queue"] == COMPLETION_QUEUE


def test_local_redis_broker_fsyncs_persistent_messages_before_acknowledgement():
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert '"--appendonly", "yes"' in compose
    assert '"--appendfsync", "always"' in compose
    assert "redis_data:/data" in compose


def test_failed_completion_snapshot_requires_a_stable_error_code():
    constraint = next(
        item
        for item in IngestCompletionEvent.__table__.constraints
        if item.name == "ck_ingest_completion_event_completed_error"
    )

    assert "error_code IS NOT NULL" in str(constraint.sqltext)


def test_completion_publisher_sends_only_internal_event_id(monkeypatch):
    calls = []

    class Result:
        id = "completion-task-id"

    monkeypatch.setattr(
        "app.ingest.tasks._claim_completion_event",
        lambda *_args, **_kwargs: ("claim-token", object()),
    )
    monkeypatch.setattr(
        "app.ingest.tasks._mark_completion_enqueued",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.connection_for_write",
        lambda **_kwargs: Connection("memory://"),
    )
    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.send_task",
        lambda name, **kwargs: calls.append((name, kwargs)) or Result(),
    )

    task_id = publish_ingest_completion_event(
        41,
        settings=replace(
            Settings(),
            broker_publish_timeout_seconds=0.2,
            broker_publish_max_retries=0,
            agent_timeout_seconds=2,
            agent_tool_timeout_seconds=1,
        ),
    )

    assert task_id == "completion-task-id"
    assert calls[0][0] == COMPLETION_TASK_NAME
    assert calls[0][1]["args"] == [41]
    assert calls[0][1]["queue"] == COMPLETION_QUEUE
    assert calls[0][1]["delivery_mode"] == 2
    assert set(calls[0][1]) >= {
        "args",
        "queue",
        "producer",
        "declare",
        "delivery_mode",
    }


def test_notification_enqueue_sends_only_internal_event_id(monkeypatch):
    calls = []

    class Result:
        id = "notification-task-id"

    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.connection_for_write",
        lambda **_kwargs: Connection("memory://"),
    )
    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.send_task",
        lambda name, **kwargs: calls.append((name, kwargs)) or Result(),
    )

    task_id = enqueue_ingest_notification(
        41,
        settings=replace(
            Settings(),
            broker_publish_timeout_seconds=0.2,
            broker_publish_max_retries=0,
            agent_timeout_seconds=2,
            agent_tool_timeout_seconds=1,
        ),
    )

    assert task_id == "notification-task-id"
    assert calls[0][0] == NOTIFICATION_TASK_NAME
    assert calls[0][1]["args"] == [41]
    assert calls[0][1]["queue"] == NOTIFICATION_QUEUE
    assert calls[0][1]["delivery_mode"] == 2


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_completion_beat_interval_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("INGEST_COMPLETION_INTERVAL_SECONDS", value)

    with pytest.raises(ValueError, match="must be a positive integer"):
        _completion_interval_from_env()


def test_completion_snapshot_requires_persisted_terminal_state():
    class Item:
        state = "pending"

    with pytest.raises(ValueError, match="completion_item_state_not_terminal"):
        _terminal_item_state(Item(), "ready")

    Item.state = "needs_asr"
    with pytest.raises(ValueError, match="completion_process_state_mismatch"):
        _terminal_item_state(Item(), "ready")
    assert _terminal_item_state(Item(), "needs_asr") == "needs_asr"


def test_completion_sweep_uses_parameterized_postgres_statement_timeout():
    captured = {}

    class Dialect:
        name = "postgresql"

    class Bind:
        dialect = Dialect()

    class DB:
        bind = Bind()

        def execute(self, statement, parameters):
            captured["sql"] = str(statement)
            captured["parameters"] = parameters

    _set_completion_statement_timeout(DB(), 0.137)

    assert "set_config('statement_timeout'" in captured["sql"]
    assert captured["parameters"] == {"timeout_text": "137ms"}


def test_completion_publish_failure_does_not_log_exception_text(
    monkeypatch, caplog
):
    sentinel = "private broker URL and credential"

    monkeypatch.setattr(
        "app.ingest.tasks._claim_completion_event",
        lambda *_args, **_kwargs: ("claim-token", object()),
    )
    monkeypatch.setitem(
        publish_ingest_completion_event.__globals__,
        "_release_completion_claim",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.connection_for_write",
        lambda **_kwargs: Connection("memory://"),
    )
    monkeypatch.setattr(
        "app.ingest.tasks.celery_app.send_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )

    with caplog.at_level("INFO", logger="notebook_agent.runtime"):
        with pytest.raises(RuntimeError, match="private broker"):
            publish_ingest_completion_event(
                41,
                settings=replace(
                    Settings(),
                    broker_publish_timeout_seconds=0.2,
                    broker_publish_max_retries=0,
                    agent_timeout_seconds=2,
                    agent_tool_timeout_seconds=1,
                ),
            )

    assert all(sentinel not in record.getMessage() for record in caplog.records)
    assert all(
        sentinel not in repr(getattr(record, "diagnostic_payload", None))
        for record in caplog.records
    )


# --------------------------------------------------------------------------
# Redis repair index: post-commit hook, task wiring, and idle-schedule defaults
# --------------------------------------------------------------------------


def _patch_repair_runtime(monkeypatch, index, *, claim_timeout=300):
    monkeypatch.setattr(
        "app.ingest.tasks._notification_repair_index",
        lambda settings=None: index,
    )
    monkeypatch.setattr(
        "app.ingest.tasks.get_settings",
        lambda: SimpleNamespace(
            ingest_notification_claim_timeout_seconds=claim_timeout
        ),
    )


def test_post_commit_hook_indexes_the_event_before_enqueueing_it(monkeypatch):
    order = []

    class _Index(InMemoryNotificationRepairIndex):
        def mark_due(self, event_id, at):
            order.append(("mark_due", event_id))
            super().mark_due(event_id, at)

    index = _Index()
    _patch_repair_runtime(monkeypatch, index)
    monkeypatch.setattr(
        "app.ingest.tasks.enqueue_ingest_notification",
        lambda event_id: order.append(("enqueue", event_id)) or "task-id",
    )

    before = time.time()
    assert _enqueue_ingest_notification_best_effort(41) == "task-id"
    after = time.time()

    assert order == [("mark_due", 41), ("enqueue", 41)]
    # Claim-timeout margin: a healthy targeted task removes it long before.
    assert before + 300 <= index.score(41) <= math.ceil(after + 300)


def test_enqueue_failure_pulls_the_repair_time_forward_to_now(monkeypatch, caplog):
    sentinel = "private broker URL and credential"
    index = InMemoryNotificationRepairIndex()
    _patch_repair_runtime(monkeypatch, index)
    monkeypatch.setattr(
        "app.ingest.tasks.enqueue_ingest_notification",
        lambda _event_id: (_ for _ in ()).throw(RuntimeError(sentinel)),
    )

    before = time.time()
    with caplog.at_level("INFO", logger="notebook_agent.runtime"):
        assert _enqueue_ingest_notification_best_effort(41) is None
    after = time.time()

    # ``mark_due`` keeps the earliest time, so the immediate mark wins.
    assert before <= index.score(41) <= math.ceil(after)
    assert all(sentinel not in record.getMessage() for record in caplog.records)
    assert all(
        sentinel not in repr(getattr(record, "diagnostic_payload", None))
        for record in caplog.records
    )


def test_repair_index_write_failure_does_not_change_the_enqueue_result(
    monkeypatch, caplog
):
    sentinel = "private redis password"
    enqueued = []

    class _BrokenIndex(InMemoryNotificationRepairIndex):
        def mark_due(self, event_id, at):
            raise ConnectionError(sentinel)

    _patch_repair_runtime(monkeypatch, _BrokenIndex())
    monkeypatch.setattr(
        "app.ingest.tasks.enqueue_ingest_notification",
        lambda event_id: enqueued.append(event_id) or "task-id",
    )

    with caplog.at_level("INFO", logger="notebook_agent.runtime"):
        assert _enqueue_ingest_notification_best_effort(41) == "task-id"

    assert enqueued == [41]
    assert any(
        getattr(record, "diagnostic_payload", {}).get("event")
        == "notification_repair_index_write_failed"
        for record in caplog.records
    )
    assert all(sentinel not in record.getMessage() for record in caplog.records)
    assert all(
        sentinel not in repr(getattr(record, "diagnostic_payload", None))
        for record in caplog.records
    )


def test_unusable_redis_url_disables_the_gate_instead_of_raising():
    assert _notification_repair_index(SimpleNamespace(redis_url="")) is None
    # Construction is lazy: a valid URL never connects at build time.
    assert _notification_repair_index(
        SimpleNamespace(redis_url="redis://127.0.0.1:1/0")
    ) is not None


def test_notification_tasks_wire_the_repair_index_into_the_poller(monkeypatch):
    index = InMemoryNotificationRepairIndex()
    calls = []

    class _Poller:
        def __init__(self, session_factory, **kwargs):
            calls.append(("init", kwargs["repair_index"] is index))

        def repair_tick(self):
            calls.append(("repair_tick",))
            return SimpleNamespace(as_dict=lambda: {"claimed": 0})

        def full_repair(self):
            calls.append(("full_repair",))
            return SimpleNamespace(as_dict=lambda: {"claimed": 0})

        def deliver_event(self, event_id):
            calls.append(("deliver_event", event_id))
            return SimpleNamespace(as_dict=lambda: {"claimed": 1})

    monkeypatch.setattr("app.ingest.tasks.IngestNotificationPoller", _Poller)
    monkeypatch.setattr("app.ingest.tasks.get_session_factory", lambda: object())
    monkeypatch.setattr("app.ingest.tasks.get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "app.ingest.tasks._notification_repair_index",
        lambda settings=None: index,
    )

    deliver_pending_ingest_notifications_task()
    repair_all_ingest_notifications_task()
    assert deliver_ingest_notification_task(41) == {"claimed": 1}

    assert calls == [
        ("init", True), ("repair_tick",),
        ("init", True), ("full_repair",),
        ("init", True), ("deliver_event", 41),
    ]


def _import_idle_schedule(extra_env):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TRASH_PURGE_INTERVAL", "INGEST_NOTIFICATION_"))
    }
    env.update(extra_env)
    root = Path(__file__).parents[1]
    env["PYTHONPATH"] = str(root)
    # A developer's .env must not leak into a defaults assertion.
    code = (
        "import json, dotenv;"
        "dotenv.load_dotenv = lambda *a, **k: False;"
        "dotenv.dotenv_values = lambda *a, **k: {};"
        "from app.ingest.tasks import celery_app as c;"
        "s = c.conf.beat_schedule;"
        "print(json.dumps({k: v['schedule'] for k, v in s.items()}))"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )


def test_idle_beat_schedule_defaults_to_one_database_visit_per_day():
    done = _import_idle_schedule({})
    assert done.returncode == 0, done.stderr[-500:]
    schedule = json.loads(done.stdout.strip().splitlines()[-1])

    assert schedule["purge-expired-items"] == 86400.0
    assert schedule["repair-all-ingest-notifications"] == 86400.0
    # The 600s tick stays, but it is Redis-gated and DB-free while idle.
    assert schedule["deliver-pending-ingest-notifications"] == 600.0


def test_explicit_purge_and_repair_overrides_still_apply_and_validate():
    done = _import_idle_schedule(
        {
            "TRASH_PURGE_INTERVAL_SECONDS": "7200",
            "INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS": "3600",
        }
    )
    assert done.returncode == 0, done.stderr[-500:]
    schedule = json.loads(done.stdout.strip().splitlines()[-1])
    assert schedule["purge-expired-items"] == 7200.0
    assert schedule["repair-all-ingest-notifications"] == 3600.0

    bad = _import_idle_schedule(
        {"INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS": "0"}
    )
    assert bad.returncode != 0
    assert "INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS" in bad.stderr


def test_settings_defaults_and_validation_for_the_daily_schedules(monkeypatch):
    monkeypatch.delenv("TRASH_PURGE_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv(
        "INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS", raising=False
    )
    settings = Settings()
    assert settings.trash_purge_interval_seconds == 86400
    assert settings.ingest_notification_full_sweep_interval_seconds == 86400

    monkeypatch.setenv("TRASH_PURGE_INTERVAL_SECONDS", "7200")
    assert Settings().trash_purge_interval_seconds == 7200

    with pytest.raises(ValueError, match="TRASH_PURGE_INTERVAL_SECONDS"):
        replace(settings, trash_purge_interval_seconds=0)
    with pytest.raises(
        ValueError, match="INGEST_NOTIFICATION_FULL_SWEEP_INTERVAL_SECONDS"
    ):
        replace(settings, ingest_notification_full_sweep_interval_seconds=0)
