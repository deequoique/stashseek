"""Bounded, redacted HTTP JSON helper and parsing utilities shared by the
ASR provider clients.

Failures are classified into the two internal ASR errors and never carry the
request URL, headers, request body, response body or provider message.  Every
exception is raised ``from None`` so a chained ``httpx`` error (whose text can
contain a signed URL) cannot reach logs through ``__cause__``.
"""

from __future__ import annotations

import json
import logging
import math
import ssl
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import httpx

from app.asr.types import AsrProtocolError, AsrProviderUnavailable
from app.tls import configure_trusted_ca

_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_CAP_SECONDS = 8.0


class _RedactUrlQuery(logging.Filter):
    """Strip credentials, query and fragment from URLs in httpx log records.

    httpx logs every request at INFO as ``HTTP Request: GET <full url>``, and a
    worker running with ``--loglevel=INFO`` would persist the provider's
    pre-signed result URL (its query carries the signature that grants access
    to the transcript).  Method, host, path and status stay visible.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                str(arg.copy_with(query=None, fragment=None, userinfo=b""))
                if isinstance(arg, httpx.URL)
                else arg
                for arg in record.args
            )
        return True


def _install_httpx_log_redaction() -> None:
    logger = logging.getLogger("httpx")
    if not any(isinstance(item, _RedactUrlQuery) for item in logger.filters):
        logger.addFilter(_RedactUrlQuery())


_install_httpx_log_redaction()


class _ResponseTooLarge(AsrProtocolError):
    pass


def finite_number(value: Any) -> float:
    """Return a finite float or raise :class:`AsrProtocolError`."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AsrProtocolError("invalid_number")
    result = float(value)
    if not math.isfinite(result):
        raise AsrProtocolError("invalid_number")
    return result


def optional_seconds(value: Any, *, scale: float = 1.0) -> float | None:
    """Best-effort non-negative duration; ``None`` when absent or unusable."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value) / scale
    return result if math.isfinite(result) and result >= 0 else None


def require_dict(value: Any) -> dict:
    if not isinstance(value, dict):
        raise AsrProtocolError("invalid_structure")
    return value


@contextmanager
def client_scope(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """Yield the injected client, or own a verified-CA client for one call."""

    if client is not None:
        yield client
        return
    # Redirects are never followed: a provider response must not steer the
    # request (and its credentials) to another host.
    owned = httpx.Client(verify=_trusted_ssl_context(), follow_redirects=False)
    try:
        yield owned
    finally:
        owned.close()


def _trusted_ssl_context() -> ssl.SSLContext:
    return configure_trusted_ca().ssl_context


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise _ResponseTooLarge("response_too_large")
    total = 0
    chunks: list[bytes] = []
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise _ResponseTooLarge("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None,
    json_body: Any,
    data: Mapping[str, Any] | None,
    files: Any,
    timeout: httpx.Timeout | float,
    max_response_bytes: int,
) -> tuple[int, bytes | None]:
    with client.stream(
        method,
        url,
        headers=headers,
        json=json_body,
        data=data,
        files=files,
        timeout=timeout,
    ) as response:
        status = response.status_code
        if 200 <= status < 300:
            return status, _read_bounded(response, max_response_bytes)
        return status, None


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    json_body: Any = None,
    data: Mapping[str, Any] | None = None,
    files: Any = None,
    timeout: httpx.Timeout | float,
    max_response_bytes: int,
    attempts: int,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Send one request with bounded retries and return the parsed JSON body.

    Transport errors, 429 and 5xx are retried with exponential backoff up to
    ``attempts`` total tries.  Every other non-2xx status (including 401/403)
    is an immediate :class:`AsrProviderUnavailable`.  An oversized or
    non-JSON 2xx body is an :class:`AsrProtocolError` and is never retried.
    """

    for attempt in range(attempts):
        try:
            status, body = _send(
                client,
                method,
                url,
                headers=headers,
                json_body=json_body,
                data=data,
                files=files,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
            )
        except _ResponseTooLarge:
            raise AsrProtocolError("response_too_large") from None
        except httpx.InvalidURL:
            raise AsrProtocolError("invalid_url") from None
        except (httpx.HTTPError, OSError):
            status, body = None, None
        if body is not None:
            try:
                return json.loads(body)
            except (UnicodeDecodeError, ValueError):
                raise AsrProtocolError("invalid_json") from None
        retryable = status is None or status == 429 or 500 <= status <= 599
        if not retryable:
            raise AsrProviderUnavailable(f"http_{status}")
        if attempt + 1 >= attempts:
            raise AsrProviderUnavailable(
                "transport_failed" if status is None else f"http_{status}"
            )
        sleep(min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * 2**attempt))
    raise AsrProviderUnavailable("transport_failed")
