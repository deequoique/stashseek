"""Allow-listed evaluation events for optional external observability.

These events are deliberately smaller than recognition responses.  They carry
counts, bounded numeric measurements, stable statuses, and local IDs only.
They do not carry media, subtitles, prompt/answer text, URLs, credentials, or
provider response bodies.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .protocol import ASRResponse, OCRResponse, RecognitionJobResult, VisionResponse
from .quality_report import UsageSummary
from .sampling import RiskAssessment
from .schema import Modality, StrictModel, TimeRange


class EvaluationEventError(ValueError):
    """Raised when an event cannot satisfy the outbound allow-list."""


class EventTimeRange(StrictModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)

    @field_validator("start_sec", "end_sec")
    @classmethod
    def finite_time(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("event timestamps must be finite")
        return value

    @model_validator(mode="after")
    def ordered(self) -> "EventTimeRange":
        if self.end_sec <= self.start_sec:
            raise ValueError("event time range must be ordered")
        return self


class EventInputSummary(StrictModel):
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    media_kind: Literal["audio_window", "frame_bundle", "frame"]
    duration_bucket: Literal["unknown", "lt_10s", "10_60s", "gt_60s"] = "unknown"
    time_range: EventTimeRange | None = None
    frame_count: int = Field(default=0, ge=0, le=100)
    has_subtitles: bool | None = None
    weak_reference_signal_count: int = Field(default=0, ge=0, le=100)


class EventPredictionSummary(StrictModel):
    terminal_state: Literal["completed", "provider_incomplete", "harness_invalid"]
    searchable: bool = False
    segment_count: int = Field(default=0, ge=0)
    word_count: int = Field(default=0, ge=0)
    observation_count: int = Field(default=0, ge=0)
    detection_count: int = Field(default=0, ge=0)
    evidence_frame_count: int = Field(default=0, ge=0)
    extracted_text_length: int = Field(default=0, ge=0)
    timestamp_range: EventTimeRange | None = None


class EvaluationScore(StrictModel):
    name: str = Field(pattern=r"[a-z][a-z0-9_.-]{1,79}")
    value: float | None = Field(default=None, ge=0, le=1)
    numerator: int | None = Field(default=None, ge=0)
    denominator: int | None = Field(default=None, ge=0)
    known: bool = True

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("evaluation score must be finite")
        return value

    @model_validator(mode="after")
    def valid_denominator(self) -> "EvaluationScore":
        if self.denominator is not None and self.numerator is not None and self.numerator > self.denominator:
            raise ValueError("score numerator exceeds denominator")
        if self.known and self.value is None:
            raise ValueError("known score requires a value")
        return self


class EvaluationTrace(StrictModel):
    trace_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    stage: Literal["recognition", "normalization", "quality_report", "sampling", "human_review"]
    attempt_index: int = Field(default=1, ge=1, le=100)


class EvaluationEvent(StrictModel):
    """A strict, content-free event suitable for optional SaaS mirroring."""

    protocol_version: Literal["video-evaluation-event.v1"] = "video-evaluation-event.v1"
    event_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    run_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    candidate_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    modality: Modality
    provider: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=160)
    input_summary: EventInputSummary
    prediction: EventPredictionSummary
    scores: list[EvaluationScore] = Field(default_factory=list, max_length=50)
    risk_score: float = Field(default=0, ge=0, le=1)
    risk_reasons: list[str] = Field(default_factory=list, max_length=20)
    latency_ms: float | None = Field(default=None, ge=0)
    usage: UsageSummary = Field(default_factory=UsageSummary)
    failure_code: str | None = Field(default=None, pattern=r"[a-z][a-z0-9_]{2,79}")
    trace: EvaluationTrace | None = None

    @field_validator("provider", "model_version")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        if "http://" in value.casefold() or "https://" in value.casefold() or "bearer " in value.casefold():
            raise ValueError("event identifiers must not contain URLs or authorization values")
        return value

    @field_validator("latency_ms", "risk_score")
    @classmethod
    def finite_numbers(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("event numeric values must be finite")
        return value

    @field_validator("risk_reasons")
    @classmethod
    def stable_reasons(cls, values: list[str]) -> list[str]:
        from .sampling import _RISK_REASONS

        if any(value not in _RISK_REASONS for value in values):
            raise ValueError("event risk reasons must be stable labels")
        if len(values) != len(set(values)):
            raise ValueError("event risk reasons must be unique")
        return values

    @model_validator(mode="after")
    def identity_matches(self) -> "EvaluationEvent":
        if self.input_summary.sample_id != self.sample_id:
            raise ValueError("event input/sample identity mismatch")
        if self.prediction.terminal_state == "completed" and self.failure_code is not None:
            raise ValueError("completed events cannot carry failure_code")
        if self.prediction.terminal_state != "completed" and self.failure_code is None:
            raise ValueError("non-completed events require failure_code")
        return self

    def as_dict(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        assert_event_safe(payload)
        return payload


class EvaluationEventBatch(StrictModel):
    protocol_version: Literal["video-evaluation-event-batch.v1"] = "video-evaluation-event-batch.v1"
    events: list[EvaluationEvent] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def unique_events(self) -> "EvaluationEventBatch":
        ids = [event.event_id for event in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("evaluation event IDs must be unique")
        return self

    def as_dict(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        assert_event_safe(payload)
        return payload


_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?i)(?:bearer\s+|(?:sk|key|token|secret)[-_:=\s]+)[A-Za-z0-9_./+=:-]{8,}")
_FORBIDDEN_KEYS = {"prompt", "query", "question", "answer", "text", "content", "body", "payload", "url", "uri", "api_key", "access_token", "cookie", "raw_response", "provider_response", "media_bytes", "audio_bytes", "frame_bytes"}


def assert_event_safe(value: object) -> None:
    """Fail closed if a future event change introduces content or secrets."""

    def walk(item: object, path: str = "event") -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                key_text = str(key).casefold()
                if key_text in _FORBIDDEN_KEYS:
                    raise EvaluationEventError(f"forbidden event field at {path}.{key}")
                if _URL_RE.search(str(child)) if isinstance(child, str) else False:
                    raise EvaluationEventError(f"forbidden event URL at {path}.{key}")
                walk(child, f"{path}.{key}")
        elif isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                walk(child, f"{path}[{index}]")
        elif isinstance(item, str) and (_URL_RE.search(item) or _SECRET_RE.search(item)):
            raise EvaluationEventError(f"forbidden event value at {path}")

    walk(value)


def _bucket_duration(value: float | None) -> Literal["unknown", "lt_10s", "10_60s", "gt_60s"]:
    if value is None or not math.isfinite(value) or value <= 0:
        return "unknown"
    if value < 10:
        return "lt_10s"
    if value <= 60:
        return "10_60s"
    return "gt_60s"


def _event_timestamp_range(result: RecognitionJobResult) -> EventTimeRange | None:
    values: list[float] = []
    if isinstance(result.asr, ASRResponse):
        values = [value for segment in result.asr.segments for value in (segment.start_sec, segment.end_sec)]
    elif isinstance(result.vision, VisionResponse):
        values = [value for observation in result.vision.observations for value in observation.evidence_frame_times]
    elif isinstance(result.ocr, OCRResponse):
        values = [detection.frame_timestamp_sec for detection in result.ocr.detections]
    return EventTimeRange(start_sec=min(values), end_sec=max(values) + (0.01 if result.modality != "asr" else 0.001)) if values else None


def _prediction_summary(result: RecognitionJobResult) -> EventPredictionSummary:
    timestamp_range = _event_timestamp_range(result)
    if isinstance(result.asr, ASRResponse):
        word_count = sum(len(segment.words) for segment in result.asr.segments)
        text_length = sum(len(segment.text) for segment in result.asr.segments)
        return EventPredictionSummary(
            terminal_state=result.terminal_state,
            searchable=bool(result.asr.segments),
            segment_count=len(result.asr.segments),
            word_count=word_count,
            extracted_text_length=text_length,
            timestamp_range=timestamp_range,
        )
    if isinstance(result.vision, VisionResponse):
        text_length = sum(
            len(value)
            for observation in result.vision.observations
            for value in (observation.scene_type, observation.short_description, *observation.search_terms)
        )
        return EventPredictionSummary(
            terminal_state=result.terminal_state,
            searchable=bool(result.vision.observations),
            observation_count=len(result.vision.observations),
            evidence_frame_count=sum(len(item.evidence_frame_times) for item in result.vision.observations),
            extracted_text_length=text_length,
            timestamp_range=timestamp_range,
        )
    if isinstance(result.ocr, OCRResponse):
        return EventPredictionSummary(
            terminal_state=result.terminal_state,
            searchable=bool(result.ocr.detections),
            detection_count=len(result.ocr.detections),
            evidence_frame_count=len({item.frame_timestamp_sec for item in result.ocr.detections}),
            extracted_text_length=sum(len(item.text) for item in result.ocr.detections),
            timestamp_range=timestamp_range,
        )
    return EventPredictionSummary(terminal_state=result.terminal_state)


def _default_input_summary(result: RecognitionJobResult) -> EventInputSummary:
    prediction = _prediction_summary(result)
    if result.modality == "asr":
        media_kind: Literal["audio_window", "frame_bundle", "frame"] = "audio_window"
    elif result.modality == "vision":
        media_kind = "frame_bundle"
    else:
        media_kind = "frame"
    frame_count = prediction.evidence_frame_count if result.modality != "asr" else 0
    return EventInputSummary(
        sample_id=result.sample_id,
        media_kind=media_kind,
        frame_count=frame_count,
    )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def build_evaluation_event(
    result: RecognitionJobResult,
    *,
    input_summary: EventInputSummary | Mapping[str, object] | None = None,
    scores: Mapping[str, float | None] | Sequence[EvaluationScore] = (),
    risk: RiskAssessment | Mapping[str, object] | None = None,
    trace_id: str | None = None,
    stage: Literal["recognition", "normalization", "quality_report", "sampling", "human_review"] = "quality_report",
) -> EvaluationEvent:
    """Project one normalized result into an outbound-safe event."""

    if not isinstance(result, RecognitionJobResult):
        raise TypeError("build_evaluation_event requires a RecognitionJobResult")
    prediction = _prediction_summary(result)
    if isinstance(scores, Mapping):
        score_rows = [EvaluationScore(name=name, value=value, known=value is not None) for name, value in sorted(scores.items())]
    else:
        score_rows = list(scores)
    if risk is None:
        from .sampling import assess_risk

        risk_row = assess_risk(
            sample_id=result.sample_id,
            modality=result.modality,
            unit_id=f"{result.sample_id.lower()}.{result.modality}.{result.candidate_id}",
            candidate_id=result.candidate_id,
            reasons=["provider_failure"] if result.terminal_state != "completed" else [],
        )
    elif isinstance(risk, RiskAssessment):
        risk_row = risk
    else:
        risk_row = RiskAssessment.model_validate(risk)
    if input_summary is None:
        input_row = _default_input_summary(result)
    elif isinstance(input_summary, EventInputSummary):
        input_row = input_summary
    else:
        input_row = EventInputSummary.model_validate(input_summary)
    trace = EvaluationTrace(
        trace_id=trace_id or _stable_id("trace", result.run_id, result.candidate_id, result.sample_id, str(result.run_index), result.modality),
        stage=stage,
        attempt_index=result.run_index,
    )
    usage = UsageSummary(
        audio_seconds=result.usage.audio_seconds,
        image_count=result.usage.image_count,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        estimated_cost_usd=result.usage.estimated_cost_usd,
        known_fields=[
            name
            for name, value in (
                ("input_tokens", result.usage.input_tokens),
                ("output_tokens", result.usage.output_tokens),
                ("estimated_cost_usd", result.usage.estimated_cost_usd),
            )
            if value is not None
        ],
    )
    return EvaluationEvent(
        event_id=_stable_id("event", result.run_id, result.candidate_id, result.sample_id, str(result.run_index), result.modality),
        run_id=result.run_id,
        candidate_id=result.candidate_id,
        sample_id=result.sample_id,
        modality=result.modality,
        provider=(result.asr or result.vision or result.ocr).provider if prediction.terminal_state == "completed" else "unknown",
        model_version=(result.asr or result.vision or result.ocr).model_version if prediction.terminal_state == "completed" else "unknown",
        input_summary=input_row,
        prediction=prediction,
        scores=score_rows,
        risk_score=risk_row.risk_score,
        risk_reasons=risk_row.reasons,
        latency_ms=result.latency_ms,
        usage=usage,
        failure_code=result.error.error_code if result.error else None,
        trace=trace,
    )


def build_evaluation_event_batch(events: Iterable[EvaluationEvent]) -> EvaluationEventBatch:
    return EvaluationEventBatch(events=list(events))


__all__ = [
    "EvaluationEvent",
    "EvaluationEventBatch",
    "EvaluationEventError",
    "EvaluationScore",
    "EvaluationTrace",
    "EventInputSummary",
    "EventPredictionSummary",
    "EventTimeRange",
    "assert_event_safe",
    "build_evaluation_event",
    "build_evaluation_event_batch",
]
