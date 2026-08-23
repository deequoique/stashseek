from datetime import UTC, datetime, timedelta
import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.channels.types import TenantContext
from app.config import Settings
from app.models import AppUser, ChannelIdentity, WebSession
from app.web_auth import (
    AuthenticatedWebSession,
    InMemoryEmailSender,
    InMemoryLoginRateLimiter,
    InvalidSession,
    RedisSessionCache,
    WebAuthService,
    _token_hash,
)


class _Redis:
    def __init__(self):
        self.values = {}
        self.expiry = {}
        self.calls = []
        self.fail = False

    def _available(self):
        if self.fail:
            raise TimeoutError("redis unavailable")

    def get(self, key):
        self._available()
        self.calls.append(("get", key))
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        self._available()
        self.calls.append(("set", key))
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = ex
        return True

    def incr(self, key):
        self._available()
        self.calls.append(("incr", key))
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    def eval(self, script, _key_count, *args):
        self._available()
        self.calls.append(("eval", args[0]))
        if "return {generation, value}" in script:
            generation_key, prefix, digest = args
            generation = self.values.get(generation_key)
            if generation is None:
                return []
            value = self.values.get(f"{prefix}:{generation}:{digest}")
            return [generation] if value is None else [generation, value]
        generation_key, expected, prefix, digest, payload, ttl = args
        generation = self.values.get(generation_key)
        if generation is None or str(generation) != str(expected):
            return 0
        key = f"{prefix}:{generation}:{digest}"
        self.values[key] = payload
        self.expiry[key] = int(ttl)
        return 1


def _session() -> AuthenticatedWebSession:
    return AuthenticatedWebSession(
        7,
        TenantContext(7, 8, "web", "web", "person@example.test"),
        datetime(2026, 8, 22, 12, tzinfo=UTC) + timedelta(hours=1),
        "public-session",
        _token_hash("csrf-secret"),
    )


def test_session_cache_round_trip_uses_only_digest_key_and_bounded_projection():
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    raw_token = "opaque-session-token"
    digest = _token_hash(raw_token)
    session = _session()
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)

    cache.put(digest, session, now=now)
    assert raw_token not in repr(redis.values)
    assert "csrf-secret" not in repr(redis.values)
    assert "person@example.test" in repr(redis.values)
    redis.calls.clear()
    assert cache.get(digest, now=now) == session
    assert [call[0] for call in redis.calls] == ["eval"]
    assert all(":" + digest in key or "generation" in key for key in redis.values)


def test_zero_ttl_is_a_database_only_mode_without_redis_operations():
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=0)
    digest = _token_hash("opaque-session-token")
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)

    cache.put(digest, _session(), now=now)

    assert cache.lookup(digest, now=now) == (None, None)
    assert cache.invalidate_all() is True
    assert redis.calls == []


def test_session_cache_rejects_corrupt_values_and_generation_invalidation():
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    digest = _token_hash("opaque-session-token")
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    cache.put(digest, _session(), now=now)
    key = next(key for key in redis.values if digest in key)
    redis.values[key] = '{"version":999,"csrf_token_hash":"sentinel"}'
    assert cache.get(digest, now=now) is None

    # The first fill after corruption is deliberately discarded while the
    # cache advances to a clean generation.
    cache.put(digest, _session(), now=now)
    assert cache.get(digest, now=now) is None
    cache.put(digest, _session(), now=now)
    assert cache.get(digest, now=now) is not None
    cache.invalidate_all()
    assert cache.get(digest, now=now) is None


def test_session_cache_cold_fill_does_not_cross_generation_invalidation():
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    digest = _token_hash("opaque-session-token")
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)

    generation, cached = cache.lookup(digest, now=now)
    assert cached is None
    assert generation == 1
    assert cache.invalidate_all() is True
    cache.put(digest, _session(), now=now, generation=generation)

    assert cache.lookup(digest, now=now)[1] is None
    assert all(f":{generation}:{digest}" not in key for key in redis.values)


def test_session_cache_outage_requires_generation_bump_before_hits_resume():
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    digest = _token_hash("opaque-session-token")
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    cache.put(digest, _session(), now=now)
    old_generation = int(redis.values[cache._GENERATION_KEY])

    redis.fail = True
    assert cache.invalidate_all() is False
    assert cache.lookup(digest, now=now) == (None, None)
    redis.fail = False
    redis.calls.clear()

    generation, session = cache.lookup(digest, now=now)
    assert generation == old_generation + 1
    assert session is None
    assert [call[0] for call in redis.calls] == ["incr", "eval"]


def test_inflight_cold_fill_cannot_clear_required_generation_bump():
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    digest = _token_hash("opaque-session-token")
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    generation, cached = cache.lookup(digest, now=now)
    assert cached is None
    assert generation == 1

    redis.fail = True
    assert cache.invalidate_all() is False
    redis.fail = False
    cache.put(digest, _session(), now=now, generation=generation)

    assert int(redis.values[cache._GENERATION_KEY]) == generation + 1
    assert cache.lookup(digest, now=now)[1] is None


