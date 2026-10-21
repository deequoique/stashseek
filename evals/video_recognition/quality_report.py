"""Provider-neutral automatic quality reports for video recognition outputs.

The report intentionally evaluates the *shape and usefulness* of normalized
recognition results.  It does not require frozen query truth, does not call a
provider, and never serializes subtitle bodies or other source content.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from .protocol import (
    ASRResponse,
    DiscoverySegment,
    OCRResponse,
    RecognitionJobResult,
    VisionResponse,
)
from .schema import Modality, StrictModel, TimeRange
from .scoring import keyword_recall, normalize_text, temporal_iou


class QualityReportError(ValueError):
    """Raised when a quality report request cannot be interpreted safely."""


class WeakReferenceCue(StrictModel):
    """An in-memory subtitle cue used as a weak reference.

    Cue text is accepted for local scoring but is never copied to a report or
    an evaluation event.  Callers should discard this object after scoring.
    """

    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("start_sec", "end_sec")
    @classmethod
    def finite_time(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("weak reference cue timestamps must be finite")
        return value

    @model_validator(mode="after")
    def ordered(self) -> "WeakReferenceCue":
        if self.end_sec <= self.start_sec:
            raise ValueError("weak reference cue end must be after start")
        return self


class WeakReference(StrictModel):
    """Optional metadata/subtitle signals for proxy scoring.

    ``subtitle_cues`` and ``reference_terms`` are weak signals, not frozen
    truth.  They are consumed in memory by :func:`build_quality_report`.
    """

    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    title: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=4000)
    chapters: list[str] = Field(default_factory=list, max_length=200)
    metadata_terms: list[str] = Field(default_factory=list, max_length=100)
    reference_terms: list[str] = Field(default_factory=list, max_length=100)
    subtitle_cues: list[WeakReferenceCue] = Field(default_factory=list, max_length=5000)
    visible_text: list[str] = Field(default_factory=list, max_length=100)
    has_subtitles: bool = False
    subtitle_coverage_ratio: float | None = Field(default=None, ge=0, le=1)

    @field_validator("subtitle_coverage_ratio")
    @classmethod
    def finite_coverage(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("subtitle coverage must be finite")
        return value


class InputBounds(StrictModel):
    """Known local input bounds for timestamp proxy checks."""

    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    modality: Modality
    time_range: TimeRange | None = None
    frame_times: list[float] = Field(default_factory=list, max_length=100)
    video_duration_sec: float | None = Field(default=None, gt=0)

    @field_validator("frame_times", "video_duration_sec")
    @classmethod
    def finite_bounds(cls, value: Any) -> Any:
        values = value if isinstance(value, list) else [value]
        if any(item is not None and (not isinstance(item, (int, float)) or not math.isfinite(float(item))) for item in values):
            raise ValueError("input bounds must be finite")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> "InputBounds":
        if self.frame_times and self.frame_times != sorted(set(self.frame_times)):
            raise ValueError("input frame times must be unique and ordered")
        if any(value < 0 for value in self.frame_times):
            raise ValueError("input frame times must be non-negative")
        if self.time_range is not None and self.video_duration_sec is not None:
            if self.time_range.end_sec > self.video_duration_sec:
                raise ValueError("input range exceeds video duration")
        return self


class LatencySummary(StrictModel):
    count: int = Field(default=0, ge=0)
    p50_ms: float | None = Field(default=None, ge=0)
    p95_ms: float | None = Field(default=None, ge=0)
    max_ms: float | None = Field(default=None, ge=0)


class UsageSummary(StrictModel):
    audio_seconds: float = Field(default=0, ge=0)
    image_count: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    known_fields: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("audio_seconds", "estimated_cost_usd")
    @classmethod
    def finite_usage(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("usage values must be finite")
        return value


class ModalityQuality(StrictModel):
    modality: Modality
    result_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    schema_valid_count: int = Field(ge=0)
    timestamp_valid_count: int = Field(ge=0)
    searchable_count: int = Field(ge=0)
    schema_valid_rate: float = Field(ge=0, le=1)
    timestamp_valid_rate: float = Field(ge=0, le=1)
    searchable_rate: float = Field(ge=0, le=1)
    weak_reference_status: Literal["known", "unknown"]
    weak_reference_metrics: dict[str, float] = Field(default_factory=dict, max_length=10)
    failure_counts: dict[str, int] = Field(default_factory=dict, max_length=30)
    latency: LatencySummary = Field(default_factory=LatencySummary)
    usage: UsageSummary = Field(default_factory=UsageSummary)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "ModalityQuality":
        if self.completed_count > self.result_count:
            raise ValueError("completed_count exceeds result_count")
        if self.schema_valid_count > self.result_count:
            raise ValueError("schema_valid_count exceeds result_count")
        if self.timestamp_valid_count > self.completed_count:
            raise ValueError("timestamp_valid_count exceeds completed_count")
        if self.searchable_count > self.completed_count:
            raise ValueError("searchable_count exceeds completed_count")
        return self


class QualityIssue(StrictModel):
    code: str = Field(pattern=r"[a-z][a-z0-9_]{2,79}")
    severity: Literal["info", "warning", "error"]
    count: int = Field(ge=1)
    sample_ids: list[str] = Field(default_factory=list, max_length=100)


class AutomaticQualityReport(StrictModel):
    """Safe aggregate report that does not depend on frozen query truth."""

    protocol_version: Literal["video-quality-report.v1"] = "video-quality-report.v1"
    report_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    revision: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{1,127}")
    sample_count: int = Field(ge=0)
    result_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    schema_valid_rate: float = Field(ge=0, le=1)
    timestamp_valid_rate: float = Field(ge=0, le=1)
    searchable_rate: float = Field(ge=0, le=1)
    cross_video_isolation_rate: float = Field(ge=0, le=1)
    weak_reference_status: Literal["known", "unknown", "partial"]
    weak_reference_metrics: dict[str, float] = Field(default_factory=dict, max_length=20)
    failure_counts: dict[str, int] = Field(default_factory=dict, max_length=100)
    issues: list[QualityIssue] = Field(default_factory=list, max_length=100)
    modalities: list[ModalityQuality] = Field(default_factory=list, max_length=3)
    risk_candidates: list[dict[str, object]] = Field(default_factory=list, max_length=1000)
    review_plan: dict[str, object] = Field(default_factory=dict, max_length=20)
    manual_truth_status: Literal["pending_manual", "complete", "not_provided"] = "not_provided"
    manual_truth_required_for_report: bool = False
    external_calls: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "AutomaticQualityReport":
        if self.completed_count > self.result_count:
            raise ValueError("completed_count exceeds result_count")
        return self

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class _ResultMeta:
    result: RecognitionJobResult | None
    sample_id: str | None
    modality: Modality | None
    schema_valid: bool
    error_code: str | None


_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")


def _tokens(value: str) -> set[str]:
    normalized = normalize_text(value)
    return set(_TOKEN_RE.findall(normalized))


def _safe_sample_id(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"(?:YT|BI)-[0-9]{2}", value):
        return value
    return None


def _safe_modality(value: Any) -> Modality | None:
    return value if value in {"asr", "vision", "ocr"} else None


def _coerce_result(value: Any) -> _ResultMeta:
    if isinstance(value, RecognitionJobResult):
        return _ResultMeta(value, value.sample_id, value.modality, True, value.error.error_code if value.error else None)
    try:
        result = RecognitionJobResult.model_validate(value)
    except ValidationError:
        sample_id = _safe_sample_id(value.get("sample_id")) if isinstance(value, Mapping) else None
        modality = _safe_modality(value.get("modality")) if isinstance(value, Mapping) else None
        return _ResultMeta(None, sample_id, modality, False, "schema_invalid")
    return _ResultMeta(result, result.sample_id, result.modality, True, result.error.error_code if result.error else None)


def _latency_summary(values: Sequence[float]) -> LatencySummary:
    if not values:
        return LatencySummary()
    ordered = sorted(values)
    # Nearest-rank quantiles are deterministic and avoid an interpolation
    # dependency in the report contract.
    def rank(percentile: float) -> float:
        index = max(0, math.ceil(percentile * len(ordered)) - 1)
        return round(ordered[index], 3)

    return LatencySummary(
        count=len(ordered),
        p50_ms=rank(0.50),
        p95_ms=rank(0.95),
        max_ms=round(max(ordered), 3),
    )


def _usage_summary(results: Sequence[RecognitionJobResult]) -> UsageSummary:
    usages = [result.usage for result in results]
    audio = round(sum(item.audio_seconds for item in usages), 6)
    images = sum(item.image_count for item in usages)
    input_values = [item.input_tokens for item in usages if item.input_tokens is not None]
    output_values = [item.output_tokens for item in usages if item.output_tokens is not None]
    costs = [item.estimated_cost_usd for item in usages if item.estimated_cost_usd is not None]
    known = []
    if input_values:
        known.append("input_tokens")
    if output_values:
        known.append("output_tokens")
    if costs:
        known.append("estimated_cost_usd")
    return UsageSummary(
        audio_seconds=audio,
        image_count=images,
        input_tokens=sum(input_values) if len(input_values) == len(usages) else None,
        output_tokens=sum(output_values) if len(output_values) == len(usages) else None,
        estimated_cost_usd=round(sum(costs), 8) if len(costs) == len(usages) else None,
        known_fields=known,
    )


def _time_valid(result: RecognitionJobResult, bound: InputBounds | None) -> bool:
    if result.terminal_state != "completed":
        return False
    response = result.asr or result.vision or result.ocr
    if response is None:
        return False
    if isinstance(response, ASRResponse):
        if bound is None or bound.time_range is None:
            return all(
                math.isfinite(value)
                and value >= 0
                for segment in response.segments
                for value in (segment.start_sec, segment.end_sec)
            )
        return all(
            bound.time_range.start_sec <= segment.start_sec < segment.end_sec <= bound.time_range.end_sec
            and all(
                bound.time_range.start_sec <= word.start_sec < word.end_sec <= bound.time_range.end_sec
                for word in segment.words
            )
            for segment in response.segments
        )
    if isinstance(response, VisionResponse):
        allowed = set(bound.frame_times) if bound and bound.frame_times else None
        return all(
            math.isfinite(timestamp)
            and timestamp >= 0
            and (allowed is None or timestamp in allowed)
            for observation in response.observations
            for timestamp in observation.evidence_frame_times
        )
    allowed_range = bound.time_range if bound else None
    duration = bound.video_duration_sec if bound else None
    return all(
        math.isfinite(detection.frame_timestamp_sec)
        and detection.frame_timestamp_sec >= 0
        and (allowed_range is None or allowed_range.start_sec <= detection.frame_timestamp_sec <= allowed_range.end_sec)
        and (duration is None or detection.frame_timestamp_sec <= duration)
        for detection in response.detections
    )


def _is_searchable(result: RecognitionJobResult) -> bool:
    if result.terminal_state != "completed":
        return False
    if isinstance(result.asr, ASRResponse):
        return any(segment.text.strip() or any(word.text.strip() for word in segment.words) for segment in result.asr.segments)
    if isinstance(result.vision, VisionResponse):
        return any(
            observation.scene_type.strip()
            or observation.short_description.strip()
            or any(value.strip() for value in (*observation.entities, *observation.actions, *observation.screen_topics, *observation.search_terms))
            for observation in result.vision.observations
        )
    if isinstance(result.ocr, OCRResponse):
        return any(detection.text.strip() for detection in result.ocr.detections)
    return False


def _result_text(result: RecognitionJobResult) -> str:
    if isinstance(result.asr, ASRResponse):
        return " ".join(
            [segment.text for segment in result.asr.segments]
            + [word.text for segment in result.asr.segments for word in segment.words]
        )
    if isinstance(result.vision, VisionResponse):
        return " ".join(
            value
            for observation in result.vision.observations
            for value in (
                observation.scene_type,
                observation.short_description,
                *observation.entities,
                *observation.actions,
                *observation.screen_topics,
                *observation.search_terms,
            )
        )
    if isinstance(result.ocr, OCRResponse):
        return " ".join(detection.text for detection in result.ocr.detections)
    return ""


def _weak_reference_metrics(
    modality: Modality,
    results: Sequence[RecognitionJobResult],
    references: Mapping[str, WeakReference],
) -> tuple[Literal["known", "unknown"], dict[str, float]]:
    rows: list[dict[str, float]] = []
    for result in results:
        reference = references.get(result.sample_id)
        if reference is None:
            continue
        text = _result_text(result)
        if modality == "asr":
            values: dict[str, float] = {}
            if reference.subtitle_cues:
                assert isinstance(result.asr, ASRResponse)
                covered = sum(
                    any(
                        temporal_iou(TimeRange(start_sec=segment.start_sec, end_sec=segment.end_sec), TimeRange(start_sec=cue.start_sec, end_sec=cue.end_sec)) > 0
                        for cue in reference.subtitle_cues
                    )
                    for segment in result.asr.segments
                )
                values["subtitle_segment_overlap_rate"] = covered / len(result.asr.segments) if result.asr.segments else 0.0
                subtitle_text = " ".join(cue.text for cue in reference.subtitle_cues)
                values["subtitle_keyword_recall"] = keyword_recall(text, _tokens(subtitle_text))
            elif reference.reference_terms:
                values["reference_keyword_recall"] = keyword_recall(text, reference.reference_terms)
            if values:
                rows.append(values)
        elif modality == "vision":
            terms = reference.reference_terms or reference.metadata_terms or list(
                _tokens(" ".join([reference.title, reference.description, *reference.chapters]))
            )
            if terms:
                rows.append({"metadata_term_overlap": keyword_recall(text, terms)})
        else:
            if reference.visible_text:
                # OCR often returns a phrase as one detection while the
                # normalized result may contain surrounding words.  Score
                # each visible-text cue as a substring/keyword hit instead
                # of comparing it to whitespace-delimited tokens.
                rows.append(
                    {
                        "visible_text_recall": statistics.fmean(
                            keyword_recall(text, [value]) for value in reference.visible_text
                        )
                    }
                )
            elif reference.reference_terms:
                rows.append({"reference_text_recall": keyword_recall(text, reference.reference_terms)})
    if not rows:
        return "unknown", {}
    names = sorted({name for row in rows for name in row})
    return "known", {name: round(statistics.fmean(row[name] for row in rows if name in row), 6) for name in names}


def _norm(value: str) -> str:
    return normalize_text(value)


def _issue(code: str, severity: Literal["info", "warning", "error"], sample_ids: Iterable[str]) -> QualityIssue | None:
    ids = sorted(set(sample_ids))
    return QualityIssue(code=code, severity=severity, count=len(ids), sample_ids=ids) if ids else None


def _default_input_bounds(
    values: Mapping[tuple[str, Modality], InputBounds | Mapping[str, Any] | TimeRange | Sequence[float]] | None,
) -> dict[tuple[str, Modality], InputBounds]:
    result: dict[tuple[str, Modality], InputBounds] = {}
    for key, value in (values or {}).items():
        sample_id, modality = key
        if isinstance(value, InputBounds):
            bound = value
        elif isinstance(value, TimeRange):
            bound = InputBounds(sample_id=sample_id, modality=modality, time_range=value)
        elif isinstance(value, Mapping):
            bound = InputBounds.model_validate({"sample_id": sample_id, "modality": modality, **value})
        else:
            bound = InputBounds(sample_id=sample_id, modality=modality, frame_times=list(value))
        result[(sample_id, modality)] = bound
    return result


def _default_references(values: Mapping[str, WeakReference | Mapping[str, Any]] | None) -> dict[str, WeakReference]:
    return {
        sample_id: reference if isinstance(reference, WeakReference) else WeakReference.model_validate(reference)
        for sample_id, reference in (values or {}).items()
    }


def build_quality_report(
    results: Sequence[RecognitionJobResult | Mapping[str, Any]],
    *,
    input_bounds: Mapping[tuple[str, Modality], InputBounds | Mapping[str, Any] | TimeRange | Sequence[float]] | None = None,
    weak_references: Mapping[str, WeakReference | Mapping[str, Any]] | None = None,
    discovery_segments: Sequence[DiscoverySegment | Mapping[str, Any]] = (),
    expected_sample_ids: Iterable[str] = (),
    report_id: str = "video-quality-report",
    revision: str = "video-eval-v1",
    manual_truth_status: Literal["pending_manual", "complete", "not_provided"] = "not_provided",
    sampling_policy: Any | None = None,
) -> AutomaticQualityReport:
    """Build a deterministic report from normalized results.

    Invalid result mappings are counted as ``schema_invalid`` instead of
    aborting the whole report.  This lets the evaluator expose partial runs
    and choose risky samples for review without making manual truth a hard
    dependency.
    """

    bounds = _default_input_bounds(input_bounds)
    references = _default_references(weak_references)
    metas = [_coerce_result(value) for value in results]
    valid_results = [meta.result for meta in metas if meta.result is not None]
    completed = [result for result in valid_results if result.terminal_state == "completed"]
    sample_ids = {meta.sample_id for meta in metas if meta.sample_id is not None}
    expected = set(expected_sample_ids) or sample_ids

    discovery_ok = 0
    discovery_total = 0
    discovery_sample_ids: set[str] = set()
    for item in discovery_segments:
        try:
            segment = item if isinstance(item, DiscoverySegment) else DiscoverySegment.model_validate(item)
        except ValidationError:
            discovery_total += 1
            continue
        discovery_total += 1
        discovery_sample_ids.add(segment.sample_id)
        if segment.sample_id in expected:
            discovery_ok += 1
    isolation_rows = [meta for meta in metas if meta.schema_valid]
    isolation_ok = sum(
        bool(meta.result and meta.result.sample_id == meta.sample_id and (not discovery_sample_ids or meta.sample_id in discovery_sample_ids or meta.sample_id in expected))
        for meta in isolation_rows
    )
    isolation_denominator = len(isolation_rows) + discovery_total
    isolation_numerator = isolation_ok + discovery_ok
    isolation_rate = isolation_numerator / isolation_denominator if isolation_denominator else 1.0

    modality_reports: list[ModalityQuality] = []
    all_failure_counts: Counter[str] = Counter()
    issues: list[QualityIssue] = []
    all_risk_rows: list[dict[str, object]] = []
    for modality in ("asr", "vision", "ocr"):
        modality_metas = [meta for meta in metas if meta.modality == modality]
        modality_results = [meta.result for meta in modality_metas if meta.result is not None]
        modality_completed = [result for result in modality_results if result.terminal_state == "completed"]
        schema_valid = sum(meta.schema_valid for meta in modality_metas)
        timestamp_valid_ids = [result.sample_id for result in modality_completed if _time_valid(result, bounds.get((result.sample_id, modality)))]
        # A result is only useful to a timestamp-level search index when both
        # its content and its evidence timestamps are valid.
        searchable_ids = [
            result.sample_id
            for result in modality_completed
            if _is_searchable(result) and _time_valid(result, bounds.get((result.sample_id, modality)))
        ]
        failures = Counter(meta.error_code or "unknown_failure" for meta in modality_metas if meta.error_code)
        all_failure_counts.update(failures)
        reference_status, reference_metrics = _weak_reference_metrics(modality, modality_completed, references)
        latency = _latency_summary([result.latency_ms for result in modality_completed if result.latency_ms is not None])
        usage = _usage_summary(modality_completed)
        count = len(modality_metas)
        completed_count = len(modality_completed)
        modality_reports.append(
            ModalityQuality(
                modality=modality,
                result_count=count,
                completed_count=completed_count,
                schema_valid_count=schema_valid,
                timestamp_valid_count=len(timestamp_valid_ids),
                searchable_count=len(searchable_ids),
                schema_valid_rate=schema_valid / count if count else 0.0,
                timestamp_valid_rate=len(timestamp_valid_ids) / completed_count if completed_count else 0.0,
                searchable_rate=len(searchable_ids) / completed_count if completed_count else 0.0,
                weak_reference_status=reference_status,
                weak_reference_metrics=reference_metrics,
                failure_counts=dict(failures),
                latency=latency,
                usage=usage,
            )
        )
        for meta in modality_metas:
            result = meta.result
            risk_reasons: list[str] = []
            if not meta.schema_valid:
                risk_reasons.append("schema_invalid")
            elif result is not None:
                if result.terminal_state != "completed":
                    risk_reasons.append("provider_failure")
                if not _time_valid(result, bounds.get((result.sample_id, modality))):
                    risk_reasons.append("timestamp_invalid")
                if not _is_searchable(result):
                    risk_reasons.append("empty_search_fields")
                reference = references.get(result.sample_id)
                if reference is None or (modality == "asr" and not reference.subtitle_cues and not reference.reference_terms):
                    risk_reasons.append("weak_reference_missing")
                if reference and reference.has_subtitles is False and modality == "asr":
                    risk_reasons.append("no_subtitles")
                if reference and reference.subtitle_coverage_ratio is not None and reference.subtitle_coverage_ratio < 0.8:
                    risk_reasons.append("partial_subtitle_coverage")
            if meta.sample_id is None or meta.modality is None:
                continue
            from .sampling import assess_risk

            assessment = assess_risk(
                sample_id=meta.sample_id,
                modality=meta.modality,
                unit_id=(
                    f"{meta.sample_id.lower()}.{meta.modality}."
                    f"{result.candidate_id if result is not None else 'unknown'}"
                ),
                candidate_id=result.candidate_id if result is not None else "unknown",
                reasons=risk_reasons,
                confidence=(
                    min(
                        [item.confidence for item in result.vision.observations],
                        default=1.0,
                    )
                    if result is not None and result.vision is not None
                    else None
                ),
            )
            all_risk_rows.append(assessment.as_dict())

    total = len(metas)
    schema_valid_count = sum(meta.schema_valid for meta in metas)
    timestamp_rows = [result for result in completed if _time_valid(result, bounds.get((result.sample_id, result.modality)))]
    searchable_rows = [
        result
        for result in completed
        if _is_searchable(result) and _time_valid(result, bounds.get((result.sample_id, result.modality)))
    ]
    weak_metrics: dict[str, list[float]] = defaultdict(list)
    known_statuses: list[str] = []
    for item in modality_reports:
        known_statuses.append(item.weak_reference_status)
        for name, value in item.weak_reference_metrics.items():
            weak_metrics[name].append(value)
    weak_status: Literal["known", "unknown", "partial"] = (
        "unknown" if not any(known_status == "known" for known_status in known_statuses)
        else "known" if all(known_status == "known" for known_status in known_statuses if modality_reports)
        else "partial"
    )
    for code, severity, ids in (
        ("schema_invalid", "error", [meta.sample_id for meta in metas if not meta.schema_valid]),
        ("provider_failure", "warning", [meta.sample_id for meta in metas if meta.schema_valid and meta.result and meta.result.terminal_state != "completed"]),
        ("timestamp_invalid", "error", [result.sample_id for result in completed if not _time_valid(result, bounds.get((result.sample_id, result.modality)))]),
        ("empty_search_fields", "warning", [result.sample_id for result in completed if not _is_searchable(result)]),
        ("cross_video_evidence", "error", [segment_id for segment_id in discovery_sample_ids if segment_id not in expected]),
    ):
        found = _issue(code, severity, ids)
        if found:
            issues.append(found)

    review_plan: dict[str, object] = {}
    if all_risk_rows:
        from .sampling import SamplingPolicy, select_review_samples

        plan = select_review_samples(all_risk_rows, policy=sampling_policy or SamplingPolicy())
        review_plan = plan.as_dict()

    return AutomaticQualityReport(
        report_id=report_id,
        revision=revision,
        sample_count=len(sample_ids),
        result_count=total,
        completed_count=len(completed),
        schema_valid_rate=schema_valid_count / total if total else 0.0,
        timestamp_valid_rate=len(timestamp_rows) / len(completed) if completed else 0.0,
        searchable_rate=len(searchable_rows) / len(completed) if completed else 0.0,
        cross_video_isolation_rate=round(isolation_rate, 6),
        weak_reference_status=weak_status,
        weak_reference_metrics={name: round(statistics.fmean(values), 6) for name, values in weak_metrics.items()},
        failure_counts=dict(all_failure_counts),
        issues=issues,
        modalities=modality_reports,
        risk_candidates=all_risk_rows,
        review_plan=review_plan,
        manual_truth_status=manual_truth_status,
        manual_truth_required_for_report=False,
        external_calls=0,
    )


# Short aliases make the layer easy to discover without coupling callers to
# the report's internal name.
automatic_quality_report = build_quality_report
build_auto_quality_report = build_quality_report


__all__ = [
    "AutomaticQualityReport",
    "InputBounds",
    "LatencySummary",
    "ModalityQuality",
    "QualityIssue",
    "QualityReportError",
    "UsageSummary",
    "WeakReference",
    "WeakReferenceCue",
    "automatic_quality_report",
    "build_auto_quality_report",
    "build_quality_report",
]
