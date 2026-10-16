from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.channels.types import TenantContext
from app.config import get_settings
from app.models import (
    AppUser,
    Base,
    ChannelIdentity,
    ExternalLoginIdentity,
    WebLoginChallenge,
    WebSession,
)
from app.web.auth import WebAuthError, WebAuthService


@pytest.fixture
def web_auth_factory():
    try:
        database_url = get_settings().database_url
    except RuntimeError as exc:
        pytest.skip(f"PostgreSQL configuration unavailable: {type(exc).__name__}")
    engine = create_engine(database_url, pool_pre_ping=True)
    schema = f"test_web_auth_{uuid4().hex}"
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}", public'))
            Base.metadata.create_all(
                connection,
                tables=[
                    AppUser.__table__,
                    ChannelIdentity.__table__,
                    ExternalLoginIdentity.__table__,
                    WebLoginChallenge.__table__,
                    WebSession.__table__,
                ],
                checkfirst=False,
            )
    except Exception as exc:
        engine.dispose()
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
        engine.dispose()


def _tenant(factory, channel="telegram"):
    with factory() as db:
        user = AppUser()
        db.add(user)
        db.flush()
        identity = ChannelIdentity(
            app_user_id=user.id,
            channel=channel,
            account_id=f"account-{uuid4().hex}",
            external_user_id=f"user-{uuid4().hex}",
        )
        db.add(identity)
        db.commit()
        return TenantContext(
            user.id,
            identity.id,
            identity.channel,
            identity.account_id,
            identity.external_user_id,
        )


def _service(factory):
    return WebAuthService(
        factory,
        secret="test-web-auth-secret-that-is-long-enough",
        challenge_ttl=timedelta(minutes=10),
        session_ttl=timedelta(hours=12),
        attempt_limit=2,
        enabled_channels=("telegram", "wechat"),
    )


def test_challenge_persists_only_hashes_and_exchanges_once(web_auth_factory):
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    tenant = _tenant(web_auth_factory)
    service = _service(web_auth_factory)

    challenge = service.create_challenge("telegram", now=now)
    with web_auth_factory() as db:
        row = db.scalar(select(WebLoginChallenge))
        assert row.code_hash != challenge.code
        assert row.browser_token_hash != challenge.browser_secret
        assert challenge.code not in repr(row.__dict__)
        assert challenge.browser_secret not in repr(row.__dict__)
        assert row.expires_at == now + timedelta(minutes=10)

    service.approve(challenge.code, tenant, now=now + timedelta(seconds=1))
    credentials = service.exchange(
        challenge.public_id,
        challenge.browser_secret,
        now=now + timedelta(seconds=2),
    )

    assert credentials.user_scope.app_user_id == tenant.app_user_id
    assert credentials.expires_at == now + timedelta(seconds=2, hours=12)
    assert service.resolve_session(
        credentials.session_token, now=now + timedelta(hours=1)
    ).app_user_id == tenant.app_user_id
    service.validate_csrf(credentials.session_token, credentials.csrf_token)

    with pytest.raises(WebAuthError) as replay:
        service.exchange(
            challenge.public_id,
            challenge.browser_secret,
            now=now + timedelta(seconds=3),
        )
    assert replay.value.code == "challenge_used"

    service.revoke_session(credentials.session_token, now=now + timedelta(hours=2))
    with pytest.raises(WebAuthError) as revoked:
        service.resolve_session(
            credentials.session_token, now=now + timedelta(hours=2)
        )
    assert revoked.value.code == "session_invalid"


def test_concurrent_exchange_creates_exactly_one_session(web_auth_factory):
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    tenant = _tenant(web_auth_factory)
    service = _service(web_auth_factory)
    challenge = service.create_challenge("telegram", now=now)
    service.approve(challenge.code, tenant, now=now + timedelta(seconds=1))
    barrier = Barrier(2)

    def exchange_once():
        barrier.wait()
        try:
            credentials = service.exchange(
                challenge.public_id,
                challenge.browser_secret,
                now=now + timedelta(seconds=2),
            )
            return "ok", credentials.session_token
        except WebAuthError as exc:
            return "error", exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _index: exchange_once(), range(2)))

    assert sorted(status for status, _value in results) == ["error", "ok"]
    assert [value for status, value in results if status == "error"] == [
        "challenge_used"
    ]
    with web_auth_factory() as db:
        assert len(db.scalars(select(WebSession)).all()) == 1


def test_channel_attempt_limit_expiry_and_disabled_user_are_enforced(
    web_auth_factory,
):
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    telegram = _tenant(web_auth_factory, "telegram")
    wechat = _tenant(web_auth_factory, "wechat")
    service = _service(web_auth_factory)

    limited = service.create_challenge("telegram", now=now)
    for _ in range(2):
        with pytest.raises(WebAuthError) as wrong_channel:
            service.approve(limited.code, wechat, now=now)
        assert wrong_channel.value.code == "challenge_invalid"
    with pytest.raises(WebAuthError) as exhausted:
        service.approve(limited.code, telegram, now=now)
    assert exhausted.value.code == "challenge_invalid"

    expired = service.create_challenge("telegram", now=now)
    with pytest.raises(WebAuthError) as expiry:
        service.approve(expired.code, telegram, now=now + timedelta(minutes=10))
    assert expiry.value.code == "challenge_expired"

    disabled = service.create_challenge("telegram", now=now)
    with web_auth_factory() as db:
        db.get(AppUser, telegram.app_user_id).disabled_at = now
        db.commit()
    with pytest.raises(WebAuthError) as rejected:
        service.approve(disabled.code, telegram, now=now)
    assert rejected.value.code == "account_disabled"


