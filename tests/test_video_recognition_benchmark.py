from __future__ import annotations

import json

import pytest

from evals.video_recognition.adapters import (
    AdapterInputError,
    FrameInput,
    ProviderCredentialsMissing,
    ProviderExecutionDisabled,
    adapter_for_profile,
    credential_status,
)
from evals.video_recognition.cleanup import CleanupError, TemporaryRun, verify_cleanup
from evals.video_recognition.media import descriptor_for_sample, synthetic_inputs
from evals.video_recognition.probe import ConnectorProbe, run_formal_probe
from evals.video_recognition.protocol import (
    ASRResponse,
    MediaSampleBundle,
    OCRResponse,
    VisionResponse,
)
from evals.video_recognition.redaction import assert_redacted, redact_report
from evals.video_recognition.runner import dry_run
from evals.video_recognition.__main__ import main
from evals.video_recognition.schema import (
    BenchmarkGateError,
    Query,
    TimeRange,
    load_catalog,
    load_probe_snapshot,
)
from evals.video_recognition.scoring import (
    QueryScore,
    RankedHit,
    aggregate_quality,
    keyword_recall,
    score_query,
    temporal_hit,
    temporal_iou,
)


def test_catalog_freezes_scope_without_opening_provider_gate():
    catalog = load_catalog()
    assert len(catalog.samples) == 12
    assert {sample.platform for sample in catalog.samples} == {"youtube", "bilibili"}
    assert len(catalog.queries) == 30
    assert {query.kind for query in catalog.queries} == {"speech", "visual", "ocr", "combined"}
    assert catalog.runs_per_profile_sample == 3
    blockers = catalog.execution_blockers()
    assert blockers
    assert "formal_probe_snapshot_pending" not in blockers
    assert "formal_subtitle_body_probe_pending" not in blockers
    assert "manual_truth_annotation_pending" in blockers
    assert "manual_media_window_and_frame_review_pending" in blockers
    with pytest.raises(BenchmarkGateError):
        catalog.assert_execution_ready()


def test_probe_snapshot_is_safe_and_complete():
    snapshot = load_probe_snapshot()
    assert len(snapshot.records) == 12
    assert all(record.publicly_accessible for record in snapshot.records)
    assert all(record.single_video for record in snapshot.records)
    assert all(not hasattr(record, "body") for record in snapshot.records)
    assert sum(bool(record.subtitle_tracks) for record in snapshot.records) == 4
    assert sum(record.probe_status == "no_platform_track" for record in snapshot.records) == 8
    assert all(record.probe_status in {"complete", "no_platform_track"} for record in snapshot.records)
    assert run_formal_probe(load_catalog()).probe_kind == "formal_connector_probe"

    catalog = load_catalog()
    catalog_urls = {sample.public_url for sample in catalog.samples}
    blocked_keys = {
        "body", "headers", "http_headers", "stderr", "stdout", "signed_url",
        "subtitle_url", "raw_response", "response_body", "authorization", "cookie",
    }

    def inspect(value):
        if isinstance(value, dict):
            assert not (set(value) & blocked_keys)
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
        elif isinstance(value, str) and value.startswith(("http://", "https://")):
            assert value in catalog_urls

    inspect(snapshot.model_dump(mode="json"))

    expected = {
        "YT-01": ("complete", "complete"),
        "YT-02": ("no_platform_track", "none"),
        "YT-03": ("complete", "complete"),
        "YT-04": ("complete", "partial"),
        "YT-05": ("no_platform_track", "none"),
        "YT-06": ("complete", "complete"),
        **{f"BI-0{index}": ("no_platform_track", "none") for index in range(1, 7)},
    }
    assert {record.sample_id: (record.probe_status, record.subtitle_coverage_status) for record in snapshot.records} == expected


def test_media_descriptor_stays_inside_minimum_boundary():
    catalog = load_catalog()
    for sample in catalog.samples:
        descriptor = descriptor_for_sample(sample)
        assert descriptor.ephemeral_only
        assert not descriptor.complete_media_present
        assert all(window.duration_sec <= 60 for window in descriptor.audio_windows)
        assert all(3 <= len(bundle) <= 6 for bundle in descriptor.frame_bundles)
        synthetic = synthetic_inputs(sample)
        assert synthetic.audio.payload.startswith(b"dry-run-")


