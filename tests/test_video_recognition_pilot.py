from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from evals.video_recognition.pilot_schema import (
    PilotManifest,
    PilotMediaSelection,
    PilotProfileSelection,
    PilotQuery,
    PilotRetryPolicy,
    PilotValidationError,
    fallback_is_allowed,
    load_pilot_manifest,
    pilot_execution_blockers,
    planned_pilot_pairs,
)
from evals.video_recognition.__main__ import main
from evals.video_recognition.runner import pilot_dry_run, planned_pilot_units
from evals.video_recognition.schema import TimeRange, load_catalog, load_probe_snapshot


def test_pilot_manifest_is_separate_planning_scope_and_keeps_regression_pool():
    catalog = load_catalog()
    pilot = load_pilot_manifest(catalog=catalog)

    assert catalog.revision == pilot.regression_catalog_revision == "benchmark-v1"
    assert len(catalog.samples) == pilot.regression_pool_sample_count == 12
    assert pilot.status == "planning_only"
    assert pilot.selected_sample_ids == ["YT-01", "YT-03", "BI-01", "BI-06"]
    assert len(pilot.queries) == 12
    assert {query.sample_scope_ids[0] for query in pilot.queries} == set(pilot.selected_sample_ids)
    assert {query.sample_scope_ids[0] for query in pilot.queries}.issubset(
        {sample.sample_id for sample in catalog.samples}
    )
    assert all(sum(query.sample_scope_ids[0] == sample_id for query in pilot.queries) == 3 for sample_id in pilot.selected_sample_ids)
    assert all(selection.selection_status == "pending" for selection in pilot.profile_selections)
    assert all(selection.default_profile_id is None for selection in pilot.profile_selections)


def test_legacy_regression_shape_and_probe_invariants_are_unchanged():
    catalog = load_catalog()
    snapshot = load_probe_snapshot(catalog.probe_snapshot)

    assert len(catalog.samples) == 12
    assert len(catalog.queries) == 30
    assert len(catalog.profiles) == 11
    assert catalog.runs_per_profile_sample == 3
    assert len(snapshot.records) == 12
    assert {record.sample_id for record in snapshot.records} == {
        sample.sample_id for sample in catalog.samples
    }


def test_pilot_queries_cover_modalities_and_plan_unique_sample_modality_units():
    pilot = load_pilot_manifest()
    kinds = {query.kind for query in pilot.queries}
    assert {"speech", "visual", "ocr", "combined"}.issubset(kinds)
    pairs = planned_pilot_pairs(pilot)
    assert len(pairs) == len(set(pairs))
    assert {sample_id for sample_id, _modality in pairs} == set(pilot.selected_sample_ids)
    assert {modality for _sample_id, modality in pairs} == {"asr", "vision", "ocr"}


def test_pilot_media_is_modality_driven_and_excludes_unselected_regression_samples():
    pilot = load_pilot_manifest()
    media_by_sample = {selection.sample_id: selection for selection in pilot.media_selections}
    assert set(media_by_sample) == set(pilot.selected_sample_ids)
    assert "YT-02" not in media_by_sample
    assert "BI-02" not in media_by_sample
    expected = {
        "YT-01": {
            "audio_windows": [(83, 95)],
            "frame_bundles": [[18, 20, 50]],
            "ocr_frame_times": [190],
        },
        "YT-03": {
            "audio_windows": [(347, 354)],
            "frame_bundles": [],
            "ocr_frame_times": [],
        },
        "BI-01": {
            "audio_windows": [],
            "frame_bundles": [],
            "ocr_frame_times": [105],
        },
        "BI-06": {
            "audio_windows": [],
            "frame_bundles": [[5, 21, 46, 76], [106, 136, 151]],
            "ocr_frame_times": [],
        },
    }
    for sample_id, modalities in pilot.required_modalities_by_sample().items():
        selection = media_by_sample[sample_id]
        assert set(selection.required_modalities) == modalities
        assert selection.selection_status == "pending_manual_review"
        assert [(window.start_sec, window.end_sec) for window in selection.audio_windows] == expected[sample_id][
            "audio_windows"
        ]
        assert selection.frame_bundles == expected[sample_id]["frame_bundles"]
        assert selection.ocr_frame_times == expected[sample_id]["ocr_frame_times"]


