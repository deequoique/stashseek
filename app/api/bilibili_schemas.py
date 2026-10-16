"""Canonical bounded browser contracts for public parts and signed import."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool

from app.api.library_schemas import BrowserModel, Lifecycle
from app.limits import MAX_WHY_SAVED_CHARS


class BilibiliPartsPreviewRequest(BrowserModel):
    url: str = Field(min_length=1, max_length=2048)
    page: Annotated[int, Field(strict=True, ge=1, le=50)] | None = None


class BilibiliPartPreview(BrowserModel):
    part_key: str = Field(max_length=33)
    part_ref: str | None = Field(default=None, max_length=1024)
    expires_at: int | None = Field(default=None, ge=1)
    page: int = Field(ge=1, le=1000)
    title: str | None = Field(default=None, max_length=1000)
    duration_sec: int | None = Field(default=None, ge=0, le=2147483647)
    selectable: bool
    unavailable_reason: Literal["source_unavailable"] | None = None
    has_unverified_legacy_item: bool = False


class BilibiliPartsPreviewResponse(BrowserModel):
    bvid: str = Field(min_length=12, max_length=12)
    title: str | None = Field(default=None, max_length=1000)
    page: int = Field(ge=1, le=50)
    page_size: Literal[20] = 20
    total_parts: int = Field(ge=0, le=1000)
    has_more: bool
    requested_page: int | None = Field(default=None, ge=1, le=1000)
    parts: tuple[BilibiliPartPreview, ...] = Field(max_length=20)


class BilibiliPartSelection(BrowserModel):
    part_ref: str = Field(min_length=1, max_length=1024)
    allow_legacy_overlap: StrictBool = False


class BilibiliPartsBatchRequest(BrowserModel):
    selections: list[BilibiliPartSelection] = Field(min_length=1, max_length=10)
    why_saved: str | None = Field(default=None, max_length=MAX_WHY_SAVED_CHARS)


class BilibiliPartSaveResult(BrowserModel):
    result_id: str
    input_index: int
    status: Literal["queued", "already_exists", "unsupported_url", "invalid_url", "queue_unavailable", "create_failed", "quota_exceeded", "part_ref_invalid", "part_ref_expired", "legacy_identity_unknown"]
    item_public_id: str | None = None
    lifecycle: Lifecycle | None = None
    safe_error_code: str | None = None


class BilibiliPartsBatchResponse(BrowserModel):
    results: tuple[BilibiliPartSaveResult, ...]


class BilibiliFavoritesPreviewRequest(BrowserModel):
    url: str = Field(min_length=1, max_length=2048)
    page: Annotated[int, Field(strict=True, ge=1, le=500)] = 1


class BilibiliFavoriteVideo(BrowserModel):
    entry_key: str = Field(min_length=1, max_length=80)
    bvid: str | None = Field(default=None, min_length=12, max_length=12)
    canonical_url: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=1000)
    available: bool
    unavailable_reason: Literal["deleted", "not_video", "invalid_video"] | None = None


class BilibiliFavoritesPreviewResponse(BrowserModel):
    collection_id: str = Field(min_length=1, max_length=20)
    title: str | None = Field(default=None, max_length=1000)
    reported_total: int = Field(ge=0, le=2147483647)
    page: int = Field(ge=1, le=500)
    page_size: Literal[20] = 20
    has_more: bool
    capped: bool
    items: tuple[BilibiliFavoriteVideo, ...] = Field(max_length=20)