def test_session_cache_rejects_oversized_payload_before_json_decode():
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    digest = _token_hash("opaque-session-token")
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    cache.put(digest, _session(), now=now)
    key = next(key for key in redis.values if digest in key)
    redis.values[key] = "{" + "x" * cache._MAX_PAYLOAD_BYTES + "}"

    assert cache.get(digest, now=now) is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": "value"}),
        lambda payload: payload.__setitem__("app_user_id", True),
        lambda payload: payload.__setitem__("channel", "telegram"),
        lambda payload: payload.__setitem__("external_user_id", "Person@example.test"),
        lambda payload: payload.__setitem__("csrf_token_hash", "raw-csrf"),
        lambda payload: payload.__setitem__("expires_at", "2026-08-22T13:00:00"),
        lambda payload: payload.pop("public_id"),
    ],
)
def test_session_cache_strictly_rejects_malformed_projection_fields(mutate):
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    digest = _token_hash("opaque-session-token")
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    cache.put(digest, _session(), now=now)
    key = next(key for key in redis.values if digest in key)
    payload = json.loads(redis.values[key])
    mutate(payload)
    redis.values[key] = json.dumps(payload)

    assert cache.get(digest, now=now) is None


def test_session_cache_startup_generation_makes_previous_process_entries_cold():
    redis = _Redis()
    first = RedisSessionCache(redis, ttl_seconds=60)
    digest = _token_hash("opaque-session-token")
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    first.put(digest, _session(), now=now)
    assert first.lookup(digest, now=now)[1] is not None

    second = RedisSessionCache(redis, ttl_seconds=60)
    assert second.invalidate_all() is True

    assert first.lookup(digest, now=now)[1] is None


def test_web_auth_cache_hit_skips_database_and_revoke_outage_fails_closed():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (AppUser.__table__, ChannelIdentity.__table__, WebSession.__table__):
        table.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 8, 22, 12, tzinfo=UTC)
    raw_token = "opaque-session-token"
    with factory() as db:
        db.add(AppUser(id=7))
        db.add(
            ChannelIdentity(
                id=8,
                app_user_id=7,
                channel="web",
                account_id="web",
                external_user_id="person@example.test",
            )
        )
        db.add(
            WebSession(
                id=9,
                public_id="public-session",
                app_user_id=7,
                channel_identity_id=8,
                token_hash=_token_hash(raw_token),
                csrf_token_hash=_token_hash("csrf-secret"),
                login_channel="web",
                expires_at=now + timedelta(hours=1),
            )
        )
        db.commit()

    settings = Settings(
        database_url="sqlite://",
        notebook_agent_env="development",
        web_auth_secret="x" * 32,
    )
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    service = WebAuthService(
        factory,
        settings,
        InMemoryEmailSender(),
        InMemoryLoginRateLimiter(settings),
        session_cache=cache,
    )
    selects: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    first = service.resolve_session(raw_token, now=now)
    assert first.tenant.external_user_id == "person@example.test"
    assert len(selects) == 1
    selects.clear()
    redis.calls.clear()

    assert service.resolve_session(raw_token, now=now) == first
    assert selects == []
    assert [call[0] for call in redis.calls] == ["eval"]

    redis.fail = True
    service.revoke_session(raw_token, now=now + timedelta(seconds=1))
    redis.fail = False
    with pytest.raises(InvalidSession):
        service.resolve_session(raw_token, now=now + timedelta(seconds=2))


def test_user_session_cache_invalidation_runs_only_after_commit():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WebSession.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    assert cache.invalidate_all() is True
    WebAuthService._cache_instances.add(cache)
    generation = int(redis.values[cache._GENERATION_KEY])

    with factory() as db:
        WebAuthService.revoke_user_sessions(db, 7)
        assert int(redis.values[cache._GENERATION_KEY]) == generation
        db.rollback()
    assert int(redis.values[cache._GENERATION_KEY]) == generation

    with factory() as db:
        WebAuthService.revoke_user_sessions(db, 7)
        assert int(redis.values[cache._GENERATION_KEY]) == generation
        db.commit()
    assert int(redis.values[cache._GENERATION_KEY]) > generation


def test_session_cache_invalidation_waits_for_outer_transaction_commit():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(engine, expire_on_commit=False)
    redis = _Redis()
    cache = RedisSessionCache(redis, ttl_seconds=60)
    assert cache.invalidate_all() is True
    WebAuthService._cache_instances.add(cache)
    generation = int(redis.values[cache._GENERATION_KEY])

    with factory() as db:
        # Identity merging uses this nested-transaction shape.
        db.connection()
        with db.begin_nested():
            from app.web_auth import mark_web_session_cache_invalidation

            mark_web_session_cache_invalidation(db)
        assert int(redis.values[cache._GENERATION_KEY]) == generation
        db.commit()

    assert int(redis.values[cache._GENERATION_KEY]) > generation
