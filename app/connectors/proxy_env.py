"""Child-only egress proxy environment shared by the video connectors.

``US_VIDEO_PROXY_URL`` names the server-local US residential egress.  Only the
yt-dlp and bounded-fetch child processes receive it; the worker process and
every other dependency keep their direct route.  Failure classification maps
yt-dlp stderr onto stable internal codes so raw stderr, proxy values and signed
URLs never reach an exception message.
"""

from __future__ import annotations

import os

_PROXY_AUTH_MARKERS = ("http error 407", "proxy authentication required")
_PROXY_TIMEOUT_MARKERS = ("proxy timeout", "proxy timed out")
_PROXY_UNAVAILABLE_MARKERS = (
    "connection refused",
    "failed to connect to proxy",
    "unable to connect to proxy",
    "cannot connect to proxy",
    "proxy connection failed",
    "proxyerror",
)
_LOOPBACK_NO_PROXY = "127.0.0.1,localhost,::1"


def build_proxy_environment(proxy_url: str | None) -> dict[str, str] | None:
    """Return a child environment routed through ``proxy_url``.

    ``None`` means "no proxy": callers must then pass no ``env`` at all so the
    child keeps inheriting the worker environment.  The returned mapping is a
    copy; ``os.environ`` is never mutated.  Ambient ``ALL_PROXY`` variants are
    removed and ``NO_PROXY`` is replaced with loopback-only values so an
    ambient exclusion cannot bypass the egress.
    """

    if proxy_url is None:
        return None
    environment = os.environ.copy()
    environment.pop("ALL_PROXY", None)
    environment.pop("all_proxy", None)
    environment.update(
        {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "NO_PROXY": _LOOPBACK_NO_PROXY,
            "no_proxy": _LOOPBACK_NO_PROXY,
        }
    )
    return environment


def classify_proxy_failure(stderr: object, *, prefix: str) -> str | None:
    """Map yt-dlp stderr to ``<prefix>_proxy_*`` or ``None``.

    The caller decides whether a proxy was configured; without one these
    markers must not be interpreted as proxy failures.
    """

    message = str(stderr or "").lower()
    if any(marker in message for marker in _PROXY_AUTH_MARKERS):
        return f"{prefix}_proxy_authentication_failed"
    if any(marker in message for marker in _PROXY_TIMEOUT_MARKERS):
        return f"{prefix}_proxy_timeout"
    if any(marker in message for marker in _PROXY_UNAVAILABLE_MARKERS):
        return f"{prefix}_proxy_unavailable"
    return None
