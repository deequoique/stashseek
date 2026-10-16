"""Credential-free HTTP boundary and configuration acceptance for external login."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.app import WebApiServices, create_app
from app.api.email_auth_routes import EmailWebAuthAdapter
from app.api.runtime import build_web_app
from app.api.external_auth_routes import (
    EXTERNAL_CSRF_COOKIE_NAME,
    EXTERNAL_FLOW_COOKIE_NAME,
    ExternalCallbackQueryRedactionMiddleware,
)
from app.config import Settings
from app.external_auth import ExternalLoginService
from app.external_providers.base import ExternalPrincipal, ProviderAuthorization
from app.models import AppUser, ChannelIdentity, ExternalLoginFlow, ExternalLoginIdentity, WebAuthChallenge, WebSession
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.web_auth import InMemoryEmailSender, LoginRateLimited, WebAuthService


ORIGIN = "https://app.example.test"
PREFIX = "/api/v1/auth/external"


class FakeMicrosoft:
    name = "microsoft"
    application_id = "test-app"
    configuration_id = "test-app-secret-v1"

    def __init__(self):
        self.calls = 0
        self.authorization_host = "login.microsoftonline.com"
        self.on_complete = None

    def begin(self, *, redirect_uri, state):
        return ProviderAuthorization(
            f"https://{self.authorization_host}/common/oauth2/v2.0/authorize?"
            + urlencode({"state": state, "redirect_uri": redirect_uri}),
            {"private_proof": "server-only"},
        )

    def complete(self, *, redirect_uri, code, material):
        self.calls += 1
        assert redirect_uri == ORIGIN + PREFIX + "/microsoft/callback"
        assert material == {"private_proof": "server-only"}
        if self.on_complete is not None:
            self.on_complete()
        return ExternalPrincipal("https://login.microsoftonline.com/tenant/v2.0", "private-subject")


class AllowAll:
    def allow(self, *_args, **_kwargs):
        return True


@pytest.fixture
def runtime():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (AppUser, ChannelIdentity, ExternalLoginIdentity, ExternalLoginFlow, WebAuthChallenge, WebSession):
        model.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url="sqlite://", notebook_agent_env="development", web_auth_enabled=True,
                        web_public_origin=ORIGIN, web_auth_secret="route-test-auth-secret-" * 3)
    sender = InMemoryEmailSender()
    email = WebAuthService(factory, settings, sender, AllowAll())
    provider = FakeMicrosoft()
    external = ExternalLoginService(email, settings, {"microsoft": provider})
    unused = object()
    app = create_app(services=WebApiServices(
        web_auth=EmailWebAuthAdapter(email), email_auth=email, external_auth=external,
        library=unused, submission=unused, transcript=unused,
        session_resolver=email.resolve_session, session_factory=factory,
        settings=settings, include_conversation_routes=False,
    ), expected_origin=ORIGIN, cookie_secure=True, web_login_channels=("email",))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        yield SimpleNamespace(app=app, client=client, settings=settings, sender=sender,
                              email=email, provider=provider, external=external, factory=factory)
    engine.dispose()


def start(rt):
    response = rt.client.post(PREFIX + "/microsoft/start", headers={"Origin": ORIGIN})
    assert response.status_code == 303
    return response, parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def authorize(rt):
    response, state = start(rt)
    callback = rt.client.get(PREFIX + "/microsoft/callback", params={"state": state, "code": "private-code"})
    assert callback.status_code == 303
    return response, callback


def flow_headers(rt):
    return {"Origin": ORIGIN, "X-CSRF-Token": rt.client.cookies[EXTERNAL_CSRF_COOKIE_NAME]}


def finish(rt, email="Person@Example.test"):
    accepted = rt.client.post(PREFIX + "/pending/challenges", json={"email": email}, headers=flow_headers(rt))
    assert accepted.json() == {"status": "accepted"}
    return rt.client.post(PREFIX + "/pending/verify", json={
        "email": email, "code": rt.sender.sent[-1].code, "confirm_binding": True,
    }, headers=flow_headers(rt))


def test_native_first_login_binds_email_and_returns_truthful_session(runtime):
    rt = runtime
    beginning, callback = authorize(rt)
    assert callback.headers["location"] == "/login/external"
    for cookie in beginning.headers.get_list("set-cookie"):
        assert "Secure" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
        assert "Domain=" not in cookie
    assert "HttpOnly" in beginning.headers.get_list("set-cookie")[0]
    pending = rt.client.get(PREFIX + "/pending")
    assert pending.json()["status"] == "email_required"
    assert set(pending.json()) == {"status", "provider", "expires_at", "error_code"}
    assert "private" not in pending.text
    verified = finish(rt)
    assert verified.status_code == 200
    assert verified.json()["login_channel"] == "microsoft"
    assert set(verified.json()) == {"authenticated", "login_channel", "expires_at"}
    assert rt.client.get("/api/v1/auth/session").json() == verified.json()
    assert rt.client.get(PREFIX + "/pending").json()["status"] == "completed"
    assert rt.client.get("/api/v1/capabilities").json()["external_login_providers"] == ["microsoft"]
    assert pending.headers["cache-control"] == "no-store"
    assert pending.headers["referrer-policy"] == "no-referrer"
    assert pending.headers["content-security-policy"].split("form-action ")[1] == "'self' https://login.microsoftonline.com"


def test_repeat_provider_login_returns_to_library_without_new_email(runtime):
    rt = runtime
    authorize(rt)
    assert finish(rt).status_code == 200
    rt.client.cookies.clear()
    _beginning, callback = authorize(rt)
    assert callback.headers["location"] == "/library"
    assert len(rt.sender.sent) == 1
    assert rt.client.get("/api/v1/auth/session").json()["login_channel"] == "microsoft"


def test_native_existing_session_failure_keeps_actual_session(runtime):
    rt = runtime
    rt.email.request_challenge("existing@example.test", "ip")
    login = rt.email.verify("existing@example.test", rt.sender.sent[-1].code)
    rt.client.cookies.set(SESSION_COOKIE_NAME, login.raw_session_token)
    response = rt.client.post(PREFIX + "/microsoft/start", headers={"Origin": ORIGIN})
    assert response.status_code == 303 and response.headers["location"] == "/login/external"
    assert rt.client.cookies[SESSION_COOKIE_NAME] == login.raw_session_token
    assert rt.client.get(PREFIX + "/pending").json()["error_code"] == "session_present"


def test_callback_after_another_tab_login_preserves_new_session(runtime):
    rt = runtime
    _response, state = start(rt)
    rt.email.request_challenge("other@example.test", "ip")
    login = rt.email.verify("other@example.test", rt.sender.sent[-1].code)
    rt.client.cookies.set(SESSION_COOKIE_NAME, login.raw_session_token)
    response = rt.client.get(PREFIX + "/microsoft/callback", params={"state": state, "code": "private-code"})
    assert response.status_code == 303 and response.headers["location"] == "/login/external"
    assert rt.provider.calls == 0
    assert rt.client.cookies[SESSION_COOKIE_NAME] == login.raw_session_token
    assert rt.client.get(PREFIX + "/pending").json()["error_code"] == "session_changed"


def test_completed_flow_does_not_claim_an_unrelated_authenticated_session(runtime):
    rt = runtime
    authorize(rt)
    assert finish(rt).status_code == 200
    rt.email.request_challenge("different@example.test", "ip")
    other = rt.email.verify("different@example.test", rt.sender.sent[-1].code)
    rt.client.cookies.set(SESSION_COOKIE_NAME, other.raw_session_token, domain="app.example.test", path="/")
    response = rt.client.get(PREFIX + "/pending")
    assert response.json()["status"] == "failed"
    assert response.json()["error_code"] == "session_changed"
    assert rt.client.get("/api/v1/auth/session").json()["login_channel"] == "email"


def test_verification_rechecks_new_session_and_does_not_replace_it(runtime):
    rt = runtime
    authorize(rt)
    rt.client.post(PREFIX + "/pending/challenges", json={"email": "person@example.test"}, headers=flow_headers(rt))
    binding_code = rt.sender.sent[-1].code
    rt.email.request_challenge("other@example.test", "ip")
    other = rt.email.verify("other@example.test", rt.sender.sent[-1].code)
    rt.client.cookies.set(SESSION_COOKIE_NAME, other.raw_session_token)
    response = rt.client.post(PREFIX + "/pending/verify", json={"email": "person@example.test", "code": binding_code, "confirm_binding": True}, headers=flow_headers(rt))
    assert response.status_code == 409 and response.json()["code"] == "session_changed"
    assert rt.client.cookies[SESSION_COOKIE_NAME] == other.raw_session_token


def test_email_login_during_provider_http_prevents_late_callback_session_replacement(runtime):
    rt = runtime
    authorize(rt)
    assert finish(rt).status_code == 200
    rt.client.cookies.clear()
    _beginning, state = start(rt)
    rt.email.request_challenge("other@example.test", "ip")
    email_code = rt.sender.sent[-1].code
    email_result = {}

    def another_tab_logs_in():
        response = rt.client.post("/api/v1/auth/verify", json={
            "email": "other@example.test", "code": email_code,
        }, headers={"Origin": ORIGIN})
        assert response.status_code == 200
        email_result["session_cookie"] = rt.client.cookies[SESSION_COOKIE_NAME]

    rt.provider.on_complete = another_tab_logs_in
    response = rt.client.get(PREFIX + "/microsoft/callback", params={"state": state, "code": "private-code"})
    assert response.status_code == 303 and response.headers["location"] == "/login/external"
    assert SESSION_COOKIE_NAME not in "".join(response.headers.get_list("set-cookie"))
    assert rt.client.cookies[SESSION_COOKIE_NAME] == email_result["session_cookie"]
    assert rt.client.get("/api/v1/auth/session").json()["login_channel"] == "email"
    assert rt.client.get(PREFIX + "/pending").json()["error_code"] == "session_changed"


@pytest.mark.parametrize("headers", [[], [("Origin", "https://foreign.example.test")], [("Origin", ORIGIN), ("Origin", "https://foreign.example.test")]])
def test_native_start_origin_rejection_does_not_change_cookies_or_create_flow(runtime, headers):
    rt = runtime
    response = rt.client.post(PREFIX + "/microsoft/start", headers=headers)
    assert response.status_code == 403 and response.json()["code"] == "origin_forbidden"
    assert "set-cookie" not in response.headers
    assert rt.client.get(PREFIX + "/pending").json()["status"] == "expired"


@pytest.mark.parametrize("query", [
    "code=private-code&state=wrong", "code=private-code&state=a&state=b",
    "code=private-code", "state=a&code=private-code&error=denied",
    "state=a&error=denied&error_description=private-provider-text",
    "state=a&code=" + "x" * 17_000,
], ids=["wrong-state", "duplicate-state", "missing-state", "code-and-error", "denial", "oversize"])
def test_callback_abuse_always_leaves_clean_url_without_provider_details(runtime, query):
    rt = runtime
    start(rt)
    response = rt.client.get(PREFIX + "/microsoft/callback?" + query)
    assert response.status_code == 303
    assert response.headers["location"] == "/login/external"
    assert "private" not in response.text
    assert "private" not in str(response.headers)
    assert rt.provider.calls == 0


def test_callback_replay_does_not_exchange_again(runtime):
    rt = runtime
    _response, state = start(rt)
    params = {"state": state, "code": "private-code"}
    rt.client.get(PREFIX + "/microsoft/callback", params=params)
    rt.client.get(PREFIX + "/microsoft/callback", params=params)
    assert rt.provider.calls == 1


def test_missing_flow_is_generic_and_wrong_browser_cannot_use_proof(runtime):
    rt = runtime
    assert rt.client.get(PREFIX + "/pending").json() == {
        "status": "expired", "provider": None, "expires_at": None, "error_code": "flow_invalid",
    }
    _response, state = start(rt)
    with TestClient(rt.app, base_url=ORIGIN, follow_redirects=False) as stranger:
        response = stranger.get(PREFIX + "/microsoft/callback", params={"state": state, "code": "private-code"})
        assert response.status_code == 303 and response.headers["location"] == "/login/external"
        assert SESSION_COOKIE_NAME not in stranger.cookies
    assert rt.provider.calls == 0


@pytest.mark.parametrize("kind", ["missing_origin", "foreign_origin", "missing_csrf", "session_csrf"])
def test_pending_mutations_require_exact_origin_and_separate_flow_csrf(runtime, kind):
    rt = runtime
    authorize(rt)
    headers = flow_headers(rt)
    if kind == "missing_origin":
        headers.pop("Origin")
    elif kind == "foreign_origin":
        headers["Origin"] = "https://foreign.example.test"
    elif kind == "missing_csrf":
        headers.pop("X-CSRF-Token")
    else:
        rt.client.cookies.set(CSRF_COOKIE_NAME, "ordinary-session-csrf")
        headers["X-CSRF-Token"] = "ordinary-session-csrf"
    response = rt.client.post(PREFIX + "/pending/challenges", json={"email": "person@example.test"}, headers=headers)
    assert response.status_code == 403
    assert rt.sender.sent == []


@pytest.mark.parametrize("confirmation", [None, False, 1, "true"])
def test_binding_requires_explicit_boolean_confirmation(runtime, confirmation):
    rt = runtime
    authorize(rt)
    payload = {"email": "person@example.test", "code": "123456"}
    if confirmation is not None:
        payload["confirm_binding"] = confirmation
    response = rt.client.post(PREFIX + "/pending/verify", json=payload, headers=flow_headers(rt))
    assert response.status_code == 422
    assert response.json() == {"code": "validation_error", "message": "请求参数无效"}
    assert SESSION_COOKIE_NAME not in rt.client.cookies


def test_code_failure_rate_limit_and_cancel_remain_safe(runtime, monkeypatch):
    rt = runtime
    authorize(rt)
    rt.client.post(PREFIX + "/pending/challenges", json={"email": "person@example.test"}, headers=flow_headers(rt))
    wrong = rt.client.post(PREFIX + "/pending/verify", json={"email": "person@example.test", "code": "000000", "confirm_binding": True}, headers=flow_headers(rt))
    assert wrong.status_code == 401 and wrong.json()["code"] == "verification_failed"

    def limited(*_args, **_kwargs):
        raise LoginRateLimited()
    monkeypatch.setattr(rt.external, "request_email_challenge", limited)
    limited_response = rt.client.post(PREFIX + "/pending/challenges", json={"email": "new@example.test"}, headers=flow_headers(rt))
    assert limited_response.json() == {"status": "accepted"}
    cancelled = rt.client.delete(PREFIX + "/pending", headers=flow_headers(rt))
    assert cancelled.status_code == 204
    assert EXTERNAL_FLOW_COOKIE_NAME not in rt.client.cookies
    assert EXTERNAL_CSRF_COOKIE_NAME not in rt.client.cookies


def test_unconfigured_provider_and_unsafe_provider_url_return_clean_failure(runtime):
    rt = runtime
    response = rt.client.post(PREFIX + "/qq/start", headers={"Origin": ORIGIN})
    assert response.status_code == 303 and response.headers["location"] == "/login/external"
    assert rt.client.get(PREFIX + "/pending").json()["error_code"] == "provider_unavailable"
    rt.provider.authorization_host = "evil.example.test"
    response = rt.client.post(PREFIX + "/microsoft/start", headers={"Origin": ORIGIN})
    assert response.status_code == 303 and response.headers["location"] == "/login/external"
    assert rt.client.get(PREFIX + "/pending").json()["error_code"] == "provider_failed"


@pytest.mark.asyncio
async def test_callback_redaction_cleans_shared_scope_before_downstream_diagnostics():
    observed = {}
    async def target(scope, _receive, _send):
        observed.update(scope)
    async def unused():
        return {}
    scope = {"type": "http", "path": PREFIX + "/microsoft/callback", "query_string": b"code=secret&state=secret-state"}
    await ExternalCallbackQueryRedactionMiddleware(target)(scope, unused, unused)
    assert observed["query_string"] == b""
    assert scope["query_string"] == b""


@pytest.mark.parametrize("provider,credentials", [
    ("microsoft", {"web_microsoft_client_id": "app", "web_microsoft_client_secret": "private-secret"}),
    ("wechat_website", {"web_wechat_website_app_id": "app", "web_wechat_website_app_secret": "private-secret"}),
    ("qq", {"web_qq_app_id": "app", "web_qq_app_key": "private-secret"}),
])
def test_enabled_provider_settings_require_email_origin_cookie_and_complete_credentials(provider, credentials):
    base = {"database_url": "sqlite://", "notebook_agent_env": "development", "web_auth_enabled": True,
            "web_public_origin": ORIGIN, "web_auth_secret": "x" * 32,
            f"web_{provider}_enabled": True}
    with pytest.raises(ValueError, match="requires its application ID and secret"):
        Settings(**base)
    config = Settings(**base, **credentials)
    assert "private-secret" not in repr(config)
    with pytest.raises(ValueError, match="WEB_COOKIE_SECURE"):
        Settings(**base, **credentials, web_cookie_secure=False)
    base["web_auth_enabled"] = False
    with pytest.raises(ValueError, match="WEB_AUTH_ENABLED"):
        Settings(**base, **credentials)


def test_external_settings_reject_origin_credentials_and_tenant_path_injection():
    base = dict(database_url="sqlite://", notebook_agent_env="development", web_auth_enabled=True, web_public_origin=ORIGIN,
                web_auth_secret="x" * 32, web_microsoft_enabled=True,
                web_microsoft_client_id="app", web_microsoft_client_secret="secret")
    with pytest.raises(ValueError, match="WEB_MICROSOFT_TENANT"):
        Settings(**base, web_microsoft_tenant="common/../../attacker")
    base["web_public_origin"] = "https://user:password@app.example.test"
    with pytest.raises(ValueError, match="HTTPS origin"):
        Settings(**base)


def test_runtime_calls_the_production_registry_and_mounts_external_contract(runtime, monkeypatch):
    rt = runtime
    configured = []

    def registry(settings):
        configured.append(settings)
        return {"microsoft": rt.provider}

    monkeypatch.setattr("app.external_providers.build_external_providers", registry)
    app = build_web_app(rt.settings, session_factory=rt.factory,
                        publisher=lambda _dispatch: None, object_store=object(),
                        email_auth=rt.email, mount_static=False)
    with TestClient(app, base_url=ORIGIN) as client:
        capabilities = client.get("/api/v1/capabilities").json()
    assert configured == [rt.settings]
    assert capabilities["external_login_providers"] == ["microsoft"]
    assert "/api/v1/auth/external/pending/verify" in app.openapi()["paths"]


@pytest.mark.parametrize("providers,expected", [
    ((), "'self'"),
    (("qq",), "'self' https://graph.qq.com"),
    (("wechat_website",), "'self' https://open.weixin.qq.com"),
    (("qq", "microsoft", "wechat_website"), "'self' https://login.microsoftonline.com https://open.weixin.qq.com https://graph.qq.com"),
    (("https://evil.example.test",), "'self'"),
])
def test_native_form_csp_uses_only_fixed_enabled_authorization_origins(providers, expected):
    unused = object()
    app = create_app(services=WebApiServices(
        web_auth=unused, email_auth=unused,
        external_auth=SimpleNamespace(available_providers=providers),
        library=unused, submission=unused, transcript=unused,
        include_conversation_routes=False,
    ), expected_origin=ORIGIN, web_login_channels=("email",))
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/v1/health")
    assert response.headers["content-security-policy"].split("form-action ")[1] == expected
    assert "evil.example.test" not in response.headers["content-security-policy"]


@pytest.mark.parametrize("provider,fields", [
    ("microsoft", {"CLIENT_ID": "app", "CLIENT_SECRET": "test-secret"}),
    ("wechat_website", {"APP_ID": "app", "APP_SECRET": "test-secret"}),
    ("qq", {"APP_ID": "app", "APP_KEY": "test-secret"}),
])
def test_provider_environment_names_are_canonical(provider, fields, monkeypatch):
    prefix = "WEB_" + provider.upper()
    monkeypatch.setenv(prefix + "_ENABLED", "true")
    for name, value in fields.items():
        monkeypatch.setenv(prefix + "_" + name, value)
    config = Settings(database_url="sqlite://", notebook_agent_env="development",
                      web_auth_enabled=True, web_public_origin=ORIGIN, web_auth_secret="x" * 32)
    assert getattr(config, "web_" + provider + "_enabled") is True
    for name, value in fields.items():
        assert getattr(config, "web_" + provider + "_" + name.lower()) == value
