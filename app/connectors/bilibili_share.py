"""Bounded resolution of Bilibili mobile share links (``b23.tv``).

The mobile app shares a short link instead of the canonical video URL.  Only
the fixed short-link hosts are resolved, only redirects to a Bilibili video URL
are accepted, and the result is cached in-process so repeated submissions of
the same share text do not re-hit the network.
"""

from __future__ import annotations

import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

SHARE_HOSTS = frozenset({"b23.tv", "www.b23.tv"})
_TARGET_HOSTS = frozenset({"bilibili.com", "www.bilibili.com", "m.bilibili.com"})
_CACHE_TTL_SECONDS = 3600.0
_CACHE_MAX_ENTRIES = 256
_TIMEOUT_SECONDS = 5.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class ShareLinkError(ValueError):
    """The short link could not be resolved to a supported video URL."""


_cache: dict[str, tuple[float, str]] = {}



def _default_opener():
    """Reuse the deployment's US video egress when it is configured.

    Share links live on Bilibili's infrastructure too, so a datacenter IP that
    needs the proxy for api.bilibili.com must not resolve b23.tv directly.
    """

    try:
        from app.config import get_settings

        proxy = getattr(get_settings(), "us_video_proxy_url", None)
    except Exception:
        proxy = None
    if proxy:
        return build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    return build_opener()

def resolve_share_link(url: str, *, opener=None, timeout: float = _TIMEOUT_SECONDS) -> str:
    """Return the canonical video URL for a b23.tv link, or the input unchanged."""

    host = (urlsplit(url).hostname or "").lower()
    if host not in SHARE_HOSTS:
        return url
    now = time.monotonic()
    cached = _cache.get(url)
    if cached is not None and cached[0] > now:
        return cached[1]
    client = opener or _default_opener()
    final = None
    for method in ("HEAD", "GET"):
        request = Request(url, method=method, headers={"User-Agent": _USER_AGENT})
        try:
            with client.open(request, timeout=timeout) as response:
                final = response.geturl()
            break
        except HTTPError:
            continue
        except (URLError, TimeoutError, OSError):
            raise ShareLinkError("share_unresolved") from None
    if final is None:
        raise ShareLinkError("share_unresolved")
    parts = urlsplit(final)
    if (
        parts.scheme not in {"http", "https"}
        or (parts.hostname or "").lower() not in _TARGET_HOSTS
        or not parts.path.startswith("/video/")
    ):
        raise ShareLinkError("share_unresolved")
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        _cache.clear()
    _cache[url] = (now + _CACHE_TTL_SECONDS, final)
    return final
