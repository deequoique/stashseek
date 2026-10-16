"""Same-origin navigation and browser-only continuation for external login."""

from __future__ import annotations

import hmac
import re
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.auth_schemas import (
    AcceptedResponse,
    AuthErrorResponse,
    EmailChallengeRequest,
    ExternalLoginPendingResponse,
    ExternalLoginVerifyRequest,
    SessionResponse,
)
from app.api.email_auth_routes import _set_session_cookies, public_login_channel
from app.web.auth import SESSION_COOKIE_NAME
from app.web_auth import (
    EmailDeliveryUnavailable,
    InvalidEmail,
    InvalidVerification,
    LoginRateLimited,
    WebAuthError,
)


EXTERNAL_FLOW_COOKIE_NAME = "__Host-kb_external_flow"
EXTERNAL_CSRF_COOKIE_NAME = "__Host-kb_external_csrf"
_PROVIDERS = frozenset({"microsoft", "wechat_website", "qq"})
_CALLBACK_PATH = re.compile(r"^/api/v1/auth/external/[^/]+/callback$")
_CALLBACK_QUERY_KEY = "stashseek.external_callback_query"
_CONTINUATION_PATH = "/login/external"
_AUTHORIZATION_ENDPOINTS = {
    "microsoft": ("login.microsoftonline.com", None),
    "wechat_website": ("open.weixin.qq.com", "/connect/qrconnect"),
    "qq": ("graph.qq.com", "/oauth2.0/authorize"),
}


def authorization_form_origins(providers: tuple[str, ...]) -> tuple[str, ...]:
    """Allow native authorization redirects only to configured fixed origins."""

    return tuple(
        f"https://{_AUTHORIZATION_ENDPOINTS[provider][0]}"
        for provider in ("microsoft", "wechat_website", "qq")
        if provider in providers
    )


_MESSAGES = {
    "origin_forbidden": "请求来源无效",
    "flow_invalid": "登录流程已失效，请重新开始",
    "flow_expired": "登录流程已过期，请重新开始",
    "flow_used": "登录流程已结束，请重新开始",
    "flow_csrf_invalid": "请求验证失败，请刷新后重试",
    "provider_unavailable": "该登录方式暂不可用，请使用邮箱登录",
    "provider_failed": "授权未能完成，请重新开始或使用邮箱登录",
    "provider_denied": "授权未完成，请重新开始或使用邮箱登录",
    "session_present": "当前已有登录，请先退出后再切换登录方式",
    "session_changed": "登录状态已变化，请确认当前账户后重新开始",
    "binding_conflict": "此登录方式无法绑定，请使用原登录方式后重试",
    "verification_failed": "验证码无效或已过期",
    "invalid_email": "邮箱地址无效",
    "login_rate_limited": "请求过多，请稍后重试",
    "email_delivery_unavailable": "登录服务暂时不可用，请稍后重试",
    "request_failed": "请求无法完成",
}


class ExternalAuthBoundary(Protocol):
    available_providers: tuple[str, ...]

    def start(self, provider: str, **kwargs): ...

    def failure_notice(self, code: str, **kwargs): ...

    def callback(self, provider: str, **kwargs): ...

    def pending(self, raw_flow_token: str, **kwargs): ...

    def validate_flow_csrf(self, raw_flow_token: str, raw_csrf: str): ...

    def request_email_challenge(self, raw_flow_token: str, email: str, client_ip: str): ...

    def verify_email(self, raw_flow_token: str, email: str, code: str, **kwargs): ...

    def cancel(self, raw_flow_token: str): ...


