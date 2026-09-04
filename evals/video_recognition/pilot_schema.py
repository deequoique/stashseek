"""Planning and readiness contracts for the small product-acceptance pilot.

The original :mod:`evals.video_recognition.schema` remains the immutable
regression-pool contract.  This module deliberately models a separate pilot
manifest so that shrinking the first product acceptance run cannot silently
shrink, rewrite, or unlock the twelve-sample regression provenance.

The checked-in pilot is planning-only.  It contains sample/query/media slots
and profile-selection slots, but it does not claim public-evidence winners or
gold query truth.  A future frozen pilot revision must fill those slots before
provider execution can be admitted.
"""

from __future__ import annotations

import math
from collections import Counter
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from .schema import (
    BenchmarkCatalog,
    CatalogValidationError,
    Modality,
    Profile,
    QueryKind,
    StrictModel,
    TimeRange,
    load_catalog,
)

PILOT_REVISION = "pilot-v1"
PILOT_CATALOG_PATH = Path(__file__).with_name("pilot_catalog.yaml")
REGRESSION_POOL_SAMPLE_COUNT = 12
MIN_PILOT_SAMPLES = 4
MAX_PILOT_SAMPLES = 6
QUERIES_PER_PILOT_SAMPLE = 3

PilotStatus = Literal["planning_only", "frozen"]
AnnotationStatus = Literal["pending_manual", "frozen"]
MediaSelectionStatus = Literal["pending_manual_review", "frozen"]
PublicEvidenceStatus = Literal["pending", "selected"]
FallbackTrigger = Literal[
    "default_quality_gate_failed",
    "default_protocol_failed",
    "default_provider_unavailable",
]
RetryReason = Literal["provider_transient", "protocol_instability"]


class PilotValidationError(ValueError):
    """Raised when a pilot manifest is structurally or referentially invalid."""

    error_code = "pilot_validation_failed"


class PilotGateError(RuntimeError):
    """Raised when the pilot is not ready for a provider execution."""

    error_code = "pilot_execution_gate_closed"

    def __init__(self, blockers: list[str]):
        self.blockers = tuple(blockers)
        super().__init__("pilot execution gate is closed: " + "; ".join(blockers))


