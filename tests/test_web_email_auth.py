from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.types import AgentAnswer
from app.agent.runtime import AgentExecution
from app.api.app import WebApiServices, create_app
from app.channels.service import ChannelService
from app.channels.types import TenantContext
from app.api.email_auth_routes import EmailWebAuthAdapter
from app.config import Settings
from app.models import (
    AppUser,
    ChannelIdentity,
    WebAuthChallenge,
    WebSession,
)
from app.web.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    ResolvedWebSession,
    WebAuthError,
)
from app.web.library import LibraryPage
from app.web_auth import (
    EmailDeliveryUnavailable,
    InMemoryEmailSender,
    InMemoryLoginRateLimiter,
    LoginRateLimited,
    WebAuthService,
)


ORIGIN = "https://app.example.test"


class _Library:
    def __init__(self) -> None:
        self.scopes = []

    def list_items(self, scope, **_kwargs):
        self.scopes.append(scope)
        return LibraryPage((), 0, 1, 20, True)


class _Unused:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected service call: {name}")


def _app():
    settings = Settings(
        database_url="sqlite://",
        notebook_agent_env="development",
        web_auth_enabled=True,
        web_public_origin=ORIGIN,
        web_auth_secret="x" * 32,
        email_provider=None,
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        AppUser.__table__,
        ChannelIdentity.__table__,
        WebAuthChallenge.__table__,
        WebSession.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(AppUser(id=101))
        db.add(
            ChannelIdentity(
                id=102,
                app_user_id=101,
                channel="web",
                account_id="web",
                external_user_id="known@example.test",
            )
        )
        db.commit()
    sender = InMemoryEmailSender()
    auth = WebAuthService(
        factory,
        settings,
        sender,
        InMemoryLoginRateLimiter(settings),
    )
    resolve_calls = []
    authoritative_resolve = auth.resolve_session

    def resolve_session(raw_token: str):
        resolve_calls.append(raw_token)
        return authoritative_resolve(raw_token)

    auth.resolve_session = resolve_session

    library = _Library()
    app = create_app(
        services=WebApiServices(
            web_auth=EmailWebAuthAdapter(auth),
            library=library,
            submission=_Unused(),
            transcript=_Unused(),
            email_auth=auth,
            channel_service=None,
            session_resolver=resolve_session,
            session_factory=factory,
            settings=settings,
        ),
        expected_origin=ORIGIN,
        cookie_secure=True,
        static_dir=None,
        web_login_channels=("email",),
    )
    app.state.email_auth = auth
    app.state.email_auth_factory = factory
    app.state.resolve_calls = resolve_calls
    return app, sender, library


async def _login(client: httpx.AsyncClient, sender: InMemoryEmailSender, email: str):
    origin = {"Origin": ORIGIN}
    accepted = await client.post(
        "/api/v1/auth/challenges",
        headers=origin,
        json={"email": email},
    )
    assert accepted.status_code == 200
    code = sender.messages[-1].code
    verified = await client.post(
        "/api/v1/auth/verify",
        headers=origin,
        json={"email": email, "code": code},
    )
    return verified


@pytest.mark.asyncio
async def test_email_logout_reuses_one_authoritative_session_resolution():
    app, sender, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        verified = await _login(client, sender, "known@example.test")
        assert verified.status_code == 200
        csrf = client.cookies.get(CSRF_COOKIE_NAME)
        app.state.resolve_calls.clear()

        response = await client.delete(
            "/api/v1/auth/session",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )

    assert response.status_code == 204
    assert len(app.state.resolve_calls) == 1


@pytest.mark.asyncio
async def test_protected_mutation_reuses_boundary_session_in_route_dependency():
    app, sender, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        verified = await _login(client, sender, "known@example.test")
        assert verified.status_code == 200
        csrf = client.cookies.get(CSRF_COOKIE_NAME)
        app.state.resolve_calls.clear()

        response = await client.post(
            "/api/v1/conversations/conversation-id/messages",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={"message_id": "message-id", "text": "question"},
        )

    assert response.status_code == 503
    assert len(app.state.resolve_calls) == 1


@pytest.mark.asyncio
async def test_protected_mutation_maps_raw_session_resolver_failure_to_401():
    app, _, _ = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        response = await client.post(
            "/api/v1/conversations/conversation-id/messages",
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": "matching-csrf",
                "Cookie": (
                    f"{SESSION_COOKIE_NAME}=invalid-session; "
                    f"{CSRF_COOKIE_NAME}=matching-csrf"
                ),
            },
            json={"message_id": "message-id", "text": "question"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "session_invalid"
    assert len(app.state.resolve_calls) == 1


@pytest.mark.asyncio
async def test_email_routes_are_canonical_safe_and_non_enumerating(monkeypatch):
    app, sender, _ = _app()
    auth = app.state.email_auth
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        origin = {"Origin": ORIGIN}
        known = await client.post(
            "/api/v1/auth/challenges",
            headers=origin,
            json={"email": "known@example.test"},
        )
        unknown = await client.post(
            "/api/v1/auth/challenges",
            headers=origin,
            json={"email": "unknown@example.test"},
        )
        invalid = await client.post(
            "/api/v1/auth/challenges",
            headers=origin,
            json={"email": "not-an-email"},
        )
        def limited(_email, _client_ip):
            raise LoginRateLimited()

        monkeypatch.setattr(auth, "request_challenge", limited)
        limited = await client.post(
            "/api/v1/auth/challenges",
            headers=origin,
            json={"email": "limited@example.test"},
        )
        malformed_code = await client.post(
            "/api/v1/auth/verify",
            headers=origin,
            json={"email": "known@example.test", "code": "12"},
        )
        failed_code = await client.post(
            "/api/v1/auth/verify",
            headers=origin,
            json={"email": "known@example.test", "code": "000000"},
        )

    assert known.status_code == unknown.status_code == limited.status_code == 200
    assert known.json() == unknown.json() == limited.json() == {"status": "accepted"}
    assert len(sender.messages) == 2
    with app.state.email_auth_factory() as db:
        existing = db.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.channel == "web",
                ChannelIdentity.account_id == "web",
                ChannelIdentity.external_user_id == "known@example.test",
            )
        )
    assert existing is not None and existing.app_user_id == 101
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_email"
    assert malformed_code.status_code == 422
    assert malformed_code.json() == {
        "code": "validation_error",
        "message": "请求参数无效",
    }
    assert failed_code.status_code == 401
    assert failed_code.json() == {
        "code": "verification_failed",
        "message": "验证码无效或已过期",
    }
    for response in (invalid, malformed_code, failed_code):
        assert "known@example.test" not in response.text
        assert "000000" not in response.text
        assert "provider" not in response.text.lower()


