"""Bounded fixed-host provider HTTP without credential-bearing request logs.

Use the public transport interface rather than Client.send(), whose informational
HTTP request log includes complete URLs (some providers require secrets in GET).
TLS stays verified, redirects are not followed, and errors remain opaque.
"""

from __future__ import annotations

import json
import ssl
from contextlib import closing
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.external_providers.base import ProviderError


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderError()
        result[key] = value
    return result


def object_json(value: str) -> dict[str, Any]:
    try:
        result = json.loads(value, object_pairs_hook=_unique_object)
    except (ValueError, TypeError):
        raise ProviderError() from None
    if not isinstance(result, dict):
        raise ProviderError()
    return result


class ProviderHTTP:
    MAX_BYTES = 128 * 1024

    def __init__(
        self,
        hosts: frozenset[str],
        *,
        ssl_context: ssl.SSLContext | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._hosts = hosts
        self._transport = transport or httpx.HTTPTransport(
            verify=ssl_context if ssl_context is not None else True,
            retries=0,
        )

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        parsed = urlsplit(url)
        if (
            method not in {"GET", "POST"}
            or parsed.scheme != "https"
            or parsed.hostname not in self._hosts
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ProviderError()
        # MSAL may supply requests-style timeout/verify; this transport owns TLS
        # and enforces a single finite timeout policy instead.
        allowed = {key: kwargs[key] for key in ("params", "data", "headers") if key in kwargs}
        try:
            request = httpx.Request(method, url, **allowed)
            request.extensions["timeout"] = dict.fromkeys(
                ("connect", "read", "write", "pool"), 10.0
            )
            with closing(self._transport.handle_request(request)) as response:
                if response.status_code != 200:
                    raise ProviderError()
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.MAX_BYTES:
                        raise ProviderError()
                    chunks.append(chunk)
                # Preserve only the fixed endpoint path in returned response
                # metadata. Parsers/SDKs do not need a credential-bearing URL.
                safe_request = httpx.Request(method, f"https://{parsed.hostname}{parsed.path}")
                return httpx.Response(
                    200, content=b"".join(chunks), headers=response.headers,
                    request=safe_request,
                )
        except ProviderError:
            raise
        except Exception:
            raise ProviderError() from None

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def json(self, url: str, *, params: dict[str, str]) -> dict[str, Any]:
        return object_json(self.get(url, params=params).text)
