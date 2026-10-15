"""Dry-run and provider-gated orchestration for the isolated benchmark."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import adapter_for_profile, credential_status
from .cleanup import TemporaryRun, verify_cleanup
from .media import descriptor_for_sample, synthetic_inputs
from .probe import run_formal_probe
from .protocol import MediaSampleBundle, RecognitionJobResult, stable_error
from .quality_report import AutomaticQualityReport, InputBounds, WeakReference, build_quality_report
from .pilot_schema import (
    PilotManifest,
    PilotValidationError,
    fallback_is_allowed,
    pilot_execution_blockers,
    pilot_reference_blockers,
    pilot_summary,
    planned_pilot_pairs,
)
from .redaction import assert_redacted, redact_report
from .schema import BenchmarkCatalog, BenchmarkGateError, TimeRange, catalog_summary, load_catalog


class HarnessError(RuntimeError):
    """Bounded local harness failure."""


@dataclass(frozen=True)
class DryRunReport:
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def automatic_quality_report(
    catalog: BenchmarkCatalog,
    *,
    fixture_dir: str | Path | None = None,
    report_id: str = "fixture-quality-report",
) -> AutomaticQualityReport:
    """Evaluate fixture outputs without opening the provider execution gate.

    This is an offline rehearsal of the automatic evaluator.  It deliberately
    uses the existing fixture adapters and does not require frozen query truth,
    subtitle bodies, credentials, or network access.
    """

    fixture_root = Path(fixture_dir) if fixture_dir else Path(__file__).with_name("fixtures")
    results: list[RecognitionJobResult] = []
    bounds: dict[tuple[str, str], InputBounds] = {}
    references: dict[str, WeakReference] = {}
    for sample in catalog.samples:
        synthetic = synthetic_inputs(sample)
        references[sample.sample_id] = WeakReference(
            sample_id=sample.sample_id,
            title=sample.title,
            metadata_terms=sample.content_tags,
            has_subtitles=bool(sample.subtitle_tracks),
            subtitle_coverage_ratio=(
                max(
                    (track.coverage_ratio or 0.0 for track in sample.subtitle_tracks),
                    default=0.0,
                )
                if sample.subtitle_tracks
                else None
            ),
        )
        bounds[(sample.sample_id, "asr")] = InputBounds(
            sample_id=sample.sample_id,
            modality="asr",
            time_range=synthetic.audio.time_range,
            video_duration_sec=float(sample.duration_sec),
        )
        bounds[(sample.sample_id, "vision")] = InputBounds(
            sample_id=sample.sample_id,
            modality="vision",
            frame_times=[frame.timestamp_sec for frame in synthetic.frame_bundle.frames],
            video_duration_sec=float(sample.duration_sec),
        )
        ocr_frame = synthetic.ocr_frames[0] if synthetic.ocr_frames else synthetic.frame_bundle.frames[0]
        bounds[(sample.sample_id, "ocr")] = InputBounds(
            sample_id=sample.sample_id,
            modality="ocr",
            time_range=TimeRange(start_sec=ocr_frame.timestamp_sec, end_sec=ocr_frame.timestamp_sec + 0.01),
            video_duration_sec=float(sample.duration_sec),
        )
        for profile in catalog.profiles:
            adapter = adapter_for_profile(profile, fixture_dir=fixture_root, mode="fixture")
            if profile.modality == "asr":
                input_unit = synthetic.audio
            elif profile.modality == "vision":
                input_unit = synthetic.frame_bundle
            else:
                input_unit = ocr_frame
            # A unique run ID per sample/profile prevents event and trace IDs
            # from colliding when one offline report contains many tuples.
            run_id = f"{report_id[:40]}.{sample.sample_id.lower()}.{profile.profile_id[:60]}"
            try:
                response = adapter.run(
                    run_id=run_id,
                    sample_id=sample.sample_id,
                    input_unit=input_unit,
                )
                result = RecognitionJobResult(
                    run_id=run_id,
                    candidate_id=profile.profile_id,
                    sample_id=sample.sample_id,
                    run_index=1,
                    modality=profile.modality,
                    terminal_state="completed",
                    latency_ms=response.latency_ms,
                    usage=response.usage,
                    asr=response if profile.modality == "asr" else None,
                    vision=response if profile.modality == "vision" else None,
                    ocr=response if profile.modality == "ocr" else None,
                )
            except Exception as exc:
                result = RecognitionJobResult(
                    run_id=run_id,
                    candidate_id=profile.profile_id,
                    sample_id=sample.sample_id,
                    run_index=1,
                    modality=profile.modality,
                    terminal_state="harness_invalid",
                    error=stable_error(
                        "fixture_adapter_failed",
                        error_class=type(exc).__name__,
                        phase="harness",
                    ),
                )
            results.append(result)
    return build_quality_report(
        results,
        input_bounds=bounds,
        weak_references=references,
        expected_sample_ids=[sample.sample_id for sample in catalog.samples],
        report_id=report_id,
        revision=catalog.revision,
        manual_truth_status=catalog.truth_annotation_status,
    )


# Keep the shorter name convenient for CLI and future callers.
fixture_quality_report = automatic_quality_report


def planned_run_terminal_states(catalog: BenchmarkCatalog) -> list[dict[str, object]]:
    """Return one safe terminal plan for every profile/sample/repeat tuple.

    The plans are not model results.  They document that a dry run accounts
    for every required tuple and classifies it as provider-incomplete while
    the external-call gate is closed.
    """

    state = "harness_invalid" if catalog.execution_blockers() else "provider_incomplete"
    error_code = "catalog_execution_gate_closed" if state == "harness_invalid" else "provider_execution_disabled"
    result: list[dict[str, object]] = []
    for profile in catalog.profiles:
        for sample in catalog.samples:
            for run_index in range(1, catalog.runs_per_profile_sample + 1):
                result.append(
                    {
                        "candidate_id": profile.profile_id,
                        "sample_id": sample.sample_id,
                        "run_index": run_index,
                        "terminal_state": state,
                        "error_code": error_code,
                    }
                )
    return result


def _validate_fixture_protocols(catalog: BenchmarkCatalog, fixture_dir: Path) -> dict[str, int]:
    """Exercise each normalized modality without a network or media file."""

    checked = {"media_bundles": 0, "asr_responses": 0, "vision_responses": 0, "ocr_responses": 0, "error_envelopes": 0}
    MediaSampleBundle.model_validate(
        json.loads((fixture_dir / "media_sample_bundle.json").read_text(encoding="utf-8"))
    )
    error = RecognitionJobResult.model_validate(
        json.loads((fixture_dir / "recognition_result.json").read_text(encoding="utf-8"))
    )
    if error.terminal_state != "provider_incomplete":
        raise HarnessError("provider-incomplete fixture does not have a terminal state")
    checked["error_envelopes"] += 1
    for sample in catalog.samples:
        descriptor_for_sample(sample)
        inputs = synthetic_inputs(sample)
        checked["media_bundles"] += 1
        for profile in catalog.profiles:
            adapter = adapter_for_profile(profile, fixture_dir=fixture_dir, mode="fixture")
            unit = inputs.audio if profile.modality == "asr" else inputs.frame_bundle if profile.modality == "vision" else inputs.ocr_frames[0] if inputs.ocr_frames else inputs.frame_bundle.frames[0]
            response = adapter.run(run_id="dry-run-protocol", sample_id=sample.sample_id, input_unit=unit)
            if profile.modality == "asr":
                checked["asr_responses"] += 1
            elif profile.modality == "vision":
                checked["vision_responses"] += 1
            else:
                checked["ocr_responses"] += 1
            # A second validation round catches adapters that return an object
            # with an unexpected provider-specific subclass.
            type(response).model_validate(response.model_dump())
    return checked


def dry_run(
    catalog: BenchmarkCatalog,
    *,
    fixture_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
    temp_base_dir: str | Path | None = None,
) -> DryRunReport:
    """Run all local checks and report the exact external-call gate."""

    fixture_root = Path(fixture_dir) if fixture_dir else Path(__file__).with_name("fixtures")
    probe = run_formal_probe(catalog, live=False)
    protocol_counts = _validate_fixture_protocols(catalog, fixture_root)
    statuses = [credential_status(profile, environ).as_dict() for profile in catalog.profiles]
    would_run_count = len(catalog.profiles) * len(catalog.samples) * catalog.runs_per_profile_sample
    blockers = catalog.execution_blockers()
    planned_terminal_states = {
        # Dry-run entries are plans, not provider executions.  Recording the
        # stable terminal classification makes the completeness check
        # deterministic while keeping external_calls at zero.
        "provider_incomplete": 0 if blockers else would_run_count,
        "completed": 0,
        "harness_invalid": would_run_count if blockers else 0,
    }
    with TemporaryRun(base_dir=temp_base_dir) as run:
        run.write_safe_json(
            "dry-run-report.json",
            {
                "status": "dry_run",
                "revision": catalog.revision,
                "probe_record_count": len(probe.records),
                "protocol_counts": protocol_counts,
                "provider_preflight": statuses,
            },
        )
        cleanup_report = run.cleanup()
    verify_cleanup(cleanup_report)
    payload: dict[str, Any] = {
        "status": "dry_run",
        "revision": catalog.revision,
        "catalog": catalog_summary(catalog),
        "probe": {
            "record_count": len(probe.records),
            "probe_kind": probe.probe_kind,
            "body_payloads_retained": False,
        },
        "protocol_fixtures": protocol_counts,
        "provider_preflight": statuses,
        "external_calls": 0,
        "would_run_count": would_run_count,
        "planned_terminal_state_counts": planned_terminal_states,
        "execution_gate": {
            "open": not blockers,
            "blockers": blockers,
            "paid_provider_calls_enabled": False,
        },
        "cleanup": cleanup_report.as_dict(),
    }
    safe = redact_report(payload)
    assert_redacted(safe)
    return DryRunReport(safe)


def planned_pilot_units(
    pilot: PilotManifest,
    *,
    catalog: BenchmarkCatalog | None = None,
    fallback_triggers: dict[str, str] | None = None,
) -> dict[str, object]:
    """Plan provider units implied by the pilot without executing them.

    A unit is one selected sample/modality pair.  The default contributes one
    attempt per unit only after its public-evidence profile selection is
    selected.  Fallback units are added only when the caller supplies an
    allowed, stable trigger for that modality; an absent trigger always yields
    zero fallback units.
    """

    catalog = catalog or load_catalog()
    if pilot_reference_blockers(pilot, catalog):
        raise PilotValidationError("pilot_manifest_reference_invalid")
    triggers = fallback_triggers or {}
    defaults: list[dict[str, object]] = []
    fallbacks: list[dict[str, object]] = []
    for sample_id, modality in planned_pilot_pairs(pilot):
        selection = pilot.profile_for_modality(modality)
        if selection.selection_status != "selected" or selection.default_profile_id is None:
            continue
        defaults.append(
            {
                "sample_id": sample_id,
                "modality": modality,
                "profile_id": selection.default_profile_id,
                "run_count": pilot.retry_policy.default_runs_per_unit,
                "max_retries": pilot.retry_policy.max_retries,
            }
        )
        trigger = triggers.get(modality)
        if fallback_is_allowed(selection, trigger):
            fallbacks.append(
                {
                    "sample_id": sample_id,
                    "modality": modality,
                    "profile_id": selection.fallback_profile_id,
                    "trigger": trigger,
                    "run_count": pilot.retry_policy.default_runs_per_unit,
                    "max_retries": pilot.retry_policy.max_retries,
                }
            )
    return {
        "potential_units": len(planned_pilot_pairs(pilot)),
        "default_units": defaults,
        "fallback_units": fallbacks,
        "planned_default_units": len(defaults),
        "fallback_planned_units": len(fallbacks),
    }


def _validate_pilot_fixture_protocols(
    pilot: PilotManifest,
    catalog: BenchmarkCatalog,
    fixture_dir: Path,
) -> dict[str, int]:
    """Validate fixtures only for selected sample/modality pairs."""

    checked = {
        "media_bundles": 0,
        "asr_responses": 0,
        "vision_responses": 0,
        "ocr_responses": 0,
        "error_envelopes": 0,
    }
    samples = {sample.sample_id: sample for sample in catalog.samples}
    fixture_profiles: dict[str, object] = {}
    for profile in catalog.profiles:
        fixture_profiles.setdefault(profile.modality, profile)
    for sample_id in pilot.selected_sample_ids:
        sample = samples.get(sample_id)
        if sample is None:
            raise HarnessError("pilot sample is missing from regression catalog")
        descriptor = descriptor_for_sample(sample)
        synthetic = synthetic_inputs(sample)
        checked["media_bundles"] += 1
        for planned_sample_id, modality in planned_pilot_pairs(pilot):
            if planned_sample_id != sample_id:
                continue
            selection = pilot.profile_for_modality(modality)
            profile = (
                next(
                    (
                        candidate
                        for candidate in catalog.profiles
                        if candidate.profile_id == selection.default_profile_id
                    ),
                    None,
                )
                if selection.selection_status == "selected"
                else fixture_profiles.get(modality)
            )
            if profile is None:
                raise HarnessError("pilot modality has no fixture profile")
            adapter = adapter_for_profile(profile, fixture_dir=fixture_dir, mode="fixture")  # type: ignore[arg-type]
            if modality == "asr":
                input_unit = synthetic.audio
            elif modality == "vision":
                input_unit = synthetic.frame_bundle
            else:
                input_unit = synthetic.ocr_frames[0] if synthetic.ocr_frames else synthetic.frame_bundle.frames[0]
            response = adapter.run(run_id="pilot-dry-run-protocol", sample_id=sample_id, input_unit=input_unit)
            type(response).model_validate(response.model_dump())
            checked[f"{modality}_responses"] += 1
        # ``descriptor`` is deliberately constructed only for selected samples;
        # it remains a local shape check and never requests or stores media.
        if descriptor.sample_id != sample_id:
            raise HarnessError("pilot media descriptor identity mismatch")
    return checked


def pilot_dry_run(
    pilot: PilotManifest,
    *,
    catalog: BenchmarkCatalog | None = None,
    fixture_dir: str | Path | None = None,
    environ: dict[str, str] | None = None,
    temp_base_dir: str | Path | None = None,
    fallback_triggers: dict[str, str] | None = None,
) -> DryRunReport:
    """Run pilot-only local checks while keeping provider calls at zero."""

    catalog = catalog or load_catalog()
    reference_blockers = pilot_reference_blockers(pilot, catalog)
    if reference_blockers:
        raise PilotValidationError("pilot_manifest_reference_invalid")
    fixture_root = Path(fixture_dir) if fixture_dir else Path(__file__).with_name("fixtures")
    probe = run_formal_probe(catalog, live=False)
    protocol_counts = _validate_pilot_fixture_protocols(pilot, catalog, fixture_root)
    # Preflight is presence-only and does not read .env or call providers.
    deterministic_environ = {} if environ is None else environ
    statuses = [credential_status(profile, deterministic_environ).as_dict() for profile in catalog.profiles]
    blockers = pilot_execution_blockers(pilot, catalog)
    plan = planned_pilot_units(pilot, catalog=catalog, fallback_triggers=fallback_triggers)
    with TemporaryRun(base_dir=temp_base_dir) as run:
        run.write_safe_json(
            "pilot-dry-run-report.json",
            {
                "status": "pilot_dry_run",
                "revision": pilot.revision,
                "regression_pool_count": len(catalog.samples),
                "pilot_sample_count": len(pilot.selected_sample_ids),
                "pilot_query_count": len(pilot.queries),
                "plan": plan,
                "external_calls": 0,
            },
        )
        cleanup_report = run.cleanup()
    verify_cleanup(cleanup_report)
    summary = pilot_summary(pilot, catalog)
    payload: dict[str, Any] = {
        "status": "pilot_dry_run",
        "revision": pilot.revision,
        "regression_pool_count": len(catalog.samples),
        "pilot_sample_count": len(pilot.selected_sample_ids),
        "pilot_sample_ids": list(pilot.selected_sample_ids),
        "pilot_query_count": len(pilot.queries),
        "pilot": summary["pilot"],
        "profile_selections": summary["profile_selections"],
        "plan": plan,
        "planned_default_units": plan["planned_default_units"],
        "fallback_planned_units": plan["fallback_planned_units"],
        "retry_policy": pilot.retry_policy.model_dump(mode="json"),
        "probe": {
            "record_count": len(probe.records),
            "probe_kind": probe.probe_kind,
            "body_payloads_retained": False,
        },
        "protocol_fixtures": protocol_counts,
        "provider_preflight": statuses,
        "external_calls": 0,
        "execution_gate": {
            "open": not blockers,
            "blockers": blockers,
            "paid_provider_calls_enabled": False,
        },
        "cleanup": cleanup_report.as_dict(),
    }
    safe = redact_report(payload)
    assert_redacted(safe)
    return DryRunReport(safe)


def run_provider_benchmark(*args: Any, **kwargs: Any) -> None:
    """Refuse provider execution until the catalog and credentials are approved."""

    catalog: BenchmarkCatalog = kwargs.get("catalog") or (args[0] if args else None)
    if not isinstance(catalog, BenchmarkCatalog):
        raise HarnessError("catalog is required")
    catalog.assert_execution_ready()
    raise BenchmarkGateError(["provider_execution_requires_explicit_implementation_and_approval"])
