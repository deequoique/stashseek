"""Canonical same-origin email-code authentication routes.

This router is mounted by :func:`app.api.app.create_app`, the one FastAPI
composition used by the browser in production.  It deliberately returns only
the browser contract: challenge acceptance, an identifier-free session
projection, and the stable ``{code, message}`` error envelope.  Raw email
codes, session tokens, provider responses, and internal tenant identifiers
never cross this boundary.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Header, Request, Response, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie

from app.api.auth_schemas import (
    AcceptedResponse,
    AuthErrorResponse,
    EmailChallengeRequest,
    EmailVerifyRequest,
    SessionResponse,
)
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, WebAuthError
from app.web_auth import (
    EmailDeliveryUnavailable,
    InvalidEmail,
    InvalidSession,
    InvalidVerification,
    LoginRateLimited,
)


class EmailAuthBoundary(Protocol):
    def request_challenge(self, email: str, client_ip: str) -> None: ...

    def verify(self, email: str, code: str): ...

    def resolve_session(self, raw_token: str): ...

    def validate_csrf(self, raw_token: str, raw_csrf_token: str) -> None: ...

    def validate_csrf_for_session(self, session, raw_csrf_token: str) -> None: ...

    def revoke_session(self, raw_token: str) -> None: ...


@dataclass(frozen=True)
class EmailResolvedSession:
    """Internal adapter shape used by authenticated library dependencies."""

    app_user_id: int
    session_public_id: str
    login_channel: str
    expires_at: datetime


class EmailWebAuthAdapter:
    """Present the email service through the library's auth boundary."""

    def __init__(self, service: EmailAuthBoundary) -> None:
        self._service = service

    def request_challenge(self, email: str, client_ip: str) -> None:
        self._service.request_challenge(email, client_ip)

    def verify(self, email: str, code: str):
        return self._service.verify(email, code)

    def resolve_session(self, raw_token: str) -> EmailResolvedSession:
        try:
            session = self._service.resolve_session(raw_token)
        except InvalidSession:
            raise WebAuthError("session_invalid") from None
        return EmailResolvedSession(
            app_user_id=session.tenant.app_user_id,
            session_public_id=session.public_id,
            login_channel="email",
            expires_at=session.expires_at,
        )

    def validate_csrf(self, raw_token: str, raw_csrf_token: str) -> None:
        try:
            self._service.validate_csrf(raw_token, raw_csrf_token)
        except InvalidSession:
            raise WebAuthError("csrf_invalid") from None

    def validate_csrf_for_session(self, session, raw_csrf_token: str) -> None:
        try:
            self._service.validate_csrf_for_session(session, raw_csrf_token)
        except InvalidSession:
            raise WebAuthError("csrf_invalid") from None

    def revoke_session(self, raw_token: str) -> None:
        self._service.revoke_session(raw_token)


_SESSION_COOKIE_SCHEMA = APIKeyCookie(
    name=SESSION_COOKIE_NAME,
    scheme_name="SessionCookie",
    auto_error=False,
)

CsrfHeader = Annotated[str | None, Header(alias="X-CSRF-Token", max_length=200)]

# Keep public messages fixed and deliberately independent of provider/error
# details.  ``app.api.app`` uses the same wording for non-auth routes.
_MESSAGES = {
    "origin_forbidden": "请求来源无效",
    "invalid_email": "邮箱地址无效",
    "email_delivery_unavailable": "登录服务暂时不可用，请稍后重试",
    "verification_failed": "验证码无效或已过期",
    "session_invalid": "登录已失效，请重新登录",
    "csrf_invalid": "请求验证失败，请刷新后重试",
    "validation_error": "请求参数无效",
    "request_failed": "请求无法完成",
}


