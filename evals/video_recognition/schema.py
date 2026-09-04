"""Frozen catalog and protocol schemas for the video benchmark.

These contracts intentionally do not import production database or queue
models.  A catalog can be loaded and reviewed without network access, media
access, or provider credentials.  ``assert_execution_ready`` is the explicit
gate which prevents an unreviewed catalog from reaching a provider adapter.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

BENCHMARK_REVISION = "benchmark-v1"
CATALOG_PATH = Path(__file__).with_name("catalog.yaml")

Platform = Literal["youtube", "bilibili"]
SubtitleKind = Literal["manual", "automatic"]
CoverageStatus = Literal["complete", "partial", "none", "unknown", "pending"]
ProbeStatus = Literal["complete", "track_declared", "no_platform_track", "incomplete", "failed", "pending"]
QueryKind = Literal["speech", "visual", "ocr", "combined"]
Modality = Literal["asr", "vision", "ocr"]
RunTerminalState = Literal["completed", "provider_incomplete", "harness_invalid"]


class CatalogValidationError(ValueError):
    """Raised when the frozen benchmark manifest is not internally valid."""

    error_code = "catalog_validation_failed"


class BenchmarkGateError(RuntimeError):
    """Raised when an execution is attempted before the review gates pass."""

    def __init__(self, blockers: list[str]):
        self.blockers = tuple(blockers)
        super().__init__("benchmark execution gate is closed: " + "; ".join(blockers))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _finite(value: float, field_name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")
    return value


def _same_optional_float(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(left, right, rel_tol=0, abs_tol=1e-6)


class TimeRange(StrictModel):
    """A finite, non-empty interval on a video's timeline."""

    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered_and_finite(self) -> "TimeRange":
        _finite(self.start_sec, "start_sec")
        _finite(self.end_sec, "end_sec")
        if self.end_sec <= self.start_sec:
            raise ValueError("time range must have end_sec > start_sec")
        return self

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


class SubtitleTrack(StrictModel):
    """A declared platform track plus body-probe coverage, if available."""

    language: str = Field(min_length=1, max_length=32)
    kind: SubtitleKind
    format: str = Field(min_length=1, max_length=32)
    available: bool = True
    body_probe_status: Literal["not_requested", "completed", "failed", "pending"] = "pending"
    cue_count: int | None = Field(default=None, ge=0)
    content_start_sec: float | None = Field(default=None, ge=0)
    content_end_sec: float | None = Field(default=None, gt=0)
    coverage_ratio: float | None = Field(default=None, ge=0, le=1)
    coverage_status: CoverageStatus = "pending"

    @model_validator(mode="after")
    def validate_body_projection(self) -> "SubtitleTrack":
        if self.body_probe_status == "completed":
            if self.cue_count is None:
                raise ValueError("completed subtitle probe requires cue_count")
            if self.coverage_status == "pending":
                raise ValueError("completed subtitle probe requires coverage_status")
        if self.content_start_sec is not None and self.content_end_sec is not None:
            _finite(self.content_start_sec, "content_start_sec")
            _finite(self.content_end_sec, "content_end_sec")
            if self.content_end_sec <= self.content_start_sec:
                raise ValueError("subtitle content end must be after start")
        return self


class ProcessingScope(StrictModel):
    """The minimum-media boundary accepted for this benchmark revision."""

    max_audio_window_sec: float = Field(default=60, gt=0, le=60)
    max_audio_windows_per_sample: int = Field(default=2, ge=1, le=2)
    frame_bundle_min_frames: int = Field(default=3, ge=1, le=6)
    frame_bundle_max_frames: int = Field(default=6, ge=1, le=6)
    max_frame_bundles_per_sample: int = Field(default=2, ge=1, le=2)
    max_ocr_frames: int = Field(default=3, ge=0, le=3)
    retain_complete_media: bool = False
    retain_provider_response: bool = False
    submit_page_url: bool = False
    submit_signed_url: bool = False
    submit_credentials: bool = False

    @model_validator(mode="after")
    def valid_frame_bounds(self) -> "ProcessingScope":
        if self.frame_bundle_min_frames > self.frame_bundle_max_frames:
            raise ValueError("frame bundle minimum exceeds maximum")
        if self.retain_complete_media or self.retain_provider_response:
            raise ValueError("complete media and provider responses must not be retained")
        if self.submit_page_url or self.submit_signed_url or self.submit_credentials:
            raise ValueError("provider inputs must not contain URLs or credentials")
        return self


