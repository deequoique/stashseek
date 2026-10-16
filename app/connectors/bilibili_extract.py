"""Isolated metadata-only Bilibili extraction with actual subtitle CID proof.

Only the explicitly registered extractor runs. Provider logs and unrestricted
results never cross this process boundary; the parent also caps time/output.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.extractor.bilibili import BiliBiliIE

from app.connectors.base import SourceChangedError, SourceUnavailableError
from app.ingest.validate import IngestLimitExceeded

_BV = re.compile(r"BV[0-9A-Za-z]{10}")
_CID = re.compile(r"[1-9][0-9]{0,19}")
EXIT_SOURCE_CHANGED = 20
EXIT_SOURCE_UNAVAILABLE = 21
EXIT_TOO_LARGE = 22


class _Discard:
    def write(self, value: object) -> int:
        return len(str(value))

    def flush(self) -> None:
        pass


class _QuietLogger:
    login_subtitles = False

    def debug(self, _message: object) -> None:
        pass

    def warning(self, message: object) -> None:
        if "subtitles are only available when logged in" in str(message).lower():
            self.login_subtitles = True

    def error(self, _message: object) -> None:
        pass


class PinnedBiliBiliIE(BiliBiliIE):
    """Retain yt-dlp's real extraction, attesting its subtitle-call arguments."""

    def __init__(self, downloader, *, bvid: str, cid: str) -> None:
        super().__init__(downloader)
        self.expected_bvid = bvid
        self.expected_cid = cid
        self.subtitle_sources: list[tuple[str, str]] = []
        self.source_mismatch = False

    def _assert_source(self, bvid: object, cid: object) -> tuple[str, str]:
        if (
            not isinstance(bvid, str)
            or isinstance(cid, bool)
            or not isinstance(cid, (str, int))
            or not _CID.fullmatch(str(cid))
        ):
            raise SourceUnavailableError()
        observed = (bvid, str(cid))
        if observed != (self.expected_bvid, self.expected_cid):
            self.source_mismatch = True
            raise SourceChangedError()
        return observed

    def _download_playinfo(self, bvid, cid, headers=None, query=None, fatal=True):
        self._assert_source(bvid, cid)
        return super()._download_playinfo(bvid, cid, headers=headers, query=query, fatal=fatal)

    def _get_subtitles(self, video_id, cid, aid=None):
        observed = self._assert_source(video_id, cid)
        # The proof is recorded only after the real caption lookup returns.
        # No-caption results still execute this exact hook.
        subtitles = super()._get_subtitles(video_id, cid, aid=aid)
        self.subtitle_sources.append(observed)
        return subtitles


def _text(value: object, maximum: int) -> str | None:
    return value[:maximum] if isinstance(value, str) else None


def extract_pinned(
    payload: dict[str, Any], *, ydl_factory=YoutubeDL, extractor_class=PinnedBiliBiliIE
) -> dict[str, Any]:
    """Run real extraction and return a bounded, secret-free metadata projection."""

    bvid, cid, page = payload.get("bvid"), payload.get("cid"), payload.get("page")
    limit, timeout = payload.get("max_bytes"), payload.get("timeout")
    if (
        not isinstance(bvid, str) or not _BV.fullmatch(bvid)
        or not isinstance(cid, str) or not _CID.fullmatch(cid)
        or isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 1000
        or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20_000_000
        or isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 0 < timeout <= 300
    ):
        raise SourceUnavailableError()
    logger = _QuietLogger()
    params = {
        "quiet": True, "no_warnings": True, "logger": logger,
        "noplaylist": True, "skip_download": True, "writesubtitles": True,
        "writeautomaticsub": False, "writethumbnail": False,
        "writeinfojson": False, "writeannotations": False,
        "cachedir": False, "cookiefile": None, "cookiesfrombrowser": None,
        "socket_timeout": float(timeout), "retries": 0, "extractor_retries": 0,
        "ignoreerrors": False, "nocheckcertificate": False,
        "extract_flat": False, "allow_unplayable_formats": False,
    }
    # Library API does not load CLI config. auto_init=False avoids default
    # extractor registration; main separately disables plugin module loading.
    with contextlib.redirect_stdout(_Discard()), contextlib.redirect_stderr(_Discard()):
        with ydl_factory(params, auto_init=False) as ydl:
            ie = extractor_class(ydl, bvid=bvid, cid=cid)
            ydl.add_info_extractor(ie)
            try:
                data = ydl.extract_info(
                    f"https://www.bilibili.com/video/{bvid}?p={page}",
                    download=False, process=False, ie_key=ie.ie_key(),
                )
            except Exception:
                if ie.source_mismatch:
                    raise SourceChangedError() from None
                raise SourceUnavailableError() from None
    if (
        not isinstance(data, dict) or data.get("_type", "video") != "video"
        or data.get("id") not in {bvid, f"{bvid}_p{page}"}
        or not ie.subtitle_sources
        or any(source != (bvid, cid) for source in ie.subtitle_sources)
    ):
        raise SourceUnavailableError()
    subtitles: dict[str, list[dict[str, str]]] = {}
    subtitle_bytes = 0
    groups = data.get("subtitles")
    if isinstance(groups, dict):
        for language, formats in list(groups.items())[:100]:
            if not isinstance(language, str) or len(language) > 64 or language.lower() == "danmaku":
                continue
            if not isinstance(formats, list):
                continue
            for track in formats[:10]:
                if not isinstance(track, dict) or track.get("ext") != "srt":
                    continue
                inline = track.get("data")
                if not isinstance(inline, str):
                    continue
                subtitle_bytes += len(inline.encode("utf-8"))
                if subtitle_bytes > limit:
                    raise IngestLimitExceeded()
                subtitles.setdefault(language, []).append({"ext": "srt", "data": inline})
    chapters = []
    for chapter in (data.get("chapters") or [])[:1000]:
        if not isinstance(chapter, dict):
            continue
        row = {"title": _text(chapter.get("title"), 500)}
        for name in ("start_time", "end_time"):
            number = chapter.get(name)
            if isinstance(number, (float, int)) and not isinstance(number, bool) and 0 <= number <= 31_536_000:
                row[name] = number
        chapters.append(row)
    return {
        "source_bvid": ie.subtitle_sources[0][0],
        "source_cid": ie.subtitle_sources[0][1], "source_page": page,
        "id": bvid, "title": _text(data.get("title"), 1000),
        "uploader": _text(data.get("uploader"), 1000),
        "description": _text(data.get("description"), 50_000),
        "timestamp": data.get("timestamp") if isinstance(data.get("timestamp"), int) else None,
        "duration": data.get("duration") if isinstance(data.get("duration"), (int, float)) else None,
        "thumbnail": _text(data.get("thumbnail"), 2048),
        "tags": [_text(tag, 200) for tag in (data.get("tags") or [])[:100] if isinstance(tag, str)],
        "chapters": chapters, "subtitles": subtitles,
        "login_subtitles": logger.login_subtitles,
    }


def main() -> int:
    # YoutubeDL's API loads plugins even with auto_init=False. The isolated
    # process must disable discovery before constructing its downloader.
    os.environ["YTDLP_NO_PLUGINS"] = "1"
    try:
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise SourceUnavailableError()
        payload = json.loads(raw)
        data = extract_pinned(payload)
        output = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(output) > int(payload["max_bytes"]) * 2 + 250_000:
            raise IngestLimitExceeded()
    except SourceChangedError:
        return EXIT_SOURCE_CHANGED
    except IngestLimitExceeded:
        return EXIT_TOO_LARGE
    except Exception:
        return EXIT_SOURCE_UNAVAILABLE
    sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