class ExternalCallbackQueryRedactionMiddleware:
    """Remove callback credentials from the shared ASGI/access-log URL scope.

    The protocol router alone reads the bounded private copy. Keeping the
    canonical scope clean also prevents exception/request URL diagnostics from
    retaining authorization codes or provider descriptions.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _CALLBACK_PATH.fullmatch(scope.get("path", "")):
            raw_query = scope.get("query_string", b"")
            scope[_CALLBACK_QUERY_KEY] = raw_query if len(raw_query) <= 16_384 else None
            scope["query_string"] = b""
        await self.app(scope, receive, send)


def build_external_auth_router(
    external_auth: ExternalAuthBoundary,
    *,
    expected_origin: str,
    cookie_secure: bool,
    trusted_proxy_hosts: str = "",
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth/external", tags=["authentication"])
    trusted_proxies = {host.strip() for host in trusted_proxy_hosts.split(",") if host.strip()}

    def same_origin(request: Request) -> JSONResponse | None:
        if request.headers.getlist("origin") != [expected_origin]:
            return _error("origin_forbidden", 403)
        return None

    def client_ip(request: Request) -> str:
        peer = request.client.host if request.client else "unknown"
        if peer in trusted_proxies:
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded[:128]
        return peer[:128]

    def failure_navigation(request: Request, code: str, provider: str) -> Response:
        response = _redirect(_CONTINUATION_PATH)
        try:
            notice = external_auth.failure_notice(
                code if code in _MESSAGES else "request_failed",
                provider=provider if provider in _PROVIDERS else "microsoft",
                previous_flow_token=request.cookies.get(EXTERNAL_FLOW_COOKIE_NAME, ""),
                client_ip=client_ip(request),
            )
            _set_flow_cookies(response, notice, secure=cookie_secure)
        except Exception:
            # A failed database/provider must still leave a clean app URL.
            _delete_flow_cookies(response, secure=cookie_secure)
        return response

    def flow_csrf(request: Request) -> JSONResponse | None:
        if error := same_origin(request):
            return error
        raw_flow = request.cookies.get(EXTERNAL_FLOW_COOKIE_NAME, "")
        cookie_csrf = request.cookies.get(EXTERNAL_CSRF_COOKIE_NAME, "")
        header_csrf = request.headers.get("x-csrf-token", "")
        if (
            not raw_flow or not cookie_csrf or not header_csrf
            or max(len(raw_flow), len(cookie_csrf), len(header_csrf)) > 200
            or not all(value.isascii() for value in (raw_flow, cookie_csrf, header_csrf))
            or not hmac.compare_digest(cookie_csrf, header_csrf)
        ):
            return _error("flow_csrf_invalid", 403)
        try:
            external_auth.validate_flow_csrf(raw_flow, header_csrf)
        except WebAuthError as exc:
            return _service_error(exc)
        except Exception:
            return _error("request_failed", 500)
        return None

    @router.post(
        "/{provider}/start", response_model=None, status_code=303,
        responses={303: {"description": "Navigate to authorization or clean continuation"}, 403: {"model": AuthErrorResponse}},
    )
    def start(provider: str, request: Request) -> Response:
        if error := same_origin(request):
            return error
        if provider not in _PROVIDERS:
            return failure_navigation(request, "provider_unavailable", provider)
        try:
            started = external_auth.start(
                provider,
                client_ip=client_ip(request),
                previous_flow_token=request.cookies.get(EXTERNAL_FLOW_COOKIE_NAME, ""),
                session_token=request.cookies.get(SESSION_COOKIE_NAME, ""),
            )
            if not _authorization_url_allowed(provider, started.authorization_url):
                return failure_navigation(request, "provider_failed", provider)
            response = _redirect(started.authorization_url)
            _set_flow_cookies(response, started, secure=cookie_secure)
            return response
        except WebAuthError as exc:
            return failure_navigation(request, exc.error_code, provider)
        except Exception:
            return failure_navigation(request, "request_failed", provider)

    @router.get(
        "/{provider}/callback", response_model=None, status_code=303,
        responses={303: {"description": "Leave protocol parameters at a fixed clean URL"}},
    )
    def callback(provider: str, request: Request) -> Response:
        try:
            parameters = _callback_parameters(request)
            if provider not in _PROVIDERS or parameters is None:
                return failure_navigation(request, "flow_invalid", provider)
            result = external_auth.callback(
                provider,
                raw_flow_token=request.cookies.get(EXTERNAL_FLOW_COOKIE_NAME, ""),
                state=parameters.get("state", ""),
                code=parameters.get("code", ""),
                error=parameters.get("error", ""),
                session_token=request.cookies.get(SESSION_COOKIE_NAME, ""),
            )
            response = _redirect("/library" if result.login is not None else _CONTINUATION_PATH)
            if result.login is not None:
                _set_session_cookies(response, result.login, secure=cookie_secure)
            return response
        except WebAuthError:
            # The service persists a safe failure on an existing bound flow.
            return _redirect(_CONTINUATION_PATH)
        except Exception:
            return failure_navigation(request, "request_failed", provider)

    @router.get("/pending", response_model=ExternalLoginPendingResponse, responses={500: {"model": AuthErrorResponse}})
    def pending(request: Request) -> Response:
        try:
            state = external_auth.pending(
                request.cookies.get(EXTERNAL_FLOW_COOKIE_NAME, ""),
                session_token=request.cookies.get(SESSION_COOKIE_NAME, ""),
            )
            payload = ExternalLoginPendingResponse(
                status=state.status, provider=state.provider, expires_at=state.expires_at,
                error_code=getattr(state, "error_code", None),
            )
        except WebAuthError as exc:
            if exc.error_code not in {"flow_invalid", "flow_expired", "flow_used"}:
                return _service_error(exc)
            payload = ExternalLoginPendingResponse(status="expired", error_code="flow_invalid")
        except Exception:
            return _error("request_failed", 500)
        return JSONResponse(payload.model_dump(mode="json"))

    @router.post("/pending/challenges", response_model=AcceptedResponse, responses={403: {"model": AuthErrorResponse}, 422: {"model": AuthErrorResponse}, 503: {"model": AuthErrorResponse}})
    def request_challenge(payload: EmailChallengeRequest, request: Request) -> Response:
        if error := flow_csrf(request):
            return error
        try:
            external_auth.request_email_challenge(
                request.cookies.get(EXTERNAL_FLOW_COOKIE_NAME, ""), payload.email, client_ip(request),
            )
        except LoginRateLimited:
            pass
        except WebAuthError as exc:
            return _service_error(exc)
        except Exception:
            return _error("request_failed", 500)
        return JSONResponse(AcceptedResponse().model_dump(mode="json"))

    @router.post("/pending/verify", response_model=SessionResponse, responses={401: {"model": AuthErrorResponse}, 403: {"model": AuthErrorResponse}, 409: {"model": AuthErrorResponse}})
    def verify(payload: ExternalLoginVerifyRequest, request: Request) -> Response:
        if error := flow_csrf(request):
            return error
        try:
            verified = external_auth.verify_email(
                request.cookies.get(EXTERNAL_FLOW_COOKIE_NAME, ""), payload.email, payload.code,
                confirm_binding=payload.confirm_binding,
                session_token=request.cookies.get(SESSION_COOKIE_NAME, ""),
            )
            response = JSONResponse(SessionResponse(
                login_channel=public_login_channel(verified.session),
                expires_at=verified.session.expires_at,
            ).model_dump(mode="json"))
            _set_session_cookies(response, verified, secure=cookie_secure)
            return response
        except WebAuthError as exc:
            return _service_error(exc)
        except Exception:
            return _error("request_failed", 500)

    @router.delete("/pending", response_model=None, status_code=204, responses={403: {"model": AuthErrorResponse}})
    def cancel(request: Request) -> Response:
        if error := flow_csrf(request):
            return error
        try:
            external_auth.cancel(request.cookies.get(EXTERNAL_FLOW_COOKIE_NAME, ""))
        except WebAuthError as exc:
            return _service_error(exc)
        except Exception:
            return _error("request_failed", 500)
        response = Response(status_code=204)
        _delete_flow_cookies(response, secure=cookie_secure)
        return response

    return router


def _callback_parameters(request: Request) -> dict[str, str] | None:
    raw = request.scope.get(_CALLBACK_QUERY_KEY, request.scope.get("query_string", b""))
    if raw is None or len(raw) > 16_384:
        return None
    try:
        pairs = parse_qsl(raw.decode("utf-8", errors="strict"), keep_blank_values=True, max_num_fields=8, errors="strict")
    except (UnicodeError, ValueError):
        return None
    limits = {"code": 8192, "state": 512, "error": 128, "error_description": 2048, "error_uri": 2048, "session_state": 512}
    values: dict[str, str] = {}
    for key, value in pairs:
        if key not in limits or key in values or len(value) > limits[key] or any(ord(c) < 32 for c in value):
            return None
        values[key] = value
    if not values.get("state") or bool(values.get("code")) == bool(values.get("error")):
        return None
    return values


def _authorization_url_allowed(provider: str, value: str) -> bool:
    parsed = urlsplit(value)
    host, path = _AUTHORIZATION_ENDPOINTS[provider]
    if parsed.scheme != "https" or parsed.hostname != host or parsed.username or parsed.password or parsed.port not in {None, 443}:
        return False
    if path is not None:
        return parsed.path == path
    return re.fullmatch(r"/[A-Za-z0-9.-]+/oauth2/v2\.0/authorize", parsed.path) is not None


def _set_flow_cookies(response: Response, started, *, secure: bool) -> None:
    for name, value, httponly in (
        (EXTERNAL_FLOW_COOKIE_NAME, started.raw_flow_token, True),
        (EXTERNAL_CSRF_COOKIE_NAME, started.raw_csrf_token, False),
    ):
        response.set_cookie(name, value, expires=started.expires_at, path="/", secure=secure, httponly=httponly, samesite="lax")


def _delete_flow_cookies(response: Response, *, secure: bool) -> None:
    for name, httponly in ((EXTERNAL_FLOW_COOKIE_NAME, True), (EXTERNAL_CSRF_COOKIE_NAME, False)):
        response.delete_cookie(name, path="/", secure=secure, httponly=httponly, samesite="lax")


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _service_error(error: WebAuthError) -> JSONResponse:
    code = error.error_code
    if isinstance(error, InvalidVerification):
        code = "verification_failed"
    status = 400
    if code in {"flow_invalid", "flow_expired", "flow_used", "flow_csrf_invalid"}:
        status = 403
    elif code == "verification_failed":
        status = 401
    elif code in {"session_present", "session_changed", "binding_conflict"}:
        status = 409
    elif isinstance(error, InvalidEmail):
        status = 422
    elif isinstance(error, EmailDeliveryUnavailable) or code in {"provider_unavailable", "provider_failed"}:
        status = 503
    elif code not in _MESSAGES:
        status = 500
    return _error(code, status)


def _error(code: str, status: int) -> JSONResponse:
    safe_code = code if code in _MESSAGES else "request_failed"
    return JSONResponse({"code": safe_code, "message": _MESSAGES[safe_code]}, status_code=status)
