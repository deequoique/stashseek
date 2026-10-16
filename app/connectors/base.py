"""Stable connector contract and normalized content types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class TransientFetchError(RuntimeError):
    """A platform failure which is safe to retry for one item."""


class SourceChangedError(RuntimeError):
    """The actual extracted source differs from the immutable selected source."""

    def __init__(self) -> None:
        super().__init__("source_changed")


class SourceUnavailableError(RuntimeError):
    """The selected source cannot currently be verified safely."""

    def __init__(self) -> None:
        super().__init__("source_unavailable")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class ItemMeta:
    platform_id: str
    url: str
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    duration_sec: int | None = None
    lang: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    cover_url: str | None = None
    # Internal extraction evidence, never inferred from an ordinal or URL.
    bilibili_bvid: str | None = None
    bilibili_cid: str | None = None
    bilibili_page: int | None = None


@dataclass(frozen=True)
class TextResult:
    raw_body: bytes
    cues: list[Cue]
    source: str
    lang: str
    format: str = "json3"


@dataclass(frozen=True)
class NeedsExtension:
    reason: str = "authenticated page capture required"


@dataclass(frozen=True)
class NeedsASR:
    reason: str = "no caption track available"


class Connector(Protocol):
    platform: str

    def match(self, url: str) -> str | None: ...
    def fetch_meta(self, platform_id: str) -> ItemMeta: ...
    def fetch_text(self, platform_id: str) -> TextResult | NeedsExtension | NeedsASR: ...