class PilotQuery(StrictModel):
    """One product-search query slot scoped to exactly one pilot sample.

    ``pending_manual`` is intentionally usable only by the planning/dry-run
    manifest.  A frozen query must explicitly identify its video-absolute
    truth ranges; a relative ASR window cannot satisfy this contract by
    accident.
    """

    query_id: str = Field(pattern=r"pilot-(?:speech|visual|ocr|combined)-[0-9]{2}")
    kind: QueryKind
    query: str = Field(min_length=1, max_length=1000)
    sample_scope_ids: list[str] = Field(min_length=1, max_length=1)
    expected_video_ids: list[str] = Field(default_factory=list, max_length=12)
    acceptable_time_ranges: dict[str, list[TimeRange]] = Field(default_factory=dict)
    required_modalities: list[Modality] = Field(min_length=1, max_length=3)
    required_key_terms: list[str] = Field(default_factory=list, max_length=20)
    distractor_video_ids: list[str] = Field(default_factory=list, max_length=12)
    metadata_leak_notes: str = Field(default="", max_length=500)
    exclusion_notes: str = Field(default="", max_length=500)
    annotation_status: AnnotationStatus = "pending_manual"
    annotation_note: str = Field(default="", max_length=1000)
    # ``pending`` is a planning marker, not an assertion that a range is
    # absolute.  Frozen truth must say ``video_absolute`` explicitly.
    time_origin: Literal["pending", "video_absolute"] = "pending"

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("pilot query text must not be blank")
        return value

    @field_validator("required_key_terms")
    @classmethod
    def key_terms_must_be_nonblank_and_unique(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().casefold() for value in values]
        if any(not value for value in normalized):
            raise ValueError("pilot key terms must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("pilot key terms must be unique")
        return values

    @model_validator(mode="after")
    def validate_query(self) -> "PilotQuery":
        expected_modalities: dict[QueryKind, set[Modality]] = {
            "speech": {"asr"},
            "visual": {"vision"},
            "ocr": {"ocr"},
            "combined": {"asr", "vision"},
        }
        if set(self.required_modalities) != expected_modalities[self.kind]:
            raise ValueError(f"{self.kind} query has invalid required_modalities")
        if len(set(self.required_modalities)) != len(self.required_modalities):
            raise ValueError("required_modalities must not contain duplicates")
        if set(self.expected_video_ids) & set(self.distractor_video_ids):
            raise ValueError("expected and distractor videos must be disjoint")
        if set(self.expected_video_ids) - set(self.sample_scope_ids):
            raise ValueError("expected video IDs must be within sample_scope_ids")

        if self.annotation_status == "frozen":
            sample_id = self.sample_scope_ids[0]
            if self.expected_video_ids != [sample_id]:
                raise ValueError("frozen pilot queries require exactly their scoped expected video")
            if self.time_origin != "video_absolute":
                raise ValueError("frozen pilot query truth must use video_absolute time_origin")
            if not self.required_key_terms:
                raise ValueError("frozen pilot queries require at least one key term")
            if set(self.acceptable_time_ranges) != {sample_id}:
                raise ValueError("frozen pilot queries require ranges for their expected video")
            if not self.acceptable_time_ranges[sample_id]:
                raise ValueError("frozen pilot queries require at least one time range")
        elif self.time_origin == "video_absolute" and self.acceptable_time_ranges:
            # It is fine to keep partial notes while planning, but a planning
            # range must not look frozen without its query annotation status.
            # This explicit rule prevents accidental scoring of a half-filled
            # truth record.
            raise ValueError("video_absolute ranges require a frozen pilot query")
        return self


class PilotMediaSelection(StrictModel):
    """Media slots required by the modalities used by one pilot sample.

    Lists are empty while planning.  Unlike the regression-pool ``Sample``
    contract, this model does not require two audio windows or two frame
    bundles: only modalities actually requested by pilot queries need media.
    """

    sample_id: str = Field(pattern=r"(?:YT|BI)-[0-9]{2}")
    required_modalities: list[Modality] = Field(min_length=1, max_length=3)
    selection_status: MediaSelectionStatus = "pending_manual_review"
    audio_windows: list[TimeRange] = Field(default_factory=list, max_length=2)
    frame_bundles: list[list[float]] = Field(default_factory=list, max_length=2)
    ocr_frame_times: list[float] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_media(self) -> "PilotMediaSelection":
        required = set(self.required_modalities)
        if len(required) != len(self.required_modalities):
            raise ValueError("pilot media modalities must be unique")
        if "asr" not in required and self.audio_windows:
            raise ValueError("audio media is not allowed when ASR is not required")
        if "vision" not in required and self.frame_bundles:
            raise ValueError("frame media is not allowed when Vision is not required")
        if "ocr" not in required and self.ocr_frame_times:
            raise ValueError("OCR media is not allowed when OCR is not required")
        if any(window.duration_sec > 60 for window in self.audio_windows):
            raise ValueError("pilot audio windows may not exceed 60 seconds")
        for times in self.frame_bundles:
            if not 3 <= len(times) <= 6:
                raise ValueError("pilot frame bundles must contain three to six frame times")
            if any(not math.isfinite(value) or value < 0 for value in times):
                raise ValueError("pilot frame times must be finite and non-negative")
            if list(times) != sorted(set(times)):
                raise ValueError("pilot frame bundle times must be unique and ordered")
        if any(not math.isfinite(value) or value < 0 for value in self.ocr_frame_times):
            raise ValueError("pilot OCR frame times must be finite and non-negative")
        if self.ocr_frame_times != sorted(set(self.ocr_frame_times)):
            raise ValueError("pilot OCR frame times must be unique and ordered")
        if self.selection_status == "frozen":
            if "asr" in required and not self.audio_windows:
                raise ValueError("frozen pilot ASR media requires an audio window")
            if "vision" in required and not self.frame_bundles:
                raise ValueError("frozen pilot Vision media requires a frame bundle")
            if "ocr" in required and not self.ocr_frame_times:
                raise ValueError("frozen pilot OCR media requires OCR frame times")
        return self


class PilotProfileSelection(StrictModel):
    """One default slot and an optional explicitly triggerable fallback slot."""

    modality: Modality
    selection_status: PublicEvidenceStatus = "pending"
    default_profile_id: str | None = Field(default=None, pattern=r"[a-z0-9][a-z0-9_.-]{2,80}")
    fallback_profile_id: str | None = Field(default=None, pattern=r"[a-z0-9][a-z0-9_.-]{2,80}")
    fallback_allowed_triggers: list[FallbackTrigger] = Field(default_factory=list, max_length=3)
    public_evidence_note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_selection(self) -> "PilotProfileSelection":
        if len(set(self.fallback_allowed_triggers)) != len(self.fallback_allowed_triggers):
            raise ValueError("fallback triggers must be unique")
        if self.selection_status == "pending":
            if self.default_profile_id is not None or self.fallback_profile_id is not None:
                raise ValueError("pending public-evidence selection must not name model winners")
            if self.fallback_allowed_triggers:
                raise ValueError("pending public-evidence selection cannot authorize fallback triggers")
        else:
            if self.default_profile_id is None:
                raise ValueError("selected profile modality requires exactly one default profile")
            if not self.public_evidence_note.strip():
                raise ValueError("selected profiles require a public evidence note")
            if self.fallback_profile_id is None and self.fallback_allowed_triggers:
                raise ValueError("fallback triggers require a fallback profile")
            if self.fallback_profile_id is not None and not self.fallback_allowed_triggers:
                raise ValueError("a fallback profile requires explicit allowed triggers")
            if self.fallback_profile_id == self.default_profile_id:
                raise ValueError("default and fallback profiles must be different")
        return self


class PilotRetryPolicy(StrictModel):
    """Bounded retry policy for one default unit."""

    default_runs_per_unit: int = Field(default=1, ge=1, le=1)
    max_retries: int = Field(default=2, ge=0, le=2)
    allowed_reasons: list[RetryReason] = Field(
        default_factory=lambda: ["provider_transient", "protocol_instability"],
        min_length=1,
        max_length=2,
    )

    @model_validator(mode="after")
    def validate_retries(self) -> "PilotRetryPolicy":
        if len(set(self.allowed_reasons)) != len(self.allowed_reasons):
            raise ValueError("retry reasons must be unique")
        return self


class PilotManifest(StrictModel):
    """Provider-neutral, separately scoped product-acceptance manifest."""

    revision: str = Field(default=PILOT_REVISION, pattern=r"pilot-v[0-9]+")
    status: PilotStatus = "planning_only"
    regression_catalog_revision: str = Field(pattern=r"benchmark-v[0-9]+")
    regression_catalog_path: str = Field(min_length=1, max_length=200)
    regression_pool_sample_count: int = Field(default=REGRESSION_POOL_SAMPLE_COUNT, ge=12, le=12)
    selected_sample_ids: list[str] = Field(min_length=MIN_PILOT_SAMPLES, max_length=MAX_PILOT_SAMPLES)
    queries: list[PilotQuery] = Field(min_length=12, max_length=18)
    media_selections: list[PilotMediaSelection] = Field(default_factory=list, max_length=6)
    profile_selections: list[PilotProfileSelection] = Field(min_length=3, max_length=3)
    truth_annotation_status: Literal["pending_manual", "complete"] = "pending_manual"
    media_input_status: Literal["pending_manual", "complete"] = "pending_manual"
    public_evidence_status: Literal["pending", "complete"] = "pending"
    retry_policy: PilotRetryPolicy = Field(default_factory=PilotRetryPolicy)

    @model_validator(mode="after")
    def validate_manifest(self) -> "PilotManifest":
        if len(set(self.selected_sample_ids)) != len(self.selected_sample_ids):
            raise ValueError("pilot sample IDs must be unique")
        if any(not _sample_id_pattern(sample_id) for sample_id in self.selected_sample_ids):
            raise ValueError("pilot selected sample IDs must use YT-/BI- form")

        expected_query_count = QUERIES_PER_PILOT_SAMPLE * len(self.selected_sample_ids)
        if len(self.queries) != expected_query_count:
            raise ValueError("pilot requires exactly three query slots per selected sample")
        query_ids = [query.query_id for query in self.queries]
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("pilot query IDs must be unique")
        query_sample_ids = [query.sample_scope_ids[0] for query in self.queries]
        if set(query_sample_ids) - set(self.selected_sample_ids):
            raise ValueError("pilot queries may only reference selected samples")
        counts = Counter(query_sample_ids)
        if any(counts[sample_id] != QUERIES_PER_PILOT_SAMPLE for sample_id in self.selected_sample_ids):
            raise ValueError("every selected pilot sample needs exactly three query slots")
        kinds = {query.kind for query in self.queries}
        if not {"speech", "visual", "ocr", "combined"}.issubset(kinds):
            raise ValueError("pilot queries must cover speech, visual, OCR, and combined")

        media_ids = [selection.sample_id for selection in self.media_selections]
        if len(set(media_ids)) != len(media_ids):
            raise ValueError("pilot media selections must be unique per sample")
        if set(media_ids) - set(self.selected_sample_ids):
            raise ValueError("pilot media selections may only reference selected samples")
        expected_modalities = self.required_modalities_by_sample()
        for selection in self.media_selections:
            if set(selection.required_modalities) != expected_modalities[selection.sample_id]:
                raise ValueError("pilot media modalities must match query modalities for the sample")

        profile_modalities = [selection.modality for selection in self.profile_selections]
        if set(profile_modalities) != {"asr", "vision", "ocr"}:
            raise ValueError("pilot needs exactly one profile selection for each modality")
        if len(profile_modalities) != len(set(profile_modalities)):
            raise ValueError("pilot profile modalities must be unique")

        if self.status == "planning_only":
            if self.public_evidence_status != "pending":
                raise ValueError("planning pilot must keep public evidence selection pending")
        else:
            if self.public_evidence_status != "complete":
                raise ValueError("frozen pilot requires completed public evidence selection")
            if self.truth_annotation_status != "complete" or any(
                query.annotation_status != "frozen" for query in self.queries
            ):
                raise ValueError("frozen pilot requires frozen query truth")
            if self.media_input_status != "complete":
                raise ValueError("frozen pilot requires complete media selection")
            if set(media_ids) != set(self.selected_sample_ids) or any(
                selection.selection_status != "frozen" for selection in self.media_selections
            ):
                raise ValueError("frozen pilot requires frozen media for every selected sample")
            if any(selection.selection_status != "selected" for selection in self.profile_selections):
                raise ValueError("frozen pilot requires selected profiles for every modality")
        if self.public_evidence_status == "pending" and any(
            selection.selection_status != "pending" for selection in self.profile_selections
        ):
            raise ValueError("pending public evidence cannot contain selected profile winners")
        if self.public_evidence_status == "complete" and any(
            selection.selection_status != "selected" for selection in self.profile_selections
        ):
            raise ValueError("complete public evidence requires selected profiles")
        return self

    def required_modalities_by_sample(self) -> dict[str, set[Modality]]:
        """Return modalities actually required by this pilot's query slots."""

        required: dict[str, set[Modality]] = {sample_id: set() for sample_id in self.selected_sample_ids}
        for query in self.queries:
            required[query.sample_scope_ids[0]].update(query.required_modalities)
        return required

    def profile_for_modality(self, modality: Modality) -> PilotProfileSelection:
        """Return the single selection slot for a modality."""

        return next(selection for selection in self.profile_selections if selection.modality == modality)


def _sample_id_pattern(value: str) -> bool:
    return len(value) == 5 and value[:2] in {"YT", "BI"} and value[2] == "-" and value[3:].isdigit()


def _catalog_profile(catalog: BenchmarkCatalog, profile_id: str | None, modality: Modality) -> Profile | None:
    if profile_id is None:
        return None
    profile = next((item for item in catalog.profiles if item.profile_id == profile_id), None)
    if profile is None or profile.modality != modality:
        return None
    return profile


def pilot_reference_blockers(manifest: PilotManifest, catalog: BenchmarkCatalog) -> list[str]:
    blockers: list[str] = []
    catalog_ids = {sample.sample_id for sample in catalog.samples}
    if manifest.regression_catalog_path != "catalog.yaml":
        blockers.append("pilot_regression_catalog_path_mismatch")
    if catalog.probe_snapshot != "probe_snapshot.yaml":
        blockers.append("pilot_regression_probe_snapshot_mismatch")
    if catalog.revision != manifest.regression_catalog_revision:
        blockers.append("pilot_regression_catalog_revision_mismatch")
    if len(catalog.samples) != REGRESSION_POOL_SAMPLE_COUNT:
        blockers.append("pilot_regression_pool_count_invalid")
    missing_samples = set(manifest.selected_sample_ids) - catalog_ids
    if missing_samples:
        blockers.append("pilot_sample_not_in_regression_pool")
    unknown_distractors = {
        sample_id
        for query in manifest.queries
        for sample_id in query.distractor_video_ids
        if sample_id not in catalog_ids
    }
    if unknown_distractors:
        blockers.append("pilot_distractor_not_in_regression_pool")

    samples = {sample.sample_id: sample for sample in catalog.samples}
    if any(
        time_range.end_sec > samples[sample_id].duration_sec
        for query in manifest.queries
        for sample_id, ranges in query.acceptable_time_ranges.items()
        if sample_id in samples
        for time_range in ranges
    ):
        blockers.append("pilot_query_time_range_out_of_bounds")
    if any(
        window.end_sec > samples[selection.sample_id].duration_sec
        for selection in manifest.media_selections
        if selection.sample_id in samples
        for window in selection.audio_windows
    ) or any(
        timestamp_sec > samples[selection.sample_id].duration_sec
        for selection in manifest.media_selections
        if selection.sample_id in samples
        for bundle in selection.frame_bundles
        for timestamp_sec in bundle
    ) or any(
        timestamp_sec > samples[selection.sample_id].duration_sec
        for selection in manifest.media_selections
        if selection.sample_id in samples
        for timestamp_sec in selection.ocr_frame_times
    ):
        blockers.append("pilot_media_selection_out_of_bounds")

    for selection in manifest.profile_selections:
        default_profile = _catalog_profile(catalog, selection.default_profile_id, selection.modality)
        if selection.default_profile_id and default_profile is None:
            blockers.append("pilot_default_profile_not_in_registry")
        elif default_profile is not None and not default_profile.supports_fixture:
            blockers.append("pilot_default_profile_not_fixture_compatible")
        fallback_profile = _catalog_profile(catalog, selection.fallback_profile_id, selection.modality)
        if selection.fallback_profile_id and fallback_profile is None:
            blockers.append("pilot_fallback_profile_not_in_registry")
        elif fallback_profile is not None and not fallback_profile.supports_fixture:
            blockers.append("pilot_fallback_profile_not_fixture_compatible")
    return blockers


def pilot_execution_blockers(manifest: PilotManifest, catalog: BenchmarkCatalog | None = None) -> list[str]:
    """Return stable blockers for provider execution, scoped to pilot inputs.

    Only selected samples are inspected for content classification and probe
    readiness.  Preliminary or incomplete records in the twelve-sample
    regression pool outside the pilot therefore cannot block pilot readiness.
    """

    catalog = catalog or load_catalog()
    blockers = pilot_reference_blockers(manifest, catalog)
    if manifest.status != "frozen":
        blockers.append("pilot_manifest_not_frozen")
    if manifest.public_evidence_status != "complete" or any(
        selection.selection_status != "selected" or selection.default_profile_id is None
        for selection in manifest.profile_selections
    ):
        blockers.append("public_evidence_selection_pending")
    if manifest.truth_annotation_status != "complete" or any(
        query.annotation_status != "frozen" for query in manifest.queries
    ):
        blockers.append("pilot_query_truth_pending")
    if manifest.media_input_status != "complete" or set(selection.sample_id for selection in manifest.media_selections) != set(
        manifest.selected_sample_ids
    ):
        blockers.append("pilot_media_selection_pending")
    elif any(selection.selection_status != "frozen" for selection in manifest.media_selections):
        blockers.append("pilot_media_selection_pending")

    samples = {sample.sample_id: sample for sample in catalog.samples}
    selected_samples = [samples[sample_id] for sample_id in manifest.selected_sample_ids if sample_id in samples]
    if any(sample.classification_status != "manually_frozen" for sample in selected_samples):
        blockers.append("pilot_content_classification_pending")
    if any(sample.probe_status not in {"complete", "no_platform_track"} for sample in selected_samples):
        blockers.append("pilot_formal_probe_pending")

    return list(dict.fromkeys(blockers))


def assert_pilot_execution_ready(manifest: PilotManifest, catalog: BenchmarkCatalog | None = None) -> None:
    blockers = pilot_execution_blockers(manifest, catalog)
    if blockers:
        raise PilotGateError(blockers)


def pilot_summary(manifest: PilotManifest, catalog: BenchmarkCatalog | None = None) -> dict[str, Any]:
    """Return safe counts and selection state for CLI/CI output."""

    catalog = catalog or load_catalog()
    blockers = pilot_execution_blockers(manifest, catalog)
    return {
        "revision": manifest.revision,
        "status": manifest.status,
        "regression_pool": {
            "revision": manifest.regression_catalog_revision,
            "sample_count": len(catalog.samples),
        },
        "pilot": {
            "sample_count": len(manifest.selected_sample_ids),
            "sample_ids": list(manifest.selected_sample_ids),
            "query_count": len(manifest.queries),
            "queries_per_sample": dict(Counter(query.sample_scope_ids[0] for query in manifest.queries)),
            "required_modalities_by_sample": {
                sample_id: sorted(modalities)
                for sample_id, modalities in manifest.required_modalities_by_sample().items()
            },
        },
        "profile_selections": [
            {
                "modality": selection.modality,
                "selection_status": selection.selection_status,
                "default_profile_id": selection.default_profile_id,
                "fallback_profile_id": selection.fallback_profile_id,
            }
            for selection in manifest.profile_selections
        ],
        "blockers": blockers,
        "execution_gate": "open" if not blockers else "closed",
    }


def load_pilot_manifest(
    path: str | Path | None = None,
    *,
    catalog: BenchmarkCatalog | None = None,
) -> PilotManifest:
    """Load and validate the pilot manifest against the regression catalog."""

    target = Path(path) if path else PILOT_CATALOG_PATH
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
        manifest = PilotManifest.model_validate(payload)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise PilotValidationError("pilot_validation_failed") from exc

    catalog = catalog or load_catalog()
    reference_errors = pilot_reference_blockers(manifest, catalog)
    if reference_errors:
        raise PilotValidationError("pilot_manifest_reference_invalid: " + ",".join(reference_errors))
    return manifest


def planned_pilot_pairs(manifest: PilotManifest) -> tuple[tuple[str, Modality], ...]:
    """Return unique sample/modality units implied by pilot queries."""

    pairs = {
        (query.sample_scope_ids[0], modality)
        for query in manifest.queries
        for modality in query.required_modalities
    }
    return tuple(sorted(pairs))


def fallback_is_allowed(selection: PilotProfileSelection, trigger: str | None) -> bool:
    """Check an explicit, stable fallback trigger without executing anything."""

    return (
        selection.fallback_profile_id is not None
        and trigger is not None
        and trigger in selection.fallback_allowed_triggers
    )


__all__ = [
    "AnnotationStatus",
    "FallbackTrigger",
    "MAX_PILOT_SAMPLES",
    "MIN_PILOT_SAMPLES",
    "PILOT_CATALOG_PATH",
    "PILOT_REVISION",
    "PilotGateError",
    "PilotManifest",
    "PilotMediaSelection",
    "PilotProfileSelection",
    "PilotQuery",
    "PilotRetryPolicy",
    "PilotValidationError",
    "assert_pilot_execution_ready",
    "fallback_is_allowed",
    "load_pilot_manifest",
    "pilot_execution_blockers",
    "pilot_reference_blockers",
    "pilot_summary",
    "planned_pilot_pairs",
]
