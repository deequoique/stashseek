from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.api.app import WebApiServices, create_app
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, ResolvedWebSession
from app.web.library import LibraryPage
from app.web.library import LibraryConflict


ORIGIN = "https://kb.example.test"


class FakeAuth:
    def resolve_session(self, token):
        if token != "session-secret":
            from app.web.auth import WebAuthError

            raise WebAuthError("session_invalid")
        return ResolvedWebSession(
            7,
            "session-public",
            "telegram",
            datetime.now(UTC) + timedelta(hours=1),
        )

    def validate_csrf(self, session_token, csrf_token):
        assert session_token == "session-secret"
        if csrf_token != "csrf-secret":
            from app.web.auth import WebAuthError

            raise WebAuthError("csrf_invalid")

    def revoke_session(self, _token):
        return None

    def create_challenge(self, _channel, *, requester_key):
        del requester_key
        raise AssertionError("not used")

    def status(self, _public_id, _browser_secret):
        raise AssertionError("not used")

    def exchange(self, _public_id, _browser_secret):
        raise AssertionError("not used")


def library_item(lifecycle="ready"):
    return SimpleNamespace(
        public_id="item-public",
        platform="youtube",
        kind="video",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Tenant-owned video",
        author=None,
        published_at=None,
        duration_sec=30,
        lang="zh",
        description=None,
        tags=(),
        chapters=(),
        cover_url=None,
        saved_at=datetime.now(UTC),
        why_saved=None,
        text_source="youtube_captions",
        lifecycle=lifecycle,
        error_code=None,
        available_actions=("archive",),
        latest_dispatch_public_id=None,
    )


class FakeLibrary:
    def __init__(self):
        self.scopes = []

    def list_items(self, scope, **_kwargs):
        self.scopes.append(scope)
        return LibraryPage((library_item(),), 1, 1, 20, False)

    def archive(self, scope, _public_id):
        self.scopes.append(scope)
        return library_item("archived")


class UnusedService:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected service call: {name}")


class InvalidExtensionSession:
    def resolve(self, _token):
        raise HTTPException(status_code=401, detail="extension_session_invalid")


def make_client(
    *,
    static_dir: Path | None = None,
    library=None,
    save_enabled: bool = True,
):
    auth = FakeAuth()
    library = library or FakeLibrary()
    app = create_app(
        services=WebApiServices(
            web_auth=auth,
            library=library,
            submission=UnusedService(),
            transcript=UnusedService(),
        ),
        expected_origin=ORIGIN,
        cookie_secure=True,
        publish_budget_seconds=1.0,
        save_enabled=save_enabled,
        static_dir=static_dir,
    )
    client = TestClient(app, base_url=ORIGIN)
    client.cookies.set(
        SESSION_COOKIE_NAME,
        "session-secret",
        domain="kb.example.test",
        path="/",
    )
    return client, library


