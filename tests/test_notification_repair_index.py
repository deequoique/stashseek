"""Contract tests for the disposable Redis notification-repair index.

The in-memory double and the Redis implementation must behave identically.
Redis-backed cases use a real local Redis on logical database 15 with a random
key prefix and skip when Redis is unreachable.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4
import time

import pytest

from app.config import Settings
from app.ingest.notification_repair_index import (
    DEFAULT_KEY_PREFIX,
    REDIS_SOCKET_TIMEOUT_SECONDS,
    InMemoryNotificationRepairIndex,
    RedisNotificationRepairIndex,
    repair_due_at,
)

DB_NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC)
SETTINGS = SimpleNamespace(ingest_notification_claim_timeout_seconds=300)


def _epoch(value: datetime) -> int:
    return int(value.timestamp())


def _delivery(**values):
    base = {
        "status": "claimed",
        "disposition": None,
        "claimed_at": None,
        "next_attempt_at": None,
    }
    base.update(values)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- derivation


def test_repair_due_at_table_matches_the_design_contract():
    def derive(delivery, *, exists=True):
        return repair_due_at(
            event_exists=exists,
            delivery=delivery,
            db_now=DB_NOW,
            settings=SETTINGS,
        )

    # Event purged/deleted, or ledger row terminal: leaves the index.
    assert derive(None, exists=False) is None
    assert derive(_delivery(status="succeeded", disposition="sent")) is None
    assert derive(_delivery(status="failed", disposition="terminal_failure")) is None
    assert derive(_delivery(status="failed", disposition="retry_exhausted")) is None

    # No delivery row: the targeted task has not claimed it, look right away.
    assert derive(None) == _epoch(DB_NOW)

    # Claimed: eligible for stale reclaim once the claim timeout has elapsed.
    claimed_at = DB_NOW - timedelta(seconds=40)
    assert derive(_delivery(status="claimed", claimed_at=claimed_at)) == _epoch(
        claimed_at + timedelta(seconds=300)
    )

    # Retryable failure: exactly its retry time (never earlier).
    retry_at = DB_NOW + timedelta(seconds=45)
    assert derive(_delivery(status="failed", next_attempt_at=retry_at)) == _epoch(
        retry_at
    )
    assert derive(_delivery(status="failed")) == _epoch(DB_NOW)


def test_repair_due_at_rounds_up_so_a_member_is_never_due_early():
    retry_at = DB_NOW + timedelta(seconds=10, milliseconds=1)
    due = repair_due_at(
        event_exists=True,
        delivery=_delivery(status="failed", next_attempt_at=retry_at),
        db_now=DB_NOW,
        settings=SETTINGS,
    )
    assert due == _epoch(DB_NOW) + 11


def test_repair_due_at_accepts_naive_datetimes_as_utc():
    naive = datetime(2026, 10, 16, 12, 0, 0)
    assert repair_due_at(
        event_exists=True, delivery=None, db_now=naive, settings=SETTINGS
    ) == _epoch(DB_NOW)


# ------------------------------------------------- shared index semantics


@pytest.fixture(params=["memory", "redis"])
def index(request):
    if request.param == "memory":
        yield InMemoryNotificationRepairIndex()
        return
    redis = pytest.importorskip("redis")
    parsed = urlsplit(Settings().redis_url)
    client = redis.Redis(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        db=15,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
    )
    try:
        client.ping()
    except Exception:
        client.close()
        pytest.skip("local Redis unavailable")
    prefix = f"test-notification-repair-{uuid4().hex}"
    try:
        yield RedisNotificationRepairIndex(client, key_prefix=prefix)
    finally:
        client.delete(f"{prefix}:due", f"{prefix}:baseline")
        client.close()


def test_mark_due_keeps_the_earliest_time(index):
    index.mark_due(7, 1_000)
    index.mark_due(7, 2_000)  # later: ignored
    assert index.score(7) == 1_000
    index.mark_due(7, 500)  # earlier: wins
    assert index.score(7) == 500
    assert index.score(8) is None


def test_due_members_are_bounded_and_only_include_due_scores(index):
    index.mark_due(1, 100)
    index.mark_due(2, 200)
    index.mark_due(3, 300)

    assert index.has_due(99) is False
    assert index.has_due(100) is True
    assert index.due_members(250, 10) == [(1, 100), (2, 200)]
    assert index.due_members(250, 1) == [(1, 100)]
    assert index.due_members(250, 0) == []
    assert index.members(10) == [(1, 100), (2, 200), (3, 300)]
    assert index.members(2) == [(1, 100), (2, 200)]


def test_reconcile_updates_or_removes_only_when_the_score_is_unchanged(index):
    index.mark_due(5, 1_000)

    assert index.reconcile(5, 1_000, 1_500) is True
    assert index.score(5) == 1_500
    assert index.reconcile(5, 1_500, None) is True
    assert index.score(5) is None


def test_reconcile_loses_to_a_concurrent_mark_due(index):
    """A stale derivation must not overwrite an operator's newer request."""

    index.mark_due(5, 1_000)
    observed = index.score(5)
    index.mark_due(5, 400)  # concurrent redrive/enqueue-failure mark

    assert index.reconcile(5, observed, None) is False
    assert index.score(5) == 400
    assert index.reconcile(5, observed, 9_999) is False
    assert index.score(5) == 400


def test_reconcile_absent_expectation_only_adds_when_still_absent(index):
    assert index.reconcile(9, None, 700) is True
    assert index.score(9) == 700
    # Already present now: an "absent" expectation must fail.
    assert index.reconcile(9, None, 800) is False
    assert index.score(9) == 700
    # Nothing to do for absent -> removed.
    assert index.reconcile(10, None, None) is False


def test_baseline_marker_is_absent_until_set_and_can_be_cleared(index):
    assert index.has_baseline() is False
    index.set_baseline()
    assert index.has_baseline() is True
    index.clear_baseline()
    assert index.has_baseline() is False
    index.clear_baseline()  # clearing an absent marker is a no-op


# ------------------------------------------------------ Redis specifics


def test_redis_client_is_built_with_sub_second_socket_timeouts():
    pytest.importorskip("redis")
    built = RedisNotificationRepairIndex.from_url("redis://127.0.0.1:6379/0")
    kwargs = built._client.connection_pool.connection_kwargs
    assert REDIS_SOCKET_TIMEOUT_SECONDS <= 0.5
    assert kwargs["socket_connect_timeout"] == REDIS_SOCKET_TIMEOUT_SECONDS
    assert kwargs["socket_timeout"] == REDIS_SOCKET_TIMEOUT_SECONDS
    built._client.close()


def test_default_key_prefix_follows_the_project_convention():
    assert DEFAULT_KEY_PREFIX == "notebook-agent:ingest-notification-repair:v1"


def test_unreachable_redis_raises_quickly_instead_of_hanging():
    pytest.importorskip("redis")
    # Port 1 on loopback refuses immediately; the point is that the error
    # surfaces to the caller (which fails open) and is bounded by the timeout.
    unreachable = RedisNotificationRepairIndex.from_url("redis://127.0.0.1:1/0")
    started = time.monotonic()
    with pytest.raises(Exception):
        unreachable.has_baseline()
    with pytest.raises(Exception):
        unreachable.mark_due(1, 1)
    assert time.monotonic() - started < 3.0
    unreachable._client.close()
