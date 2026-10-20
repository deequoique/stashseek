"""Private bounded public-HTTP child. No arbitrary URL or credential inputs."""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, HTTPSHandler

from app.bilibili.public import MAX_PUBLIC_BYTES, valid_bvid
from app.tls import configure_trusted_ca


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read(4097))
        params = payload["params"]
        endpoint = payload["endpoint"]
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
        opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=configure_trusted_ca().ssl_context))
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
        return 3 if exc.code == 429 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
