"""Opt-in OpenAI connectivity smoke test for the video benchmark.

This module is deliberately independent from the benchmark catalog and the
provider benchmark runner.  It sends only locally generated, tiny inputs and
projects every provider response into a content-free summary.  The normal
benchmark remains disabled while its catalog gates are closed.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import shutil
import socket
import ssl
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol

from app.tls import TLSConfigurationError, configure_trusted_ca


ASR_MODEL = "whisper-1"
VISION_MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
SMOKE_CONFIRM_ENV = "VIDEO_RECOGNITION_OPENAI_SMOKE_CONFIRM"
DEFAULT_BASE_URL = "https://api.openai.com"
_MARKER_NAME = ".openai-video-recognition-smoke"
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_TIMEOUT_SECONDS = 120.0
_MULTIPART_BOUNDARY = "video-recognition-openai-smoke"
_VISION_PROMPT = "Return only a minimal acknowledgement object; do not describe the image."
SMOKE_PROVIDER_FAILURE_EXIT_CODE = 3


class OpenAISmokeError(RuntimeError):
    """A stable, content-free smoke-test admission or execution failure."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class _PayloadError(Exception):
    """Internal marker for a provider body that cannot be normalized."""


class _TransportError(Exception):
    """Internal marker for transport failures without carrying provider text."""

    def __init__(self, category: str) -> None:
        self.category = category


class _ProviderHTTPError(Exception):
    """Internal HTTP failure carrying only a validated status code."""

    def __init__(self, status_code: int) -> None:
        if isinstance(status_code, bool) or not isinstance(status_code, int) or not 100 <= status_code <= 599:
            raise ValueError("status code is outside the HTTP range")
        self.status_code = status_code
        self.error_code = _http_error_code(status_code)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Fail closed instead of forwarding the bearer header to a redirect."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


@dataclass(frozen=True)
class TransportResponse:
    """The bounded part of an HTTP response needed by the smoke test."""

    status_code: int
    body: bytes = b""

    def __post_init__(self) -> None:
        if isinstance(self.status_code, bool) or not isinstance(self.status_code, int):
            raise ValueError("status code must be an integer")
        if not 100 <= self.status_code <= 599:
            raise ValueError("status code is outside the HTTP range")
        if not isinstance(self.body, bytes):
            raise ValueError("response body must be bytes")
        if len(self.body) > _MAX_RESPONSE_BYTES:
            raise ValueError("response body exceeds the smoke-test bound")


class SmokeTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> TransportResponse: ...