def test_protocol_fixtures_normalize_all_modalities():
    catalog = load_catalog()
    sample = catalog.samples[0]
    inputs = synthetic_inputs(sample)
    profiles = {profile.modality: profile for profile in catalog.profiles}
    asr = adapter_for_profile(profiles["asr"]).run(run_id="test-run", sample_id=sample.sample_id, input_unit=inputs.audio)
    vision = adapter_for_profile(profiles["vision"]).run(run_id="test-run", sample_id=sample.sample_id, input_unit=inputs.frame_bundle)
    ocr_input = inputs.ocr_frames[0] if inputs.ocr_frames else inputs.frame_bundle.frames[0]
    ocr = adapter_for_profile(profiles["ocr"]).run(run_id="test-run", sample_id=sample.sample_id, input_unit=ocr_input)
    assert isinstance(asr, ASRResponse)
    assert isinstance(vision, VisionResponse)
    assert isinstance(ocr, OCRResponse)
    assert asr.segments[0].start_sec >= 0
    assert all(len(item.polygon) == 4 for item in ocr.detections)


def test_provider_adapter_fails_closed_without_paid_call():
    profile = load_catalog().profiles[1]
    status = credential_status(profile, {})
    assert not status.available
    adapter = adapter_for_profile(profile, mode="provider", environ={})
    with pytest.raises(ProviderCredentialsMissing):
        adapter.run(run_id="test-run", sample_id="YT-01", input_unit=synthetic_inputs(load_catalog().samples[0]).audio)
    adapter = adapter_for_profile(profile, mode="provider", environ={profile.required_env[0]: "present"})
    with pytest.raises(ProviderExecutionDisabled):
        adapter.run(run_id="test-run", sample_id="YT-01", input_unit=synthetic_inputs(load_catalog().samples[0]).audio)


def test_fixture_adapter_rejects_identity_mismatch_and_normalizes_input_evidence():
    catalog = load_catalog()
    sample = catalog.samples[0]
    inputs = synthetic_inputs(sample)
    profiles = {profile.modality: profile for profile in catalog.profiles}
    with pytest.raises(AdapterInputError, match="identity mismatch"):
        adapter_for_profile(profiles["asr"]).run(run_id="test-run", sample_id="YT-02", input_unit=inputs.audio)
    asr = adapter_for_profile(profiles["asr"]).run(run_id="test-run", sample_id=sample.sample_id, input_unit=inputs.audio)
    vision = adapter_for_profile(profiles["vision"]).run(run_id="test-run", sample_id=sample.sample_id, input_unit=inputs.frame_bundle)
    assert all(inputs.audio.time_range.start_sec <= item.start_sec < item.end_sec <= inputs.audio.time_range.end_sec for item in asr.segments)
    allowed = {frame.timestamp_sec for frame in inputs.frame_bundle.frames}
    assert all(time in allowed for item in vision.observations for time in item.evidence_frame_times)
    with pytest.raises(AdapterInputError):
        FrameInput(sample.sample_id, "bad", float("nan"), b"frame")


def test_scoring_uses_temporal_and_content_rules():
    left = TimeRange(start_sec=10, end_sec=20)
    right = TimeRange(start_sec=15, end_sec=25)
    assert temporal_iou(left, right) == pytest.approx(1 / 3)
    assert temporal_hit(left, [right])
    assert keyword_recall("普通话 code-switch terminology", ["普通话", "terminology"]) == 1


