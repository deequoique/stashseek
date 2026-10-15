"""Connector wrapper which turns a ``NeedsASR`` result into a transcript.

``process_item`` accepts any object with the connector interface.  The ASR
worker wraps the platform connector so that metadata and caption handling stay
unchanged and only the "no caption track" case runs transcription.
"""

from __future__ import annotations

from typing import Any

from app.asr.transcriber import AsrTranscriber, transcript_to_text_result
from app.asr.types import AsrError
from app.connectors.base import ItemMeta, NeedsASR, NeedsExtension, TextResult


class AsrFallbackConnector:
    def __init__(self, inner: Any, transcriber: AsrTranscriber, *, platform: str) -> None:
        self._inner = inner
        self._transcriber = transcriber
        self.platform = platform
        self._meta: dict[str, ItemMeta] = {}

    def match(self, url: str) -> str | None:
        return self._inner.match(url)

    def fetch_meta(self, platform_id: str) -> ItemMeta:
        meta = self._inner.fetch_meta(platform_id)
        if meta is not None:
            self._meta[platform_id] = meta
        return meta

    def fetch_text(self, platform_id: str) -> TextResult | NeedsExtension | NeedsASR:
        result = self._inner.fetch_text(platform_id)
        if not isinstance(result, NeedsASR):
            return result
        meta = self._meta.get(platform_id) or self.fetch_meta(platform_id)
        url = getattr(meta, "url", None)
        if not url:
            raise AsrError("asr_audio_unavailable")
        transcript = self._transcriber.transcribe(
            platform=self.platform,
            url=url,
            duration_sec=meta.duration_sec,
        )
        return transcript_to_text_result(transcript)