class UrllibTransport:
    """Small HTTPS transport with no SDK dependency and no response logging."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        normalized = base_url.rstrip("/")
        if (
            normalized != DEFAULT_BASE_URL
            or parsed.scheme != "https"
            or parsed.hostname != "api.openai.com"
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise OpenAISmokeError("invalid_base_url")
        try:
            trusted = configure_trusted_ca(os.environ.get("TLS_CA_BUNDLE"))
        except TLSConfigurationError:
            raise OpenAISmokeError("tls_configuration_invalid") from None
        self._base_url = normalized
        self._ssl_context = trusted.ssl_context
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_context),
            _NoRedirectHandler(),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> TransportResponse:
        # ``path`` is fixed by this module.  Keeping URL composition here also
        # prevents a provider response or input from selecting a destination.
        url = f"{self._base_url}{path}"
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", response.getcode()))
                return TransportResponse(status, _read_response_body(response))
        except urllib.error.HTTPError as exc:
            # Do not read or retain an error body.  Its status is sufficient to
            # produce a stable category and it may contain provider content.
            return TransportResponse(int(exc.code), b"")
        except (TimeoutError, socket.timeout):
            raise _TransportError("provider_timeout") from None
        except ssl.SSLError:
            raise _TransportError("provider_tls_error") from None
        except urllib.error.URLError:
            raise _TransportError("provider_unavailable") from None
        except OSError:
            raise _TransportError("provider_unavailable") from None


def _read_response_body(response: Any) -> bytes:
    """Read at most the bounded response body without exposing it."""

    body = response.read(_MAX_RESPONSE_BYTES + 1)
    if not isinstance(body, bytes) or len(body) > _MAX_RESPONSE_BYTES:
        raise _TransportError("provider_response_too_large")
    return body


@dataclass(frozen=True)
class SmokeRecord:
    model: str
    modality: str
    success: bool
    status: str
    latency_ms: float
    summary_signal: str
    usage: dict[str, object]
    error_code: str | None = None
    http_status: int | None = None

    def __post_init__(self) -> None:
        if self.http_status is not None and (
            isinstance(self.http_status, bool)
            or not isinstance(self.http_status, int)
            or not 100 <= self.http_status <= 599
        ):
            raise ValueError("HTTP status must be a validated integer")

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "modality": self.modality,
            "success": self.success,
            "status": self.status,
            "status_category": self.error_code or self.status,
            "latency_ms": self.latency_ms,
            "usage": dict(self.usage),
            "summary_signal": self.summary_signal,
        }
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        if self.http_status is not None:
            payload["http_status"] = self.http_status
        return payload


@dataclass(frozen=True)
class OpenAISmokeReport:
    records: tuple[SmokeRecord, ...]
    cleanup_removed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "completed" if all(record.success for record in self.records) else "completed_with_errors",
            "records": [record.as_dict() for record in self.records],
            "cleanup": {"marked_temp_dir": True, "removed": self.cleanup_removed},
        }


@dataclass(frozen=True)
class _SmokeInputs:
    root: Path
    wav_path: Path
    image_path: Path


@contextmanager
def _temporary_inputs(base_dir: str | Path | None = None) -> Iterator[_SmokeInputs]:
    """Generate and always remove a marked directory containing smoke inputs."""

    base = Path(base_dir) if base_dir is not None else None
    if base is not None:
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise OpenAISmokeError("invalid_temp_base_dir") from None
        if not base.is_dir():
            raise OpenAISmokeError("invalid_temp_base_dir")
    try:
        root = Path(tempfile.mkdtemp(prefix="openai-video-recognition-smoke-", dir=str(base) if base else None))
    except OSError:
        raise OpenAISmokeError("invalid_temp_base_dir") from None
    marker = root / _MARKER_NAME
    try:
        try:
            marker.write_text("owned-by-openai-video-recognition-smoke\n", encoding="utf-8")
            wav_path = root / "smoke.wav"
            image_path = root / "smoke.png"
            wav_path.write_bytes(_deterministic_wav())
            image_path.write_bytes(_deterministic_png())
        except (OSError, ValueError):
            raise OpenAISmokeError("input_generation_failed") from None
        yield _SmokeInputs(root, wav_path, image_path)
    finally:
        # ``root`` is always the exact return value of mkdtemp, never the
        # caller-provided base directory.  Remove it even if marker creation or
        # a later input write failed before the context manager could yield.
        try:
            shutil.rmtree(root)
        except OSError:
            # The caller receives a stable cleanup error instead of an OS
            # message that might disclose a path.
            raise OpenAISmokeError("cleanup_failed") from None
        if root.exists():
            raise OpenAISmokeError("cleanup_failed")


def _deterministic_wav() -> bytes:
    """Return a tiny deterministic mono WAV containing a quiet test tone."""

    sample_rate = 8000
    sample_count = 800  # 100 ms; small enough for a connectivity-only probe.
    pcm = bytearray()
    for index in range(sample_count):
        # A deterministic, bounded PCM pattern is enough to exercise upload;
        # this is not intended to be speech or a benchmark evidence sample.
        value = int(5000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        pcm.extend(struct.pack("<h", value))
    buffer = _BytesWriter()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(pcm))
    return buffer.getvalue()


class _BytesWriter:
    """Minimal seekable writer accepted by :mod:`wave` for deterministic data."""

    def __init__(self) -> None:
        self._data = bytearray()
        self._position = 0

    def write(self, data: bytes) -> int:
        end = self._position + len(data)
        if end > len(self._data):
            self._data.extend(b"\0" * (end - len(self._data)))
        self._data[self._position : end] = data
        self._position = end
        return len(data)

    def tell(self) -> int:
        return self._position

    def seek(self, position: int, whence: int = 0) -> int:
        if whence == 0:
            target = position
        elif whence == 1:
            target = self._position + position
        elif whence == 2:
            target = len(self._data) + position
        else:
            raise ValueError("unsupported seek mode")
        if target < 0:
            raise ValueError("negative seek")
        self._position = target
        return self._position

    def getvalue(self) -> bytes:
        return bytes(self._data)

    def flush(self) -> None:
        return None


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)


def _deterministic_png() -> bytes:
    """Return a tiny deterministic 2x2 RGB PNG generated without Pillow."""

    # Two rows of fixed RGB pixels, each preceded by PNG's filter byte.
    pixels = b"\x00\xff\x00\x00\x00\xff\x00\x00\x00\x00\xff\xff\xff\xff"
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    return header + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(pixels, 9)) + _png_chunk(b"IEND", b"")


def confirmation_from_environment(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return values.get(SMOKE_CONFIRM_ENV, "").strip().casefold() in {"1", "true", "yes"}


def _validate_timeout(timeout_seconds: float) -> float:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= _MAX_TIMEOUT_SECONDS
    ):
        raise OpenAISmokeError("invalid_timeout")
    return float(timeout_seconds)


def _validate_key(api_key: str | None) -> str:
    if not isinstance(api_key, str) or not api_key.strip():
        raise OpenAISmokeError("credentials_missing")
    normalized = api_key.strip()
    if any(character.isspace() or not 33 <= ord(character) <= 126 for character in normalized):
        raise OpenAISmokeError("credentials_invalid")
    return normalized


def _multipart_audio(file_bytes: bytes) -> tuple[bytes, str]:
    if not file_bytes or len(file_bytes) > 25 * 1024 * 1024:
        raise OpenAISmokeError("input_generation_failed")
    boundary = _MULTIPART_BOUNDARY
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )

    field("model", ASR_MODEL)
    field("response_format", "verbose_json")
    field("timestamp_granularities[]", "segment")
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="smoke.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _vision_payload(model: str, image_bytes: bytes) -> bytes:
    image_data = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "store": False,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 16,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _VISION_PROMPT},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{image_data}", "detail": "low"},
                ],
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _auth_headers(api_key: str, content_type: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": content_type,
        "Accept": "application/json",
    }


def _parse_json(response: TransportResponse) -> Mapping[str, Any]:
    if response.status_code < 200 or response.status_code >= 300:
        raise _ProviderHTTPError(response.status_code)
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _PayloadError from None
    if not isinstance(payload, Mapping):
        raise _PayloadError
    return payload


def _http_error_code(status_code: int) -> str:
    if status_code in {401, 403}:
        return "provider_auth_failed"
    if status_code in {408, 425, 429} or status_code >= 500:
        return "provider_transient"
    if 400 <= status_code < 500:
        return "provider_rejected"
    return "provider_unexpected_status"


def _normalize_asr(payload: Mapping[str, Any]) -> dict[str, object]:
    if "text" not in payload or not isinstance(payload["text"], str):
        raise _PayloadError
    segments = payload.get("segments", [])
    if not isinstance(segments, list):
        raise _PayloadError
    previous_start = -1.0
    for segment in segments:
        if not isinstance(segment, Mapping):
            raise _PayloadError
        start = segment.get("start")
        end = segment.get("end")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in (start, end)
        ):
            raise _PayloadError
        start_number = float(start)
        end_number = float(end)
        if start_number < 0 or end_number < start_number or start_number < previous_start:
            raise _PayloadError
        previous_start = start_number
    usage = _normalize_usage(payload.get("usage"))
    duration = payload.get("duration")
    if (
        not isinstance(duration, bool)
        and isinstance(duration, (int, float))
        and math.isfinite(float(duration))
        and float(duration) >= 0
    ):
        usage["audio_seconds"] = round(float(duration), 3)
        usage["reported"] = True
    return {
        "summary_signal": "asr_response_shape_valid_with_segments" if segments else "asr_response_shape_valid_no_segments",
        "usage": usage,
    }


def _normalize_usage(value: Any) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {"reported": False}
    usage: dict[str, object] = {"reported": False}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            continue
        usage[key] = number
    if len(usage) > 1:
        usage["reported"] = True
    return usage


def _normalize_vision(payload: Mapping[str, Any]) -> dict[str, object]:
    if payload.get("status") != "completed":
        raise _PayloadError
    output = payload.get("output")
    if not isinstance(output, list) or not output:
        raise _PayloadError
    has_output_text = False
    for item in output:
        if not isinstance(item, Mapping):
            raise _PayloadError
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise _PayloadError
        for part in content:
            if not isinstance(part, Mapping):
                raise _PayloadError
            if part.get("type") == "output_text" and isinstance(part.get("text"), str) and part["text"].strip():
                has_output_text = True
    if not has_output_text:
        raise _PayloadError
    return {
        "summary_signal": "vision_response_shape_valid",
        "usage": _normalize_usage(payload.get("usage")),
    }


def _latency_ms(started: float) -> float:
    elapsed = max(0.0, (time.perf_counter() - started) * 1000)
    return round(elapsed, 2)


def _error_record(
    model: str,
    modality: str,
    started: float,
    error_code: str,
    *,
    http_status: int | None = None,
) -> SmokeRecord:
    return SmokeRecord(
        model=model,
        modality=modality,
        success=False,
        status="failed",
        latency_ms=_latency_ms(started),
        summary_signal="no_content_emitted",
        usage={"reported": False},
        error_code=error_code,
        http_status=http_status,
    )


def _call_asr(transport: SmokeTransport, api_key: str, file_bytes: bytes, timeout_seconds: float) -> SmokeRecord:
    started = time.perf_counter()
    try:
        body, content_type = _multipart_audio(file_bytes)
        response = transport.request(
            "POST",
            "/v1/audio/transcriptions",
            headers=_auth_headers(api_key, content_type),
            body=body,
            timeout_seconds=timeout_seconds,
        )
        payload = _parse_json(response)
        normalized = _normalize_asr(payload)
        return SmokeRecord(
            ASR_MODEL,
            "asr",
            True,
            "completed",
            _latency_ms(started),
            normalized["summary_signal"],
            normalized["usage"],
        )
    except OpenAISmokeError as exc:
        return _error_record(ASR_MODEL, "asr", started, exc.error_code)
    except _ProviderHTTPError as exc:
        return _error_record(ASR_MODEL, "asr", started, exc.error_code, http_status=exc.status_code)
    except _TransportError as exc:
        return _error_record(ASR_MODEL, "asr", started, exc.category)
    except (TimeoutError, socket.timeout):
        return _error_record(ASR_MODEL, "asr", started, "provider_timeout")
    except ssl.SSLError:
        return _error_record(ASR_MODEL, "asr", started, "provider_tls_error")
    except urllib.error.URLError:
        return _error_record(ASR_MODEL, "asr", started, "provider_unavailable")
    except _PayloadError:
        return _error_record(ASR_MODEL, "asr", started, "provider_malformed_response")
    except (ValueError, TypeError):
        return _error_record(ASR_MODEL, "asr", started, "request_invalid")
    except Exception:
        return _error_record(ASR_MODEL, "asr", started, "provider_unavailable")


def _call_vision(transport: SmokeTransport, api_key: str, model: str, image_bytes: bytes, timeout_seconds: float) -> SmokeRecord:
    started = time.perf_counter()
    try:
        body = _vision_payload(model, image_bytes)
        response = transport.request(
            "POST",
            "/v1/responses",
            headers=_auth_headers(api_key, "application/json"),
            body=body,
            timeout_seconds=timeout_seconds,
        )
        payload = _parse_json(response)
        normalized = _normalize_vision(payload)
        return SmokeRecord(model, "vision", True, "completed", _latency_ms(started), normalized["summary_signal"], normalized["usage"])
    except OpenAISmokeError as exc:
        return _error_record(model, "vision", started, exc.error_code)
    except _ProviderHTTPError as exc:
        return _error_record(model, "vision", started, exc.error_code, http_status=exc.status_code)
    except _TransportError as exc:
        return _error_record(model, "vision", started, exc.category)
    except (TimeoutError, socket.timeout):
        return _error_record(model, "vision", started, "provider_timeout")
    except ssl.SSLError:
        return _error_record(model, "vision", started, "provider_tls_error")
    except urllib.error.URLError:
        return _error_record(model, "vision", started, "provider_unavailable")
    except _PayloadError:
        return _error_record(model, "vision", started, "provider_malformed_response")
    except (ValueError, TypeError):
        return _error_record(model, "vision", started, "request_invalid")
    except Exception:
        return _error_record(model, "vision", started, "provider_unavailable")


def run_openai_smoke(
    *,
    api_key: str | None = None,
    confirm: bool = False,
    environ: Mapping[str, str] | None = None,
    transport: SmokeTransport | None = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = 30.0,
    temp_base_dir: str | Path | None = None,
) -> OpenAISmokeReport:
    """Run the explicitly confirmed, four-call OpenAI smoke test.

    ``confirm`` is intentionally separate from credential presence.  The CLI
    supplies it only for ``--openai-smoke`` plus the confirmation flag (or the
    dedicated environment gate).  The catalog and formal provider gate are not
    loaded or consulted here.
    """

    if not confirm:
        raise OpenAISmokeError("confirmation_required")
    values = os.environ if environ is None else environ
    key = _validate_key(api_key if api_key is not None else values.get(OPENAI_API_KEY_ENV))
    timeout = _validate_timeout(timeout_seconds)
    client = transport or UrllibTransport(base_url)

    records: list[SmokeRecord] = []
    run_root: Path | None = None
    with _temporary_inputs(temp_base_dir) as inputs:
        run_root = inputs.root
        try:
            asr_bytes = inputs.wav_path.read_bytes()
            image_bytes = inputs.image_path.read_bytes()
        except OSError:
            raise OpenAISmokeError("input_generation_failed") from None
        records.append(_call_asr(client, key, asr_bytes, timeout))
        for model in VISION_MODELS:
            records.append(_call_vision(client, key, model, image_bytes, timeout))
    # The context manager owns deletion; no path is emitted.
    cleanup_removed = run_root is not None and not run_root.exists()
    return OpenAISmokeReport(tuple(records), cleanup_removed)


def safe_blocked_report(error_code: str) -> dict[str, object]:
    """Return a CLI-safe report for a blocked smoke invocation."""

    allowed = {
        "confirmation_required",
        "credentials_missing",
        "credentials_invalid",
        "invalid_base_url",
        "invalid_timeout",
        "invalid_temp_base_dir",
        "input_generation_failed",
        "cleanup_failed",
        "tls_configuration_invalid",
    }
    code = error_code if error_code in allowed else "smoke_unavailable"
    return {"status": "blocked", "error_code": code}


__all__ = [
    "ASR_MODEL",
    "VISION_MODELS",
    "OPENAI_API_KEY_ENV",
    "SMOKE_CONFIRM_ENV",
    "OpenAISmokeError",
    "OpenAISmokeReport",
    "SMOKE_PROVIDER_FAILURE_EXIT_CODE",
    "SmokeRecord",
    "SmokeTransport",
    "TransportResponse",
    "confirmation_from_environment",
    "run_openai_smoke",
    "safe_blocked_report",
]