def test_pilot_candidate_queries_keep_truth_pending_and_ranges_out_of_truth_fields():
    pilot = load_pilot_manifest()
    assert all(query.annotation_status == "pending_manual" for query in pilot.queries)
    assert all(query.time_origin == "pending" for query in pilot.queries)
    assert all(query.expected_video_ids == [] for query in pilot.queries)
    assert all(query.acceptable_time_ranges == {} for query in pilot.queries)
    assert all(query.annotation_note.strip() for query in pilot.queries)
    assert {query.distractor_video_ids[0] for query in pilot.queries if query.sample_scope_ids == ["YT-01"]} == {"BI-01"}
    assert {
        tuple(query.distractor_video_ids)
        for query in pilot.queries
        if query.sample_scope_ids == ["YT-03"]
    } == {("BI-02", "BI-03")}
    assert {
        tuple(query.distractor_video_ids)
        for query in pilot.queries
        if query.sample_scope_ids == ["BI-01"]
    } == {("YT-01",)}
    assert {
        tuple(query.distractor_video_ids)
        for query in pilot.queries
        if query.sample_scope_ids == ["BI-06"]
    } == {("YT-05", "BI-05")}


def test_partial_candidates_cannot_open_execution_or_become_frozen_by_themselves():
    pilot = load_pilot_manifest()
    blockers = pilot_execution_blockers(pilot)
    plan = planned_pilot_units(pilot)

    assert pilot.status == "planning_only"
    assert pilot.truth_annotation_status == "pending_manual"
    assert pilot.media_input_status == "pending_manual"
    assert pilot.public_evidence_status == "pending"
    assert all(query.annotation_status == "pending_manual" for query in pilot.queries)
    assert all(query.time_origin == "pending" for query in pilot.queries)
    assert all(query.expected_video_ids == [] for query in pilot.queries)
    assert all(query.acceptable_time_ranges == {} for query in pilot.queries)
    assert all(selection.selection_status == "pending" for selection in pilot.profile_selections)
    assert all(selection.default_profile_id is None for selection in pilot.profile_selections)
    assert all(selection.fallback_profile_id is None for selection in pilot.profile_selections)
    assert "pilot_manifest_not_frozen" in blockers
    assert "public_evidence_selection_pending" in blockers
    assert "pilot_query_truth_pending" in blockers
    assert "pilot_media_selection_pending" in blockers
    assert plan["potential_units"] == 10
    assert plan["planned_default_units"] == 0
    assert plan["fallback_planned_units"] == 0

    promoted = pilot.model_dump(mode="python")
    promoted.update(
        status="frozen",
        truth_annotation_status="complete",
        media_input_status="complete",
        public_evidence_status="complete",
    )
    for query in promoted["queries"]:
        query["annotation_status"] = "frozen"
        query["time_origin"] = "video_absolute"
    with pytest.raises(ValidationError):
        PilotManifest.model_validate(promoted)


def test_pilot_profile_proposal_is_not_promoted_to_winners_or_fallback_triggers():
    pilot = load_pilot_manifest()
    assert all(selection.selection_status == "pending" for selection in pilot.profile_selections)
    assert all(selection.default_profile_id is None for selection in pilot.profile_selections)
    assert all(selection.fallback_profile_id is None for selection in pilot.profile_selections)
    assert all(selection.fallback_allowed_triggers == [] for selection in pilot.profile_selections)
    assert all("modality-default-fallback-freeze-proposal-2026-08-28.md" in selection.public_evidence_note for selection in pilot.profile_selections)


def _frozen_query(**overrides: object) -> PilotQuery:
    payload: dict[str, object] = {
        "query_id": "pilot-speech-01",
        "kind": "speech",
        "query": "find the spoken concept",
        "sample_scope_ids": ["YT-01"],
        "expected_video_ids": ["YT-01"],
        "acceptable_time_ranges": {"YT-01": [TimeRange(start_sec=10, end_sec=20)]},
        "required_modalities": ["asr"],
        "required_key_terms": ["concept"],
        "annotation_status": "frozen",
        "time_origin": "video_absolute",
    }
    payload.update(overrides)
    return PilotQuery.model_validate(payload)