def build_email_auth_router(
    email_auth: EmailAuthBoundary,
    *,
    expected_origin: str,
    cookie_secure: bool,
    trusted_proxy_hosts: str = "",
) -> APIRouter:
    """Build the canonical email login/session routes.

    Auth POSTs require an exact Origin and JSON payload (FastAPI's strict
    models reject unknown fields).  The challenge endpoint intentionally maps
    a rate-limited request to the same accepted response as a normal request;
    account existence and limiter state are never exposed to the browser.
    """

    origin = str(expected_origin).strip()
    if not origin or origin.endswith("/"):
        raise ValueError("expected_origin must be an exact origin without a slash")
    router = APIRouter(
        prefix="/api/v1/auth",
        tags=["authentication"],
    )
    trusted_proxies = {
        value.strip()
        for value in trusted_proxy_hosts.split(",")
        if value.strip()
    }

    def same_origin(request: Request) -> JSONResponse | None:
        if request.headers.get("origin") != origin:
            return _error("origin_forbidden", 403)
        return None

    def client_ip(request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if peer in trusted_proxies:
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0]
            if forwarded.strip():
                return forwarded.strip()[:128]
        return peer[:128]

    @router.post(
        "/challenges",
        response_model=AcceptedResponse,
        status_code=200,
        responses={
            200: {"model": AcceptedResponse},
            403: {"model": AuthErrorResponse},
            422: {"model": AuthErrorResponse},
            503: {"model": AuthErrorResponse},
            500: {"model": AuthErrorResponse},
        },
    )
    def request_challenge(
        payload: EmailChallengeRequest, request: Request
    ) -> Response:
        if error := same_origin(request):
            return error
        try:
            email_auth.request_challenge(payload.email, client_ip(request))
        except InvalidEmail:
            return _error("invalid_email", 422)
        except LoginRateLimited:
            # Deliberately indistinguishable from a normal accepted request.
            pass
        except EmailDeliveryUnavailable:
            return _error("email_delivery_unavailable", 503)
        except Exception:
            # Provider and database exception details are not browser data.
            return _error("request_failed", 500)
        return JSONResponse(
            AcceptedResponse().model_dump(mode="json"),
            status_code=200,
        )

    @router.post(
        "/verify",
        response_model=SessionResponse,
        status_code=200,
        responses={
            200: {"model": SessionResponse},
            401: {"model": AuthErrorResponse},
            403: {"model": AuthErrorResponse},
            422: {"model": AuthErrorResponse},
            500: {"model": AuthErrorResponse},
        },
    )
    def verify(payload: EmailVerifyRequest, request: Request) -> Response:
        if error := same_origin(request):
            return error
        try:
            verified = email_auth.verify(payload.email, payload.code)
        except (InvalidEmail, InvalidVerification):
            return _error("verification_failed", 401)
        except Exception:
            return _error("request_failed", 500)
        response = JSONResponse(
            SessionResponse(
                authenticated=True,
                login_channel="email",
                expires_at=verified.session.expires_at,
            ).model_dump(mode="json"),
            status_code=200,
        )
        _set_session_cookies(response, verified, secure=cookie_secure)
        return response

    @router.get(
        "/session",
        response_model=SessionResponse,
        responses={
            401: {"model": AuthErrorResponse},
            500: {"model": AuthErrorResponse},
        },
    )
    def current_session(
        request: Request,
        _session_cookie: str | None = Security(_SESSION_COOKIE_SCHEMA),
    ) -> Response:
        raw = request.cookies.get(SESSION_COOKIE_NAME, "")
        try:
            session = email_auth.resolve_session(raw)
        except (InvalidSession, WebAuthError):
            return _error("session_invalid", 401)
        except Exception:
            return _error("request_failed", 500)
        return JSONResponse(
            SessionResponse(
                authenticated=True,
                login_channel="email",
                expires_at=session.expires_at,
            ).model_dump(mode="json"),
            status_code=200,
        )

    @router.delete(
        "/session",
        response_model=None,
        status_code=204,
        responses={
            204: {"description": "Session revoked"},
            401: {"model": AuthErrorResponse},
            403: {"model": AuthErrorResponse},
            500: {"model": AuthErrorResponse},
        },
    )
    def logout(
        request: Request,
        _csrf: CsrfHeader = None,
        _session_cookie: str | None = Security(_SESSION_COOKIE_SCHEMA),
    ) -> Response:
        if error := same_origin(request):
            return error
        raw_session = request.cookies.get(SESSION_COOKIE_NAME, "")
        cookie_csrf = request.cookies.get(CSRF_COOKIE_NAME, "")
        header_csrf = request.headers.get("x-csrf-token", "")
        if not raw_session or not cookie_csrf or not header_csrf or not hmac.compare_digest(
            cookie_csrf, header_csrf
        ):
            return _error("csrf_invalid", 403)
        try:
            resolved = email_auth.resolve_session(raw_session)
            validate_for_session = getattr(email_auth, "validate_csrf_for_session", None)
            if callable(validate_for_session):
                validate_for_session(resolved, header_csrf)
            else:
                email_auth.validate_csrf(raw_session, header_csrf)
            email_auth.revoke_session(raw_session)
        except (InvalidSession, WebAuthError):
            return _error("session_invalid", 401)
        except Exception:
            return _error("request_failed", 500)
        response = Response(status_code=204)
        _delete_session_cookies(response, secure=cookie_secure)
        return response

    return router


def _set_session_cookies(response: Response, verified, *, secure: bool) -> None:
    expires = verified.session.expires_at
    response.set_cookie(
        SESSION_COOKIE_NAME,
        verified.raw_session_token,
        expires=expires,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        verified.raw_csrf_token,
        expires=expires,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )


def _delete_session_cookies(response: Response, *, secure: bool) -> None:
    for name, httponly in ((SESSION_COOKIE_NAME, True), (CSRF_COOKIE_NAME, False)):
        response.delete_cookie(
            name,
            path="/",
            secure=secure,
            httponly=httponly,
            samesite="lax",
        )


def _error(code: str, status_code: int) -> JSONResponse:
    safe_code = code if code in _MESSAGES else "request_failed"
    return JSONResponse(
        {"code": safe_code, "message": _MESSAGES[safe_code]},
        status_code=status_code,
    )