def test_challenge_creation_is_rate_limited_and_stores_only_requester_hash(
    web_auth_factory,
):
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    service = WebAuthService(
        web_auth_factory,
        secret="test-web-auth-secret-that-is-long-enough",
        challenge_rate_window=timedelta(minutes=1),
        challenge_rate_limit_per_requester=2,
        challenge_global_rate_limit=10,
        challenge_active_limit_per_requester=10,
    )

    service.create_challenge("telegram", requester_key="203.0.113.7", now=now)
    service.create_challenge(
        "telegram",
        requester_key="203.0.113.7",
        now=now + timedelta(seconds=1),
    )
    with pytest.raises(WebAuthError) as limited:
        service.create_challenge(
            "telegram",
            requester_key="203.0.113.7",
            now=now + timedelta(seconds=2),
        )
    assert limited.value.code == "rate_limited"
    service.create_challenge(
        "telegram",
        requester_key="203.0.113.8",
        now=now + timedelta(seconds=2),
    )

    with web_auth_factory() as db:
        rows = db.scalars(select(WebLoginChallenge)).all()
        assert len(rows) == 3
        assert all(row.requester_hash != "203.0.113.7" for row in rows)
        assert len({row.requester_hash for row in rows}) == 2


def test_challenge_creation_purges_expired_auth_records_after_retention(
    web_auth_factory,
):
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    service = WebAuthService(
        web_auth_factory,
        secret="test-web-auth-secret-that-is-long-enough",
        challenge_retention=timedelta(hours=1),
        session_retention=timedelta(hours=1),
    )
    old = service.create_challenge(
        "telegram",
        requester_key="old-client",
        now=now - timedelta(hours=3),
    )

    service.create_challenge("telegram", requester_key="new-client", now=now)

    with web_auth_factory() as db:
        public_ids = set(db.scalars(select(WebLoginChallenge.public_id)).all())
    assert old.public_id not in public_ids
    assert len(public_ids) == 1


def test_concurrent_challenge_creation_enforces_requester_limit_exactly(
    web_auth_factory,
):
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    service = WebAuthService(
        web_auth_factory,
        secret="test-web-auth-secret-that-is-long-enough",
        challenge_rate_limit_per_requester=3,
        challenge_global_rate_limit=20,
        challenge_active_limit_per_requester=20,
    )
    barrier = Barrier(10)

    def create_once(_index):
        barrier.wait()
        try:
            service.create_challenge(
                "telegram",
                requester_key="shared-requester",
                now=now,
            )
            return "created"
        except WebAuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = tuple(pool.map(create_once, range(10)))

    assert results.count("created") == 3
    assert results.count("rate_limited") == 7
    with web_auth_factory() as db:
        assert len(db.scalars(select(WebLoginChallenge)).all()) == 3


def test_concurrent_distinct_requesters_enforce_global_limit_exactly(
    web_auth_factory,
):
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    service = WebAuthService(
        web_auth_factory,
        secret="test-web-auth-secret-that-is-long-enough",
        challenge_rate_limit_per_requester=10,
        challenge_global_rate_limit=4,
        challenge_active_limit_per_requester=10,
    )
    barrier = Barrier(10)

    def create_once(index):
        barrier.wait()
        try:
            service.create_challenge(
                "telegram",
                requester_key=f"requester-{index}",
                now=now,
            )
            return "created"
        except WebAuthError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = tuple(pool.map(create_once, range(10)))

    assert results.count("created") == 4
    assert results.count("rate_limited") == 6
    with web_auth_factory() as db:
        assert len(db.scalars(select(WebLoginChallenge)).all()) == 4


def test_challenge_retention_deletes_at_most_one_hundred_and_keeps_active_rows(
    web_auth_factory,
):
    now = datetime(2026, 8, 7, 12, tzinfo=UTC)
    service = WebAuthService(
        web_auth_factory,
        secret="test-web-auth-secret-that-is-long-enough",
        challenge_retention=timedelta(hours=1),
        session_retention=timedelta(hours=1),
    )
    active_public_id = "active-challenge"
    with web_auth_factory() as db:
        db.add_all(
            [
                WebLoginChallenge(
                    public_id=f"expired-{index}",
                    code_hash=f"expired-code-{index}",
                    browser_token_hash=f"expired-browser-{index}",
                    requester_hash="expired-requester",
                    target_channel="telegram",
                    expires_at=now - timedelta(hours=2),
                    created_at=now - timedelta(hours=3),
                )
                for index in range(101)
            ]
            + [
                WebLoginChallenge(
                    public_id=active_public_id,
                    code_hash="active-code",
                    browser_token_hash="active-browser",
                    requester_hash="active-requester",
                    target_channel="telegram",
                    expires_at=now + timedelta(minutes=10),
                    created_at=now,
                )
            ]
        )
        db.commit()

    service.create_challenge("telegram", requester_key="new-requester", now=now)

    with web_auth_factory() as db:
        rows = db.scalars(select(WebLoginChallenge)).all()
        assert sum(row.expires_at < now for row in rows) == 1
        assert any(row.public_id == active_public_id for row in rows)

    service.create_challenge("telegram", requester_key="new-requester", now=now)

    with web_auth_factory() as db:
        rows = db.scalars(select(WebLoginChallenge)).all()
        assert all(row.expires_at >= now for row in rows)
        assert any(row.public_id == active_public_id for row in rows)
