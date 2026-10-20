"""Public metadata: fixed endpoints, bounded child process, no account secrets."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
import sys
from urllib.parse import parse_qsl, urlsplit


MAX_PUBLIC_BYTES = 1024 * 1024
PUBLIC_DEADLINE_SECONDS = 15
MAX_PARTS = 1000
MAX_FAVORITES_PAGES = 500
_BV = re.compile(r"BV[0-9A-Za-z]{10}\Z")
_CID = re.compile(r"[1-9][0-9]{0,19}\Z")
_COLLECTION_QUERY_FIELDS = frozenset({"fid", "ftype", "share_source", "share_medium", "share_plat", "share_session_id", "share_tag", "timestamp", "unique_k", "share_from", "vd_source", "spm_id_from"})


class BilibiliPublicError(ValueError):
    def __init__(self, code: str = "bilibili_unavailable") -> None:
        self.error_code = code
        super().__init__(code)


def valid_bvid(value: object) -> bool:
    return isinstance(value, str) and _BV.fullmatch(value) is not None


def valid_cid(value: object) -> bool:
    return isinstance(value, str) and _CID.fullmatch(value) is not None


def positive_int(value: object, maximum: int) -> bool:
    return type(value) is int and 1 <= value <= maximum


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BilibiliPublicError()
        result[key] = value
    return result


def parse_object(raw: bytes) -> dict:
    if len(raw) > MAX_PUBLIC_BYTES:
        raise BilibiliPublicError("bilibili_limit_exceeded")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise BilibiliPublicError() from None
    if not isinstance(value, dict):
        raise BilibiliPublicError()
    return value


def bounded_title(value: object) -> str | None:
    return value[:1000] if isinstance(value, str) and value else None


@dataclass(frozen=True)
class BilibiliPart:
    bvid: str
    cid: str
    page: int
    title: str | None = None
    duration_sec: int | None = None


@dataclass(frozen=True)
class BilibiliPartMap:
    bvid: str
    title: str | None
    parts: tuple[BilibiliPart, ...]

    def resolve_part(self, cid: str) -> BilibiliPart:
        matches = [part for part in self.parts if part.cid == cid]
        if len(matches) != 1:
            raise BilibiliPublicError("source_unavailable")
        return matches[0]


def parse_video_input(value: str) -> tuple[str, int | None]:
    """Explicit preview only; ordinary local P1 submission remains unchanged."""
    value = value.strip()
    if re.fullmatch(r"[bB][vV][0-9A-Za-z]{10}", value):
        return "BV" + value[2:], None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc not in {"bilibili.com", "www.bilibili.com"}
            or "#" in value
            or any(char.isspace() for char in value)
        ):
            raise ValueError
        match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", parsed.path)
        if match is None:
            raise ValueError
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=50)
        pages = [item for key, item in query if key == "p"]
        if len(pages) > 1 or (pages and not re.fullmatch(r"[1-9][0-9]{0,3}", pages[0])):
            raise ValueError
        requested = int(pages[0]) if pages else None
        if requested is not None and requested > MAX_PARTS:
            raise ValueError
        return match[1], requested
    except (ValueError, TypeError):
        raise BilibiliPublicError("bilibili_invalid_url") from None


def parse_favorites_input(value: str) -> str:
    """Parse only known public favorites aliases into an ID, never a fetch URL."""
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise BilibiliPublicError("bilibili_invalid_favorites_url")
    value = value.strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or "#" in value or any(char.isspace() for char in value):
            raise ValueError
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=30, strict_parsing=True)
        if len({key for key, _ in query}) != len(query) or any(key not in _COLLECTION_QUERY_FIELDS for key, _ in query):
            raise ValueError
        fields = dict(query)
        if "ftype" in fields and fields["ftype"] != "create":
            raise ValueError
        if parsed.netloc == "space.bilibili.com":
            path = re.fullmatch(r"/([1-9][0-9]{0,19})/favlist/?", parsed.path)
            fid = fields.get("fid")
            if path is None or not valid_cid(fid):
                raise ValueError
        elif parsed.netloc in {"bilibili.com", "www.bilibili.com"}:
            path = re.fullmatch(r"/medialist/detail/ml([1-9][0-9]{0,19})/?", parsed.path)
            if path is None or "fid" in fields or "ftype" in fields:
                raise ValueError
            fid = path[1]
        else:
            raise ValueError
        return fid
    except (ValueError, TypeError):
        raise BilibiliPublicError("bilibili_invalid_favorites_url") from None


@dataclass(frozen=True)
class BilibiliFavorite:
    entry_key: str
    bvid: str | None
    canonical_url: str | None
    title: str | None
    available: bool
    unavailable_reason: str | None = None


@dataclass(frozen=True)
class BilibiliFavoritesPage:
    collection_id: str
    title: str | None
    reported_total: int
    page: int
    has_more: bool
    capped: bool
    items: tuple[BilibiliFavorite, ...]
    page_size: int = 20


class BoundedPublicHTTP:
    """Killable boundary: a slow-drip socket cannot outlive the whole-call cap.

    The repository-owned child sends only a fixed endpoint/validated parameters,
    bounds its response before printing and never prints provider error text.
    No thread can continue making requests after a request deadline.

    ``proxy_url`` is the credential-free loopback egress (``US_VIDEO_PROXY_URL``).
    It travels in the child's stdin payload, never its argv or environment, and
    the child re-validates it before use. ``None`` keeps the direct route.
    """

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url

    def get_json(self, endpoint: str, params: dict[str, str]) -> dict:
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "app.bilibili.fetch_public"],
                input=json.dumps({"endpoint": endpoint, "params": params, "proxy": self._proxy_url}).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=PUBLIC_DEADLINE_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise BilibiliPublicError("bilibili_unavailable") from None
        if completed.returncode == 2:
            raise BilibiliPublicError("bilibili_limit_exceeded")
        if completed.returncode == 3:
            raise BilibiliPublicError("bilibili_rate_limited")
        if completed.returncode != 0:
            raise BilibiliPublicError()
        return parse_object(completed.stdout)


class BilibiliPublicClient:
    def __init__(self, http=None, *, proxy_url: str | None = None) -> None:
        self._http = http or BoundedPublicHTTP(proxy_url)

    def get_parts(self, bvid: str) -> BilibiliPartMap:
        if not valid_bvid(bvid):
            raise BilibiliPublicError("bilibili_invalid_url")
        value = self._http.get_json("parts", {"bvid": bvid})
        code = value.get("code")
        if type(code) is int and code == -352:
            raise BilibiliPublicError("bilibili_rate_limited")
        if type(code) is not int or code != 0:
            raise BilibiliPublicError()
        data = value.get("data")
        if not isinstance(data, dict) or data.get("bvid") != bvid:
            raise BilibiliPublicError()
        pages = data.get("pages")
        if not isinstance(pages, list) or not pages:
            raise BilibiliPublicError("source_unavailable")
        if len(pages) > MAX_PARTS:
            raise BilibiliPublicError("bilibili_limit_exceeded")
        parts = []
        cids: set[str] = set()
        for index, row in enumerate(pages, 1):
            if not isinstance(row, dict):
                raise BilibiliPublicError()
            raw_cid = row.get("cid")
            cid = str(raw_cid) if positive_int(raw_cid, 10**20 - 1) else raw_cid
            if not valid_cid(cid) or cid in cids or type(row.get("page")) is not int or row["page"] != index:
                raise BilibiliPublicError()
            duration = row.get("duration")
            if duration is not None and (type(duration) is not int or not 0 <= duration <= 2147483647):
                raise BilibiliPublicError()
            cids.add(cid)
            parts.append(BilibiliPart(bvid, cid, index, bounded_title(row.get("part")), duration))
        return BilibiliPartMap(bvid, bounded_title(data.get("title")), tuple(parts))

    def get_favorites(self, collection_id: str, *, page: int = 1) -> BilibiliFavoritesPage:
        if not valid_cid(collection_id) or not positive_int(page, MAX_FAVORITES_PAGES):
            raise BilibiliPublicError("bilibili_invalid_favorites_url")
        value = self._http.get_json("favorites", {"media_id": collection_id, "pn": str(page), "ps": "20"})
        code = value.get("code")
        if type(code) is not int:
            raise BilibiliPublicError()
        if code == -403:
            raise BilibiliPublicError("bilibili_favorites_private")
        if code == -352:
            raise BilibiliPublicError("bilibili_rate_limited")
        if code != 0:
            raise BilibiliPublicError()
        data = value.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("info"), dict):
            raise BilibiliPublicError()
        info = data["info"]
        # Some upstream versions omit info.id. When present it must agree;
        # neither an upstream URL nor a next-page link chooses our destination.
        reported_id = info.get("id")
        if reported_id is not None and (type(reported_id) is not int or str(reported_id) != collection_id):
            raise BilibiliPublicError()
        count, has_more, rows = info.get("media_count"), data.get("has_more"), data.get("medias")
        if type(count) is not int or not 0 <= count <= 2147483647 or type(has_more) is not bool:
            raise BilibiliPublicError()
        if rows is None and count == 0 and not has_more:
            rows = []
        if not isinstance(rows, list):
            raise BilibiliPublicError()
        if len(rows) > 20:
            raise BilibiliPublicError("bilibili_limit_exceeded")
        if not rows and (has_more or (page == 1 and count > 0)):
            raise BilibiliPublicError()
        items = []
        for index, row in enumerate(rows):
            key = f"{collection_id}:{page}:{index}"
            if not isinstance(row, dict):
                items.append(BilibiliFavorite(key, None, None, None, False, "invalid_video"))
                continue
            title = bounded_title(row.get("title"))
            bvid = row.get("bvid")
            video_type = row.get("type")
            if title in {"已失效视频", "已删除视频"}:
                reason = "deleted"
            elif type(video_type) is not int or video_type != 2:
                reason = "not_video"
            elif not valid_bvid(bvid):
                reason = "invalid_video"
            else:
                reason = None
            available = reason is None
            items.append(BilibiliFavorite(key, bvid if valid_bvid(bvid) else None,
                f"https://www.bilibili.com/video/{bvid}" if available else None,
                title, available, reason))
        return BilibiliFavoritesPage(collection_id, bounded_title(info.get("title")), count,
            page, has_more, page == MAX_FAVORITES_PAGES and has_more, tuple(items))


def build_public_client(proxy_url: str | None = None) -> BilibiliPublicClient:
    """Low-level public client; browser gates are service-owned.

    ``proxy_url`` routes discovery through the loopback video egress; ``None``
    keeps the direct route.
    """
    return BilibiliPublicClient(proxy_url=proxy_url)
