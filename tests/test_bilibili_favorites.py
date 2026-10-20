"""Bounded public favorites parsing, projection and read-only browser boundary."""

from contextlib import contextmanager
from dataclasses import asdict
import io
import json
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError

from fastapi.testclient import TestClient
import pytest

from app.api.app import WebApiServices, create_app
from app.api.bilibili_schemas import BilibiliFavoritesPreviewResponse
from app.bilibili import fetch_public
from app.bilibili.public import BilibiliPublicClient, BilibiliPublicError, BoundedPublicHTTP, parse_favorites_input
from app.bilibili.service import BilibiliService
from app.channels.types import UserScope
from app.web.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, WebAuthError


BV = "BV1xx411c7mD"
FID = "4157547776"
URL = f"https://space.bilibili.com/400561576/favlist?fid={FID}&ftype=create"


def response(rows=8):
    return {"code": 0, "data": {
        "info": {"id": int(FID), "title": "study", "media_count": rows},
        "has_more": False,
        "medias": [{"type": 2, "bvid": BV, "title": f"Video {index}"} for index in range(rows)],
    }}


class HTTP:
    def __init__(self, value):
        self.value, self.calls = value, []
    def get_json(self, endpoint, params):
        self.calls.append((endpoint, params))
        return self.value


@pytest.mark.parametrize("url", [
    URL,
    f"https://space.bilibili.com/400561576/favlist/?fid={FID}",
    f"https://www.bilibili.com/medialist/detail/ml{FID}",
    f"https://bilibili.com/medialist/detail/ml{FID}/?share_source=copy_web",
    "  " + URL + "  ",
])
def test_known_aliases_only_choose_numeric_collection_id(url):
    assert parse_favorites_input(url) == FID


@pytest.mark.parametrize("url", [
    URL.replace("https:", "http:"),
    URL.replace("space.bilibili.com", "space.bilibili.com.evil.test"),
    URL.replace("space.bilibili.com", "user@space.bilibili.com"),
    URL.replace("space.bilibili.com", "space.bilibili.com:443"),
    URL + "#",
    URL + "&fid=123",
    URL.replace("fid=" + FID, "fid=0"),
    URL.replace("fid=" + FID, "fid=00" + FID),
    URL.replace("fid=" + FID, "fid=" + "1" * 21),
    URL.replace("400561576/favlist", "0/favlist"),
    URL.replace("400561576/favlist", "400561576/favlist/evil"),
    URL.replace("ftype=create", "ftype=collect"),
    URL + "&next=https://evil.test",
    URL + "&fid%3d=2",
    URL + "&fid",
    URL.replace("?", "/?") + "&ftype=create",
    f"https://www.bilibili.com/medialist/play/ml{FID}",
    f"https://www.bilibili.com/medialist/detail/ml{FID}?fid=123",
    f"https://www.bilibili.com/medialist/detail/ml{FID}?ftype=create",
    f"https://www.bilibili.com/medialist/detail/ml{FID}/more",
    f"https://b23.tv/{FID}",
    "收藏夹 " + URL,
    URL + "\nhttps://evil.test",
    "x" * 2049,
])
def test_unsafe_or_unsupported_favorites_input_never_becomes_fetch_destination(url):
    with pytest.raises(BilibiliPublicError, match="bilibili_invalid_favorites_url"):
        parse_favorites_input(url)


def test_one_page_projection_matches_public_eight_row_shape():
    http = HTTP(response())
    page = BilibiliPublicClient(http).get_favorites(FID)
    projected = BilibiliFavoritesPreviewResponse.model_validate(asdict(page))
    assert projected.title == "study" and projected.reported_total == 8
    assert len(projected.items) == 8 and not projected.has_more and not projected.capped
    assert len({item.entry_key for item in projected.items}) == 8
    assert all(item.available and item.canonical_url == f"https://www.bilibili.com/video/{BV}" for item in projected.items)
    assert http.calls == [("favorites", {"media_id": FID, "pn": "1", "ps": "20"})]


def test_unavailable_rows_preserved_without_arbitrary_provider_urls():
    value = response(5)
    value["data"]["medias"] = [
        {"type": 2, "bvid": BV, "title": "已失效视频", "link": "https://evil.test"},
        {"type": 12, "bvid": BV, "title": "Audio"},
        {"type": 2, "bvid": "evil", "title": "Invalid"},
        None,
        {"type": 2, "bvid": BV, "title": "<script>title</script>", "link": "https://evil.test"},
    ]
    page = BilibiliPublicClient(HTTP(value)).get_favorites(FID)
    assert [item.unavailable_reason for item in page.items] == ["deleted", "not_video", "invalid_video", "invalid_video", None]
    assert all(item.canonical_url is None for item in page.items[:4])
    assert page.items[4].title == "<script>title</script>"  # UI text escaping, no HTML interpretation.
    assert page.items[4].canonical_url == f"https://www.bilibili.com/video/{BV}"
    assert "evil.test" not in json.dumps(asdict(page))


