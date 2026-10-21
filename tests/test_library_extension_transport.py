"""Library mutations accept an extension-session Bearer without a CSRF header.

Mirrors ``test_conversation_extension_transport.py``: the route-level
``X-CSRF-Token`` header is optional, and the app boundary still enforces the
double-submit check for cookie sessions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.app import WebApiServices, create_app
from app.channels.types import TenantContext
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, WebAuthError
from tests.test_web_api_app import library_item

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
    def resolve(self, token):
        if token != "extension-session":
            raise HTTPException(status_code=401, detail="extension_session_invalid")
        return _session(), TENANT.app_user_id, "user@example.test"


class Library:
    def __init__(self):
        self.calls = []

    def get_item(self, scope, public_id):
        self.calls.append(("get", scope.app_user_id, public_id))
        entered = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
        value = library_item("processing")
        value.stage = "transcribing"
        value.stage_updated_at = entered
        return value

    def archive(self, scope, public_id):
        self.calls.append(("archive", scope.app_user_id, public_id))
        return library_item("archived")

    def restore(self, scope, public_id):
        self.calls.append(("restore", scope.app_user_id, public_id))
        return library_item("ready")

    def update_why_saved(self, scope, public_id, why_saved):
        self.calls.append(("update", scope.app_user_id, public_id))
        return library_item("ready")


class Unused:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected service call: {name}")


def _client():
    library = Library()
    app = create_app(
        services=WebApiServices(
            web_auth=CookieAuth(),
            library=library,
            submission=Unused(),
            transcript=Unused(),
            session_resolver=CookieAuth().resolve_session,
            settings=SimpleNamespace(
                browser_companion_allowed_origins=(EXTENSION_ORIGIN,),
                browser_companion_max_request_bytes=5_500_000,
            ),
            extension_sessions=ExtensionSessions(),
        ),
        expected_origin=ORIGIN,
        cookie_secure=True,
        publish_budget_seconds=1.0,
    )
    return TestClient(app, base_url=ORIGIN), library


def _with_cookies(client: TestClient) -> TestClient:
    client.cookies.set(
        SESSION_COOKIE_NAME, "session-secret", domain="kb.example.test", path="/"
    )
    client.cookies.set(
        CSRF_COOKIE_NAME, "csrf-secret", domain="kb.example.test", path="/"
    )
    return client


MUTATIONS = (
    ("post", "/api/v1/library/items/item-public:archive", None),
    ("post", "/api/v1/library/items/item-public:restore", None),
    ("patch", "/api/v1/library/items/item-public", {"why_saved": "for later"}),
)


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_extension_bearer_needs_no_csrf_header(method, path, body):
    client, library = _client()

    response = client.request(
        method,
        path,
        json=body,
        headers={
            "Authorization": "Bearer extension-session",
            "Origin": EXTENSION_ORIGIN,
        },
    )

    assert response.status_code == 200, response.text
    assert library.calls and library.calls[0][1:] == (7, "item-public")


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_cookie_without_csrf_header_is_rejected_by_the_app_boundary(method, path, body):
    client, library = _client()
    _with_cookies(client)

    response = client.request(method, path, json=body, headers={"Origin": ORIGIN})

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"
    assert library.calls == []


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_cookie_with_wrong_csrf_header_is_rejected(method, path, body):
    client, library = _client()
    _with_cookies(client)

    response = client.request(
        method,
        path,
        json=body,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "not-the-cookie-value"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"
    assert library.calls == []


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_cookie_with_matching_csrf_header_succeeds(method, path, body):
    client, library = _client()
    _with_cookies(client)

    response = client.request(
        method,
        path,
        json=body,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "csrf-secret"},
    )

    assert response.status_code == 200, response.text
    assert len(library.calls) == 1


def test_extension_bearer_reads_an_item_with_its_processing_stage():
    client, library = _client()

    response = client.get(
        "/api/v1/library/items/item-public",
        headers={
            "Authorization": "Bearer extension-session",
            "Origin": EXTENSION_ORIGIN,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lifecycle"] == "processing"
    assert body["stage"] == "transcribing"
    assert body["stage_updated_at"].startswith("2026-10-17T12:00:00")
    assert library.calls == [("get", 7, "item-public")]


def test_stage_fields_are_optional_in_the_generated_item_schema():
    client, _ = _client()
    schema = client.get("/api/v1/openapi.json").json()["components"]["schemas"][
        "LibraryItemResponse"
    ]

    assert "stage" in schema["properties"]
    assert "stage_updated_at" in schema["properties"]
    assert "stage" not in schema["required"]
    assert "stage_updated_at" not in schema["required"]
    assert set(schema["properties"]["stage"]["enum"]) == {
        "queued",
        "fetching",
        "transcribing",
        "chunking",
        "embedding",
        "ready",
        "needs_action",
        "failed",
        "archived",
    }


def test_status_fields_are_optional_in_the_generated_schemas():
    client, _ = _client()
    schemas = client.get("/api/v1/openapi.json").json()["components"]["schemas"]

    item = schemas["LibraryItemResponse"]
    for name in ("stages", "reason", "body_searchable"):
        assert name in item["properties"]
        assert name not in item["required"]

    page = schemas["LibraryPageResponse"]
    for name in ("unsearchable_total", "processing_total"):
        assert name in page["properties"]
        assert name not in page["required"]

    # ``key`` stays a plain string so a new stage never changes the generated type.
    stage = schemas["IngestStageResponse"]
    assert stage["properties"]["key"] == {"type": "string", "title": "Key"}
    assert set(stage["required"]) == {"key", "status"}
    assert set(stage["properties"]["status"]["enum"]) == {
        "pending",
        "running",
        "done",
        "failed",
        "skipped",
    }
    detail = next(
        value for value in stage["properties"]["detail"]["anyOf"] if "enum" in value
    )
    assert set(detail["enum"]) == {"queued", "fetching", "transcribing", "indexing"}

    reason = schemas["IngestReasonResponse"]
    assert set(reason["required"]) == {"code", "category", "retry_useful"}
    assert set(reason["properties"]["category"]["enum"]) == {
        "no_speech",
        "no_transcription",
        "fetch_failed",
        "asr_failed",
        "quota",
        "too_large",
        "login_required",
        "error",
    }

    # The legacy enums keep exactly their values.
    assert set(item["properties"]["lifecycle"]["enum"]) == {
        "archived",
        "ready",
        "needs_action",
        "failed",
        "processing",
        "queued",
    }


def test_capabilities_transcription_platforms_is_optional_and_closed():
    client, _ = _client()
    schemas = client.get("/api/v1/openapi.json").json()["components"]["schemas"]

    capabilities = schemas["CapabilitiesResponse"]
    assert "transcription_platforms" in capabilities["properties"]
    assert "transcription_platforms" not in capabilities.get("required", [])
    assert set(
        capabilities["properties"]["transcription_platforms"]["items"]["enum"]
    ) == {"youtube", "bilibili"}


def test_item_response_serializes_stages_reason_and_searchability():
    from app.ingest.reasons import reason_for
    from app.web.library import IngestStageDTO

    client, library = _client()
    value = library_item("needs_action")
    reason = reason_for("asr_disabled")
    value.stages = (
        IngestStageDTO("body", "failed", None, reason),
        IngestStageDTO("classify", "skipped"),
        IngestStageDTO("summary", "running", None, None),
    )
    value.reason = reason
    value.body_searchable = False
    library.get_item = lambda _scope, _public_id: value

    response = client.get(
        "/api/v1/library/items/item-public",
        headers={
            "Authorization": "Bearer extension-session",
            "Origin": EXTENSION_ORIGIN,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    expected_reason = {
        "code": "asr_disabled",
        "category": "no_transcription",
        "retry_useful": True,
    }
    assert body["reason"] == expected_reason
    assert body["body_searchable"] is False
    assert body["stages"] == [
        {"key": "body", "status": "failed", "detail": None, "reason": expected_reason},
        {"key": "classify", "status": "skipped", "detail": None, "reason": None},
        {"key": "summary", "status": "running", "detail": None, "reason": None},
    ]
    # ``error_code`` keeps meaning "a failed item's code", not the new reason.
    assert body["error_code"] is None


def test_item_response_defaults_for_services_that_predate_the_status_fields():
    client, _ = _client()

    response = client.get(
        "/api/v1/library/items/item-public",
        headers={
            "Authorization": "Bearer extension-session",
            "Origin": EXTENSION_ORIGIN,
        },
    )

    body = response.json()
    assert (body["stages"], body["reason"], body["body_searchable"]) == ([], None, False)


def test_csrf_header_is_documented_but_optional_on_every_library_mutation():
    client, _ = _client()
    document = client.get("/api/v1/openapi.json").json()

    for path, method in (
        ("/api/v1/library/items:batch", "post"),
        ("/api/v1/library/items/{item_public_id}", "patch"),
        ("/api/v1/library/items/{item_public_id}:archive", "post"),
        ("/api/v1/library/items/{item_public_id}:restore", "post"),
        ("/api/v1/library/items/{item_public_id}:retry", "post"),
    ):
        parameters = document["paths"][path][method]["parameters"]
        csrf = next(item for item in parameters if item["name"] == "X-CSRF-Token")
        assert csrf.get("required", False) is False
