"""Private bounded public-HTTP child. No arbitrary URL or credential inputs."""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, HTTPSHandler

from app.bilibili.public import MAX_PUBLIC_BYTES, valid_bvid
from app.tls import configure_trusted_ca


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_LOOPBACK_PROXY_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _loopback_proxy(value: object) -> str | None:
    """Return ``value`` only if it is a credential-free loopback HTTP proxy.

    The parent already holds a validated ``US_VIDEO_PROXY_URL``; this child
    re-checks the shape so a corrupted payload can never aim the request at an
    arbitrary host. Anything else raises ``ValueError``.
    """

    if not isinstance(value, str) or value != value.strip():
        raise ValueError
    parts = urlsplit(value)
    if (
        parts.scheme != "http"
        or parts.hostname not in _LOOPBACK_PROXY_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
        or parts.port is None  # also raises ValueError for an invalid port
    ):
        raise ValueError
    return value


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read(4097))
        params = payload["params"]
        endpoint = payload["endpoint"]
        raw_proxy = payload.get("proxy")
        proxy = None if raw_proxy is None else _loopback_proxy(raw_proxy)
        if endpoint == "parts" and set(params) == {"bvid"} and valid_bvid(params["bvid"]):
            path = "/x/web-interface/view"
        elif endpoint == "favorites" and set(params) == {"media_id", "pn", "ps"}:
            if not all(isinstance(v, str) and v.isascii() and v.isdecimal() and 1 <= len(v) <= 20 for v in params.values()):
                return 1
            if params["ps"] != "20" or not 1 <= int(params["pn"]) <= 500 or int(params["media_id"]) <= 0:
                return 1
            path = "/x/v3/fav/resource/list"
        else:
            return 1
        url = "https://api.bilibili.com" + path + "?" + urlencode(params)
        # No cookie jar, proxy credentials, redirect, body logging or SDK client.
        # Without a validated loopback proxy the ambient proxy environment is
        # ignored (direct route); with one, only that proxy is used.
        proxies = {} if proxy is None else {"http": proxy, "https": proxy}
        opener = build_opener(ProxyHandler(proxies), _NoRedirect(), HTTPSHandler(context=configure_trusted_ca().ssl_context))
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Accept-Encoding": "identity"})
        with opener.open(request, timeout=14) as response:
            if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                return 1
            raw = response.read(MAX_PUBLIC_BYTES + 1)
            if len(raw) > MAX_PUBLIC_BYTES:
                return 2
        sys.stdout.buffer.write(raw)
        return 0
    except HTTPError as exc:
        # 412 is Bilibili risk control (e.g. a blocked datacenter egress).
        return 3 if exc.code in (412, 429) else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
