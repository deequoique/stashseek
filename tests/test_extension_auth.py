from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.extension_routes import ExtensionSessionService, build_extension_router
from app.web_auth import InvalidSession


class Email:
    def __init__(self):
        self.sent = []
        self.revoked = []

    def request_challenge(self, email, _ip):
        self.sent.append(email)

    def verify(self, email, _code):
        now = datetime.now(UTC) + timedelta(minutes=5)
        session = SimpleNamespace(
            expires_at=now,
            tenant=SimpleNamespace(app_user_id=7),
        )
        return SimpleNamespace(raw_session_token="web-secret", session=session)

    def resolve_session(self, raw):
        if raw != "web-secret":
            raise InvalidSession()
        return SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(minutes=5))

    def revoke_session(self, raw):
        self.revoked.append(raw)


def make_client():
    email = Email()
    service = ExtensionSessionService(email)
    app = FastAPI()
    app.include_router(build_extension_router(service))
    return TestClient(app), email


def test_extension_challenge_verify_session_and_logout():
    client, email = make_client()
    challenge = client.post("/api/v1/extension/auth/challenge", json={"email": "a@example.com"})
    assert challenge.status_code == 200
    verified = client.post("/api/v1/extension/auth/verify", json={"challenge_id": challenge.json()["challenge_id"], "code": "123456"})
    assert verified.status_code == 200
    token = verified.json()["access_token"]
    current = client.get("/api/v1/extension/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert current.status_code == 200 and current.json()["tenant"]["id"] == "7"
    assert client.post("/api/v1/extension/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert client.get("/api/v1/extension/auth/session", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert email.revoked == ["web-secret"]


def test_extension_challenge_is_single_use():
    client, _ = make_client()
    challenge = client.post("/api/v1/extension/auth/challenge", json={"email": "a@example.com"}).json()["challenge_id"]
    assert client.post("/api/v1/extension/auth/verify", json={"challenge_id": challenge, "code": "123456"}).status_code == 200
    assert client.post("/api/v1/extension/auth/verify", json={"challenge_id": challenge, "code": "123456"}).status_code == 401
