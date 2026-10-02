from dataclasses import replace
from pathlib import Path

import pytest
from kombu import Connection, Queue

from app.config import Settings
from app.ingest.tasks import (
    COMPLETION_QUEUE,
    COMPLETION_TASK_NAME,
    NOTIFICATION_QUEUE,
    NOTIFICATION_TASK_NAME,
    _COMPLETION_QUEUES,
    _completion_interval_from_env,
    _set_completion_statement_timeout,
    _terminal_item_state,
    celery_app,
    enqueue_ingest_notification,
    publish_ingest_completion_event,
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
