"""Canonical Session/CSRF/save bounds and safe source adapter behavior."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.app import WebApiServices, create_app
from app.bilibili.public import BilibiliPublicError
from app.channels.types import UserScope
from app.ingest.submission import BatchSaveResult, SaveItemResult
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, WebAuthError


ORIGIN = "https://app.test"
BV = "BV1xx411c7mD"


class Auth:
    def resolve_session(self, token):
        if token != "session":
            raise WebAuthError("session_invalid")
        return SimpleNamespace(tenant=UserScope(7))

    def validate_csrf_for_session(self, session, csrf):
        if csrf != "csrf":
            raise WebAuthError("csrf_invalid")


class Service:
    def __init__(self):
        self.calls = []
        self.error = None

    def preview_parts(self, scope, url, *, page):
        self.calls.append(("preview", scope, url, page))
        if self.error:
            raise BilibiliPublicError(self.error)
        return dict(bvid=BV, title="Title", page=1, page_size=20, total_parts=1, has_more=False, requested_page=None, parts=[])

    def submit_parts(self, scope, selections, **kwargs):
        self.calls.append(("save", scope, selections, kwargs))
        return BatchSaveResult((SaveItemResult("A1", 0, "part_ref_invalid", safe_error_code="part_ref_invalid"),
                                SaveItemResult("A2", 1, "queued", item_public_id="owned", state="pending")))

    def open_source(self, scope, public, *, timestamp):
        self.calls.append(("source", scope, public, timestamp))
        if self.error:
            raise BilibiliPublicError(self.error)
        return f"https://www.bilibili.com/video/{BV}?p=2&t={timestamp}"


def client_for(*, save_enabled=True, authenticated=True):
    auth, service = Auth(), Service()
    app = create_app(services=WebApiServices(web_auth=auth, library=object(), submission=object(), transcript=object(),
                     session_resolver=auth.resolve_session, bilibili=service, include_conversation_routes=False),
                     expected_origin=ORIGIN, cookie_secure=True, save_enabled=save_enabled)
    client = TestClient(app, base_url=ORIGIN)
    if authenticated:
        client.cookies.set(SESSION_COOKIE_NAME, "session")
        client.cookies.set(CSRF_COOKIE_NAME, "csrf")
    return client, service


HEADERS = {"Origin": ORIGIN, "X-CSRF-Token": "csrf", "Idempotency-Key": "stable-key"}


def test_preview_requires_session_origin_csrf_and_does_not_save():
    client, service = client_for()
    with client:
        for headers in ({}, {"Origin": "https://evil.test"}, {"Origin": ORIGIN, "X-CSRF-Token": "wrong"}):
            response = client.post("/api/v1/bilibili/parts:preview", json={"url": BV}, headers=headers)
            assert response.status_code == 403
        valid = client.post("/api/v1/bilibili/parts:preview", json={"url": BV}, headers=HEADERS)
    assert valid.status_code == 200 and valid.headers["cache-control"] == "no-store"
    assert service.calls == [("preview", UserScope(7), BV, None)]
    anonymous, unused = client_for(authenticated=False)
    with anonymous:
        assert anonymous.get("/api/v1/library/items/owned/source", follow_redirects=False).status_code == 401
    assert unused.calls == []


def test_strict_selection_bounds_partial_results_and_save_disabled():
    client, service = client_for()
    with client:
        for body in ({"selections": [{"part_ref": "ref"}] * 11}, {"selections": [{"part_ref": "ref", "allow_legacy_overlap": "true"}]}, {"selections": [{"part_ref": "ref", "cid": "101"}]}):
            assert client.post("/api/v1/bilibili/parts:batch", json=body, headers=HEADERS).status_code == 422
        response = client.post("/api/v1/bilibili/parts:batch", json={"selections": [{"part_ref": "bad"}, {"part_ref": "good"}]}, headers=HEADERS)
    assert [row["status"] for row in response.json()["results"]] == ["part_ref_invalid", "queued"]
    assert "7:" in service.calls[0][3]["request_key"]
    assert "stable-key" not in service.calls[0][3]["request_key"]
    disabled, unused = client_for(save_enabled=False)
    with disabled:
        assert disabled.post("/api/v1/bilibili/parts:batch", json={"selections": [{"part_ref": "ref"}]}, headers=HEADERS).status_code == 503
        assert not disabled.get("/api/v1/capabilities").json()["bilibili_parts_enabled"]
    assert unused.calls == []


def test_source_303_timestamp_bounds_and_provider_failures_have_no_location():
    client, service = client_for()
    with client:
        response = client.get("/api/v1/library/items/owned/source?t=42", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"https://www.bilibili.com/video/{BV}?p=2&t=42"
        for timestamp in ("-1", "2147483648", "1.5"):
            assert client.get("/api/v1/library/items/owned/source?t=" + timestamp, follow_redirects=False).status_code == 422
        for code, status in (("not_found", 404), ("source_unavailable", 409), ("bilibili_rate_limited", 429), ("bilibili_unavailable", 503)):
            service.error = code
            response = client.get("/api/v1/library/items/owned/source", follow_redirects=False)
            assert response.status_code == status and response.json()["code"] == code
            assert "location" not in response.headers
