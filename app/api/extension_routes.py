"""Authenticated API transport owned by the browser extension.

Extension credentials are deliberately separate from cookies and capture grants.
The in-process index is keyed by token hashes; its values reference the
authoritative email session so normal tenant/session revocation still applies.
It is bounded and expiry-pruned, but is not shared across worker processes.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from app.web_auth import EmailDeliveryUnavailable


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChallengeRequest(_Model):
    email: str = Field(min_length=3, max_length=320)


class ChallengeResponse(_Model):
    challenge_id: str
    expires_at: datetime


class VerifyRequest(_Model):
    challenge_id: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=6, max_length=6)


class ExtensionSessionResponse(_Model):
    access_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime
    user: dict[str, str]
    tenant: dict[str, str]


Bearer = Annotated[HTTPAuthorizationCredentials | None, Security(HTTPBearer(auto_error=False))]


class ExtensionSessionService:
    def __init__(
        self, email_auth, *, challenge_ttl_seconds: int = 600, max_entries: int = 10000
    ):
        self.email_auth = email_auth
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self.max_entries = max(100, max_entries)
        self._challenges: dict[str, tuple[str, datetime]] = {}
        self._tokens: dict[str, tuple[str, datetime, int, str]] = {}

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def challenge(self, email: str, client_ip: str) -> ChallengeResponse:
        self._prune()
        self.email_auth.request_challenge(email, client_ip)
        challenge_id = secrets.token_urlsafe(32)
        expires = datetime.now(UTC).replace(microsecond=0)
        from datetime import timedelta
        expires += timedelta(seconds=self.challenge_ttl_seconds)
        self._challenges[self._digest(challenge_id)] = (email, expires)
        if len(self._challenges) > self.max_entries:
            self._prune(force=True)
        return ChallengeResponse(challenge_id=challenge_id, expires_at=expires)

    def verify(self, challenge_id: str, code: str) -> ExtensionSessionResponse:
        self._prune()
        key = self._digest(challenge_id)
        entry = self._challenges.pop(key, None)
        if entry is None or entry[1] <= datetime.now(UTC):
            raise HTTPException(status_code=401, detail="extension_session_invalid")
        email, _ = entry
        verified = self.email_auth.verify(email, code)
        session = verified.session
        token = secrets.token_urlsafe(48)
        self._tokens[self._digest(token)] = (
            verified.raw_session_token,
            session.expires_at,
            session.tenant.app_user_id,
            email,
        )
        if len(self._tokens) > self.max_entries:
            self._prune(force=True)
        return self._response(token, session.expires_at, session.tenant.app_user_id, email)

    def resolve(self, token: str):
        self._prune()
        entry = self._tokens.get(self._digest(token))
        if entry is None or entry[1] <= datetime.now(UTC):
            raise HTTPException(status_code=401, detail="extension_session_invalid")
        raw, _, app_user_id, email = entry
        try:
            session = self.email_auth.resolve_session(raw)
        except Exception:
            self._tokens.pop(self._digest(token), None)
            raise HTTPException(status_code=401, detail="extension_session_invalid") from None
        return session, app_user_id, email

    def revoke(self, token: str) -> None:
        entry = self._tokens.pop(self._digest(token), None)
        if entry:
            try:
                self.email_auth.revoke_session(entry[0])
            except Exception:
                pass

    def _prune(self, *, force: bool = False) -> None:
        now = datetime.now(UTC)
        self._challenges = {k: v for k, v in self._challenges.items() if v[1] > now}
        self._tokens = {k: v for k, v in self._tokens.items() if v[1] > now}
        if force and len(self._challenges) > self.max_entries:
            self._challenges = dict(list(self._challenges.items())[-self.max_entries:])
        if force and len(self._tokens) > self.max_entries:
            self._tokens = dict(list(self._tokens.items())[-self.max_entries:])

    @staticmethod
    def _response(token, expires, app_user_id, email):
        return ExtensionSessionResponse(
            access_token=token,
            expires_at=expires,
            user={"id": str(app_user_id), "email": email},
            tenant={"id": str(app_user_id)},
        )


def build_extension_router(service: ExtensionSessionService, *, allowed_origins=()):
    router = APIRouter(prefix="/api/v1/extension", tags=["extension"])

    @router.post("/auth/challenge", response_model=ChallengeResponse)
    def challenge(payload: ChallengeRequest, request: Request):
        try:
            return service.challenge(payload.email, request.client.host if request.client else "unknown")
        except EmailDeliveryUnavailable:
            raise HTTPException(status_code=503, detail="request_failed") from None
        except Exception as exc:
            raise HTTPException(status_code=400, detail="extension_session_invalid") from exc

    @router.post("/auth/verify", response_model=ExtensionSessionResponse)
    def verify(payload: VerifyRequest):
        return service.verify(payload.challenge_id, payload.code)

    @router.get("/auth/session", response_model=ExtensionSessionResponse)
    def session(credentials: Bearer):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="extension_session_invalid")
        current, user_id, email = service.resolve(credentials.credentials)
        return service._response(None, current.expires_at, user_id, email)

    @router.post("/auth/logout", status_code=204)
    def logout(credentials: Bearer):
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="extension_session_invalid")
        service.revoke(credentials.credentials)

    return router
