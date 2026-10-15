"""R0: conversation routes accept an extension-session Bearer without CSRF.

A ``chrome-extension://`` page cannot read the ``__Host-kb_csrf`` cookie, so
the route-level ``X-CSRF-Token`` header must not be required.  Enforcement
stays in the app boundary: cookie sessions still need the double-submit header.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.agent.types import AgentAnswer
from app.api.app import WebApiServices, create_app
from app.channels.types import TenantContext
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, WebAuthError

ORIGIN = "https://kb.example.test"
EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32
TENANT = TenantContext(7, 11, "web", "web", "user-7")


def _session():
    return SimpleNamespace(
        tenant=TENANT, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )


class CookieAuth:
    def resolve_session(self, token):
        if token != "session-secret":
            raise WebAuthError("session_invalid")
        return _session()

    def validate_csrf(self, _session_token, csrf_token):
        if csrf_token != "csrf-secret":
            raise WebAuthError("csrf_invalid")


class ExtensionSessions:
    """Knows only the extension session token; a capture token is unknown."""

    def resolve(self, token):
        if token != "extension-session":
            raise HTTPException(status_code=401, detail="extension_session_invalid")
        return _session(), TENANT.app_user_id, "user@example.test"


class Channel:
    def __init__(self):
        self.envelopes = []

    async def handle(self, envelope):
        self.envelopes.append(envelope)
        return AgentAnswer(status="ok", text="answer")


class Unused:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected service call: {name}")


def _client():
    channel = Channel()
    app = create_app(
        services=WebApiServices(
            web_auth=CookieAuth(),
            library=Unused(),
            submission=Unused(),
            transcript=Unused(),
            channel_service=channel,
            session_resolver=CookieAuth().resolve_session,
            session_identity_resolver=lambda _session: TENANT,
            session_factory=lambda: None,
            settings=SimpleNamespace(
                agent_timeout_seconds=5.0,
                agent_streaming_enabled=True,
                browser_companion_allowed_origins=(EXTENSION_ORIGIN,),
                browser_companion_max_request_bytes=5_500_000,
            ),
            extension_sessions=ExtensionSessions(),
        ),
        expected_origin=ORIGIN,
        cookie_secure=True,
        publish_budget_seconds=1.0,
    )
    return TestClient(app, base_url=ORIGIN), channel


def _with_cookies(client: TestClient) -> TestClient:
    client.cookies.set(
        SESSION_COOKIE_NAME, "session-secret", domain="kb.example.test", path="/"
    )
    client.cookies.set(
        CSRF_COOKIE_NAME, "csrf-secret", domain="kb.example.test", path="/"
    )
    return client


ROUTES = (
    ("/api/v1/conversations/c-1/messages", {"message_id": "m-1", "text": "hello"}),
    ("/api/v1/conversations/c-1/reset", None),
)


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_extension_bearer_needs_no_csrf_header(path, body):
    client, channel = _client()

    response = client.post(
        path,
        json=body,
        headers={
            "Authorization": "Bearer extension-session",
            "Origin": EXTENSION_ORIGIN,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["text"] == "answer"
    assert len(channel.envelopes) == 1
    assert channel.envelopes[0].external_user_id == "user-7"


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_cookie_without_csrf_header_is_rejected_by_the_app_boundary(path, body):
    client, channel = _client()
    _with_cookies(client)

    response = client.post(path, json=body, headers={"Origin": ORIGIN})

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"
    assert channel.envelopes == []


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_cookie_with_wrong_csrf_header_is_rejected(path, body):
    client, channel = _client()
    _with_cookies(client)

    response = client.post(
        path,
        json=body,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "not-the-cookie-value"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"
    assert channel.envelopes == []


@pytest.mark.parametrize(("path", "body"), ROUTES)
def test_cookie_with_matching_csrf_header_succeeds(path, body):
    client, channel = _client()
    _with_cookies(client)

    response = client.post(
        path,
        json=body,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "csrf-secret"},
    )

    assert response.status_code == 200, response.text
    assert len(channel.envelopes) == 1


def test_unknown_bearer_is_unauthorized_not_a_validation_error():
    client, channel = _client()

    response = client.post(
        ROUTES[0][0],
        json=ROUTES[0][1],
        headers={
            "Authorization": "Bearer capture-token",
            "Origin": EXTENSION_ORIGIN,
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "extension_session_invalid"
    assert channel.envelopes == []


def test_csrf_header_is_documented_but_optional_on_conversation_routes():
    client, _ = _client()
    document = client.get("/api/v1/openapi.json").json()

    for path in (
        "/api/v1/conversations/{conversation_id}/messages",
        "/api/v1/conversations/{conversation_id}/messages/stream",
        "/api/v1/conversations/{conversation_id}/reset",
    ):
        parameters = document["paths"][path]["post"]["parameters"]
        csrf = next(item for item in parameters if item["name"] == "X-CSRF-Token")
        assert csrf["in"] == "header"
        assert csrf.get("required", False) is False
