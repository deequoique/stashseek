"""Disposable Redis index that gates the periodic notification repair sweep.

PostgreSQL (``ingest_completion_event`` / ``ingest_completion_delivery``) stays
the only source of truth for notification delivery.  This module owns a small,
*losable* Redis structure that answers one question for the Beat repair tick:
"is there anything worth waking the database for right now?"  When the answer
is no, the idle tick issues zero PostgreSQL statements so a Neon compute can
scale to zero.

Layout (``notebook-agent:<area>:v1`` key convention)::

    <prefix>:due       ZSET  member=str(event_id), score=integer epoch seconds
                             "earliest time the repair sweep should look at it"
    <prefix>:baseline  STRING (no TTL) written only after a full repair has
                             rebuilt the index from PostgreSQL

Losing either key, or failing to write them, can only delay a notification
until the next daily full repair; it can never duplicate or drop one, because
claim/ACK idempotency stays in the PostgreSQL ledger.  Every Redis failure is
raised to the caller, which decides between fail-open reads and best-effort
writes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
import math
from typing import Any, Protocol


DEFAULT_KEY_PREFIX = "notebook-agent:ingest-notification-repair:v1"
# Every Redis round trip on the Beat/worker path is bounded well below the
# claim and sweep budgets so an unreachable Redis fails open quickly.
REDIS_SOCKET_TIMEOUT_SECONDS = 0.5

# Delivery dispositions that are final: no further claim will ever run.
_TERMINAL_FAILED_DISPOSITIONS = frozenset({"terminal_failure", "retry_exhausted"})

# Compare-and-set: change/remove a member only while its score is still the one
# the caller observed.  ARGV[2]/ARGV[3] are '' for "absent"/"remove".  A
# concurrent ``mark_due`` (for example an operator redrive) that moved the
# score therefore wins instead of being overwritten by a stale derivation.
_RECONCILE_SCRIPT = """
local current = redis.call('ZSCORE', KEYS[1], ARGV[1])
if ARGV[2] == '' then
  if current then return 0 end
else
  if (not current) or tonumber(current) ~= tonumber(ARGV[2]) then return 0 end
end
if ARGV[3] == '' then
  redis.call('ZREM', KEYS[1], ARGV[1])
else
  redis.call('ZADD', KEYS[1], ARGV[3], ARGV[1])
end
return 1
"""


class NotificationRepairIndex(Protocol):
    """Contract shared by the Redis index and the in-memory test double."""

    def mark_due(self, event_id: int, at: int) -> None:
        """Make ``event_id`` due at ``at`` (epoch seconds); earliest time wins."""

    def score(self, event_id: int) -> int | None:
        """Return the member's due time, or ``None`` when it is not indexed."""

    def has_due(self, now: int) -> bool:
        """Whether any member is due at or before ``now``."""

    def due_members(self, now: int, limit: int) -> list[tuple[int, int]]:
        """Bounded ``(event_id, score)`` pairs due at or before ``now``."""

    def members(self, limit: int) -> list[tuple[int, int]]:
        """Bounded ``(event_id, score)`` pairs, soonest first."""

    def reconcile(
        self, event_id: int, expected_score: int | None, new_score: int | None
    ) -> bool:
        """Set (or, with ``None``, remove) a member iff its score is unchanged."""

    def has_baseline(self) -> bool: ...

    def set_baseline(self) -> None: ...

    def clear_baseline(self) -> None:
        """Forget the baseline so the next tick runs an ungated full repair."""


class InMemoryNotificationRepairIndex:
    """Process-local implementation with the exact Redis semantics (for tests)."""

    def __init__(self) -> None:
        self._due: dict[int, int] = {}
        self._baseline = False

    def mark_due(self, event_id: int, at: int) -> None:
        key, value = int(event_id), int(at)
        current = self._due.get(key)
        if current is None or value < current:
            self._due[key] = value

    def score(self, event_id: int) -> int | None:
        return self._due.get(int(event_id))

    def has_due(self, now: int) -> bool:
        return any(score <= int(now) for score in self._due.values())

    def due_members(self, now: int, limit: int) -> list[tuple[int, int]]:
        due = sorted(
            ((event_id, score) for event_id, score in self._due.items()
             if score <= int(now)),
            key=lambda pair: (pair[1], pair[0]),
        )
        return due[: max(0, int(limit))]

    def members(self, limit: int) -> list[tuple[int, int]]:
        ordered = sorted(self._due.items(), key=lambda pair: (pair[1], pair[0]))
        return ordered[: max(0, int(limit))]

    def reconcile(
        self, event_id: int, expected_score: int | None, new_score: int | None
    ) -> bool:
        key = int(event_id)
        if expected_score is None and new_score is None:
            return False
        expected = None if expected_score is None else int(expected_score)
        if self._due.get(key) != expected:
            return False
        if new_score is None:
            self._due.pop(key, None)
        else:
            self._due[key] = int(new_score)
        return True

    def has_baseline(self) -> bool:
        return self._baseline

    def set_baseline(self) -> None:
        self._baseline = True

    def clear_baseline(self) -> None:
        self._baseline = False


