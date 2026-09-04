"""Deterministic quality scoring for normalized benchmark outputs."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean
from typing import Iterable, Mapping, Sequence

from .protocol import DiscoverySegment, OCRResponse, VisionResponse
from .schema import BenchmarkCatalog, Query, TimeRange


class ScoringError(ValueError):
    """Raised when a score cannot be calculated from a complete truth record."""


def temporal_iou(left: TimeRange, right: TimeRange) -> float:
    overlap = max(0.0, min(left.end_sec, right.end_sec) - max(left.start_sec, right.start_sec))
    union = max(left.end_sec, right.end_sec) - min(left.start_sec, right.start_sec)
    return overlap / union if union else 0.0


def temporal_hit(candidate: TimeRange, accepted: Sequence[TimeRange], *, min_iou: float = 0.30, max_segment_sec: float = 120) -> bool:
    if candidate.duration_sec > max_segment_sec:
        return False
    midpoint = (candidate.start_sec + candidate.end_sec) / 2
    return any(
        temporal_iou(candidate, truth) >= min_iou or truth.start_sec <= midpoint <= truth.end_sec
        for truth in accepted
    )


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value


def keyword_recall(found_text: str, required_terms: Iterable[str]) -> float:
    terms = [normalize_text(term) for term in required_terms if normalize_text(term)]
    if not terms:
        return 1.0
    found = normalize_text(found_text)
    return sum(term in found for term in terms) / len(terms)


def normalized_exact_recall(found: Iterable[str], expected: Iterable[str]) -> float:
    expected_values = {normalize_text(value) for value in expected if normalize_text(value)}
    if not expected_values:
        return 1.0
    found_values = {normalize_text(value) for value in found if normalize_text(value)}
    return len(expected_values & found_values) / len(expected_values)


def pairwise_jaccard(sets: Sequence[set[str]]) -> float:
    if len(sets) < 2:
        return 1.0
    scores: list[float] = []
    for index, left in enumerate(sets):
        for right in sets[index + 1 :]:
            union = left | right
            scores.append(len(left & right) / len(union) if union else 1.0)
    return mean(scores) if scores else 1.0


@dataclass(frozen=True)
class RankedHit:
    sample_id: str
    segment_id: str
    time_range: TimeRange
    modalities: frozenset[str] = frozenset()
    text: str = ""
    rank: int = 1


@dataclass(frozen=True)
class QueryScore:
    query_id: str
    candidate_id: str
    run_id: str
    complete: bool
    schema_valid: bool
    top1_video_hit: bool
    top3_video_hit: bool
    temporal_hit: bool
    keyword_recall: float
    modality_hit: bool
    subgroup: str
    latency_ms: float | None = None
    estimated_cost_usd: float | None = None
    failure_code: str | None = None


@dataclass(frozen=True)
class QualityGateDecision:
    candidate_id: str
    eligible: bool
    metrics: Mapping[str, float]
    failed_gates: tuple[str, ...] = ()
    subgroup_metrics: Mapping[str, Mapping[str, float]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "eligible": self.eligible,
            "metrics": dict(self.metrics),
            "failed_gates": list(self.failed_gates),
            "subgroups": {key: dict(value) for key, value in self.subgroup_metrics.items()},
        }


def score_query(
    query: Query,
    *,
    candidate_id: str,
    run_id: str,
    hits: Sequence[RankedHit],
    schema_valid: bool = True,
    complete: bool = True,
    latency_ms: float | None = None,
    estimated_cost_usd: float | None = None,
) -> QueryScore:
    """Score one query without using title/author/URL metadata."""

    if query.annotation_status != "frozen":
        raise ScoringError("query truth annotation is not frozen")
    expected = set(query.expected_video_ids)
    top1 = bool(hits and hits[0].sample_id in expected)
    top3 = any(hit.sample_id in expected for hit in hits[:3])
    best = next((hit for hit in hits if hit.sample_id in expected), None)
    accepted_ranges = query.acceptable_time_ranges.get(best.sample_id, []) if best else []
    temporal = bool(best and accepted_ranges and temporal_hit(best.time_range, accepted_ranges))
    correct_top_hits = [hit for hit in hits[:3] if hit.sample_id in expected]
    text = " ".join(hit.text for hit in correct_top_hits)
    terms = keyword_recall(text, query.required_key_terms)
    required = set(query.required_modalities)
    modality_hit = any(
        required <= set(hit.modalities)
        and temporal_hit(hit.time_range, query.acceptable_time_ranges.get(hit.sample_id, []))
        for hit in correct_top_hits
    )
    return QueryScore(
        query_id=query.query_id,
        candidate_id=candidate_id,
        run_id=run_id,
        complete=complete,
        schema_valid=schema_valid,
        top1_video_hit=top1,
        top3_video_hit=top3,
        temporal_hit=temporal,
        keyword_recall=terms,
        modality_hit=modality_hit,
        subgroup=query.kind,
        latency_ms=latency_ms,
        estimated_cost_usd=estimated_cost_usd,
        failure_code=None if complete else "provider_incomplete",
    )


def _rate(values: Iterable[bool | float]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return sum(float(value) for value in values_list) / len(values_list)


def aggregate_quality(
    catalog: BenchmarkCatalog,
    candidate_id: str,
    query_scores: Sequence[QueryScore],
    *,
    schema_once_pass_rate: float | None = None,
    asr_keyword_recall_value: float | None = None,
    asr_language_subgroup_recall_value: float | None = None,
    ocr_exact_recall_value: float | None = None,
    ocr_small_text_recall_value: float | None = None,
    vision_required_recall_value: float | None = None,
    vision_hallucination_rate: float | None = None,
    asr_consistency_value: float | None = None,
    asr_boundary_drift_sec: float | None = None,
    ocr_consistency_value: float | None = None,
    vision_consistency_jaccard_value: float | None = None,
) -> QualityGateDecision:
    """Apply hard gates before any latency/cost comparison."""

    if not query_scores:
        raise ScoringError("cannot score an empty candidate")
    gates = catalog.quality_gates
    completed = [row for row in query_scores if row.complete]
    metrics = {
        "eligible_completion_rate": len(completed) / len(query_scores),
        "schema_once_pass_rate": schema_once_pass_rate if schema_once_pass_rate is not None else _rate(row.schema_valid for row in query_scores),
        "top3_video_hit_rate": _rate(row.top3_video_hit for row in completed),
        "top1_video_hit_rate": _rate(row.top1_video_hit for row in completed),
        "temporal_hit_rate": _rate(row.temporal_hit for row in completed),
        "keyword_recall": _rate(row.keyword_recall for row in completed),
        "modality_hit_rate": _rate(row.modality_hit for row in completed),
    }
    optional = {
        "asr_keyword_recall": asr_keyword_recall_value,
        "asr_language_subgroup_recall": asr_language_subgroup_recall_value,
        "ocr_exact_recall": ocr_exact_recall_value,
        "ocr_small_text_recall": ocr_small_text_recall_value,
        "vision_required_recall": vision_required_recall_value,
        "vision_hallucination_rate": vision_hallucination_rate,
        "asr_consistency": asr_consistency_value,
        "asr_boundary_drift_sec": asr_boundary_drift_sec,
        "ocr_consistency": ocr_consistency_value,
        "vision_consistency_jaccard": vision_consistency_jaccard_value,
    }
    metrics.update({key: value for key, value in optional.items() if value is not None})
    failed: list[str] = []
    checks: list[tuple[str, bool]] = [
        ("schema_once_pass_rate", metrics["schema_once_pass_rate"] >= gates.schema_once_pass_rate),
        ("eligible_completion_rate", metrics["eligible_completion_rate"] >= gates.eligible_completion_rate),
        ("top3_video_hit_rate", metrics["top3_video_hit_rate"] >= gates.top3_video_hit_rate),
        ("top1_video_hit_rate", metrics["top1_video_hit_rate"] >= gates.top1_video_hit_rate),
        ("temporal_hit_rate", metrics["temporal_hit_rate"] >= gates.temporal_hit_rate),
    ]
    profile = next((item for item in catalog.profiles if item.profile_id == candidate_id), None)
    if profile is None:
        raise ScoringError("candidate is not present in the frozen catalog")
    required_metrics: dict[str, tuple[float | None, bool]] = {}
    if profile.modality == "asr":
        required_metrics = {
            "asr_keyword_recall": (asr_keyword_recall_value, asr_keyword_recall_value is not None and asr_keyword_recall_value >= gates.asr_keyword_recall),
            "asr_language_subgroup_recall": (asr_language_subgroup_recall_value, asr_language_subgroup_recall_value is not None and asr_language_subgroup_recall_value >= gates.asr_language_subgroup_recall),
            "asr_consistency": (asr_consistency_value, asr_consistency_value is not None and asr_consistency_value >= gates.asr_consistency),
            "asr_boundary_drift_sec": (asr_boundary_drift_sec, asr_boundary_drift_sec is not None and asr_boundary_drift_sec <= gates.asr_boundary_drift_sec),
        }
    elif profile.modality == "ocr":
        required_metrics = {
            "ocr_exact_recall": (ocr_exact_recall_value, ocr_exact_recall_value is not None and ocr_exact_recall_value >= gates.ocr_exact_recall),
            "ocr_small_text_recall": (ocr_small_text_recall_value, ocr_small_text_recall_value is not None and ocr_small_text_recall_value >= gates.ocr_small_text_recall),
            "ocr_consistency": (ocr_consistency_value, ocr_consistency_value is not None and ocr_consistency_value >= gates.ocr_consistency),
        }
    else:
        required_metrics = {
            "vision_required_recall": (vision_required_recall_value, vision_required_recall_value is not None and vision_required_recall_value >= gates.vision_required_recall),
            "vision_hallucination_rate": (vision_hallucination_rate, vision_hallucination_rate is not None and vision_hallucination_rate <= gates.vision_hallucination_rate),
            "vision_consistency_jaccard": (vision_consistency_jaccard_value, vision_consistency_jaccard_value is not None and vision_consistency_jaccard_value >= gates.vision_consistency_jaccard),
        }
    checks.extend((name, passed) for name, (_value, passed) in required_metrics.items())
    failed.extend(name for name, passed in checks if not passed)

    subgroup_metrics: dict[str, Mapping[str, float]] = {}
    for subgroup in sorted({row.subgroup for row in query_scores}):
        rows = [row for row in query_scores if row.subgroup == subgroup]
        subgroup_metrics[subgroup] = {
            "count": float(len(rows)),
            "top3_video_hit_rate": _rate(row.complete and row.top3_video_hit for row in rows),
            "temporal_hit_rate": _rate(row.complete and row.temporal_hit for row in rows),
        }
        if subgroup_metrics[subgroup]["top3_video_hit_rate"] < gates.subgroup_hit_rate:
            failed.append(f"{subgroup}.top3_video_hit_rate")
        if subgroup_metrics[subgroup]["temporal_hit_rate"] < gates.subgroup_hit_rate:
            failed.append(f"{subgroup}.temporal_hit_rate")
    return QualityGateDecision(candidate_id, not failed, metrics, tuple(dict.fromkeys(failed)), subgroup_metrics)


def score_ocr_response(response: OCRResponse, expected_text: Iterable[str]) -> float:
    return normalized_exact_recall((item.text for item in response.detections), expected_text)


def score_vision_response(response: VisionResponse, required_labels: Iterable[str], unsupported_labels: Iterable[str] = ()) -> tuple[float, float]:
    observed = {normalize_text(value) for item in response.observations for value in (*item.entities, *item.actions, *item.screen_topics)}
    expected = {normalize_text(value) for value in required_labels if normalize_text(value)}
    required_recall = len(observed & expected) / len(expected) if expected else 1.0
    unsupported = {normalize_text(value) for value in unsupported_labels if normalize_text(value)}
    hallucination = len(observed & unsupported) / len(observed) if observed else 0.0
    return required_recall, hallucination
