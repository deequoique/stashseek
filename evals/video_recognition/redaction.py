"""Fail-closed redaction for benchmark reports and diagnostics."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_BLOCKED_KEYS = {
    "prompt",
    "query",
    "question",
    "history",
    "answer",
    "model_output",
    "tool_payload",
    "tool_arguments",
    "raw_response",
    "response_body",
    "stderr",
    "stdout",
    "exception_message",
    "provider_body",
    "provider_response",
    "credential",
    "credentials",
    "api_key",
    "access_token",
    "refresh_token",
    "cookie",
    "cookies",
    "authorization",
    "signed_url",
    "url",
    "uri",
    "page_url",
    "media_bytes",
    "audio_bytes",
    "frame_bytes",
    "payload",
    "temporary_path",
    "source_path",
}
_URL_RE = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)(?:bearer\s+|(?:sk|key|token|secret)[-_:=\s]+)[A-Za-z0-9_./+=:-]{8,}"
)
_TEMP_PATH_RE = re.compile(r"(?:/private/var|/tmp/|/var/folders/|\\Temp\\|\.runtime/|\.eval-results/)[^\s\"']*")


def _safe_string(value: str, secrets: tuple[str, ...]) -> str:
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = _URL_RE.sub("[REDACTED_URL]", result)
    result = _SECRET_RE.sub("[REDACTED_SECRET]", result)
    result = _TEMP_PATH_RE.sub("[REDACTED_PATH]", result)
    return result


def redact_report(value: Any, secrets_to_remove: Iterable[str] = ()) -> Any:
    """Recursively project safe counters while omitting sensitive fields.

    The function is intentionally conservative: unknown mappings are retained
    only when their keys are not on the blocklist, and all strings are scanned
    for URLs/secrets even when they occur under a safe key.
    """

    secrets = tuple(str(secret) for secret in secrets_to_remove if str(secret))
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            key_normalized = key_text.casefold()
            if key_normalized in _BLOCKED_KEYS:
                continue
            if any(marker in key_normalized for marker in ("signed_url", "secret", "token", "credential", "stderr", "raw_response", "provider_body", "media_bytes", "source_path")):
                continue
            result[key_text] = redact_report(item, secrets)
        return result
    if isinstance(value, list):
        return [redact_report(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [redact_report(item, secrets) for item in value]
    if isinstance(value, str):
        return _safe_string(value, secrets)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _safe_string(str(value), secrets)


def safe_error_projection(exc: BaseException, *, error_code: str, phase: str, retryable: bool = False) -> dict[str, object]:
    """Project an exception to safe class metadata only."""

    return {
        "error_code": error_code,
        "error_class": type(exc).__name__,
        "phase": phase,
        "retryable": retryable,
    }


def assert_redacted(value: Any, *, forbidden_values: Iterable[str] = ()) -> None:
    """Raise if a report still contains an obvious secret/content sentinel."""

    forbidden = tuple(str(item) for item in forbidden_values if str(item))

    def walk(item: Any, path: str = "report") -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                key_normalized = str(key).casefold()
                if key_normalized in _BLOCKED_KEYS or any(marker in key_normalized for marker in ("token", "secret", "credential", "stderr", "raw_response", "provider_body", "media_bytes")):
                    raise ValueError(f"blocked report field at {path}.{key}")
                walk(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")
        elif isinstance(item, str):
            if _URL_RE.search(item) or _SECRET_RE.search(item) or any(value in item for value in forbidden):
                raise ValueError(f"sensitive report value at {path}")

    walk(value)
