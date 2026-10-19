"""Public signed temporary-audio route used by the ASR provider."""

import io
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from app.api.app import WebApiServices, create_app
from app.asr.signing import new_audio_object_name, sign_audio_url
from app.object_store import RawObjectStore

ORIGIN = "https://kb.example.test"
BASE_URL = "https://stashseek.example.test"
SECRET = "r" * 32
MAX_TTL = 7200
MAX_BYTES = 1_000_000
AUDIO = bytes(range(256)) * 800  # 204,800 bytes, several 64 KiB chunks
NOT_FOUND = {"code": "not_found", "message": "未找到请求的资源"}


class FakeBody:
    def __init__(self, data):
        self._io = io.BytesIO(data)
        self.read_sizes = []
        self.closed = False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self._io.read(size)

    def close(self):
        self.closed = True


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.bodies = []
        self.get_keys = []
        self.error = None

    def get_object(self, *, Bucket, Key):
        self.get_keys.append(Key)
        if self.error is not None:
            raise self.error
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        data, content_type = self.objects[Key]
        body = FakeBody(data)
        self.bodies.append(body)
        return {"Body": body, "ContentLength": len(data), "ContentType": content_type}


class Unused:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected service use: {name}")


def make_settings(**overrides):
    values = {
        "asr_enabled": True,
        "asr_audio_url_secret": SECRET,
        "asr_audio_url_ttl_seconds": MAX_TTL,
        "asr_max_audio_bytes": MAX_BYTES,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_app(*, settings=None, store=None, static_dir=None, services=True):
    s3 = FakeS3()
    name = new_audio_object_name("m4a")
    s3.objects[f"asr-tmp/{name}"] = (AUDIO, "audio/mp4")
    store = store if store is not None else RawObjectStore(client=s3, bucket="raw")
    web_services = (
        WebApiServices(
            web_auth=Unused(),
            library=Unused(),
            submission=Unused(),
            transcript=Unused(),
            settings=settings if settings is not None else make_settings(),
            asr_audio_store=store,
        )
        if services
        else None
    )
    app = create_app(
        services=web_services,
        expected_origin=ORIGIN if services else None,
        static_dir=static_dir,
    )
    return app, s3, name


def signed_path(name, *, secret=SECRET, ttl=MAX_TTL, now=None):
    url = sign_audio_url(
        base_url=BASE_URL, name=name, secret=secret, ttl_seconds=ttl, now=now
    )
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


def client_for(app):
    return TestClient(app, base_url=ORIGIN)


def test_signed_get_streams_audio_without_session_or_origin():
    app, s3, name = make_app()
    client = client_for(app)

    response = client.get(signed_path(name))

    assert response.status_code == 200
    assert response.content == AUDIO
    assert response.headers["content-type"] == "audio/mp4"
    assert response.headers["content-length"] == str(len(AUDIO))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in response.headers
    assert "access-control-allow-origin" not in response.headers
    assert s3.get_keys == [f"asr-tmp/{name}"]
    assert not client.cookies


def test_response_is_streamed_in_bounded_chunks_and_body_is_closed():
    app, s3, name = make_app()

    response = client_for(app).get(signed_path(name))

    assert response.status_code == 200
    body = s3.bodies[0]
    assert len(body.read_sizes) > 2
    assert set(body.read_sizes) == {64 * 1024}
    assert body.closed is True


def test_foreign_origin_header_does_not_block_the_provider_download():
    app, _s3, name = make_app()

    response = client_for(app).get(
        signed_path(name), headers={"Origin": "https://evil.example.test"}
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_head_returns_headers_only_and_closes_the_body():
    app, s3, name = make_app()

    response = client_for(app).head(signed_path(name))

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-type"] == "audio/mp4"
    assert response.headers["content-length"] == str(len(AUDIO))
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert s3.bodies[0].read_sizes == []
    assert s3.bodies[0].closed is True


def test_head_with_a_bad_signature_is_not_found():
    app, s3, name = make_app()

    response = client_for(app).head(signed_path(name, secret="z" * 32))

    assert response.status_code == 404
    assert s3.get_keys == []


def test_unsafe_methods_are_not_allowed():
    app, _s3, name = make_app()

    response = client_for(app).post(
        signed_path(name), headers={"Origin": ORIGIN}
    )

    assert response.status_code in {403, 405}


def failing_store_cases():
    def too_large(s3, name):
        s3.objects[f"asr-tmp/{name}"] = (b"x" * (MAX_BYTES + 1), "audio/mp4")

    def missing(s3, name):
        s3.objects.pop(f"asr-tmp/{name}")

    def backend_down(s3, name):
        s3.error = ConnectionError("private minio host detail")

    def access_denied(s3, name):
        s3.error = ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    return [too_large, missing, backend_down, access_denied]


@pytest.mark.parametrize("break_store", failing_store_cases())
def test_store_failures_are_the_same_generic_404(break_store, caplog):
    app, s3, name = make_app()
    break_store(s3, name)

    with caplog.at_level("WARNING"):
        response = client_for(app).get(signed_path(name))

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert "asr-tmp" not in response.text
    assert name not in response.text
    assert "private" not in response.text
    assert "private" not in caplog.text
    assert name not in caplog.text
    assert "cache-control" in response.headers


def bad_signature_requests(name):
    other = new_audio_object_name("m4a")
    far_future_ttl = MAX_TTL + 3600
    good = signed_path(name)
    expires = good.split("expires=")[1].split("&")[0]
    sig = good.split("sig=")[1]
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
    base = f"/api/v1/asr-audio/{name}"
    return {
        "tampered_sig": f"{base}?expires={expires}&sig={flipped}",
        "tampered_expiry": f"{base}?expires={int(expires) + 1}&sig={sig}",
        "wrong_secret": signed_path(name, secret="z" * 32),
        "expired": signed_path(name, now=1.0),
        "far_future": signed_path(name, ttl=far_future_ttl),
        "other_object_name": f"/api/v1/asr-audio/{other}?expires={expires}&sig={sig}",
        "traversal_name": f"/api/v1/asr-audio/..%2F{name}?expires={expires}&sig={sig}",
        "bad_extension": f"/api/v1/asr-audio/{name[:-4]}.exe?expires={expires}&sig={sig}",
        "missing_params": base,
        "missing_sig": f"{base}?expires={expires}",
        "missing_expires": f"{base}?sig={sig}",
        "empty_params": f"{base}?expires=&sig=",
        "non_numeric_expires": f"{base}?expires=abc&sig={sig}",
    }


@pytest.mark.parametrize(
    "case",
    [
        "tampered_sig",
        "tampered_expiry",
        "wrong_secret",
        "expired",
        "far_future",
        "other_object_name",
        "traversal_name",
        "bad_extension",
        "missing_params",
        "missing_sig",
        "missing_expires",
        "empty_params",
        "non_numeric_expires",
    ],
)
def test_invalid_signature_requests_are_the_same_generic_404(case):
    app, s3, name = make_app()
    path = bad_signature_requests(name)[case]

    response = client_for(app).get(path)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert "asr-tmp" not in response.text
    assert name not in response.text
    assert s3.get_keys == []


def test_missing_signature_params_do_not_leak_a_validation_error():
    app, _s3, name = make_app()

    response = client_for(app).get(f"/api/v1/asr-audio/{name}")

    assert response.json()["code"] == "not_found"


def test_route_is_absent_when_asr_is_disabled():
    app, s3, name = make_app(settings=make_settings(asr_enabled=False))

    response = client_for(app).get(signed_path(name))

    assert response.status_code == 404
    assert s3.get_keys == []
    assert all(
        getattr(route, "path", "") != "/api/v1/asr-audio/{name}"
        for route in app.routes
    )


@pytest.mark.parametrize("secret", [None, ""])
def test_route_is_absent_without_a_signing_secret(secret):
    app, s3, name = make_app(settings=make_settings(asr_audio_url_secret=secret))

    response = client_for(app).get(
        f"/api/v1/asr-audio/{name}?expires=9999999999&sig={'0' * 64}"
    )

    assert response.status_code == 404
    assert s3.get_keys == []
    assert all(
        getattr(route, "path", "") != "/api/v1/asr-audio/{name}"
        for route in app.routes
    )


def test_route_is_absent_without_services_or_settings():
    for app in (
        create_app(),
        make_app(settings=SimpleNamespace())[0],
    ):
        assert all(
            getattr(route, "path", "") != "/api/v1/asr-audio/{name}"
            for route in app.routes
        )


def test_route_is_excluded_from_openapi():
    app, _s3, _name = make_app()

    assert not any("asr-audio" in path for path in app.openapi()["paths"])


def test_route_is_registered_before_the_spa_fallback(tmp_path: Path):
    (tmp_path / "index.html").write_text("<html>spa</html>")
    app, _s3, name = make_app(static_dir=tmp_path)
    client = client_for(app)

    streamed = client.get(signed_path(name))
    invalid = client.get(signed_path(name, secret="z" * 32))

    assert streamed.status_code == 200
    assert streamed.content == AUDIO
    assert invalid.status_code == 404
    assert invalid.json() == NOT_FOUND


def test_default_store_is_built_lazily_on_first_request(monkeypatch):
    s3 = FakeS3()
    name = new_audio_object_name("m4a")
    s3.objects[f"asr-tmp/{name}"] = (AUDIO, "audio/mp4")
    built = []

    def build_store():
        built.append(1)
        return RawObjectStore(client=s3, bucket="raw")

    monkeypatch.setattr("app.object_store.RawObjectStore", build_store)
    app = create_app(
        services=WebApiServices(
            web_auth=Unused(),
            library=Unused(),
            submission=Unused(),
            transcript=Unused(),
            settings=make_settings(),
        ),
        expected_origin=ORIGIN,
    )
    assert built == []
    client = client_for(app)

    assert client.get(signed_path(name)).content == AUDIO
    assert client.get(signed_path(name)).content == AUDIO
    assert built == [1]


def test_runtime_composition_passes_its_object_store_to_the_route():
    from app.api.runtime import build_web_app

    s3 = FakeS3()
    name = new_audio_object_name("m4a")
    s3.objects[f"asr-tmp/{name}"] = (AUDIO, "audio/mp4")
    config = SimpleNamespace(
        validate_web_auth=lambda: None,
        web_auth_secret="x" * 32,
        web_auth_challenge_ttl_seconds=600,
        web_auth_session_ttl_seconds=2592000,
        web_auth_attempt_limit=5,
        web_auth_rate_window_seconds=60,
        web_auth_rate_limit_per_requester=5,
        web_auth_global_rate_limit=100,
        web_auth_active_challenge_limit=3,
        web_auth_challenge_retention_seconds=86400,
        web_auth_session_retention_seconds=604800,
        web_login_channels=("telegram", "wechat"),
        web_origin=ORIGIN,
        web_cookie_secure=True,
        web_publish_budget_seconds=1.5,
        web_serve_static=False,
        web_static_dir="web/dist",
        trash_retention_days=30,
        ingest_max_active_per_user=10,
        ingest_daily_new_item_limit=50,
        ingest_max_items_per_user=1000,
        ingest_max_active_global=100,
        ingest_daily_new_item_limit_global=300,
        ingest_daily_dispatch_limit_per_user=100,
        ingest_daily_dispatch_limit_global=1000,
        asr_enabled=True,
        asr_audio_url_secret=SECRET,
        asr_audio_url_ttl_seconds=MAX_TTL,
        asr_max_audio_bytes=MAX_BYTES,
    )

    app = build_web_app(
        config,
        session_factory=lambda: None,
        publisher=lambda _dispatch_id: "task",
        object_store=RawObjectStore(client=s3, bucket="raw"),
        mount_static=False,
    )

    response = client_for(app).get(signed_path(name))
    assert response.status_code == 200
    assert response.content == AUDIO