class MediaPlanWindow(StrictModel):
    window_id: str = Field(pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]{1,127}")
    purpose: Literal["representative", "difficulty", "manual_review"]
    time_range: TimeRange
    selection_status: Literal["pending_manual_review", "frozen"] = "pending_manual_review"
    source_hash: str | None = Field(default=None, pattern=r"[a-f0-9]{64}")


class MediaPlanFrame(StrictModel):
    frame_id: str = Field(pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]{1,127}")
    timestamp_sec: float = Field(ge=0)
    selection_status: Literal["pending_manual_review", "frozen"] = "pending_manual_review"
    source_hash: str | None = Field(default=None, pattern=r"[a-f0-9]{64}")

    @field_validator("timestamp_sec")
    @classmethod
    def finite_timestamp(cls, value: float) -> float:
        return _finite(value, "timestamp_sec")


class MediaPlanBundle(StrictModel):
    bundle_id: str = Field(pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]{1,127}")
    time_range: TimeRange
    frames: list[MediaPlanFrame] = Field(min_length=3, max_length=6)
    selection_status: Literal["pending_manual_review", "frozen"] = "pending_manual_review"


class ProbeRecord(StrictModel):
    """Safe projection emitted by the formal metadata/subtitle connector probe.

    The probe may inspect subtitle bytes in memory to calculate coverage, but
    this record never carries the body, subtitle URL, response headers, or
    stderr.  A declared track with no usable body remains ``incomplete`` and
    cannot satisfy the formal subtitle-body gate.
    """

    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    platform: Platform
    platform_id: str = Field(min_length=3, max_length=32)
    public_url: str = Field(min_length=1, max_length=2048)
    checked_at: str = Field(min_length=10, max_length=64)
    publicly_accessible: bool
    single_video: bool
    metadata_status: Literal["ok", "failed", "pending"]
    metadata_source: Literal["yt-dlp", "youtube-player-response", "bilibili-view-api", "fixture"]
    title: str | None = Field(default=None, max_length=500)
    creator: str | None = Field(default=None, max_length=300)
    duration_sec: int | None = Field(default=None, gt=0)
    # YouTube can expose many translated tracks; keep the bounded projection
    # large enough to preserve language/source distinctions without retaining
    # any body or signed URL.
    subtitle_tracks: list[SubtitleTrack] = Field(default_factory=list, max_length=256)
    subtitle_coverage_status: CoverageStatus = "pending"
    hard_subtitle_status: Literal["present", "absent", "unknown", "pending"] = "pending"
    probe_status: ProbeStatus
    stable_error_code: str | None = Field(default=None, pattern=r"[a-z0-9_]{3,80}")
    rights_status: Literal["uncertain_user_requested", "open_license", "official_public", "user_owned"] = "uncertain_user_requested"
    rights_note: str = Field(min_length=1, max_length=1000)
    notes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("public_url")
    @classmethod
    def probe_url_is_safe(cls, value: str) -> str:
        # Reuse the same public URL boundary as Sample without constructing a
        # Sample (which would require content classification fields).
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.username or parts.password or parts.fragment:
            raise ValueError("probe URL must be HTTPS without credentials or fragments")
        if host in {"youtube.com", "www.youtube.com"}:
            if parts.path != "/watch" or set(parse_qs(parts.query)) - {"v"}:
                raise ValueError("probe YouTube URL must be canonical")
        elif host in {"bilibili.com", "www.bilibili.com"}:
            if not re.fullmatch(r"/video/(?:BV|av)[0-9A-Za-z]+/?", parts.path) or parts.query:
                raise ValueError("probe Bilibili URL must be canonical")
        else:
            raise ValueError("probe URL host is not approved")
        return value

    @model_validator(mode="after")
    def valid_probe_state(self) -> "ProbeRecord":
        if self.probe_status == "failed" and not self.stable_error_code:
            raise ValueError("failed probe records require a stable error code")
        if self.probe_status == "incomplete" and not self.stable_error_code:
            raise ValueError("incomplete probe records require a stable error code")
        if self.metadata_status == "ok" and self.duration_sec is None:
            raise ValueError("successful metadata probes require duration_sec")
        return self


