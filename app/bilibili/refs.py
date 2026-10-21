"""Purpose-separated short-lived part proofs bound to the authenticated owner."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.bilibili.public import BilibiliPart, positive_int, valid_bvid, valid_cid


class PartRefError(ValueError):
    def __init__(self, code: str = "part_ref_invalid") -> None:
        self.error_code = code
        super().__init__(code)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, UnicodeError):
        raise PartRefError() from None


class PartRefSigner:
    TTL_SECONDS = 30 * 60

    def __init__(self, secret: str, *, clock=time.time) -> None:
        if not isinstance(secret, str) or len(secret) < 32:
            raise ValueError("part signing secret is required")
        self._key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"stashseek/bilibili-part-ref/v1").derive(secret.encode())
        self._clock = clock

    def _mac(self, tenant_id: int, payload: bytes) -> bytes:
        if type(tenant_id) is not int or tenant_id <= 0:
            raise PartRefError()
        return hmac.digest(self._key, b"bilibili-part/v1\0" + str(tenant_id).encode() + b"\0" + payload, hashlib.sha256)

    def issue(self, tenant_id: int, part: BilibiliPart) -> str:
        return self.issue_with_expiry(tenant_id, part)[0]

    def issue_with_expiry(self, tenant_id: int, part: BilibiliPart) -> tuple[str, int]:
        if not valid_bvid(part.bvid) or not valid_cid(part.cid) or not positive_int(part.page, 1000):
            raise PartRefError()
        expiry = int(self._clock()) + self.TTL_SECONDS
        payload = json.dumps({"v": 1, "bvid": part.bvid, "cid": part.cid, "page": part.page, "expires_at": expiry}, sort_keys=True, separators=(",", ":")).encode()
        return _encode(payload) + "." + _encode(self._mac(tenant_id, payload)), expiry

    def verify(self, tenant_id: int, ref: str) -> BilibiliPart:
        if not isinstance(ref, str) or not 1 <= len(ref) <= 1024:
            raise PartRefError()
        try:
            encoded, signature = ref.split(".")
            payload = _decode(encoded)
            mac = _decode(signature)
            if not hmac.compare_digest(self._mac(tenant_id, payload), mac):
                raise PartRefError()
            data = json.loads(payload)
            if not isinstance(data, dict) or set(data) != {"v", "bvid", "cid", "page", "expires_at"}:
                raise PartRefError()
            if type(data["v"]) is not int or data["v"] != 1 or not valid_bvid(data["bvid"]) or not valid_cid(data["cid"]) or not positive_int(data["page"], 1000) or type(data["expires_at"]) is not int:
                raise PartRefError()
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
            if canonical != payload:
                raise PartRefError()
            now = int(self._clock())
            if data["expires_at"] <= now:
                raise PartRefError("part_ref_expired")
            if data["expires_at"] > now + self.TTL_SECONDS:
                raise PartRefError()
            return BilibiliPart(data["bvid"], data["cid"], data["page"])
        except PartRefError:
            raise
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise PartRefError() from None