@pytest.mark.asyncio
async def test_email_challenge_rate_limit_is_indistinguishable_and_does_not_send(monkeypatch):
    app, sender, _ = _app()
    auth = app.state.email_auth
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        normal = await client.post(
            "/api/v1/auth/challenges",
            headers={"Origin": ORIGIN},
            json={"email": "person@example.test"},
        )
        sent_count = len(sender.messages)

        def limited(_email, _client_ip):
            raise LoginRateLimited()

        monkeypatch.setattr(auth, "request_challenge", limited)
        limited_response = await client.post(
            "/api/v1/auth/challenges",
            headers={"Origin": ORIGIN},
            json={"email": "person@example.test"},
        )

    assert limited_response.status_code == normal.status_code == 200
    assert limited_response.json() == normal.json() == {"status": "accepted"}
    assert len(sender.messages) == sent_count


@pytest.mark.asyncio
async def test_email_provider_failures_are_bounded_and_never_echo_details(monkeypatch):
    app, sender, _ = _app()
    auth = app.state.email_auth

    def unavailable(_email, _client_ip):
        raise EmailDeliveryUnavailable()

    monkeypatch.setattr(auth, "request_challenge", unavailable)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        unavailable_response = await client.post(
            "/api/v1/auth/challenges",
            headers={"Origin": ORIGIN},
            json={"email": "person@example.test"},
        )

        def exploding(_email, _client_ip):
            raise RuntimeError("provider-secret-token")

        monkeypatch.setattr(auth, "request_challenge", exploding)
        unexpected_challenge = await client.post(
            "/api/v1/auth/challenges",
            headers={"Origin": ORIGIN},
            json={"email": "person@example.test"},
        )

        def exploding_verify(_email, _code):
            raise RuntimeError("provider-secret-token")

        monkeypatch.setattr(auth, "verify", exploding_verify)
        unexpected_verify = await client.post(
            "/api/v1/auth/verify",
            headers={"Origin": ORIGIN},
            json={"email": "person@example.test", "code": "123456"},
        )

    assert unavailable_response.status_code == 503
    assert unavailable_response.json() == {
        "code": "email_delivery_unavailable",
        "message": "登录服务暂时不可用，请稍后重试",
    }
    for response in (unavailable_response, unexpected_challenge, unexpected_verify):
        assert response.status_code in {500, 503}
        assert "provider-secret-token" not in response.text
        assert "person@example.test" not in response.text
    assert unexpected_challenge.status_code == unexpected_verify.status_code == 500
    assert unexpected_challenge.json() == unexpected_verify.json() == {
        "code": "request_failed",
        "message": "请求无法完成",
    }
    assert sender.messages == []


