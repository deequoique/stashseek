from __future__ import annotations

import base64
import binascii
import io
import json
import struct
import wave
import zlib
from pathlib import Path

import pytest

from evals.video_recognition import openai_smoke
from evals.video_recognition.__main__ import main
from evals.video_recognition.redaction import assert_redacted, redact_report


class FakeTransport:
    def __init__(self, responses: list[openai_smoke.TransportResponse] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses = list(responses or [])

    def request(self, method, path, *, headers, body, timeout_seconds):
        self.calls.append({"method": method, "path": path, "headers": dict(headers), "body": body, "timeout": timeout_seconds})
        if self.responses:
            return self.responses.pop(0)
        if path == "/v1/audio/transcriptions":
            return openai_smoke.TransportResponse(200, json.dumps({"text": "private transcription sentinel", "segments": [{"start": 0, "end": 0.1}]}).encode())
        return openai_smoke.TransportResponse(
            200,
            json.dumps(
                {
                    "status": "completed",
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "private vision sentinel"}]}],
                    "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
                }
            ).encode(),
        )


def test_smoke_is_disabled_without_confirmation_and_does_not_construct_transport():
    class NeverTransport:
        def request(self, *args, **kwargs):  # pragma: no cover - a failure proves the gate opened
            raise AssertionError("transport must not be called")

    with pytest.raises(openai_smoke.OpenAISmokeError) as exc_info:
        openai_smoke.run_openai_smoke(api_key="test-key", transport=NeverTransport())
    assert exc_info.value.error_code == "confirmation_required"


def test_cli_smoke_requires_dedicated_command_and_confirmation(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def catalog_must_not_load(*args, **kwargs):
        raise AssertionError("the smoke path must not read the formal catalog")

    monkeypatch.setattr("evals.video_recognition.__main__.load_catalog", catalog_must_not_load)
    monkeypatch.setattr("evals.video_recognition.__main__.run_provider_benchmark", catalog_must_not_load)
    assert main(["--openai-smoke", "--temp-base-dir", str(tmp_path)]) == 2
    blocked = json.loads(capsys.readouterr().out)
    assert blocked == {"status": "blocked", "error_code": "confirmation_required"}


def test_environment_confirmation_is_a_second_explicit_opt_in(monkeypatch):
    monkeypatch.delenv(openai_smoke.SMOKE_CONFIRM_ENV, raising=False)
    assert not openai_smoke.confirmation_from_environment()
    monkeypatch.setenv(openai_smoke.SMOKE_CONFIRM_ENV, "1")
    assert openai_smoke.confirmation_from_environment()


def test_cli_returns_provider_failure_exit_code_after_printing_safe_report(monkeypatch, capsys, tmp_path):
    failed = openai_smoke.SmokeRecord(
        model="whisper-1",
        modality="asr",
        success=False,
        status="failed",
        latency_ms=1.0,
        summary_signal="no_content_emitted",
        usage={"reported": False},
        error_code="provider_transient",
        http_status=429,
    )
    monkeypatch.setattr(
        openai_smoke,
        "run_openai_smoke",
        lambda **kwargs: openai_smoke.OpenAISmokeReport((failed,), cleanup_removed=True),
    )
    exit_code = main(["--openai-smoke", "--confirm-openai-smoke", "--temp-base-dir", str(tmp_path)])
    assert exit_code == openai_smoke.SMOKE_PROVIDER_FAILURE_EXIT_CODE
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed_with_errors"
    assert output["records"][0]["http_status"] == 429


@pytest.mark.parametrize("invalid_status", [True, 99, 600, "429"])
def test_http_status_projection_requires_a_real_http_integer(invalid_status):
    with pytest.raises(ValueError):
        openai_smoke.SmokeRecord(
            model="whisper-1",
            modality="asr",
            success=False,
            status="failed",
            latency_ms=1.0,
            summary_signal="no_content_emitted",
            usage={"reported": False},
            error_code="provider_transient",
            http_status=invalid_status,
        )


def test_fake_transport_receives_only_bounded_local_inputs_and_exact_models(tmp_path):
    transport = FakeTransport()
    report = openai_smoke.run_openai_smoke(
        api_key="test-key",
        confirm=True,
        transport=transport,
        temp_base_dir=tmp_path,
    )
    assert report.cleanup_removed
    assert not list(tmp_path.iterdir())
    assert [call["path"] for call in transport.calls] == [
        "/v1/audio/transcriptions",
        "/v1/responses",
        "/v1/responses",
        "/v1/responses",
    ]

    asr_call = transport.calls[0]
    asr_body = asr_call["body"]
    assert isinstance(asr_body, bytes)
    assert b"whisper-1" in asr_body
    assert b'name="timestamp_granularities[]"\r\n\r\nsegment' in asr_body
    assert b"smoke.wav" in asr_body
    assert b"video" not in asr_body.lower().replace(b"video-recognition-openai-smoke", b"")

    vision_models: list[str] = []
    for call in transport.calls[1:]:
        payload = json.loads(call["body"])
        vision_models.append(payload["model"])
        assert payload["store"] is False
        assert payload["reasoning"] == {"effort": "none"}
        assert payload["max_output_tokens"] == 16
        image = payload["input"][0]["content"][1]
        assert image["type"] == "input_image"
        assert image["image_url"].startswith("data:image/png;base64,")
        assert "smoke.png" not in image["image_url"]
        assert "/private/" not in image["image_url"]
        assert len(base64.b64decode(image["image_url"].split(",", 1)[1])) < 1024
    assert vision_models == list(openai_smoke.VISION_MODELS)

    safe = json.dumps(report.as_dict(), ensure_ascii=False)
    assert "private transcription sentinel" not in safe
    assert "private vision sentinel" not in safe
    assert "test-key" not in safe
    assert_redacted(redact_report(report.as_dict()))


def test_success_normalization_reports_vision_usage_but_not_asr_tokens(tmp_path):
    transport = FakeTransport()
    report = openai_smoke.run_openai_smoke(api_key="key", confirm=True, transport=transport, temp_base_dir=tmp_path)
    records = report.as_dict()["records"]
    assert records[0]["usage"] == {"reported": False}
    assert all(record["usage"] == {"reported": True, "input_tokens": 11, "output_tokens": 3, "total_tokens": 14} for record in records[1:])
    assert records[0]["summary_signal"] == "asr_response_shape_valid_with_segments"
    assert all(record["summary_signal"] == "vision_response_shape_valid" for record in records[1:])


def test_asr_duration_is_the_only_content_free_usage_fallback(tmp_path):
    responses = [
        openai_smoke.TransportResponse(
            200,
            json.dumps({"text": "discard me", "segments": [], "duration": 0.1}).encode(),
        )
    ]
    transport = FakeTransport(responses)
    report = openai_smoke.run_openai_smoke(api_key="key", confirm=True, transport=transport, temp_base_dir=tmp_path)
    assert report.records[0].usage == {"reported": True, "audio_seconds": 0.1}
    assert "discard me" not in json.dumps(report.as_dict())


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "provider_auth_failed"),
        (429, "provider_transient"),
        (503, "provider_transient"),
        (422, "provider_rejected"),
    ],
)
def test_provider_errors_map_to_stable_content_free_codes(tmp_path, status_code, expected):
    body = b'{"error":"Authorization: Bearer secret-key; x-request-id: private-request-id; private provider message"}'
    transport = FakeTransport([openai_smoke.TransportResponse(status_code, body)] * 4)
    report = openai_smoke.run_openai_smoke(api_key="secret-key", confirm=True, transport=transport, temp_base_dir=tmp_path)
    records = report.as_dict()["records"]
    assert len(records) == 4
    assert all(not record["success"] and record["error_code"] == expected for record in records)
    assert all(record["http_status"] == status_code for record in records)
    serialized = json.dumps(report.as_dict())
    assert "private provider message" not in serialized
    assert "private-request-id" not in serialized
    assert "Authorization" not in serialized
    assert "secret-key" not in serialized