class ProbeSnapshot(StrictModel):
    revision: str = Field(default=BENCHMARK_REVISION, pattern=r"benchmark-v[0-9]+")
    probe_date: str = Field(min_length=10, max_length=64)
    probe_kind: Literal["formal_connector_probe", "fixture_projection"]
    source_methods: list[str] = Field(min_length=1, max_length=10)
    records: list[ProbeRecord] = Field(min_length=12, max_length=12)

    @model_validator(mode="after")
    def unique_records(self) -> "ProbeSnapshot":
        ids = [record.sample_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("probe sample IDs must be unique")
        if Counter(record.platform for record in self.records) != Counter({"youtube": 6, "bilibili": 6}):
            raise ValueError("probe snapshot must contain six records per platform")
        return self


class Sample(StrictModel):
    """One of the 12 user-selected public videos.

    ``rights_status=uncertain_user_requested`` is deliberate: public viewing
    and user selection are not represented as an open-reuse license.
    """

    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    platform: Platform
    platform_id: str = Field(min_length=3, max_length=32)
    public_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=500)
    creator: str = Field(min_length=1, max_length=300)
    duration_sec: int = Field(gt=0)
    publicly_accessible_at: str = Field(min_length=1, max_length=64)
    single_video: bool = True
    probe_status: ProbeStatus
    subtitle_tracks: list[SubtitleTrack] = Field(default_factory=list, max_length=20)
    subtitle_coverage_status: CoverageStatus = "pending"
    hard_subtitle_status: Literal["present", "absent", "unknown", "pending"] = "pending"
    content_languages: list[str] = Field(default_factory=list, max_length=10)
    content_tags: list[str] = Field(default_factory=list, max_length=30)
    rights_status: Literal["open_license", "official_public", "user_owned", "uncertain_user_requested"]
    rights_evidence_note: str = Field(min_length=1, max_length=1000)
    download_and_processing_scope: str = Field(min_length=1, max_length=1000)
    media_plan_status: Literal["pending_manual_review", "frozen"] = "pending_manual_review"
    audio_plan: list[MediaPlanWindow] = Field(default_factory=list, max_length=2)
    frame_plan: list[MediaPlanBundle] = Field(default_factory=list, max_length=2)
    ocr_frame_plan: list[MediaPlanFrame] = Field(default_factory=list, max_length=3)
    classification_status: Literal["preliminary", "manually_frozen"] = "preliminary"
    notes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("public_url")
    @classmethod
    def safe_public_url(cls, value: str) -> str:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.username or parts.password or parts.fragment:
            raise ValueError("sample URL must be an HTTPS URL without credentials or fragments")
        if host in {"youtube.com", "www.youtube.com"}:
            if parts.path != "/watch" or set(parse_qs(parts.query)) - {"v"}:
                raise ValueError("YouTube sample URL must be a canonical watch URL")
        elif host in {"bilibili.com", "www.bilibili.com"}:
            if not re.fullmatch(r"/video/(?:BV|av)[0-9A-Za-z]+/?", parts.path):
                raise ValueError("Bilibili sample URL must be a canonical video URL")
            if parts.query:
                raise ValueError("Bilibili sample URL must not contain tracking parameters")
        else:
            raise ValueError("sample URL host is outside the approved public platforms")
        return value

    @model_validator(mode="after")
    def validate_platform_id_and_tracks(self) -> "Sample":
        parts = urlsplit(self.public_url)
        if self.platform == "youtube" and not re.fullmatch(r"[A-Za-z0-9_-]{11}", self.platform_id):
            raise ValueError("YouTube platform_id must be an 11-character video ID")
        if self.platform == "bilibili" and not re.fullmatch(r"BV[0-9A-Za-z]{10}|av[0-9]+", self.platform_id):
            raise ValueError("Bilibili platform_id must be a BV or av ID")
        if self.platform == "youtube":
            values = parse_qs(parts.query).get("v", [])
            if values != [self.platform_id] or not self.sample_id.startswith("YT-"):
                raise ValueError("YouTube sample URL, ID, and sample prefix must agree")
        elif parts.path.rstrip("/").rsplit("/", 1)[-1] != self.platform_id or not self.sample_id.startswith("BI-"):
            raise ValueError("Bilibili sample URL, ID, and sample prefix must agree")
        if len({(track.language.lower(), track.kind) for track in self.subtitle_tracks}) != len(self.subtitle_tracks):
            raise ValueError("subtitle track language/kind pairs must be unique")
        if self.probe_status == "no_platform_track" and self.subtitle_tracks:
            raise ValueError("no-platform-track samples cannot declare subtitle tracks")
        if self.duration_sec > 0:
            for plan in [*self.audio_plan, *self.frame_plan]:
                if plan.time_range.end_sec > self.duration_sec:
                    raise ValueError("media plan exceeds sample duration")
            for frame in self.ocr_frame_plan:
                if frame.timestamp_sec > self.duration_sec:
                    raise ValueError("OCR plan exceeds sample duration")
        if self.media_plan_status == "frozen":
            if len(self.audio_plan) != 2 or len(self.frame_plan) != 2:
                raise ValueError("frozen samples require two audio windows and two frame bundles")
            planned = [*self.audio_plan, *self.frame_plan, *self.ocr_frame_plan]
            if any(item.selection_status != "frozen" for item in planned):
                raise ValueError("frozen media plans cannot contain pending selections")
            if any(item.source_hash is None for item in [*self.audio_plan, *self.ocr_frame_plan]):
                raise ValueError("frozen audio/OCR selections require source hashes")
            if any(
                frame.source_hash is None or frame.selection_status != "frozen"
                for bundle in self.frame_plan
                for frame in bundle.frames
            ):
                raise ValueError("frozen frame selections require source hashes")
        return self