class RedisNotificationRepairIndex:
    """Redis implementation; every method raises the underlying Redis error."""

    def __init__(self, client: Any, *, key_prefix: str = DEFAULT_KEY_PREFIX) -> None:
        self._client = client
        self._due_key = f"{key_prefix}:due"
        self._baseline_key = f"{key_prefix}:baseline"

    @classmethod
    def from_url(
        cls, redis_url: str, *, key_prefix: str = DEFAULT_KEY_PREFIX
    ) -> "RedisNotificationRepairIndex":
        import redis

        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
            health_check_interval=0,
        )
        return cls(client, key_prefix=key_prefix)

    @staticmethod
    def _pairs(raw: Any) -> list[tuple[int, int]]:
        return [(int(member), int(score)) for member, score in raw]

    def mark_due(self, event_id: int, at: int) -> None:
        # LT: a new member is added; an existing one only moves earlier.
        self._client.zadd(self._due_key, {str(int(event_id)): int(at)}, lt=True)

    def score(self, event_id: int) -> int | None:
        value = self._client.zscore(self._due_key, str(int(event_id)))
        return None if value is None else int(value)

    def has_due(self, now: int) -> bool:
        return bool(
            self._client.zrangebyscore(
                self._due_key, "-inf", int(now), start=0, num=1
            )
        )

    def due_members(self, now: int, limit: int) -> list[tuple[int, int]]:
        if int(limit) <= 0:
            return []
        return self._pairs(
            self._client.zrangebyscore(
                self._due_key,
                "-inf",
                int(now),
                start=0,
                num=int(limit),
                withscores=True,
            )
        )

    def members(self, limit: int) -> list[tuple[int, int]]:
        if int(limit) <= 0:
            return []
        return self._pairs(
            self._client.zrange(self._due_key, 0, int(limit) - 1, withscores=True)
        )

    def reconcile(
        self, event_id: int, expected_score: int | None, new_score: int | None
    ) -> bool:
        if expected_score is None and new_score is None:
            return False
        result = self._client.eval(
            _RECONCILE_SCRIPT,
            1,
            self._due_key,
            str(int(event_id)),
            "" if expected_score is None else str(int(expected_score)),
            "" if new_score is None else str(int(new_score)),
        )
        return bool(int(result))

    def has_baseline(self) -> bool:
        return bool(self._client.exists(self._baseline_key))

    def set_baseline(self) -> None:
        self._client.set(self._baseline_key, "1")

    def clear_baseline(self) -> None:
        self._client.delete(self._baseline_key)


@lru_cache(maxsize=4)
def redis_repair_index(redis_url: str) -> RedisNotificationRepairIndex:
    """Process-cached index client.  Construction is lazy: no connection yet."""

    return RedisNotificationRepairIndex.from_url(redis_url)


def _epoch(value: datetime) -> int:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    # Round up so a member is never due before PostgreSQL would consider the
    # ledger row eligible.
    return int(math.ceil(aware.timestamp()))


def repair_due_at(
    *,
    event_exists: bool,
    delivery: Any | None,
    db_now: datetime,
    settings: Any,
) -> int | None:
    """The single derivation of "when must repair look at this event".

    Returns integer epoch seconds, or ``None`` when the event needs no further
    repair (terminal, succeeded, or gone) and should leave the index.

    ====================================  ===============================
    PostgreSQL state                      due time
    ====================================  ===============================
    event missing / purged                ``None``
    no delivery row (targeted task idle)  ``db_now``
    ``claimed``                           ``claimed_at + claim timeout``
    ``failed`` and retryable              ``next_attempt_at`` (or ``db_now``)
    ``failed`` terminal / exhausted       ``None``
    ``succeeded``                         ``None``
    ====================================  ===============================
    """

    if not event_exists:
        return None
    if delivery is None:
        return _epoch(db_now)
    status = getattr(delivery, "status", None)
    if status == "succeeded":
        return None
    if status == "claimed":
        claimed_at = getattr(delivery, "claimed_at", None)
        if claimed_at is None:
            return _epoch(db_now)
        timeout = settings.ingest_notification_claim_timeout_seconds
        return _epoch(claimed_at + timedelta(seconds=timeout))
    if status == "failed":
        if getattr(delivery, "disposition", None) in _TERMINAL_FAILED_DISPOSITIONS:
            return None
        next_attempt_at = getattr(delivery, "next_attempt_at", None)
        return _epoch(next_attempt_at if next_attempt_at is not None else db_now)
    # Unknown ledger vocabulary: let the sweep decide.
    return _epoch(db_now)