def test_provider_transport_failure_is_redacted_and_inputs_are_cleaned(tmp_path):
    class FailingTransport:
        def request(self, *args, **kwargs):
            raise RuntimeError("https://provider.example with secret-key and response body")

    report = openai_smoke.run_openai_smoke(api_key="secret-key", confirm=True, transport=FailingTransport(), temp_base_dir=tmp_path)
    assert report.cleanup_removed
    assert not list(tmp_path.iterdir())
    assert all(record.error_code == "provider_unavailable" for record in report.records)
    assert all(record.http_status is None for record in report.records)
    assert all("http_status" not in record.as_dict() for record in report.records)
    serialized = json.dumps(report.as_dict())
    assert "provider.example" not in serialized
    assert "secret-key" not in serialized
    assert "response body" not in serialized


def test_parse_failure_has_no_http_status_or_provider_content(tmp_path):
    response = openai_smoke.TransportResponse(
        200,
        b'not-json Authorization: Bearer secret-key private-request-id private provider message',
    )
    report = openai_smoke.run_openai_smoke(
        api_key="secret-key",
        confirm=True,
        transport=FakeTransport([response]),
        temp_base_dir=tmp_path,
    )
    record = report.records[0]
    assert record.error_code == "provider_malformed_response"
    assert record.http_status is None
    assert "http_status" not in record.as_dict()
    serialized = json.dumps(report.as_dict())
    for sentinel in ("Authorization", "secret-key", "private-request-id", "private provider message"):
        assert sentinel not in serialized


