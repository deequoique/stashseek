"""Chrome MV3 extension pages omit ``Origin`` on cross-origin GET requests.

``/api/v1/extension/*`` authenticates only with an explicit Bearer session, so a
read-only GET/HEAD without any Origin header passes the Origin gate while every
present-but-disallowed Origin and every unsafe method without Origin is still
rejected.  No CORS allow-origin header is emitted for a missing Origin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.app import WebApiServices, create_app
from app.api.extension_routes import ExtensionSessionService
from app.web_auth import InvalidSession

WEB_ORIGIN = "https://kb.example.test"
EXTENSION_ORIGIN = "chrome-extension://" + "a" * 32


class Email:
    def request_challenge(self, _email, _ip):
        return None

    def verify(self, _email, _code):
        session = SimpleNamespace(
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            tenant=SimpleNamespace(app_user_id=7),
        )
        return SimpleNamespace(raw_session_token="web-secret", session=session)

    def resolve_session(self, raw):
        if raw != "web-secret":
            raise InvalidSession()
        return SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(minutes=5))

    def revoke_session(self, _raw):
        return None


class Unused:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected service call: {name}")


def _client():
    email = Email()
    extension_sessions = ExtensionSessionService(email)
    app = create_app(
        services=WebApiServices(
            web_auth=Unused(),
            library=Unused(),
            submission=Unused(),
            transcript=Unused(),
            email_auth=email,
            extension_sessions=extension_sessions,
            settings=SimpleNamespace(
                browser_companion_allowed_origins=(EXTENSION_ORIGIN,),
                browser_companion_max_request_bytes=5_500_000,
            ),
        ),
        expected_origin=WEB_ORIGIN,
        cookie_secure=True,
        publish_budget_seconds=1.0,
    )
    return TestClient(app, base_url=WEB_ORIGIN), extension_sessions


def _token(client: TestClient) -> str:
    challenge = client.post(
        "/api/v1/extension/auth/challenge",
        json={"email": "a@example.com"},
        headers={"Origin": EXTENSION_ORIGIN},
    )
    assert challenge.status_code == 200
    verified = client.post(
        "/api/v1/extension/auth/verify",
        json={"challenge_id": challenge.json()["challenge_id"], "code": "123456"},
        headers={"Origin": EXTENSION_ORIGIN},
    )
    assert verified.status_code == 200
    return verified.json()["access_token"]


def test_session_get_without_origin_and_valid_bearer_succeeds():
    client, _sessions = _client()
    token = _token(client)

    response = client.get(
        "/api/v1/extension/auth/session",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["tenant"]["id"] == "7"
    assert "access-control-allow-origin" not in response.headers


def test_session_get_without_origin_and_invalid_bearer_is_401_not_403():
    client, _sessions = _client()

    missing = client.get("/api/v1/extension/auth/session")
    invalid = client.get(
        "/api/v1/extension/auth/session",
        headers={"Authorization": "Bearer not-a-session"},
    )

    for response in (missing, invalid):
        assert response.status_code == 401
        assert response.json()["code"] == "extension_session_invalid"
        assert "access-control-allow-origin" not in response.headers


def test_session_get_with_allowed_origin_still_emits_cors():
    client, _sessions = _client()
    token = _token(client)

    response = client.get(
        "/api/v1/extension/auth/session",
        headers={"Authorization": f"Bearer {token}", "Origin": EXTENSION_ORIGIN},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == EXTENSION_ORIGIN


def test_session_get_with_disallowed_origin_is_rejected():
    client, _sessions = _client()
    token = _token(client)

    for origin in (
        "https://evil.example",
        WEB_ORIGIN,
        "null",
        "chrome-extension://" + "b" * 32,
    ):
        response = client.get(
            "/api/v1/extension/auth/session",
            headers={"Authorization": f"Bearer {token}", "Origin": origin},
        )
        assert response.status_code == 403, origin
        assert response.json()["code"] == "extension_origin_invalid"
        assert "access-control-allow-origin" not in response.headers


def test_unsafe_extension_requests_without_origin_are_rejected():
    client, _sessions = _client()
    token = _token(client)

    challenge = client.post(
        "/api/v1/extension/auth/challenge", json={"email": "a@example.com"}
    )
    logout = client.post(
        "/api/v1/extension/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    options = client.options("/api/v1/extension/auth/session")

    for response in (challenge, logout, options):
        assert response.status_code == 403
        assert response.json()["code"] == "extension_origin_invalid"

    # The rejected logout did not revoke the session.
    assert (
        client.get(
            "/api/v1/extension/auth/session",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 200
    )


def test_unsafe_extension_request_with_allowed_origin_behaves_normally():
    client, _sessions = _client()

    response = client.post(
        "/api/v1/extension/auth/challenge",
        json={"email": "a@example.com"},
        headers={"Origin": EXTENSION_ORIGIN},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