def _frozen_manifest() -> PilotManifest:
    payload = load_pilot_manifest().model_dump(mode="json")
    payload.update(
        status="frozen",
        truth_annotation_status="complete",
        media_input_status="complete",
        public_evidence_status="complete",
    )
    for query in payload["queries"]:
        sample_id = query["sample_scope_ids"][0]
        query.update(
            expected_video_ids=[sample_id],
            acceptable_time_ranges={sample_id: [{"start_sec": 1, "end_sec": 2}]},
            required_key_terms=["accepted term"],
            annotation_status="frozen",
            time_origin="video_absolute",
        )
    for selection in payload["media_selections"]:
        required = set(selection["required_modalities"])
        selection["selection_status"] = "frozen"
        if "asr" in required:
            selection["audio_windows"] = [{"start_sec": 1, "end_sec": 2}]
        if "vision" in required:
            selection["frame_bundles"] = [[1, 1.5, 2]]
        if "ocr" in required:
            selection["ocr_frame_times"] = [1]
    selected_profiles = {
        "asr": ("asr-whisper-1", None),
        "vision": ("vision-gpt-5-6-luna", "vision-gpt-5-6-terra"),
        "ocr": ("ocr-google-cloud-vision", "ocr-aliyun-recognize-all-text"),
    }
    for selection in payload["profile_selections"]:
        default_profile_id, fallback_profile_id = selected_profiles[selection["modality"]]
        selection.update(
            selection_status="selected",
            default_profile_id=default_profile_id,
            fallback_profile_id=fallback_profile_id,
            fallback_allowed_triggers=(
                ["default_quality_gate_failed"] if fallback_profile_id else []
            ),
            public_evidence_note="Frozen from a separately reviewed evidence record.",
        )
    return PilotManifest.model_validate(payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"time_origin": "pending"},
        {"acceptable_time_ranges": {}},
        {"required_key_terms": []},
        {"expected_video_ids": []},
    ],
)
def test_frozen_pilot_queries_require_expected_video_absolute_ranges_and_key_terms(overrides):
    with pytest.raises(ValidationError):
        _frozen_query(**overrides)


def test_frozen_pilot_query_accepts_only_explicit_video_absolute_truth():
    query = _frozen_query()
    assert query.time_origin == "video_absolute"
    assert query.acceptable_time_ranges["YT-01"][0].start_sec == 10


@pytest.mark.parametrize("required_key_terms", [[""], ["   "], ["Term", " term "]])
def test_pilot_query_rejects_blank_or_duplicate_key_terms(required_key_terms):
    with pytest.raises(ValidationError):
        _frozen_query(required_key_terms=required_key_terms)


def test_frozen_truth_and_media_must_fit_the_regression_source_duration():
    catalog = load_catalog()
    manifest = _frozen_manifest()
    duration = next(sample.duration_sec for sample in catalog.samples if sample.sample_id == "YT-01")

    query_payload = manifest.model_dump(mode="json")
    query_payload["queries"][0]["acceptable_time_ranges"]["YT-01"] = [
        {"start_sec": duration, "end_sec": duration + 1}
    ]
    query_manifest = PilotManifest.model_validate(query_payload)
    assert "pilot_query_time_range_out_of_bounds" in pilot_execution_blockers(query_manifest, catalog)

    media_payload = manifest.model_dump(mode="json")
    media_payload["media_selections"][0]["frame_bundles"] = [
        [duration - 1, duration, duration + 1]
    ]
    media_manifest = PilotManifest.model_validate(media_payload)
    assert "pilot_media_selection_out_of_bounds" in pilot_execution_blockers(media_manifest, catalog)


def test_distractors_and_profiles_must_resolve_to_compatible_registry_entries():
    catalog = load_catalog()
    manifest = _frozen_manifest()

    payload = manifest.model_dump(mode="json")
    payload["queries"][0]["distractor_video_ids"] = ["YT-99"]
    unknown_distractor = PilotManifest.model_validate(payload)
    assert "pilot_distractor_not_in_regression_pool" in pilot_execution_blockers(
        unknown_distractor, catalog
    )

    profiles = [
        profile.model_copy(update={"supports_fixture": False})
        if profile.profile_id == "asr-whisper-1"
        else profile
        for profile in catalog.profiles
    ]
    incompatible_catalog = catalog.model_copy(update={"profiles": profiles})
    assert "pilot_default_profile_not_fixture_compatible" in pilot_execution_blockers(
        manifest, incompatible_catalog
    )
    with pytest.raises(PilotValidationError):
        planned_pilot_units(manifest, catalog=incompatible_catalog)


def test_pilot_rejects_noncanonical_regression_catalog_and_probe_references():
    catalog = load_catalog()
    pilot_payload = load_pilot_manifest().model_dump(mode="python")
    pilot_payload["regression_catalog_path"] = "other-catalog.yaml"
    mismatched_pilot = PilotManifest.model_validate(pilot_payload)
    assert "pilot_regression_catalog_path_mismatch" in pilot_execution_blockers(
        mismatched_pilot, catalog
    )

    mismatched_catalog = catalog.model_copy(update={"probe_snapshot": "/tmp/other-probe.yaml"})
    assert "pilot_regression_probe_snapshot_mismatch" in pilot_execution_blockers(
        load_pilot_manifest(), mismatched_catalog
    )
    with pytest.raises(PilotValidationError):
        planned_pilot_units(load_pilot_manifest(), catalog=mismatched_catalog)