class Query(StrictModel):
    query_id: str = Field(pattern=r"(?:speech|visual|ocr|combined)-[0-9]{2}")
    kind: QueryKind
    query: str = Field(min_length=1, max_length=1000)
    # During planning this records which selected sample is to be watched for
    # annotation.  It is not gold truth and cannot be used by a provider run.
    sample_scope_ids: list[str] = Field(min_length=1, max_length=12)
    expected_video_ids: list[str] = Field(default_factory=list, max_length=12)
    acceptable_time_ranges: dict[str, list[TimeRange]] = Field(default_factory=dict)
    required_modalities: list[Modality] = Field(min_length=1, max_length=3)
    required_key_terms: list[str] = Field(default_factory=list, max_length=20)
    distractor_video_ids: list[str] = Field(default_factory=list, max_length=12)
    metadata_leak_notes: str = Field(default="", max_length=500)
    exclusion_notes: str = Field(default="", max_length=500)
    annotation_status: Literal["pending_manual", "frozen"] = "pending_manual"
    annotation_note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_query_kind(self) -> "Query":
        expected: dict[QueryKind, set[Modality]] = {
            "speech": {"asr"},
            "visual": {"vision"},
            "ocr": {"ocr"},
            "combined": {"asr", "vision"},
        }
        if set(self.required_modalities) != expected[self.kind]:
            raise ValueError(f"{self.kind} query has invalid required_modalities")
        if len(set(self.required_modalities)) != len(self.required_modalities):
            raise ValueError("required_modalities must not contain duplicates")
        if set(self.expected_video_ids) & set(self.distractor_video_ids):
            raise ValueError("expected and distractor videos must be disjoint")
        if set(self.expected_video_ids) - set(self.sample_scope_ids):
            raise ValueError("expected video IDs must be within sample_scope_ids")
        if self.annotation_status == "frozen" and not self.expected_video_ids:
            raise ValueError("frozen queries require expected video IDs")
        if self.annotation_status == "frozen" and not self.acceptable_time_ranges:
            raise ValueError("frozen queries require acceptable time ranges")
        if self.annotation_status == "frozen" and set(self.acceptable_time_ranges) != set(self.expected_video_ids):
            raise ValueError("frozen query time ranges must exactly match expected videos")
        return self


class Profile(StrictModel):
    profile_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,80}")
    modality: Modality
    provider: str = Field(min_length=1, max_length=80)
    model_alias: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=200)
    required_env: list[str] = Field(default_factory=list, max_length=5)
    official_sources: list[str] = Field(min_length=1, max_length=10)
    queried_at: str = Field(min_length=1, max_length=32)
    supports_fixture: bool = True

    @field_validator("required_env")
    @classmethod
    def valid_env_names(cls, values: list[str]) -> list[str]:
        for value in values:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", value):
                raise ValueError("required_env entries must be environment variable names")
        return values


