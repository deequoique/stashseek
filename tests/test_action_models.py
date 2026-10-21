from app.models import (
    ConversationTurn,
    IngestCompletionDelivery,
    IngestDispatch,
    PendingChannelAction,
)


def test_pending_action_has_durable_single_active_contract():
    table = PendingChannelAction.__table__

    assert set(table.columns.keys()) == {
        "id",
        "thread_id",
        "kind",
        "payload",
        "expires_at",
        "consumed_at",
        "consumed_message_id",
        "cancelled_at",
        "created_at",
    }
    active = next(
        index
        for index in table.indexes
        if index.name == "uq_pending_channel_action_thread_active"
    )
    assert active.unique
    predicate = str(active.dialect_options["postgresql"]["where"])
    assert "consumed_at IS NULL" in predicate
    assert "cancelled_at IS NULL" in predicate


def test_ingest_dispatch_has_request_and_active_item_idempotency():
    table = IngestDispatch.__table__

    assert set(table.columns.keys()) == {
        "id",
        "public_id",
        "item_id",
        "source_thread_id",
        "request_key",
        "attempt",
        "state",
        "task_id",
        "error_code",
        "created_at",
        "updated_at",
        "asr_handoff_at",
    }
    assert any(
        constraint.name == "uq_ingest_dispatch_request_item"
        for constraint in table.constraints
    )
    source_fk = next(
        foreign_key
        for foreign_key in table.foreign_keys
        if str(foreign_key.column) == "conversation_thread.id"
    )
    assert source_fk.ondelete == "SET NULL"
    assert any(
        index.name == "ix_ingest_dispatch_source_thread"
        for index in table.indexes
    )
    active = next(
        index
        for index in table.indexes
        if index.name == "uq_ingest_dispatch_item_active"
    )
    assert active.unique
    predicate = str(active.dialect_options["postgresql"]["where"])
    assert "pending" in predicate
    assert "enqueued" in predicate
    assert "running" in predicate


def test_completion_delivery_terminal_dispositions_require_failed_status():
    contract = next(
        constraint
        for constraint in IngestCompletionDelivery.__table__.constraints
        if constraint.name == "ck_ingest_completion_delivery_state_contract"
    )
    sql = str(contract.sqltext)
    succeeded_clause = sql.split("(status = 'succeeded'", 1)[1].split(
        ") OR (status = 'failed'", 1
    )[0]
    failed_clause = sql.split(") OR (status = 'failed'", 1)[1]

    assert "terminal_failure" not in succeeded_clause
    assert "retry_exhausted" not in succeeded_clause
    assert "terminal_failure" in failed_clause
    assert "retry_exhausted" in failed_clause


def test_conversation_turn_persists_original_answer_and_action_contract():
    table = ConversationTurn.__table__

    assert table.c.answer_status.nullable is False
    assert "legacy" in str(table.c.answer_status.server_default.arg)
    assert table.c.error_code.nullable
    assert table.c.action_results.nullable is False