@pytest.mark.asyncio
async def test_email_login_establishes_id_free_session_library_scope_and_csrf_logout():
    app, sender, library = _app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        verified = await _login(client, sender, "person@example.test")
        assert verified.status_code == 200
        assert verified.json()["authenticated"] is True
        assert verified.json()["login_channel"] == "email"
        assert set(verified.json()) == {"authenticated", "login_channel", "expires_at"}
        assert "app_user_id" not in verified.text
        assert SESSION_COOKIE_NAME in client.cookies
        csrf = client.cookies.get(CSRF_COOKIE_NAME)
        assert csrf

        session = await client.get("/api/v1/auth/session")
        library_response = await client.get("/api/v1/library/items")
        blocked_logout = await client.delete(
            "/api/v1/auth/session", headers={"Origin": ORIGIN}
        )
        wrong_csrf = await client.delete(
            "/api/v1/auth/session",
            headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
        )
        logout = await client.delete(
            "/api/v1/auth/session",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )
        after = await client.get("/api/v1/auth/session")

    assert session.status_code == 200
    assert set(session.json()) == {"authenticated", "login_channel", "expires_at"}
    assert library_response.status_code == 200
    assert library_response.json()["is_true_first_empty"] is True
    assert library.scopes[0].app_user_id > 0
    assert blocked_logout.status_code == wrong_csrf.status_code == 403
    assert blocked_logout.json()["code"] == wrong_csrf.json()["code"] == "csrf_invalid"
    assert logout.status_code == 204
    assert after.status_code == 401


def test_production_email_composition_mounts_browser_compatibility_paths():
    app, _sender, _library = _app()
    document = app.openapi()
    paths = document["paths"]
    assert paths["/api/v1/auth/challenges"]["post"]["responses"]["500"]
    assert paths["/api/v1/auth/verify"]["post"]["responses"]["500"]
    assert paths["/api/v1/conversations/{conversation_id}/messages"]
    assert paths["/api/v1/conversations/{conversation_id}/reset"]
    assert paths["/api/v1/link-tokens"]["post"]["parameters"]
    assert paths["/api/v1/link-tokens/consume"]["post"]["parameters"]
    serialized = str(document)
    for forbidden in ("item_id", "segment_id", "app_user_id", "session_id"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_legacy_session_identity_adapter_builds_external_web_envelope():
    settings = Settings(
        database_url="sqlite://",
        notebook_agent_env="development",
        web_origin=ORIGIN,
        web_auth_secret="x" * 32,
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        AppUser.__table__,
        ChannelIdentity.__table__,
    ):
        table.create(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE conversation_thread (
                id INTEGER PRIMARY KEY,
                public_id TEXT NOT NULL UNIQUE,
                app_user_id INTEGER NOT NULL,
                channel_identity_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                account_id TEXT NOT NULL,
                external_conversation_id TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                closed_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE conversation_turn (
                id INTEGER PRIMARY KEY,
                thread_id INTEGER NOT NULL,
                message_id TEXT NOT NULL,
                user_text TEXT NOT NULL,
                assistant_text TEXT NOT NULL,
                sources TEXT NOT NULL,
                model_messages TEXT NOT NULL,
                answer_status TEXT NOT NULL DEFAULT 'legacy',
                error_code TEXT,
                action_results TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'completed',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(thread_id, message_id)
            )
            """
        )
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(AppUser(id=7))
        db.add(
            ChannelIdentity(
                id=11,
                app_user_id=7,
                channel="telegram",
                account_id="bot",
                external_user_id="telegram-user",
            )
        )
        db.commit()

    class LegacyAuth:
        def resolve_session(self, token):
            if token != "legacy-session":
                raise WebAuthError("session_invalid")
            return ResolvedWebSession(
                7,
                "session-public",
                "telegram",
                datetime.now(UTC) + timedelta(hours=1),
            )

        def validate_csrf(self, token, csrf):
            if token != "legacy-session" or csrf != "legacy-csrf":
                raise WebAuthError("csrf_invalid")

        def revoke_session(self, _token):
            return None

    class RecordingAgent:
        def __init__(self):
            self.requests = []

        async def run(self, request, *, diagnostics=None):
            self.requests.append(request)
            return AgentExecution(
                AgentAnswer(
                    status="ok",
                    text="ok",
                    thread_id=request.thread_public_id,
                ),
                [],
            )

    agent = RecordingAgent()
    channel_service = ChannelService(factory, agent, settings)
    legacy_auth = LegacyAuth()
    app = create_app(
        services=WebApiServices(
            web_auth=legacy_auth,
            library=_Unused(),
            submission=_Unused(),
            transcript=_Unused(),
            email_auth=None,
            channel_service=channel_service,
            session_resolver=legacy_auth.resolve_session,
            session_factory=factory,
            settings=settings,
        ),
        expected_origin=ORIGIN,
        cookie_secure=True,
        static_dir=None,
        web_login_channels=("telegram",),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as client:
        client.cookies.set(SESSION_COOKIE_NAME, "legacy-session")
        client.cookies.set(CSRF_COOKIE_NAME, "legacy-csrf")
        response = await client.post(
            "/api/v1/conversations/legacy-thread/messages",
            headers={"Origin": ORIGIN, "X-CSRF-Token": "legacy-csrf"},
            json={"message_id": "m1", "text": "hello"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["text"] == "ok"
    assert body["citations"] == []
    assert body["action_results"] == []
    assert body["thread_id"]
    assert body["error_code"] is None
    assert agent.requests[-1].tenant == TenantContext(
        7,
        11,
        "telegram",
        "bot",
        "telegram-user",
    )
    assert agent.requests[-1].tenant.app_user_id == 7