class QualityGates(StrictModel):
    schema_once_pass_rate: float = Field(default=1.0, ge=0, le=1)
    eligible_completion_rate: float = Field(default=0.95, ge=0, le=1)
    top3_video_hit_rate: float = Field(default=0.90, ge=0, le=1)
    top1_video_hit_rate: float = Field(default=0.75, ge=0, le=1)
    temporal_hit_rate: float = Field(default=0.85, ge=0, le=1)
    subgroup_hit_rate: float = Field(default=0.75, ge=0, le=1)
    asr_keyword_recall: float = Field(default=0.90, ge=0, le=1)
    asr_language_subgroup_recall: float = Field(default=0.85, ge=0, le=1)
    ocr_exact_recall: float = Field(default=0.90, ge=0, le=1)
    ocr_small_text_recall: float = Field(default=0.80, ge=0, le=1)
    vision_required_recall: float = Field(default=0.85, ge=0, le=1)
    vision_hallucination_rate: float = Field(default=0.05, ge=0, le=1)
    asr_consistency: float = Field(default=0.90, ge=0, le=1)
    asr_boundary_drift_sec: float = Field(default=2.0, ge=0)
    ocr_consistency: float = Field(default=0.95, ge=0, le=1)
    vision_consistency_jaccard: float = Field(default=0.75, ge=0, le=1)


class BenchmarkCatalog(StrictModel):
    revision: str = Field(default=BENCHMARK_REVISION, pattern=r"benchmark-v[0-9]+")
    catalog_status: Literal["dry_run_only", "frozen"] = "dry_run_only"
    freeze_date: str = Field(min_length=10, max_length=32)
    query_date_cutoff: str = Field(min_length=10, max_length=32)
    truth_annotation_status: Literal["pending_manual", "complete"] = "pending_manual"
    media_input_status: Literal["pending_manual", "complete"] = "pending_manual"
    probe_snapshot: str = Field(min_length=1, max_length=200)
    samples: list[Sample] = Field(min_length=12, max_length=12)
    queries: list[Query] = Field(min_length=30, max_length=30)
    profiles: list[Profile] = Field(min_length=11, max_length=11)
    runs_per_profile_sample: int = Field(default=3, ge=3, le=3)
    processing_scope: ProcessingScope = Field(default_factory=ProcessingScope)
    quality_gates: QualityGates = Field(default_factory=QualityGates)
    official_sources: list[str] = Field(min_length=1, max_length=50)
    rights_note: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def validate_manifest(self) -> "BenchmarkCatalog":
        sample_ids = {sample.sample_id for sample in self.samples}
        if len(sample_ids) != len(self.samples):
            raise ValueError("sample IDs must be unique")
        platforms = Counter(sample.platform for sample in self.samples)
        if platforms != Counter({"youtube": 6, "bilibili": 6}):
            raise ValueError("catalog must contain exactly six YouTube and six Bilibili samples")
        urls = [sample.public_url for sample in self.samples]
        if len(urls) != len(set(urls)):
            raise ValueError("sample URLs must be unique")
        query_ids = {query.query_id for query in self.queries}
        if len(query_ids) != len(self.queries):
            raise ValueError("query IDs must be unique")
        distribution = Counter(query.kind for query in self.queries)
        if distribution != Counter({"speech": 12, "visual": 8, "ocr": 6, "combined": 4}):
            raise ValueError("query distribution must be 12 speech, 8 visual, 6 OCR, 4 combined")
        references: Counter[str] = Counter()
        for query in self.queries:
            unknown = (
                set(query.sample_scope_ids)
                | set(query.expected_video_ids)
                | set(query.distractor_video_ids)
                | set(query.acceptable_time_ranges)
            ) - sample_ids
            if unknown:
                raise ValueError(f"query {query.query_id} references unknown samples: {sorted(unknown)}")
            references.update(query.expected_video_ids or query.sample_scope_ids)
        if any(references[sample_id] < 1 for sample_id in sample_ids):
            raise ValueError("every sample must appear in at least one query truth record")
        profile_ids = {profile.profile_id for profile in self.profiles}
        if len(profile_ids) != len(self.profiles):
            raise ValueError("profile IDs must be unique")
        profile_counts = Counter(profile.modality for profile in self.profiles)
        if profile_counts != Counter({"asr": 4, "vision": 4, "ocr": 3}):
            raise ValueError("profile distribution must be 4 ASR, 4 Vision, 3 OCR")
        return self

    def execution_blockers(self) -> list[str]:
        """Return stable, user-actionable reasons providers must not run."""

        blockers: list[str] = []
        if self.catalog_status != "frozen":
            blockers.append("catalog_status_not_frozen")
        if self.truth_annotation_status != "complete":
            blockers.append("manual_truth_annotation_pending")
        if self.media_input_status != "complete":
            blockers.append("manual_media_window_and_frame_review_pending")
        if any(sample.probe_status not in {"complete", "no_platform_track"} for sample in self.samples):
            blockers.append("formal_subtitle_body_probe_pending")
        if any(sample.classification_status != "manually_frozen" for sample in self.samples):
            blockers.append("manual_content_classification_pending")
        if any(sample.media_plan_status != "frozen" for sample in self.samples):
            blockers.append("manual_media_plan_pending")
        if any(query.annotation_status != "frozen" for query in self.queries):
            blockers.append("query_truth_annotation_pending")
        try:
            snapshot = load_probe_snapshot(self.probe_snapshot)
        except CatalogValidationError:
            blockers.append("formal_probe_snapshot_invalid")
        else:
            if snapshot.revision != self.revision or snapshot.probe_kind != "formal_connector_probe":
                blockers.append("formal_probe_snapshot_pending")
            records = {record.sample_id: record for record in snapshot.records}
            if any(
                (record := records.get(sample.sample_id)) is None
                or record.platform != sample.platform
                or record.platform_id != sample.platform_id
                or record.public_url != sample.public_url
                or not record.publicly_accessible
                or not record.single_video
                or record.metadata_status != "ok"
                or record.probe_status not in {"complete", "no_platform_track"}
                or record.probe_status != sample.probe_status
                or record.subtitle_coverage_status != sample.subtitle_coverage_status
                or any(
                    not any(
                        candidate.language.casefold() == track.language.casefold()
                        and candidate.kind == track.kind
                        and candidate.format == track.format
                        and candidate.body_probe_status == "completed"
                        and candidate.cue_count == track.cue_count
                        and candidate.coverage_status == track.coverage_status
                        and _same_optional_float(candidate.content_start_sec, track.content_start_sec)
                        and _same_optional_float(candidate.content_end_sec, track.content_end_sec)
                        and _same_optional_float(candidate.coverage_ratio, track.coverage_ratio)
                        for candidate in record.subtitle_tracks
                    )
                    for track in sample.subtitle_tracks
                )
                for sample in self.samples
            ):
                blockers.append("formal_probe_catalog_mismatch")
        return blockers

    def assert_execution_ready(self) -> None:
        blockers = self.execution_blockers()
        if blockers:
            raise BenchmarkGateError(blockers)


