"""Provider-neutral recognition protocol models and safe error projection."""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schema import Modality, RunTerminalState, StrictModel, TimeRange


class ProtocolValidationError(ValueError):
    """Raised when a provider result cannot be normalized safely."""


class Usage(StrictModel):
    audio_seconds: float = Field(default=0, ge=0)
    image_count: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)

    @field_validator("audio_seconds", "estimated_cost_usd")
    @classmethod
    def finite_numbers(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("usage values must be finite")
        return value


class ASRWord(StrictModel):
    text: str = Field(min_length=1, max_length=500)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def valid_word(self) -> "ASRWord":
        if not math.isfinite(self.start_sec) or not math.isfinite(self.end_sec):
            raise ValueError("ASR word timestamps must be finite")
        if self.end_sec <= self.start_sec:
            raise ValueError("ASR word end must be after start")
        return self


class ASRSegment(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    words: list[ASRWord] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def valid_segment(self) -> "ASRSegment":
        if not math.isfinite(self.start_sec) or not math.isfinite(self.end_sec):
            raise ValueError("ASR segment timestamps must be finite")
        if self.end_sec <= self.start_sec:
            raise ValueError("ASR segment end must be after start")
        previous = self.start_sec
        for word in self.words:
            if word.start_sec < self.start_sec or word.end_sec > self.end_sec or word.start_sec < previous:
                raise ValueError("ASR word timestamps must be ordered within the segment")
            previous = word.start_sec
        return self


class ASRResponse(StrictModel):
    protocol_version: Literal["asr.v1"] = "asr.v1"
    run_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    input_window_id: str = Field(min_length=2, max_length=128)
    provider: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=160)
    language: str = Field(min_length=1, max_length=32)
    # Empty is valid for a bounded music/silence window; the protocol still
    # requires timestamps whenever speech is present.
    segments: list[ASRSegment] = Field(default_factory=list, max_length=1000)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(ge=0)

    @field_validator("latency_ms")
    @classmethod
    def finite_latency(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("latency_ms must be finite")
        return value

    @model_validator(mode="after")
    def ordered_segments(self) -> "ASRResponse":
        previous = -1.0
        for segment in self.segments:
            if segment.start_sec < previous:
                raise ValueError("ASR segments must be time ordered")
            previous = segment.start_sec
        return self


class VisionObservation(StrictModel):
    scene_type: str = Field(min_length=1, max_length=120)
    short_description: str = Field(min_length=1, max_length=500)
    entities: list[str] = Field(default_factory=list, max_length=30)
    actions: list[str] = Field(default_factory=list, max_length=30)
    screen_topics: list[str] = Field(default_factory=list, max_length=30)
    search_terms: list[str] = Field(default_factory=list, max_length=30)
    evidence_frame_times: list[float] = Field(min_length=1, max_length=6)
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence_frame_times")
    @classmethod
    def finite_frame_times(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("vision evidence frame times must be finite and non-negative")
        return values


class VisionResponse(StrictModel):
    protocol_version: Literal["vision.v1"] = "vision.v1"
    run_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    input_bundle_id: str = Field(min_length=2, max_length=128)
    provider: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=160)
    observations: list[VisionObservation] = Field(min_length=1, max_length=30)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(ge=0)

    @field_validator("latency_ms")
    @classmethod
    def finite_latency(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("latency_ms must be finite")
        return value


class PolygonPoint(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)

    @field_validator("x", "y")
    @classmethod
    def finite_coordinate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("OCR coordinates must be finite")
        return value


class OCRDetection(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    polygon: list[PolygonPoint] = Field(min_length=4, max_length=4)
    confidence: float = Field(ge=0, le=1)
    frame_timestamp_sec: float = Field(ge=0)

    @field_validator("frame_timestamp_sec")
    @classmethod
    def finite_frame_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("OCR frame timestamp must be finite")
        return value


class OCRResponse(StrictModel):
    protocol_version: Literal["ocr.v1"] = "ocr.v1"
    run_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    input_frame_id: str = Field(min_length=2, max_length=128)
    provider: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=160)
    detections: list[OCRDetection] = Field(default_factory=list, max_length=200)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = Field(ge=0)

    @field_validator("latency_ms")
    @classmethod
    def finite_latency(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("latency_ms must be finite")
        return value


class DiscoverySegment(StrictModel):
    """Search-oriented projection, independent of provider response shape."""

    protocol_version: Literal["discovery-segment.v1"] = "discovery-segment.v1"
    segment_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    time_range: TimeRange
    text: str = Field(default="", max_length=4000)
    scene_type: str | None = Field(default=None, max_length=120)
    search_terms: list[str] = Field(default_factory=list, max_length=50)
    evidence_modalities: list[Modality] = Field(min_length=1, max_length=3)
    evidence_frame_times: list[float] = Field(default_factory=list, max_length=6)
    answer_eligible: bool = False

    @model_validator(mode="after")
    def valid_segment_projection(self) -> "DiscoverySegment":
        if not self.text.strip() and not self.search_terms and not self.scene_type:
            raise ValueError("discovery segment needs at least one content projection")
        if "vision" in self.evidence_modalities and not self.evidence_frame_times:
            raise ValueError("vision discovery evidence needs frame times")
        if any(not math.isfinite(value) or value < 0 for value in self.evidence_frame_times):
            raise ValueError("discovery evidence frame times must be finite")
        if len(set(self.evidence_modalities)) != len(self.evidence_modalities):
            raise ValueError("discovery evidence modalities must be unique")
        if any(not self.time_range.start_sec <= value <= self.time_range.end_sec for value in self.evidence_frame_times):
            raise ValueError("discovery evidence frame times must lie within the segment")
        return self


class MediaSampleBundle(StrictModel):
    """Descriptor for ephemeral inputs; it contains no bytes or filesystem path."""

    protocol_version: Literal["media-sample-bundle.v1"] = "media-sample-bundle.v1"
    bundle_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    revision: str = Field(pattern=r"benchmark-v[0-9]+")
    audio_windows: list[TimeRange] = Field(min_length=1, max_length=2)
    frame_bundles: list[list[float]] = Field(min_length=1, max_length=2)
    ocr_frame_times: list[float] = Field(default_factory=list, max_length=3)
    ephemeral_only: bool = True
    complete_media_present: bool = False

    @model_validator(mode="after")
    def enforce_boundary(self) -> "MediaSampleBundle":
        if not self.ephemeral_only or self.complete_media_present:
            raise ValueError("MediaSampleBundle cannot contain complete or persistent media")
        if any(len(times) < 3 or len(times) > 6 for times in self.frame_bundles):
            raise ValueError("frame bundles must contain three to six frame times")
        if any(not math.isfinite(value) or value < 0 for times in self.frame_bundles for value in times):
            raise ValueError("frame times must be finite and non-negative")
        if any(times != sorted(set(times)) for times in self.frame_bundles):
            raise ValueError("frame bundle times must be unique and ordered")
        if any(not math.isfinite(value) or value < 0 for value in self.ocr_frame_times):
            raise ValueError("OCR frame times must be finite and non-negative")
        if any(window.duration_sec > 60 for window in self.audio_windows):
            raise ValueError("audio windows may not exceed the 60 second benchmark boundary")
        return self


class StableError(StrictModel):
    """Safe error envelope: no provider body, URL, stderr, or exception text."""

    protocol_version: Literal["recognition-error.v1"] = "recognition-error.v1"
    error_code: str = Field(pattern=r"[a-z][a-z0-9_]{2,79}")
    error_class: str = Field(pattern=r"[A-Za-z_][A-Za-z0-9_]{0,79}")
    retryable: bool = False
    phase: Literal["admission", "media", "provider", "normalize", "score", "cleanup", "harness"]


class RecognitionJobResult(StrictModel):
    protocol_version: Literal["recognition-result.v1"] = "recognition-result.v1"
    run_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    candidate_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    run_index: int = Field(ge=1, le=3)
    modality: Modality
    terminal_state: RunTerminalState
    latency_ms: float | None = Field(default=None, ge=0)
    usage: Usage = Field(default_factory=Usage)
    asr: ASRResponse | None = None
    vision: VisionResponse | None = None
    ocr: OCRResponse | None = None
    error: StableError | None = None

    @field_validator("latency_ms")
    @classmethod
    def finite_result_latency(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("result latency must be finite")
        return value

    @model_validator(mode="after")
    def result_matches_state(self) -> "RecognitionJobResult":
        if self.terminal_state == "completed":
            expected = {"asr": self.asr, "vision": self.vision, "ocr": self.ocr}[self.modality]
            if expected is None or self.error is not None:
                raise ValueError("completed result needs the matching normalized response")
            responses = [value for value in (self.asr, self.vision, self.ocr) if value is not None]
            if len(responses) != 1:
                raise ValueError("completed result must contain exactly one modality response")
            if self.latency_ms is None:
                raise ValueError("completed result needs latency_ms")
            if expected.run_id != self.run_id or expected.sample_id != self.sample_id:
                raise ValueError("result and normalized response identities must agree")
            if expected.latency_ms != self.latency_ms or expected.usage != self.usage:
                raise ValueError("result and normalized response accounting must agree")
        else:
            if self.error is None:
                raise ValueError("non-completed result needs a stable error")
            if any(value is not None for value in (self.asr, self.vision, self.ocr)):
                raise ValueError("non-completed result cannot contain normalized responses")
        if self.terminal_state == "provider_incomplete" and self.error.phase != "provider":
            raise ValueError("provider_incomplete must use a provider-phase error")
        if self.terminal_state == "harness_invalid" and self.error.phase not in {"harness", "normalize", "cleanup"}:
            raise ValueError("harness_invalid must use a harness/normalize/cleanup error")
        return self


def stable_error(
    error_code: str,
    *,
    error_class: str = "BenchmarkError",
    retryable: bool = False,
    phase: Literal["admission", "media", "provider", "normalize", "score", "cleanup", "harness"] = "harness",
) -> StableError:
    """Create an error envelope without serializing an exception message."""

    return StableError(
        error_code=error_code,
        error_class=error_class,
        retryable=retryable,
        phase=phase,
    )


def project_discovery_segments(
    *,
    sample_id: str,
    asr: ASRResponse | None = None,
    vision: VisionResponse | None = None,
    ocr: OCRResponse | None = None,
) -> list[DiscoverySegment]:
    """Project normalized modality responses into isolated search segments."""

    segments: list[DiscoverySegment] = []
    if asr is not None:
        for index, item in enumerate(asr.segments, 1):
            segments.append(
                DiscoverySegment(
                    segment_id=f"{sample_id.lower()}.asr.{index}",
                    sample_id=sample_id,
                    time_range=TimeRange(start_sec=item.start_sec, end_sec=item.end_sec),
                    text=item.text,
                    search_terms=[word.text for word in item.words[:30]],
                    evidence_modalities=["asr"],
                    answer_eligible=True,
                )
            )
    if vision is not None:
        for index, item in enumerate(vision.observations, 1):
            start = min(item.evidence_frame_times)
            end = max(item.evidence_frame_times) + 0.01
            segments.append(
                DiscoverySegment(
                    segment_id=f"{sample_id.lower()}.vision.{index}",
                    sample_id=sample_id,
                    time_range=TimeRange(start_sec=start, end_sec=end),
                    scene_type=item.scene_type,
                    text=item.short_description,
                    search_terms=[*item.entities, *item.actions, *item.screen_topics, *item.search_terms],
                    evidence_modalities=["vision"],
                    evidence_frame_times=item.evidence_frame_times,
                    answer_eligible=True,
                )
            )
    if ocr is not None:
        for index, item in enumerate(ocr.detections, 1):
            segments.append(
                DiscoverySegment(
                    segment_id=f"{sample_id.lower()}.ocr.{index}",
                    sample_id=sample_id,
                    time_range=TimeRange(start_sec=item.frame_timestamp_sec, end_sec=item.frame_timestamp_sec + 0.01),
                    text=item.text,
                    search_terms=[item.text],
                    evidence_modalities=["ocr"],
                    evidence_frame_times=[item.frame_timestamp_sec],
                    answer_eligible=True,
                )
            )
    return segments