def test_unhandled_request_failure_is_contained_without_private_traceback(
    caplog,
):
    class ExplodingLibrary(FakeLibrary):
        def list_items(self, _scope, **_kwargs):
            raise RuntimeError("PRIVATE search/body/sql params")

    client, _ = make_client(library=ExplodingLibrary())

    with caplog.at_level("ERROR"):
        response = client.get("/api/v1/library/items")

    assert response.status_code == 500
    assert response.json() == {
        "code": "request_failed",
        "message": "请求无法完成",
    }
    assert "PRIVATE" not in response.text
    assert "PRIVATE" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_expired_extension_bearer_is_reported_as_unauthorized():
    auth = FakeAuth()
    app = create_app(
        services=WebApiServices(
            web_auth=auth,
            library=FakeLibrary(),
            submission=UnusedService(),
            transcript=UnusedService(),
            extension_sessions=InvalidExtensionSession(),
        ),
        expected_origin=ORIGIN,
        cookie_secure=True,
        publish_budget_seconds=1.0,
    )
    client = TestClient(app, base_url=ORIGIN)

    response = client.get(
        "/api/v1/library/items",
        headers={"Authorization": "Bearer expired-token"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "extension_session_invalid"


def test_public_api_rejects_fixed_and_streamed_oversized_bodies_before_parsing():
    client, _ = make_client()
    headers = {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
    }

    fixed = client.post(
        "/api/v1/auth/challenges",
        headers=headers,
        content=b"x" * 70_000,
    )
    streamed = client.post(
        "/api/v1/auth/challenges",
        headers=headers,
        content=iter((b"x" * 40_000, b"y" * 40_000)),
    )

    for response in (fixed, streamed):
        assert response.status_code == 413
        assert response.json() == {
            "code": "request_too_large",
            "message": "请求内容过大",
        }


def test_protected_library_scope_comes_only_from_server_session():
    client, library = make_client()

    response = client.get("/api/v1/library/items")

    assert response.status_code == 200
    assert library.scopes[0].app_user_id == 7
    assert "user_id" not in response.text
    schema_text = str(client.get("/api/v1/openapi.json").json())
    for forbidden in (
        "user_id",
        "app_user_id",
        "channel_identity_id",
        "external_user_id",
        "account_id",
    ):
        assert forbidden not in schema_text


def test_openapi_documents_browser_auth_csrf_and_safe_validation_contracts():
    client, _ = make_client()
    document = client.get("/api/v1/openapi.json").json()

    schemes = document["components"]["securitySchemes"]
    assert schemes["SessionCookie"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": SESSION_COOKIE_NAME,
    }
    assert schemes["BrowserSecret"]["type"] == "http"
    assert schemes["BrowserSecret"]["scheme"] == "bearer"

    assert document["paths"]["/api/v1/auth/challenges"]["post"].get("security") is None
    assert document["paths"]["/api/v1/auth/challenges/status"]["post"]["security"] == [
        {"BrowserSecret": []}
    ]
    assert document["paths"]["/api/v1/auth/session"]["get"]["security"] == [
        {"SessionCookie": []}
    ]
    assert document["paths"]["/api/v1/library/items"]["get"]["security"] == [
        {"SessionCookie": []}
    ]

    protected_mutations = (
        ("/api/v1/auth/session", "delete"),
        ("/api/v1/library/items:batch", "post"),
        ("/api/v1/library/items/{item_public_id}", "patch"),
        ("/api/v1/library/items/{item_public_id}:archive", "post"),
        ("/api/v1/library/items/{item_public_id}:restore", "post"),
        ("/api/v1/library/items/{item_public_id}:retry", "post"),
    )
    for path, method in protected_mutations:
        operation = document["paths"][path][method]
        header_names = {
            parameter["name"]
            for parameter in operation.get("parameters", [])
            if parameter["in"] == "header"
        }
        assert "X-CSRF-Token" in header_names

    for path_item in document["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            response = operation.get("responses", {}).get("422")
            if response is None:
                continue
            reference = response["content"]["application/json"]["schema"]["$ref"]
            assert reference.endswith(("/ErrorResponse", "/AuthErrorResponse"))
    assert "HTTPValidationError" not in document["components"]["schemas"]


def test_every_protected_mutation_requires_origin_fetch_metadata_and_bound_csrf():
    client, library = make_client()
    client.cookies.set(
        CSRF_COOKIE_NAME,
        "csrf-secret",
        domain="kb.example.test",
        path="/",
    )

    blocked = client.post("/api/v1/library/items/item-public:archive")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "csrf_invalid"

    allowed = client.post(
        "/api/v1/library/items/item-public:archive",
        headers={
            "Origin": ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            "X-CSRF-Token": "csrf-secret",
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["lifecycle"] == "archived"
    assert library.scopes[-1].app_user_id == 7


def test_retry_quota_is_a_safe_429_with_a_bounded_retry_hint():
    class QuotaLibrary(FakeLibrary):
        def retry(self, _scope, _public_id, *, request_key, publish_budget_seconds):
            assert request_key.startswith("web:7:")
            assert publish_budget_seconds == 1.0
            raise LibraryConflict("quota_exceeded")

    client, _ = make_client(library=QuotaLibrary())
    client.cookies.set(
        CSRF_COOKIE_NAME,
        "csrf-secret",
        domain="kb.example.test",
        path="/",
    )

    response = client.post(
        "/api/v1/library/items/item-public:retry",
        headers={
            "Origin": ORIGIN,
            "Sec-Fetch-Site": "same-origin",
            "X-CSRF-Token": "csrf-secret",
            "Idempotency-Key": "retry-after-quota",
        },
    )

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert response.json() == {
        "code": "quota_exceeded",
        "message": "已达到当前保存额度，请稍后重试",
    }


def test_global_save_switch_returns_a_safe_read_only_error_before_writes():
    client, _ = make_client(save_enabled=False)
    client.cookies.set(
        CSRF_COOKIE_NAME,
        "csrf-secret",
        domain="kb.example.test",
        path="/",
    )
    headers = {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "X-CSRF-Token": "csrf-secret",
        "Idempotency-Key": "read-only",
    }

    batch = client.post(
        "/api/v1/library/items:batch",
        headers=headers,
        json={"urls": ["https://youtu.be/dQw4w9WgXcQ"]},
    )
    retry = client.post(
        "/api/v1/library/items/item-public:retry",
        headers=headers,
    )

    assert batch.status_code == retry.status_code == 503
    assert batch.json() == retry.json() == {
        "code": "save_disabled",
        "message": "资料库当前为只读模式，暂时不能添加或重新整理视频",
    }


def test_static_spa_fallback_never_swallows_api_typos(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html><body>spa-shell</body></html>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    client = TestClient(create_app(static_dir=tmp_path), base_url=ORIGIN)

    route = client.get("/videos/item-public")
    asset = client.get("/assets/app.js")
    api_typo = client.get("/api/v1/library/typo")

    assert route.status_code == 200 and "spa-shell" in route.text
    assert route.headers["cache-control"] == "no-store"
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]
    assert api_typo.status_code == 404
    assert api_typo.headers["content-type"].startswith("application/json")
    assert "spa-shell" not in api_typo.text
