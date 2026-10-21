"""Database-independent media inspection contracts for Agent retrieval tools."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from urllib.parse import unquote, urlsplit
from typing import Literal, Protocol


MEDIA_TEXT_LIMIT = 500
MEDIA_EVIDENCE_ITEMS_LIMIT = 8
MEDIA_EVIDENCE_TEXT_LIMIT = 360
MEDIA_LABEL_LIMIT = 64
MEDIA_ID_LIMIT = 128
MEDIA_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MEDIA_EVIDENCE_ID_RE = re.compile(r"^M[A-Za-z0-9_-]{1,128}$")


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_source_location(value: object) -> str:
    """Validate adapter-owned canonical locations, never provider URLs."""
    if (
        not isinstance(value, str) or not 1 <= len(value) <= 2048
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or any(ord(char) < 32 or ord(char) == 127 for char in unquote(value))
        or any(char in value for char in ('\\', '<', '>', '"'))
    ):
        raise MediaInspectionValidationError("invalid media source location")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError()
        _ = parsed.port
    except ValueError:
        raise MediaInspectionValidationError("invalid media source location") from None
    return value


def _metadata_text(value: object) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= MEDIA_ID_LIMIT
            and value == value.strip()
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _finite_number(value: object) -> bool:
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
                and math.isfinite(value))
    except (OverflowError, ValueError):
        return False


class MediaInspectionValidationError(ValueError):
    """A provider result or manifest value failed the public media contract."""


@dataclass(frozen=True)
class MediaState:
    """Server-owned state recheck result; adapters must populate every gate."""

    tenant_matches: bool
    parent_owned: bool
    deleted: bool
    archived: bool
    ready: bool
    purge_valid: bool

    @property
    def inspectable(self) -> bool:
        return (
            all(type(value) is bool for value in (
                self.tenant_matches, self.parent_owned, self.deleted,
                self.archived, self.ready, self.purge_valid,
            ))
            and
            self.tenant_matches
            and self.parent_owned
            and not self.deleted
            and not self.archived
            and self.ready
            and self.purge_valid
        )


@dataclass(frozen=True)
class MediaCandidate:
    media_id: str
    tenant_id: int
    run_id: str
    item_id: int
    content_hash: str
    model_version: str
    prompt_version: str

    def __post_init__(self) -> None:
        if not _is_positive_int(self.tenant_id) or not _is_positive_int(self.item_id):
            raise ValueError("invalid media candidate identity")
        if not isinstance(self.media_id, str) or not MEDIA_OPAQUE_ID_RE.fullmatch(self.media_id):
            raise ValueError("invalid media candidate ID")
        for value in (self.media_id, self.run_id, self.content_hash, self.model_version, self.prompt_version):
            if not _metadata_text(value):
                raise ValueError("invalid media candidate metadata")


@dataclass(frozen=True)
class MediaCandidateManifest:
    tenant_id: int
    run_id: str
    candidates: tuple[MediaCandidate, ...] = ()

    def __post_init__(self) -> None:
        if not _is_positive_int(self.tenant_id) or not _metadata_text(self.run_id):
            raise ValueError("invalid media candidate manifest identity")
        if not isinstance(self.candidates, tuple) or len(self.candidates) > 10:
            raise ValueError("invalid media candidate manifest bounds")
        if any(type(candidate) is not MediaCandidate for candidate in self.candidates):
            raise ValueError("invalid media candidate manifest values")
        if any(candidate.tenant_id != self.tenant_id or candidate.run_id != self.run_id for candidate in self.candidates):
            raise ValueError("media candidates do not match manifest scope")
        ids = [candidate.media_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("media candidate IDs must be unique")

    def get(self, media_id: str) -> MediaCandidate | None:
        return next((candidate for candidate in self.candidates if candidate.media_id == media_id), None)


@dataclass(frozen=True)
class MediaInspectionRequest:
    tenant_id: int
    item_id: int
    run_id: str
    media_id: str
    content_hash: str
    question: str
    model_version: str
    prompt_version: str


@dataclass(frozen=True)
class MediaInspectionResult:
    """Structured, bounded evidence returned by an injected vision service."""

    tenant_id: int
    item_id: int
    run_id: str
    media_id: str
    content_hash: str
    model_version: str
    prompt_version: str
    relevant: bool
    confidence: float
    evidence: tuple[str, ...] = ()
    ocr: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    start_sec: float | None = None
    end_sec: float | None = None

    def validate(self) -> "MediaInspectionResult":
        if not _is_positive_int(self.tenant_id) or not _is_positive_int(self.item_id):
            raise MediaInspectionValidationError("invalid inspection provenance")
        for value in (
            self.run_id,
            self.media_id,
            self.content_hash,
            self.model_version,
            self.prompt_version,
        ):
            if not _metadata_text(value):
                raise MediaInspectionValidationError("invalid inspection provenance")
        if not MEDIA_OPAQUE_ID_RE.fullmatch(self.media_id):
            raise MediaInspectionValidationError("invalid inspection provenance")
        if not isinstance(self.relevant, bool):
            raise MediaInspectionValidationError("invalid inspection relevance")
        if (
            not _finite_number(self.confidence)
            or not 0 <= self.confidence <= 1
        ):
            raise MediaInspectionValidationError("inspection confidence is out of range")
        if (self.start_sec is None) != (self.end_sec is None):
            raise MediaInspectionValidationError("inspection time range is incomplete")
        if self.start_sec is not None:
            if (
                not _finite_number(self.start_sec)
                or not _finite_number(self.end_sec)
            ):
                raise MediaInspectionValidationError("inspection time range is invalid")
            if self.start_sec < 0 or self.end_sec < self.start_sec:
                raise MediaInspectionValidationError("inspection time range is invalid")
        for values in (self.evidence, self.ocr, self.objects, self.actions):
            if not isinstance(values, tuple):
                raise MediaInspectionValidationError("inspection evidence is out of bounds")
            if len(values) > MEDIA_EVIDENCE_ITEMS_LIMIT or any(
                not isinstance(value, str)
                or not value.strip()
                or len(value) > MEDIA_EVIDENCE_TEXT_LIMIT
                for value in values
            ):
                raise MediaInspectionValidationError("inspection evidence is out of bounds")
        return self

    def as_dict(self) -> dict:
        self.validate()
        return {
            "media_id": self.media_id,
            "relevant": self.relevant,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "ocr": list(self.ocr),
            "objects": list(self.objects),
            "actions": list(self.actions),
            "start_sec": self.start_sec,
            "end_sec": self.end_sec,
        }

    @property
    def publishable(self) -> bool:
        return self.relevant and any(
            values for values in (self.evidence, self.ocr, self.objects, self.actions)
        )


@dataclass(frozen=True)
class MediaEvidence:
    """Trusted media evidence kept separate from text Segment citations."""

    evidence_id: str
    candidate: MediaCandidate
    inspection: MediaInspectionResult
    title: str | None = None
    location: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not MEDIA_EVIDENCE_ID_RE.fullmatch(self.evidence_id):
            raise ValueError("media evidence IDs must use the M prefix")
        if type(self.candidate) is not MediaCandidate or type(self.inspection) is not MediaInspectionResult:
            raise MediaInspectionValidationError("invalid media evidence")
        self.inspection.validate()
        if self.evidence_id != "M" + self.candidate.media_id or not self.inspection.publishable:
            raise MediaInspectionValidationError("media evidence is not grounded")
        if any(getattr(self.candidate, name) != getattr(self.inspection, name) for name in (
            "tenant_id", "item_id", "run_id", "media_id", "content_hash", "model_version", "prompt_version",
        )):
            raise MediaInspectionValidationError("media evidence provenance mismatch")
        if self.title is not None and (
            not isinstance(self.title, str)
            or not self.title.strip()
            or len(self.title) > MEDIA_EVIDENCE_TEXT_LIMIT
            or any(ord(char) < 32 for char in self.title)
        ):
            raise ValueError("invalid media evidence title")
        if not self.title:
            raise MediaInspectionValidationError("media source title is required")
        validate_source_location(self.location)

    def as_dict(self) -> dict:
        self.__post_init__()
        return {
            "evidence_id": self.evidence_id,
            "media_id": self.candidate.media_id,
            "item_id": self.candidate.item_id,
            "content_hash": self.candidate.content_hash,
            "title": self.title,
            "location": self.location,
            "relevant": self.inspection.relevant,
            "confidence": self.inspection.confidence,
            "evidence": list(self.inspection.evidence),
            "ocr": list(self.inspection.ocr),
            "objects": list(self.inspection.objects),
            "actions": list(self.inspection.actions),
            "start_sec": self.inspection.start_sec,
            "end_sec": self.inspection.end_sec,
        }


class VisionService(Protocol):
    """Injected boundary; implementations own media access and model calls."""

    def inspect_media(self, request: MediaInspectionRequest) -> MediaInspectionResult: ...


class MediaCandidateAdapter(Protocol):
    """Server-owned boundary for future manifest assembly from media rows."""

    def manifest_for_run(self, *, tenant_id: int, run_id: str) -> MediaCandidateManifest | None: ...

    def recheck(
        self, candidate: MediaCandidate, *, phase: Literal["before_inspection", "before_publish", "before_answer"]
    ) -> MediaState: ...

    def publish_evidence(
        self, candidate: MediaCandidate, inspection: MediaInspectionResult
    ) -> MediaEvidence: ...


@dataclass
class MediaInspectionCache:
    """Turn-local cache hook for later provider-backed implementations."""

    values: dict[tuple[int, str, str, str, str, str, str], MediaInspectionResult] = field(default_factory=dict)


__all__ = [
    "MediaCandidate",
    "MediaCandidateManifest",
    "MediaEvidence",
    "MediaInspectionCache",
    "MediaInspectionRequest",
    "MediaInspectionResult",
    "MediaInspectionValidationError",
    "MediaCandidateAdapter",
    "MediaState",
    "VisionService",
]
