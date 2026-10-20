"""Authenticated read-only discovery, proof admission and owned source opening."""

from __future__ import annotations

import time
from dataclasses import asdict
from sqlalchemy import select

from app.bilibili.public import BilibiliPublicError, BilibiliPublicClient, parse_favorites_input, parse_video_input
from app.bilibili.refs import PartRefError, PartRefSigner
from app.ingest.submission import ItemReference, PreparedBatch, PreparedItem, SaveItemResult
from app.models import ContentItem


# Source clicks are a navigation, not a discovery operation: cache the
# BV -> part map briefly so repeated clicks do not hammer api.bilibili.com.
_PARTS_CACHE_TTL_SECONDS = 300.0
_PARTS_CACHE_MAX_ENTRIES = 256
# Only these failures say "the provider/admission is unavailable right now";
# every other code (notably source_unavailable for a vanished CID) is real.
_TRANSIENT_OPEN_CODES = frozenset({"bilibili_rate_limited", "bilibili_busy", "bilibili_unavailable"})


class BilibiliService:
    def __init__(self, session_factory, submission, public: BilibiliPublicClient, signer: PartRefSigner, gate) -> None:
        self._factory = session_factory
        self._submission = submission
        self._public = public
        self._signer = signer
        self._gate = gate
        self._parts_cache: dict[str, tuple[float, object]] = {}

    def _cached_parts(self, bvid: str):
        now = time.monotonic()
        cached = self._parts_cache.get(bvid)
        if cached is not None and cached[0] > now:
            return cached[1]
        if len(self._parts_cache) >= _PARTS_CACHE_MAX_ENTRIES:
            self._parts_cache = {
                key: value for key, value in self._parts_cache.items() if value[0] > now
            }
            if len(self._parts_cache) >= _PARTS_CACHE_MAX_ENTRIES:
                self._parts_cache.pop(next(iter(self._parts_cache)))
        with self._gate.upstream():
            parts = self._public.get_parts(bvid)
        self._parts_cache[bvid] = (now + _PARTS_CACHE_TTL_SECONDS, parts)
        return parts

    def preview_parts(self, scope, url: str, *, page: int | None = None) -> dict:
        bvid, requested = parse_video_input(url)
        if page is not None and (type(page) is not int or not 1 <= page <= 50):
            raise BilibiliPublicError("bilibili_invalid_url")
        self._gate.admit(scope.app_user_id)
        with self._gate.upstream():
            part_map = self._public.get_parts(bvid)
        if requested is not None and requested > len(part_map.parts):
            raise BilibiliPublicError("source_unavailable")
        local_page = page if page is not None else ((requested - 1) // 20 + 1 if requested else 1)
        start = (local_page - 1) * 20
        if start >= len(part_map.parts) and local_page != 1:
            raise BilibiliPublicError("bilibili_invalid_url")
        with self._factory() as db:
            has_legacy = db.scalar(select(ContentItem.id).where(
                ContentItem.user_id == scope.app_user_id,
                ContentItem.platform == "bilibili", ContentItem.platform_id == bvid,
                ContentItem.bilibili_cid.is_(None),
                ContentItem.deleted_at.is_(None),
            ).limit(1)) is not None
        parts = []
        for part in part_map.parts[start:start + 20]:
            proof, expires_at = self._signer.issue_with_expiry(scope.app_user_id, part)
            parts.append({
                "part_key": f"{part.bvid}:{part.cid}", "part_ref": proof,
                "expires_at": expires_at, "page": part.page, "title": part.title,
                "duration_sec": part.duration_sec, "selectable": True,
                "unavailable_reason": None, "has_unverified_legacy_item": has_legacy,
            })
        return {
            "bvid": bvid, "title": part_map.title, "page": local_page,
            "page_size": 20, "total_parts": len(part_map.parts),
            "has_more": start + 20 < len(part_map.parts), "requested_page": requested,
            "parts": parts,
        }

    def preview_favorites(self, scope, url: str, *, page: int = 1) -> dict:
        collection_id = parse_favorites_input(url)
        if type(page) is not int or not 1 <= page <= 500:
            raise BilibiliPublicError("bilibili_invalid_favorites_url")
        self._gate.admit(scope.app_user_id)
        with self._gate.upstream():
            result = self._public.get_favorites(collection_id, page=page)
        # No DB, per-video part discovery, queue or persistent preview job.
        return asdict(result)

    def submit_parts(self, scope, selections, *, why_saved, request_key, publish_budget_seconds):
        prepared = []
        for index, selection in enumerate(selections):
            try:
                part = self._signer.verify(scope.app_user_id, selection.part_ref)
            except PartRefError as exc:
                prepared.append(PreparedItem(index, failure=SaveItemResult(
                    f"A{index + 1}", index, exc.error_code, safe_error_code=exc.error_code,
                )))
                continue
            reference = ItemReference(
                "bilibili", f"{part.bvid}_cid{part.cid}",
                f"https://www.bilibili.com/video/{part.bvid}?p={part.page}",
                bilibili_bvid=part.bvid, bilibili_cid=part.cid, bilibili_page=part.page,
                allow_legacy_overlap=selection.allow_legacy_overlap,
            )
            prepared.append(PreparedItem(index, reference=reference))
        return self._submission.submit_prepared(
            scope, PreparedBatch(tuple(prepared)), why_saved=why_saved,
            request_key=request_key, publish_budget_seconds=publish_budget_seconds,
        )

    def open_source(self, scope, item_public_id: str, *, timestamp: int = 0) -> str:
        if type(timestamp) is not int or not 0 <= timestamp <= 2147483647:
            raise BilibiliPublicError("bilibili_invalid_url")
        with self._factory() as db:
            source = db.execute(select(
                ContentItem.bilibili_bvid,
                ContentItem.bilibili_cid,
                ContentItem.bilibili_page,
            ).where(
                ContentItem.public_id == item_public_id,
                ContentItem.user_id == scope.app_user_id, ContentItem.deleted_at.is_(None),
            )).one_or_none()
            if source is None:
                raise BilibiliPublicError("not_found")
            bvid, cid, stored_page = source
        # Unverified old records retain provider-direct source links; this
        # verified resolver cannot claim that an unpinned source is a CID.
        if bvid is None or cid is None:
            raise BilibiliPublicError("source_unavailable")
        try:
            self._gate.admit(scope.app_user_id)
            part = self._cached_parts(bvid).resolve_part(cid)
            page = part.page
        except BilibiliPublicError as exc:
            # A navigation must not end on a JSON error page, so a transient
            # upstream or admission failure falls back to the stored page: the
            # last attested position, which still opens the video. Any other
            # failure (e.g. the CID no longer exists) must not silently open
            # a different part, so it propagates.
            if (
                exc.error_code not in _TRANSIENT_OPEN_CODES
                or type(stored_page) is not int
                or not 1 <= stored_page <= 1000
            ):
                raise
            page = stored_page
        # Repeat ownership/deletion and immutable pin check after network I/O.
        with self._factory() as db:
            still_owned = db.scalar(select(ContentItem.id).where(
                ContentItem.public_id == item_public_id, ContentItem.user_id == scope.app_user_id,
                ContentItem.deleted_at.is_(None), ContentItem.bilibili_bvid == bvid,
                ContentItem.bilibili_cid == cid,
            ))
            if still_owned is None:
                raise BilibiliPublicError("not_found")
        return f"https://www.bilibili.com/video/{bvid}?p={page}&t={timestamp}"


def build_bilibili_service(settings, factory, submission) -> BilibiliService:
    import redis
    from app.bilibili.gate import RedisPublicGate
    from app.bilibili.public import build_public_client

    client = redis.Redis.from_url(getattr(settings, "redis_url", "redis://127.0.0.1:6379/0"), socket_connect_timeout=1, socket_timeout=1)
    return BilibiliService(factory, submission, build_public_client(getattr(settings, "us_video_proxy_url", None)), PartRefSigner(settings.web_auth_secret), RedisPublicGate(client, settings.web_auth_secret))
