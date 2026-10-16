"""Short-lived HMAC-signed download URLs for temporary ASR audio objects.

Fun-ASR fetches audio by URL. The public route validates one signature that
covers only the object name and expiry, so a URL grants a single temporary
object and never reveals a storage key.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from uuid import uuid4

ASR_AUDIO_PREFIX = "asr-tmp/"
ALLOWED_AUDIO_EXTS = frozenset(
    {"m4a", "webm", "mp3", "mp4", "opus", "ogg", "wav", "aac", "flac"}
)

_NAME_PATTERN = re.compile(
    r"[0-9a-f]{32}\.(?:" + "|".join(sorted(ALLOWED_AUDIO_EXTS)) + r")"
)
_SIGNATURE_CONTEXT = "asr-audio.v1"
_ROUTE_PATH = "/api/v1/asr-audio/"
# Tolerated clock skew between signer and verifier when bounding the expiry.
_CLOCK_SKEW_SECONDS = 60
_MAX_EXPIRES_DIGITS = 12


def _valid_name(name: object) -> bool:
    return isinstance(name, str) and _NAME_PATTERN.fullmatch(name) is not None


def _signature(secret: str, name: str, expires: str) -> str:
    message = f"{_SIGNATURE_CONTEXT}\n{name}\n{expires}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def new_audio_object_name(ext: str) -> str:
    """Return ``<32 hex>.<ext>``; ``ext`` must be an allowed audio extension."""

    if not isinstance(ext, str) or ext not in ALLOWED_AUDIO_EXTS:
        raise ValueError("unsupported audio extension")
    return f"{uuid4().hex}.{ext}"


def audio_object_key(name: str) -> str:
    """Map a validated public object name to its private storage key."""

    if not _valid_name(name):
        raise ValueError("invalid audio object name")
    return f"{ASR_AUDIO_PREFIX}{name}"


def sign_audio_url(
    *,
    base_url: str,
    name: str,
    secret: str,
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    """Return a signed download URL valid for ``ttl_seconds``."""

    if not _valid_name(name):
        raise ValueError("invalid audio object name")
    if not isinstance(secret, str) or not secret:
        raise ValueError("signing secret is required")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    issued = time.time() if now is None else now
    expires = str(int(issued + ttl_seconds))
    sig = _signature(secret, name, expires)
    return f"{base_url.rstrip('/')}{_ROUTE_PATH}{name}?expires={expires}&sig={sig}"


def verify_audio_signature(
    *,
    name: str,
    expires: str,
    sig: str,
    secret: str,
    max_ttl_seconds: int,
    now: float | None = None,
) -> bool:
    """Return True only for an unexpired, untampered, in-bounds signature.

    Never raises: every malformed input is an invalid signature.
    """

    try:
        if not _valid_name(name) or not isinstance(secret, str) or not secret:
            return False
        if (
            not isinstance(expires, str)
            or not expires.isascii()
            or not expires.isdigit()
            or len(expires) > _MAX_EXPIRES_DIGITS
        ):
            return False
        if not isinstance(sig, str) or not sig.isascii():
            return False
        current = time.time() if now is None else now
        expires_at = int(expires)
        if current > expires_at:
            return False
        if expires_at > current + max_ttl_seconds + _CLOCK_SKEW_SECONDS:
            return False
        expected = _signature(secret, name, expires)
        return hmac.compare_digest(expected.encode("ascii"), sig.encode("ascii"))
    except Exception:
        return False
