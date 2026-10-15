"""Deterministic risk scoring and low-effort human review sampling."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .schema import Modality, StrictModel


RiskStratum = Literal["low", "medium", "high", "critical"]
ReviewStatus = Literal["pending", "accepted", "rejected", "disputed"]

_RISK_REASONS = {
    "schema_invalid",
    "timestamp_invalid",
    "cross_video_evidence",
    "provider_failure",
    "empty_search_fields",
    "weak_reference_missing",
    "proxy_conflict",
    "weak_reference_conflict",
    "low_confidence",
    "no_subtitles",
    "partial_subtitle_coverage",
    "long_video",
    "dense_visual",
    "cross_language",
    "model_changed",
    "historical_failure",
    "manual_dispute",
    "non_deterministic",
    "no_evidence",
    "other_risk",
}

_RISK_WEIGHTS = {
    "schema_invalid": 1.0,
    "cross_video_evidence": 1.0,
    "timestamp_invalid": 0.95,
    "provider_failure": 0.85,
    "empty_search_fields": 0.75,
    "manual_dispute": 0.8,
    "non_deterministic": 0.7,
    "weak_reference_conflict": 0.6,
    "proxy_conflict": 0.6,
    "historical_failure": 0.55,
    "low_confidence": 0.45,
    "dense_visual": 0.3,
    "cross_language": 0.25,
    "no_subtitles": 0.2,
    "partial_subtitle_coverage": 0.2,
    "long_video": 0.15,
    "weak_reference_missing": 0.15,
    "model_changed": 0.15,
    "no_evidence": 0.4,
    "other_risk": 0.25,
}

_REASON_ALIASES = {
    "weak_reference_conflict": "weak_reference_conflict",
    "proxy_conflict": "proxy_conflict",
    "cross_video": "cross_video_evidence",
    "cross_video_evidence": "cross_video_evidence",
    "failed": "provider_failure",
    "provider_incomplete": "provider_failure",
    "history_failure": "historical_failure",
}


class RiskSignals(StrictModel):
    """Stable inputs to the risk scorer; no media or free-form content."""

    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    modality: Modality
    unit_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    candidate_id: str = Field(default="unknown", pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    confidence: float | None = Field(default=None, ge=0, le=1)
    reasons: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("confidence")
    @classmethod
    def finite_confidence(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("confidence must be finite")
        return value


class RiskAssessment(StrictModel):
    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    modality: Modality
    unit_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    candidate_id: str = Field(default="unknown", pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    risk_score: float = Field(ge=0, le=1)
    stratum: RiskStratum
    reasons: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("risk_score")
    @classmethod
    def finite_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("risk score must be finite")
        return round(value, 6)

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class ReviewCandidate(RiskAssessment):
    baseline: bool = False
    selected: bool = False
    review_status: ReviewStatus = "pending"
    review_codes: list[str] = Field(default_factory=list, max_length=20)


class SamplingPolicy(StrictModel):
    """Small fixed baseline plus risk-triggered supplement policy."""

    fixed_per_modality: int = Field(default=1, ge=0, le=10)
    risk_threshold: float = Field(default=0.5, ge=0, le=1)
    max_supplement_per_modality: int = Field(default=2, ge=0, le=20)
    max_total: int = Field(default=12, ge=1, le=1000)

    @field_validator("risk_threshold")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("risk threshold must be finite")
        return value


class SamplingPlan(StrictModel):
    policy: SamplingPolicy
    selected: list[ReviewCandidate] = Field(default_factory=list, max_length=1000)
    deferred: list[ReviewCandidate] = Field(default_factory=list, max_length=2000)
    fixed_count: int = Field(ge=0)
    supplemental_count: int = Field(ge=0)

    @model_validator(mode="after")
    def unique_units(self) -> "SamplingPlan":
        ids = [candidate.unit_id for candidate in [*self.selected, *self.deferred]]
        if len(ids) != len(set(ids)):
            raise ValueError("sampling plan unit IDs must be unique")
        if self.fixed_count != sum(candidate.baseline for candidate in self.selected):
            raise ValueError("sampling plan fixed count is inconsistent")
        if self.supplemental_count != len(self.selected) - self.fixed_count:
            raise ValueError("sampling plan supplemental count is inconsistent")
        return self

    def as_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy.model_dump(mode="json"),
            "selected": [item.model_dump(mode="json") for item in self.selected],
            "deferred": [item.model_dump(mode="json") for item in self.deferred],
            "fixed_count": self.fixed_count,
            "supplemental_count": self.supplemental_count,
            "selected_count": len(self.selected),
            "deferred_count": len(self.deferred),
        }


class ReviewFeedback(StrictModel):
    """Human outcome that can be merged without carrying review prose."""

    unit_id: str = Field(pattern=r"[a-z0-9][a-z0-9_.-]{2,127}")
    status: ReviewStatus
    defect_codes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("defect_codes")
    @classmethod
    def stable_defect_codes(cls, values: list[str]) -> list[str]:
        if any(value not in _RISK_REASONS for value in values):
            raise ValueError("review defect codes must be stable risk reasons")
        if len(values) != len(set(values)):
            raise ValueError("review defect codes must be unique")
        return values


def _canonical_reasons(reasons: Iterable[str]) -> list[str]:
    values = []
    for reason in reasons:
        normalized = _REASON_ALIASES.get(str(reason).strip(), str(reason).strip())
        if normalized not in _RISK_REASONS:
            normalized = "other_risk"
        if normalized not in values:
            values.append(normalized)
    return values


def _stratum(score: float) -> RiskStratum:
    if score >= 0.9:
        return "critical"
    if score >= 0.6:
        return "high"
    if score >= 0.3:
        return "medium"
    return "low"


def assess_risk(
    *,
    sample_id: str,
    modality: Modality,
    unit_id: str,
    candidate_id: str = "unknown",
    reasons: Iterable[str] = (),
    confidence: float | None = None,
) -> RiskAssessment:
    """Return a bounded additive risk score with stable reason labels."""

    canonical = _canonical_reasons(reasons)
    if confidence is not None:
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be finite and between zero and one")
        if confidence < 0.5 and "low_confidence" not in canonical:
            canonical.append("low_confidence")
    score = sum(_RISK_WEIGHTS.get(reason, 0.0) for reason in canonical)
    score = min(1.0, score)
    return RiskAssessment(
        sample_id=sample_id,
        modality=modality,
        unit_id=unit_id,
        candidate_id=candidate_id,
        risk_score=score,
        stratum=_stratum(score),
        reasons=canonical,
    )


def compute_risk_score(**kwargs: object) -> float:
    """Numeric convenience wrapper for callers that need only the score."""

    return assess_risk(**kwargs).risk_score  # type: ignore[arg-type]


def risk_score(**kwargs: object) -> float:
    return compute_risk_score(**kwargs)


def _coerce_candidate(value: ReviewCandidate | RiskAssessment | Mapping[str, object]) -> ReviewCandidate:
    if isinstance(value, ReviewCandidate):
        return value
    if isinstance(value, RiskAssessment):
        return ReviewCandidate(**value.model_dump(mode="python"))
    return ReviewCandidate.model_validate(value)


def select_review_samples(
    candidates: Sequence[ReviewCandidate | RiskAssessment | Mapping[str, object]],
    *,
    policy: SamplingPolicy | None = None,
    excluded_unit_ids: Iterable[str] = (),
) -> SamplingPlan:
    """Select deterministic baseline and risk-triggered review units.

    The first sorted unit for each modality is the fixed baseline.  Remaining
    units are selected by descending risk score and stable IDs, so a repeated
    run produces the same review queue.
    """

    selected_policy = policy or SamplingPolicy()
    excluded = set(excluded_unit_ids)
    by_unit: dict[str, ReviewCandidate] = {}
    for item in candidates:
        candidate = _coerce_candidate(item)
        if candidate.unit_id in by_unit:
            raise ValueError("review candidates must have unique unit IDs")
        by_unit[candidate.unit_id] = candidate
    available = [candidate for candidate in by_unit.values() if candidate.unit_id not in excluded]
    groups: dict[Modality, list[ReviewCandidate]] = defaultdict(list)
    for candidate in available:
        groups[candidate.modality].append(candidate)
    chosen: dict[str, ReviewCandidate] = {}
    for modality in sorted(groups):
        baseline = sorted(groups[modality], key=lambda item: (item.sample_id, item.unit_id))[: selected_policy.fixed_per_modality]
        for candidate in baseline:
            chosen[candidate.unit_id] = candidate.model_copy(update={"baseline": True, "selected": True})
    remaining_capacity = max(0, selected_policy.max_total - len(chosen))
    if remaining_capacity:
        supplement: list[ReviewCandidate] = []
        for modality in sorted(groups):
            baseline_ids = {item.unit_id for item in groups[modality] if item.unit_id in chosen}
            eligible = [
                candidate
                for candidate in groups[modality]
                if candidate.unit_id not in baseline_ids and candidate.risk_score >= selected_policy.risk_threshold
            ]
            supplement.extend(
                sorted(eligible, key=lambda item: (-item.risk_score, item.sample_id, item.unit_id))[: selected_policy.max_supplement_per_modality]
            )
        for candidate in sorted(supplement, key=lambda item: (-item.risk_score, item.sample_id, item.unit_id))[:remaining_capacity]:
            chosen[candidate.unit_id] = candidate.model_copy(update={"selected": True, "baseline": False})
    # Keep fixed baselines first and append risk-triggered supplements in
    # descending risk order.  This makes the review queue easy to scan while
    # remaining deterministic across input ordering.
    selected = sorted(
        chosen.values(),
        key=lambda item: (
            0 if item.baseline else 1,
            -item.risk_score if not item.baseline else 0,
            item.modality,
            item.sample_id,
            item.unit_id,
        ),
    )
    deferred = sorted(
        [candidate.model_copy(update={"selected": False, "baseline": False}) for candidate in available if candidate.unit_id not in chosen],
        key=lambda item: (-item.risk_score, item.modality, item.sample_id, item.unit_id),
    )
    return SamplingPlan(
        policy=selected_policy,
        selected=selected,
        deferred=deferred,
        fixed_count=sum(candidate.baseline for candidate in selected),
        supplemental_count=sum(not candidate.baseline for candidate in selected),
    )


def apply_review_feedback(
    candidates: Sequence[ReviewCandidate | RiskAssessment | Mapping[str, object]],
    feedback: Sequence[ReviewFeedback | Mapping[str, object]],
) -> list[ReviewCandidate]:
    """Merge structured human outcomes back into local candidates."""

    rows = [_coerce_candidate(item) for item in candidates]
    feedback_rows = [item if isinstance(item, ReviewFeedback) else ReviewFeedback.model_validate(item) for item in feedback]
    by_unit = {item.unit_id: item for item in feedback_rows}
    if len(by_unit) != len(feedback_rows):
        raise ValueError("review feedback unit IDs must be unique")
    known = {item.unit_id for item in rows}
    unknown = set(by_unit) - known
    if unknown:
        raise ValueError("review feedback references an unknown unit")
    return [
        item.model_copy(
            update={
                "review_status": by_unit[item.unit_id].status if item.unit_id in by_unit else item.review_status,
                "review_codes": by_unit[item.unit_id].defect_codes if item.unit_id in by_unit else item.review_codes,
            }
        )
        for item in rows
    ]


__all__ = [
    "RiskAssessment",
    "RiskSignals",
    "ReviewCandidate",
    "ReviewFeedback",
    "SamplingPlan",
    "SamplingPolicy",
    "apply_review_feedback",
    "assess_risk",
    "compute_risk_score",
    "risk_score",
    "select_review_samples",
]