def test_pending_truth_is_allowed_only_in_planning_manifest():
    pilot = load_pilot_manifest()
    assert pilot.truth_annotation_status == "pending_manual"
    assert all(query.annotation_status == "pending_manual" for query in pilot.queries)
    assert "pilot_query_truth_pending" in pilot_execution_blockers(pilot)


def test_planning_manifest_fails_closed_on_scoped_blockers_but_not_unselected_records():
    blockers = pilot_execution_blockers(load_pilot_manifest())
    assert "pilot_manifest_not_frozen" in blockers
    assert "public_evidence_selection_pending" in blockers
    assert "pilot_query_truth_pending" in blockers
    assert "pilot_media_selection_pending" in blockers
    assert "pilot_content_classification_pending" in blockers
    assert all(sample_id not in " ".join(blockers) for sample_id in ("YT-02", "YT-04", "BI-02", "BI-05"))


def test_profile_selection_requires_one_default_and_optional_triggered_fallback():
    with pytest.raises(ValidationError):
        PilotProfileSelection(
            modality="vision",
            selection_status="pending",
            default_profile_id="vision-gpt-5-6-luna",
        )

    selected = PilotProfileSelection(
        modality="vision",
        selection_status="selected",
        default_profile_id="vision-gpt-5-6-luna",
        fallback_profile_id="vision-gpt-5-6-terra",
        fallback_allowed_triggers=["default_protocol_failed"],
        public_evidence_note="Reviewed evidence record.",
    )
    assert not fallback_is_allowed(selected, None)
    assert not fallback_is_allowed(selected, "default_quality_gate_failed")
    assert fallback_is_allowed(selected, "default_protocol_failed")

    with pytest.raises(ValidationError):
        PilotProfileSelection(
            modality="vision",
            selection_status="selected",
            default_profile_id="vision-gpt-5-6-luna",
            fallback_profile_id="vision-gpt-5-6-terra",
            public_evidence_note="Reviewed evidence record.",
        )


def test_retry_policy_runs_default_once_and_caps_retries_at_two():
    policy = PilotRetryPolicy()
    assert policy.default_runs_per_unit == 1
    assert policy.max_retries == 2
    assert set(policy.allowed_reasons) == {"provider_transient", "protocol_instability"}
    with pytest.raises(ValidationError):
        PilotRetryPolicy(max_retries=3)
    with pytest.raises(ValidationError):
        PilotRetryPolicy(allowed_reasons=["provider_transient", "other"])


def test_pilot_media_rejects_unrequired_or_missing_frozen_modalities():
    with pytest.raises(ValidationError):
        PilotMediaSelection(
            sample_id="YT-01",
            required_modalities=["vision"],
            audio_windows=[TimeRange(start_sec=0, end_sec=10)],
        )
    with pytest.raises(ValidationError):
        PilotMediaSelection(
            sample_id="YT-01",
            required_modalities=["asr"],
            selection_status="frozen",
        )


def _selected_profile_pilot(*, with_fallbacks: bool = False) -> PilotManifest:
    pilot = _frozen_manifest()
    defaults = {
        "asr": "asr-whisper-1",
        "vision": "vision-gpt-5-6-luna",
        "ocr": "ocr-google-cloud-vision",
    }
    fallbacks = {
        "asr": "asr-qwen-filetrans",
        "vision": "vision-gpt-5-6-terra",
        "ocr": "ocr-aliyun-recognize-all-text",
    }
    payload = pilot.model_dump(mode="python")
    payload["profile_selections"] = [
        {
            "modality": modality,
            "selection_status": "selected",
            "default_profile_id": defaults[modality],
            "fallback_profile_id": fallbacks[modality] if with_fallbacks else None,
            "fallback_allowed_triggers": ["default_protocol_failed"] if with_fallbacks else [],
            "public_evidence_note": "Reviewed evidence record.",
        }
        for modality in ("asr", "vision", "ocr")
    ]
    return PilotManifest.model_validate(payload)


