"""Browser-safe schemas for library, ingestion, and transcript routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.limits import MAX_WHY_SAVED_CHARS


Lifecycle = Literal[
    "archived", "ready", "needs_action", "failed", "processing", "queued"
]
Stage = Literal[
    "queued",
    "fetching",
    "transcribing",
    "chunking",
    "embedding",
    "ready",
    "needs_action",
    "failed",
    "archived",
]


class BrowserModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ErrorResponse(BrowserModel):
    code: str
    message: str


class ChapterResponse(BaseModel):
    """Normalized public subset of connector-provided chapter metadata."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    start: float | None = None
    end: float | None = None
    start_time: float | None = None
    end_time: float | None = None
    start_sec: float | None = None
    end_sec: float | None = None


class LibraryItemResponse(BrowserModel):
    public_id: str
    platform: str
    kind: str
    url: str
    title: str | None
    author: str | None
    published_at: datetime | None
    duration_sec: int | None
    lang: str | None
    description: str | None
    tags: tuple[str, ...]
    chapters: tuple[ChapterResponse, ...]
    cover_url: str | None
    saved_at: datetime
    why_saved: str | None
    text_source: str
    lifecycle: Lifecycle
    error_code: str | None
    available_actions: tuple[str, ...]
    latest_dispatch_public_id: str | None
    summary: str | None = None
    # Read-only processing step derived from the internal ingest state. The
    # service always supplies both values. ``default_factory`` (unlike a plain
    # default) keeps ``stage`` out of the schema's ``default`` key, so the
    # generated TypeScript field stays optional, like ``page_qa``.
    stage: Stage = Field(default_factory=lambda: "queued")
    stage_updated_at: datetime | None = None


class LibraryPageResponse(BrowserModel):
    items: tuple[LibraryItemResponse, ...]
    total: int
    page: int
    page_size: int
    is_true_first_empty: bool


class BatchSaveRequest(BrowserModel):
    urls: list[Annotated[str, Field(min_length=1, max_length=2048)]] = Field(
        min_length=1,
        max_length=10,
    )
    why_saved: str | None = Field(default=None, max_length=MAX_WHY_SAVED_CHARS)


class BatchItemResponse(BrowserModel):
    result_id: str
    input_index: int
    status: Literal[
        "queued",
        "already_exists",
        "unsupported_url",
        "invalid_url",
        "queue_unavailable",
        "create_failed",
        "quota_exceeded",
    ]
    item_public_id: str | None = None
    lifecycle: Lifecycle | None = None
    safe_error_code: str | None = None


class BatchSaveResponse(BrowserModel):
    results: tuple[BatchItemResponse, ...]


class WhySavedRequest(BrowserModel):
    why_saved: str | None = Field(default=None, max_length=MAX_WHY_SAVED_CHARS)


class DispatchResponse(BrowserModel):
    public_id: str
    item_public_id: str
    attempt: int
    state: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class TranscriptBlockResponse(BrowserModel):
    ordinal: int
    start_sec: float
    end_sec: float
    text: str
    source_url: str


class TranscriptPageResponse(BrowserModel):
    blocks: tuple[TranscriptBlockResponse, ...]
    next_cursor: str | None