@pytest.mark.parametrize("change", [
    lambda x: x.update(code=True),
    lambda x: x.update(code=-404),
    lambda x: x.update(data=None),
    lambda x: x["data"].update(info=[]),
    lambda x: x["data"]["info"].update(id=123),
    lambda x: x["data"]["info"].update(media_count=True),
    lambda x: x["data"]["info"].update(media_count=-1),
    lambda x: x["data"]["info"].update(media_count=2**31),
    lambda x: x["data"].update(has_more=1),
    lambda x: x["data"].update(medias=None),
    lambda x: x["data"].update(medias=[]),
    lambda x: x["data"].update(medias=[], has_more=True),
])
def test_malformed_provider_result_is_not_a_successful_empty_folder(change):
    value = response()
    change(value)
    with pytest.raises(BilibiliPublicError):
        BilibiliPublicClient(HTTP(value)).get_favorites(FID)


def test_true_empty_public_list_is_distinct_from_private_or_provider_failure():
    value = response(0)
    value["data"]["medias"] = None
    page = BilibiliPublicClient(HTTP(value)).get_favorites(FID)
    assert page.reported_total == 0 and page.items == () and not page.has_more
    with pytest.raises(BilibiliPublicError, match="bilibili_favorites_private"):
        BilibiliPublicClient(HTTP({"code": -403})).get_favorites(FID)


def test_provider_risk_limit_is_distinct_from_empty_or_temporary_failure():
    for read in (
        lambda client: client.get_favorites(FID),
        lambda client: client.get_parts(BV),
    ):
        with pytest.raises(BilibiliPublicError, match="^bilibili_rate_limited$"):
            read(BilibiliPublicClient(HTTP({"code": -352, "message": "private provider detail"})))


def test_parent_public_boundary_projects_only_fixed_rate_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=3, stdout=b"private provider body"))
    with pytest.raises(BilibiliPublicError, match="^bilibili_rate_limited$"):
        BoundedPublicHTTP().get_json("favorites", {"media_id": FID, "pn": "1", "ps": "20"})


def test_public_http_429_does_not_emit_provider_body_or_exception_text(monkeypatch):
    class Opener:
        def open(self, request, **kwargs):
            raise HTTPError(request.full_url, 429, "private provider detail", {}, io.BytesIO(b"private body"))
    monkeypatch.setattr(fetch_public, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(fetch_public.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps({"endpoint": "favorites", "params": {"media_id": FID, "pn": "1", "ps": "20"}}).encode())))
    output = io.BytesIO()
    monkeypatch.setattr(fetch_public.sys, "stdout", SimpleNamespace(buffer=output))
    assert fetch_public.main() == 3
    assert output.getvalue() == b""


def test_preview_caps_are_explicit_and_do_not_walk_unseen_pages():
    value = response(20)
    value["data"]["has_more"] = True
    value["data"]["info"]["media_count"] = 10001
    http = HTTP(value)
    page = BilibiliPublicClient(http).get_favorites(FID, page=500)
    assert page.capped and page.has_more and page.reported_total == 10001
    assert http.calls == [("favorites", {"media_id": FID, "pn": "500", "ps": "20"})]
    with pytest.raises(BilibiliPublicError, match="bilibili_limit_exceeded"):
        BilibiliPublicClient(HTTP(response(21))).get_favorites(FID)
    for number in (0, 501, True, 1.5):
        with pytest.raises(BilibiliPublicError):
            BilibiliPublicClient(http).get_favorites(FID, page=number)
    assert len(http.calls) == 1


def test_service_is_read_only_and_reuses_same_tenant_and_upstream_gate():
    events = []
    class Gate:
        def admit(self, user):
            events.append(("admit", user))
        @contextmanager
        def upstream(self):
            events.append("lease")
            try:
                yield
            finally:
                events.append("release")
    def forbidden_db():
        raise AssertionError("preview must not open a database")
    http = HTTP(response())
    service = BilibiliService(forbidden_db, object(), BilibiliPublicClient(http), object(), Gate())
    result = service.preview_favorites(UserScope(77), URL)
    assert result["title"] == "study" and len(result["items"]) == 8
    assert events == [("admit", 77), "lease", "release"]
    assert http.calls == [("favorites", {"media_id": FID, "pn": "1", "ps": "20"})]