def load_catalog(path: str | Path | None = None) -> BenchmarkCatalog:
    """Load and strictly validate the YAML/JSON benchmark manifest."""

    target = Path(path) if path else CATALOG_PATH
    try:
        raw_text = target.read_text(encoding="utf-8")
        payload = json.loads(raw_text) if target.suffix.lower() == ".json" else yaml.safe_load(raw_text)
        return BenchmarkCatalog.model_validate(payload)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogValidationError("catalog_validation_failed") from exc


def load_probe_snapshot(path: str | Path | None = None) -> ProbeSnapshot:
    """Load the redacted metadata/subtitle probe projection."""

    target = Path(path) if path else Path("probe_snapshot.yaml")
    if not target.is_absolute():
        target = Path(__file__).with_name(target.name) if target.parent == Path(".") else Path(__file__).parent / target
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
        return ProbeSnapshot.model_validate(payload)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise CatalogValidationError("probe_snapshot_validation_failed") from exc


def catalog_summary(catalog: BenchmarkCatalog) -> dict[str, Any]:
    """Return only safe counters for CLI output and CI logs."""

    return {
        "revision": catalog.revision,
        "catalog_status": catalog.catalog_status,
        "sample_count": len(catalog.samples),
        "platform_counts": dict(Counter(sample.platform for sample in catalog.samples)),
        "query_count": len(catalog.queries),
        "query_kinds": dict(Counter(query.kind for query in catalog.queries)),
        "profile_count": len(catalog.profiles),
        "runs_per_profile_sample": catalog.runs_per_profile_sample,
        "execution_gate": "open" if not catalog.execution_blockers() else "closed",
        "execution_blockers": catalog.execution_blockers(),
    }