def test_scoring_cannot_borrow_time_keywords_or_modalities_from_other_hits():
    query = Query(
        query_id="combined-01",
        kind="combined",
        query="frozen query",
        sample_scope_ids=["YT-01", "YT-02", "BI-01"],
        expected_video_ids=["YT-01", "YT-02"],
        acceptable_time_ranges={
            "YT-01": [TimeRange(start_sec=10, end_sec=20)],
            "YT-02": [TimeRange(start_sec=100, end_sec=110)],
        },
        required_modalities=["asr", "vision"],
        required_key_terms=["needle"],
        distractor_video_ids=["BI-01"],
        annotation_status="frozen",
    )
    score = score_query(
        query,
        candidate_id="vision-gpt-5-6-luna",
        run_id="run-001",
        hits=[
            RankedHit("YT-01", "a", TimeRange(start_sec=100, end_sec=110), frozenset({"asr"}), "wrong range"),
            RankedHit("YT-01", "b", TimeRange(start_sec=10, end_sec=20), frozenset({"vision"}), "no keyword", rank=2),
            RankedHit("BI-01", "c", TimeRange(start_sec=10, end_sec=20), frozenset({"asr"}), "needle", rank=3),
        ],
    )
    assert not score.temporal_hit
    assert not score.modality_hit
    assert score.keyword_recall == 0


def test_quality_gate_fails_when_modality_specific_metrics_are_missing():
    catalog = load_catalog()
    candidate = next(profile.profile_id for profile in catalog.profiles if profile.modality == "asr")
    row = QueryScore("speech-01", candidate, "run-001", True, True, True, True, True, 1.0, True, "speech")
    decision = aggregate_quality(catalog, candidate, [row])
    assert not decision.eligible
    assert "asr_keyword_recall" in decision.failed_gates
    assert "asr_language_subgroup_recall" in decision.failed_gates


def test_probe_rejects_untrusted_subtitle_destinations_and_headers():
    probe = ConnectorProbe()
    with pytest.raises(Exception, match="subtitle_body_probe_failed"):
        probe._fetch_subtitle("http://127.0.0.1/private", {"Authorization": "Bearer secret"})

    seen = {}

    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return b""

    def opener(request, **_kwargs):
        seen["headers"] = dict(request.header_items())
        return Response()

    ConnectorProbe(urlopen=opener)._fetch_subtitle(
        "https://www.youtube.com/api/timedtext",
        {"Authorization": "Bearer secret", "User-Agent": "safe-agent"},
    )
    assert "Authorization" not in seen["headers"]
    assert seen["headers"]["User-agent"] == "safe-agent"


def test_redaction_removes_content_urls_and_secrets():
    report = redact_report(
        {
            "safe": "ok",
            "query_id": "speech-01",
            "query": "private question",
            "provider_response": {"body": "secret body"},
            "url": "https://provider.example/upload?signature=abc",
            "token": "sk-1234567890",
            "nested": ["Bearer abcdefghijklmnop", "ok"],
        },
        secrets_to_remove=["private question"],
    )
    assert report == {"safe": "ok", "query_id": "speech-01", "nested": ["[REDACTED_SECRET]", "ok"]}
    assert_redacted(report)


def test_temporary_run_requires_ownership_and_cleans(tmp_path):
    with TemporaryRun(base_dir=tmp_path) as run:
        run.write_safe_json("report.json", {"safe": 1, "query": "removed"})
        report = run.cleanup()
    verify_cleanup(report)
    assert not tmp_path.joinpath("report.json").exists()
    with pytest.raises(CleanupError):
        from evals.video_recognition.cleanup import cleanup_run_directory

        cleanup_run_directory(tmp_path)


def test_dry_run_has_no_external_calls(tmp_path):
    report = dry_run(load_catalog(), temp_base_dir=tmp_path)
    assert report.payload["external_calls"] == 0
    assert report.payload["execution_gate"]["open"] is False
    assert report.payload["cleanup"]["removed"] is True
    assert report.payload["protocol_fixtures"]["media_bundles"] == 12
    assert report.payload["planned_terminal_state_counts"]["harness_invalid"] == 396
    assert report.payload["planned_terminal_state_counts"]["provider_incomplete"] == 0


def test_cli_dry_run_is_machine_readable(capsys, tmp_path):
    assert main(["--dry-run", "--temp-base-dir", str(tmp_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "dry_run"
    assert output["external_calls"] == 0


def test_cli_requires_explicit_probe_flag_for_live_network(capsys):
    assert main(["--live"]) == 2
    assert "live_probe_requires_probe_flag" in capsys.readouterr().err
