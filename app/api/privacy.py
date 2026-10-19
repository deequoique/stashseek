"""Public privacy policy page.

The page is static HTML plus one external stylesheet, so it satisfies the app's
``script-src 'self'; style-src 'self'`` policy without any inline style or
script. Only four values are substituted into the template, each HTML-escaped:
the contact email, the recycle-bin retention days, the effective date and the
service origin the page was requested on. The origin comes from the request
(so a transition between two domains needs no code change) and is the only
request-derived value; there is no logic in the template.
"""

from __future__ import annotations

from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response

from app.config import DEFAULT_PRIVACY_CONTACT_EMAIL, DEFAULT_TRASH_RETENTION_DAYS

# Update together with the policy text; the page shows it at the top.
PRIVACY_EFFECTIVE_DATE = "2026-10-19"

_ASSET_DIR = Path(__file__).resolve().parent.parent / "static" / "privacy"
_CSS_MAX_AGE_SECONDS = 3600


@lru_cache(maxsize=None)
def _asset(name: str) -> str:
    return (_ASSET_DIR / name).read_text(encoding="utf-8")


def request_origin(request: Request) -> str:
    """Scheme and host of the request, without a trailing slash.

    Uvicorn's proxy-header handling already sets the scheme behind Caddy and
    the Host header is passed through. The raw header is read instead of
    ``request.url`` because Starlette parses that URL and raises on a
    malformed Host. The result is untrusted text: it is only ever HTML-escaped
    into the page, never used for routing or links.
    """

    scheme = request.scope.get("scheme")
    if scheme not in ("http", "https"):
        scheme = "https"
    host = request.headers.get("host", "").strip()
    if not host:
        server = request.scope.get("server")
        host = str(server[0]) if server and server[0] else "localhost"
        if server and server[1] not in (None, 80 if scheme == "http" else 443):
            host = f"{host}:{server[1]}"
    return f"{scheme}://{host[:255]}".rstrip("/")


def render_privacy_page(
    *,
    contact_email: str,
    retention_days: int,
    service_origin: str,
    effective_date: str = PRIVACY_EFFECTIVE_DATE,
) -> str:
    """Render the policy by plain, escaped placeholder substitution."""

    page = _asset("index.html")
    for placeholder, value in (
        ("{{contact_email}}", contact_email),
        ("{{retention_days}}", str(retention_days)),
        ("{{service_origin}}", service_origin),
        ("{{effective_date}}", effective_date),
    ):
        page = page.replace(placeholder, escape(value, quote=True))
    return page


def register_privacy_routes(app: FastAPI, settings: Any | None = None) -> None:
    """Mount ``GET /privacy`` and its stylesheet.

    Call this before any SPA catch-all so the fixed paths are not shadowed.
    Settings doubles that lack the two values fall back to the defaults.
    """

    contact_email = str(
        getattr(settings, "privacy_contact_email", DEFAULT_PRIVACY_CONTACT_EMAIL)
    )
    retention_days = int(
        getattr(settings, "trash_retention_days", DEFAULT_TRASH_RETENTION_DAYS)
    )
    stylesheet = _asset("privacy.css")

    # The security middleware sets ``Cache-Control: no-store`` on every HTML
    # response, so a policy edit is never served stale and the per-request
    # service origin is never cached across hosts; only the CSS is cached.
    # The trailing-slash form is explicit because a later SPA catch-all would
    # match it before Starlette's slash redirect could run.
    @app.api_route("/privacy", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/privacy/", methods=["GET", "HEAD"], include_in_schema=False)
    def privacy_page(request: Request) -> Response:
        page = render_privacy_page(
            contact_email=contact_email,
            retention_days=retention_days,
            service_origin=request_origin(request),
        )
        return Response(page, media_type="text/html; charset=utf-8")

    @app.api_route(
        "/privacy/privacy.css", methods=["GET", "HEAD"], include_in_schema=False
    )
    def privacy_stylesheet() -> Response:
        return Response(
            stylesheet,
            media_type="text/css; charset=utf-8",
            headers={"Cache-Control": f"public, max-age={_CSS_MAX_AGE_SECONDS}"},
        )