@pytest.mark.parametrize(
    "response_body",
    [
        {"segments": []},
        {"text": "discard", "segments": [{"start": 0.2, "end": 0.1}]},
    ],
)
def test_asr_false_success_shapes_are_rejected(tmp_path, response_body):
    responses = [openai_smoke.TransportResponse(200, json.dumps(response_body).encode())]
    report = openai_smoke.run_openai_smoke(
        api_key="key",
        confirm=True,
        transport=FakeTransport(responses),
        temp_base_dir=tmp_path,
    )
    assert not report.records[0].success
    assert report.records[0].error_code == "provider_malformed_response"


@pytest.mark.parametrize(
    "response_body",
    [
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "discard"}]}]},
        {"status": "incomplete", "output": [{"type": "message", "content": [{"type": "output_text", "text": "discard"}]}]},
        {"status": "completed", "output": [{"type": "reasoning"}]},
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "discard"}]}]},
    ],
)
def test_vision_false_success_shapes_are_rejected(tmp_path, response_body):
    responses = [
        openai_smoke.TransportResponse(200, json.dumps({"text": "", "segments": []}).encode()),
        openai_smoke.TransportResponse(200, json.dumps(response_body).encode()),
    ]
    report = openai_smoke.run_openai_smoke(
        api_key="key",
        confirm=True,
        transport=FakeTransport(responses),
        temp_base_dir=tmp_path,
    )
    assert not report.records[1].success
    assert report.records[1].error_code == "provider_malformed_response"


def test_invalid_key_never_reaches_a_transport_or_header(tmp_path):
    class NeverTransport:
        def request(self, *args, **kwargs):  # pragma: no cover - a failure proves unsafe admission
            raise AssertionError("invalid credentials must not reach transport")

    with pytest.raises(openai_smoke.OpenAISmokeError) as exc_info:
        openai_smoke.run_openai_smoke(
            api_key="key\r\nInjected: header",
            confirm=True,
            transport=NeverTransport(),
            temp_base_dir=tmp_path,
        )
    assert exc_info.value.error_code == "credentials_invalid"


def test_input_generation_failure_removes_pre_yield_temp_root(monkeypatch, tmp_path):
    def generation_fails():
        raise ValueError("private generation detail")

    monkeypatch.setattr(openai_smoke, "_deterministic_png", generation_fails)
    with pytest.raises(openai_smoke.OpenAISmokeError) as exc_info:
        openai_smoke.run_openai_smoke(
            api_key="key",
            confirm=True,
            transport=FakeTransport(),
            temp_base_dir=tmp_path,
        )
    assert exc_info.value.error_code == "input_generation_failed"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com.evil.example",
        "https://api.openai.com/v1",
        "https://api.openai.com?redirect=evil",
        "https://api.openai.com:443",
    ],
)
def test_transport_will_not_send_openai_key_to_a_custom_origin(base_url):
    with pytest.raises(openai_smoke.OpenAISmokeError) as exc_info:
        openai_smoke.UrllibTransport(base_url)
    assert exc_info.value.error_code == "invalid_base_url"


def test_transport_uses_verified_project_tls_context_and_disables_redirects(monkeypatch):
    ssl_context = openai_smoke.ssl.create_default_context()

    class Trusted:
        pass

    trusted = Trusted()
    trusted.ssl_context = ssl_context
    configured: list[str | None] = []

    def configure(bundle):
        configured.append(bundle)
        return trusted

    monkeypatch.setenv("TLS_CA_BUNDLE", "/safe/test-ca.pem")
    monkeypatch.setattr(openai_smoke, "configure_trusted_ca", configure)
    transport = openai_smoke.UrllibTransport()
    assert configured == ["/safe/test-ca.pem"]
    assert transport._ssl_context is ssl_context
    assert ssl_context.check_hostname
    assert ssl_context.verify_mode == openai_smoke.ssl.CERT_REQUIRED
    assert any(isinstance(handler, openai_smoke._NoRedirectHandler) for handler in transport._opener.handlers)


def test_deterministic_inputs_are_small_and_valid():
    wav_bytes = openai_smoke._deterministic_wav()
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 8000
        assert wav.getnframes() == 800
    png_bytes = openai_smoke._deterministic_png()
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    chunks: dict[bytes, bytes] = {}
    while offset < len(png_bytes):
        length = struct.unpack(">I", png_bytes[offset : offset + 4])[0]
        kind = png_bytes[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        payload = png_bytes[payload_start:payload_end]
        expected_crc = struct.unpack(">I", png_bytes[payload_end : payload_end + 4])[0]
        assert binascii.crc32(kind + payload) & 0xFFFFFFFF == expected_crc
        chunks[kind] = payload
        offset = payload_end + 4
    assert offset == len(png_bytes)
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[b"IHDR"]
    )
    assert (width, height, bit_depth, color_type, compression, filtering, interlace) == (2, 2, 8, 2, 0, 0, 0)
    raw_scanlines = zlib.decompress(chunks[b"IDAT"])
    assert len(raw_scanlines) == height * (1 + width * 3)
    assert raw_scanlines[0] == 0
    assert raw_scanlines[7] == 0
    assert len(wav_bytes) < 4096
    assert len(png_bytes) < 1024