def test_pending_public_evidence_cannot_name_selected_profile_winners():
    payload = load_pilot_manifest().model_dump(mode="python")
    payload["profile_selections"][0] = {
        "modality": "asr",
        "selection_status": "selected",
        "default_profile_id": "asr-whisper-1",
        "public_evidence_note": "Unfrozen claim.",
    }
    with pytest.raises(ValidationError):
        PilotManifest.model_validate(payload)


def test_pilot_planner_counts_selected_default_units_dynamically_and_never_fallback_without_trigger():
    pilot = _selected_profile_pilot(with_fallbacks=True)
    plan = planned_pilot_units(pilot)
    assert plan["potential_units"] == 10
    assert plan["planned_default_units"] == 10
    assert plan["fallback_planned_units"] == 0
    plan_with_trigger = planned_pilot_units(pilot, fallback_triggers={"vision": "default_protocol_failed"})
    assert plan_with_trigger["planned_default_units"] == 10
    assert plan_with_trigger["fallback_planned_units"] == 4
    assert all(item["modality"] == "vision" for item in plan_with_trigger["fallback_units"])


def test_validate_pilot_cli_is_offline_machine_readable_and_fails_closed(capsys):
    assert main(["--validate-pilot"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "pilot_validated"
    assert output["regression_pool"]["sample_count"] == 12
    assert output["pilot"]["sample_count"] == 4
    assert output["pilot"]["query_count"] == 12
    assert output["potential_units"] == 10
    assert output["planned_default_units"] == 0
    assert output["fallback_planned_units"] == 0
    assert output["plan"]["default_units"] == []
    assert output["plan"]["fallback_units"] == []
    assert output["external_calls"] == 0
    assert output["execution_gate"] == "closed"
    assert "public_evidence_selection_pending" in output["blockers"]


def test_pilot_dry_run_is_dynamic_selected_scope_and_network_free(tmp_path):
    report = pilot_dry_run(load_pilot_manifest(), temp_base_dir=tmp_path)
    payload = report.as_dict()
    assert payload["regression_pool_count"] == 12
    assert payload["pilot_sample_count"] == 4
    assert payload["pilot_query_count"] == 12
    assert payload["planned_default_units"] == 0
    assert payload["fallback_planned_units"] == 0
    assert payload["plan"]["potential_units"] == 10
    assert payload["protocol_fixtures"] == {
        "media_bundles": 4,
        "asr_responses": 4,
        "vision_responses": 4,
        "ocr_responses": 2,
        "error_envelopes": 0,
    }
    assert payload["external_calls"] == 0
    assert payload["execution_gate"]["open"] is False
    assert payload["cleanup"]["removed"] is True


def test_pilot_dry_run_default_output_ignores_ambient_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-secret-sentinel")
    payload = pilot_dry_run(load_pilot_manifest(), temp_base_dir=tmp_path).as_dict()
    openai_profiles = [
        item for item in payload["provider_preflight"] if item["provider"] == "openai"
    ]
    assert openai_profiles
    assert all(item["available"] is False for item in openai_profiles)
    assert "ambient-secret-sentinel" not in json.dumps(payload)


def test_pilot_cli_flags_do_not_open_or_reuse_formal_run(capsys):
    assert main(["--validate-pilot", "--dry-run"]) == 2
    assert "pilot_flags_conflict" in capsys.readouterr().err
    assert main(["--run"]) == 2
    assert "BenchmarkGateError" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "error_code"),
    [
        (["--pilot-catalog", "ignored.yaml"], "pilot_catalog_requires_pilot_flag"),
        (["--pilot-catalog", "ignored.yaml", "--dry-run"], "pilot_catalog_requires_pilot_flag"),
        (["--confirm-openai-smoke"], "smoke_confirmation_requires_smoke_flag"),
        (["--probe-output", "ignored.yaml"], "probe_output_requires_probe_flag"),
    ],
)
def test_cli_mode_modifiers_cannot_be_silently_ignored(argv, error_code, capsys):
    assert main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert error_code in captured.err
    assert "ignored.yaml" not in captured.err


def test_smoke_rejects_catalog_modifiers_before_loading_or_calling_anything(capsys):
    assert main(["--openai-smoke", "--confirm-openai-smoke", "--catalog", "ignored.yaml"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "openai_smoke_flags_conflict" in captured.err
    assert "ignored.yaml" not in captured.err


def test_malformed_cli_arguments_use_stable_content_safe_error(capsys):
    sentinel = "https://user:secret@example.invalid/private"
    assert main([f"--unknown={sentinel}"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "video-recognition benchmark unavailable: cli_arguments_invalid"
    assert sentinel not in captured.err
