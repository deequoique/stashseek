"""Browser-only source links derived from owned segment provenance.

Generic Agent/channel citations remain provider-direct. Only this adapter may
replace a source with a browser resolver; a URL never supplies content identity.
"""

from __future__ import annotations

import math
from urllib.parse import quote, urlsplit

from sqlalchemy import select

from app.models import ContentItem, Segment


def item_source_url(public_id: str, seconds: float = 0) -> str:
    timestamp = min(2_147_483_647, max(0, math.floor(seconds))) if math.isfinite(seconds) else 0
    return f"/api/v1/library/items/{quote(public_id, safe='')}/source?t={timestamp}"


def has_verified_bilibili_source(
    item: object, *, bilibili_public_enabled: bool = True
) -> bool:
    """Whether the item may use the server-side Bilibili source resolver.

    When the public Bilibili feature is switched off the resolver route answers
    ``bilibili_disabled``; callers must fall back to the provider URL instead of
    handing browsers a link that can only fail.
    """

    if not bilibili_public_enabled:
        return False
    return (
        getattr(item, "platform", None) == "bilibili"
        and bool(getattr(item, "bilibili_bvid", None))
        and bool(getattr(item, "bilibili_cid", None))
        and bool(getattr(item, "bilibili_page", None))
    )


def _positive_id(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def project_browser_sources(
    db, user_id: int, sources: list[dict], *, bilibili_public_enabled: bool = True
) -> list[dict | None]:
    """Project in bounded batches, retaining input positions for transcript turns.

    Historical absolute links without provable provenance stay honest legacy
    links. Relative/model-authored resolver paths never become trusted links.
    Provenance that no longer belongs to this tenant (deleted item, replaced
    segments, another tenant) keeps its original provider URL but is never
    rewritten to this tenant's resolver.
    """
    projected: list[dict | None] = []
    candidates: dict[int, tuple[int | None, int]] = {}
    for source in sources:
        title, excerpt, url = (source.get(name) for name in ("title", "excerpt", "url"))
        if not all(isinstance(value, str) for value in (title, excerpt, url)):
            projected.append(None)
            continue
        try:
            parsed = urlsplit(url)
            safe = parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            safe = False
        if not safe:
            projected.append(None)
            continue
        start = source.get("start_sec")
        seconds = float(start) if type(start) in {int, float} and math.isfinite(start) and start >= 0 else None
        projected.append({"title": title, "excerpt": excerpt, "url": url, "start_sec": seconds})
        # The hostname decides whether a lookup is useful, never which CID or
        # item to authorize. Exact ownership comes from the segment join below.
        segment_id = _positive_id(source.get("segment_id"))
        if (
            bilibili_public_enabled
            and parsed.hostname in {"www.bilibili.com", "bilibili.com"}
            and segment_id is not None
        ):
            candidates[len(projected) - 1] = (_positive_id(source.get("item_id")), segment_id)

    owned: dict[int, tuple[int, str, bool]] = {}
    segment_ids = sorted({segment_id for _, segment_id in candidates.values()})
    for offset in range(0, len(segment_ids), 500):
        rows = db.execute(
            select(
                Segment.id, ContentItem.id, ContentItem.public_id,
                ContentItem.platform, ContentItem.bilibili_bvid,
                ContentItem.bilibili_cid, ContentItem.bilibili_page,
            )
            .join(ContentItem, ContentItem.id == Segment.item_id)
            .where(
                Segment.id.in_(segment_ids[offset:offset + 500]),
                ContentItem.user_id == user_id,
                ContentItem.deleted_at.is_(None),
            )
        )
        for segment_id, item_id, public_id, platform, bvid, cid, page in rows:
            owned[segment_id] = (item_id, public_id, platform == "bilibili" and bool(bvid and cid and page))
    for index, (expected_item, segment_id) in candidates.items():
        row = owned.get(segment_id)
        # A deleted item, a reprocessed item whose old segments were replaced,
        # or a segment owned by another tenant cannot be projected onto this
        # tenant's resolver. Keep the original provider URL instead of
        # dropping the citation: history must survive reprocessing and trash.
        if row is None or (expected_item is not None and row[0] != expected_item):
            continue
        if row[2]:
            value = projected[index]
            if value is not None:
                value["url"] = item_source_url(row[1], value["start_sec"] or 0)
    return projected