def test_public_http_child_fixed_favorites_operation_bounds_and_no_proxy(monkeypatch):
    raw = json.dumps(response()).encode()
    calls = []
    class Response:
        status = 200
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, maximum):
            calls.append(("read", maximum))
            return raw
    class Opener:
        def open(self, request, *, timeout):
            calls.append((request.full_url, timeout, request.header_items()))
            return Response()
    def opener(*handlers):
        assert any(isinstance(handler, fetch_public.ProxyHandler) and handler.proxies == {} for handler in handlers)
        assert any(isinstance(handler, fetch_public._NoRedirect) for handler in handlers)
        return Opener()
    monkeypatch.setattr(fetch_public, "build_opener", opener)
    output = io.BytesIO()
    monkeypatch.setattr(fetch_public.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps({"endpoint": "favorites", "params": {"media_id": FID, "pn": "500", "ps": "20"}}).encode())))
    monkeypatch.setattr(fetch_public.sys, "stdout", SimpleNamespace(buffer=output))
    assert fetch_public.main() == 0
    assert output.getvalue() == raw
    url, timeout, headers = calls[0]
    assert url == f"https://api.bilibili.com/x/v3/fav/resource/list?media_id={FID}&pn=500&ps=20"
    assert timeout == 14
    assert not any(name.lower() in {"cookie", "authorization", "proxy-authorization"} for name, _ in headers)
    assert calls[1] == ("read", 1024 * 1024 + 1)


@pytest.mark.parametrize("params", [
    {"media_id": FID, "pn": "501", "ps": "20"},
    {"media_id": FID, "pn": "1", "ps": "100"},
    {"media_id": "https://evil.test", "pn": "1", "ps": "20"},
    {"media_id": FID, "pn": "1", "ps": "20", "next": "https://evil.test"},
])
def test_public_child_rejects_invalid_operation_before_network(monkeypatch, params):
    monkeypatch.setattr(fetch_public.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps({"endpoint": "favorites", "params": params}).encode())))
    monkeypatch.setattr(fetch_public, "build_opener", lambda *args: pytest.fail("network must not be reached"))
    assert fetch_public.main() == 1


def test_favorites_api_uses_canonical_auth_csrf_and_strict_safe_response():
    origin = "https://app.test"
    class Auth:
        def resolve_session(self, token):
            if token != "session":
                raise WebAuthError("session_invalid")
            return SimpleNamespace(tenant=UserScope(77))
        def validate_csrf_for_session(self, session, csrf):
            if csrf != "csrf":
                raise WebAuthError("csrf_invalid")
    calls = []
    class Service:
        def preview_favorites(self, scope, url, *, page):
            calls.append((scope, url, page))
            if url == "private":
                raise BilibiliPublicError("bilibili_favorites_private")
            return asdict(BilibiliPublicClient(HTTP(response())).get_favorites(FID, page=page))
    auth = Auth()
    app = create_app(services=WebApiServices(web_auth=auth, library=object(), submission=object(), transcript=object(), bilibili=Service(), session_resolver=auth.resolve_session, include_conversation_routes=False), expected_origin=origin, bilibili_public_enabled=True)
    headers = {"Origin": origin, "X-CSRF-Token": "csrf"}
    with TestClient(app, base_url=origin) as client:
        client.cookies.set(SESSION_COOKIE_NAME, "session")
        client.cookies.set(CSRF_COOKIE_NAME, "csrf")
        assert client.get("/api/v1/capabilities").json()["bilibili_favorites_enabled"]
        assert client.post("/api/v1/bilibili/favorites:preview", json={"url": URL}).status_code == 403
        for page in (0, 501, True, "1"):
            assert client.post("/api/v1/bilibili/favorites:preview", json={"url": URL, "page": page}, headers=headers).status_code == 422
        assert client.post("/api/v1/bilibili/favorites:preview", json={"url": URL, "upstream": "https://evil.test"}, headers=headers).status_code == 422
        result = client.post("/api/v1/bilibili/favorites:preview", json={"url": URL}, headers=headers)
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert result.json()["reported_total"] == 8
        private = client.post("/api/v1/bilibili/favorites:preview", json={"url": "private"}, headers=headers)
        assert private.status_code == 409 and private.json()["code"] == "bilibili_favorites_private"
        client.cookies.set(SESSION_COOKIE_NAME, "invalid")
        assert client.post("/api/v1/bilibili/favorites:preview", json={"url": URL}, headers=headers).status_code == 401
    assert calls == [(UserScope(77), URL, 1), (UserScope(77), "private", 1)]
