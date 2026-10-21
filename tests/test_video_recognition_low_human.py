from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from evals.video_recognition.__main__ import main
from evals.video_recognition.events import (
    EvaluationEventError,
    EvaluationScore,
    EventInputSummary,
    assert_event_safe,
    build_evaluation_event,
)
from evals.video_recognition.quality_report import (
    InputBounds,
    WeakReference,
    WeakReferenceCue,
    build_quality_report,
)
from evals.video_recognition.sampling import (
    ReviewFeedback,
    SamplingPolicy,
    apply_review_feedback,
    assess_risk,
    select_review_samples,
)
from evals.video_recognition.runner import automatic_quality_report
from evals.video_recognition.schema import TimeRange, load_catalog
from evals.video_recognition.protocol import (
    ASRResponse,
    ASRSegment,
    OCRDetection,
    OCRResponse,
    PolygonPoint,
    RecognitionJobResult,
    Usage,
    VisionObservation,
    VisionResponse,
    stable_error,
)


def _asr_result(
    *,
    sample_id: str = "YT-01",
    candidate_id: str = "asr-test",
    text: str = "hello world",
    start_sec: float = 10.0,
    end_sec: float = 15.0,
    segments: list[ASRSegment] | None = None,
    usage: Usage | None = None,
) -> RecognitionJobResult:
    run_id = f"run-{candidate_id}"
    response = ASRResponse(
        run_id=run_id,
        sample_id=sample_id,
        input_window_id=f"{sample_id.lower()}.audio.1",
        provider="test-provider",
        model_version="test-asr-v1",
        language="en",
        segments=segments
        if segments is not None
        else [ASRSegment(text=text, start_sec=start_sec, end_sec=end_sec)],
        usage=usage or Usage(audio_seconds=5.0, input_tokens=11, output_tokens=7),
        latency_ms=12.0,
    )
    return RecognitionJobResult(
        run_id=run_id,
        candidate_id=candidate_id,
        sample_id=sample_id,
        run_index=1,
        modality="asr",
        terminal_state="completed",
        latency_ms=response.latency_ms,
        usage=response.usage,
        asr=response,
    )


def _vision_result(*, sample_id: str = "YT-01", candidate_id: str = "vision-test") -> RecognitionJobResult:
    run_id = f"run-{candidate_id}"
    response = VisionResponse(
        run_id=run_id,
        sample_id=sample_id,
        input_bundle_id=f"{sample_id.lower()}.bundle.1",
        provider="test-provider",
        model_version="test-vision-v1",
        observations=[
            VisionObservation(
                scene_type="slide",
                short_description="a code editor slide",
                entities=["editor"],
                actions=["typing"],
                screen_topics=["python"],
                search_terms=["code"],
                evidence_frame_times=[10.0, 12.0, 14.0],
                confidence=0.8,
            )
        ],
        usage=Usage(image_count=3, input_tokens=20, output_tokens=8),
        latency_ms=20.0,
    )
    return RecognitionJobResult(
        run_id=run_id,
        candidate_id=candidate_id,
        sample_id=sample_id,
        run_index=1,
        modality="vision",
        terminal_state="completed",
        latency_ms=response.latency_ms,
        usage=response.usage,
        vision=response,
    )


def _ocr_result(*, sample_id: str = "YT-01", candidate_id: str = "ocr-test") -> RecognitionJobResult:
    run_id = f"run-{candidate_id}"
    response = OCRResponse(
        run_id=run_id,
        sample_id=sample_id,
        input_frame_id=f"{sample_id.lower()}.frame.1",
        provider="test-provider",
        model_version="test-ocr-v1",
        detections=[
            OCRDetection(
                text="Applications of Machine Learning",
                polygon=[
                    PolygonPoint(x=0.1, y=0.1),
                    PolygonPoint(x=0.9, y=0.1),
                    PolygonPoint(x=0.9, y=0.2),
                    PolygonPoint(x=0.1, y=0.2),
                ],
                confidence=0.9,
                frame_timestamp_sec=10.0,
            )
        ],
        usage=Usage(image_count=1, input_tokens=6, output_tokens=4),
        latency_ms=8.0,
    )
    return RecognitionJobResult(
        run_id=run_id,
        candidate_id=candidate_id,
        sample_id=sample_id,
        run_index=1,
        modality="ocr",
        terminal_state="completed",
        latency_ms=response.latency_ms,
        usage=response.usage,
        ocr=response,
    )


def test_quality_report_runs_without_frozen_truth_or_external_calls():
    report = automatic_quality_report(load_catalog())

    assert report.manual_truth_status == "pending_manual"
    assert report.manual_truth_required_for_report is False
    assert report.external_calls == 0
    assert report.sample_count == 12
    assert report.result_count > 0
    assert {item.modality for item in report.modalities} == {"asr", "vision", "ocr"}


def test_quality_report_classifies_schema_timestamp_search_and_provider_failures():
    valid_empty = _asr_result(segments=[])
    out_of_bounds = _asr_result(candidate_id="asr-out-of-bounds", start_sec=0.0, end_sec=2.0)
    provider_failure = RecognitionJobResult(
        run_id="run-provider-failure",
        candidate_id="asr-failed",
        sample_id="YT-01",
        run_index=1,
        modality="asr",
        terminal_state="provider_incomplete",
        error=stable_error("provider_timeout", phase="provider", retryable=True),
    )
    report = build_quality_report(
        [
            valid_empty,
            out_of_bounds,
            provider_failure,
            {"sample_id": "YT-01", "modality": "asr", "malformed": True},
        ],
        input_bounds={
            ("YT-01", "asr"): InputBounds(
                sample_id="YT-01",
                modality="asr",
                time_range=TimeRange(start_sec=10.0, end_sec=20.0),
            )
        },
        expected_sample_ids=["YT-01"],
    )

    assert report.result_count == 4
    assert report.completed_count == 2
    assert report.schema_valid_rate == pytest.approx(0.75)
    assert report.failure_counts == {"provider_timeout": 1, "schema_invalid": 1}
    assert {issue.code for issue in report.issues} == {
        "schema_invalid",
        "provider_failure",
        "timestamp_invalid",
        "empty_search_fields",
    }
    asr = next(item for item in report.modalities if item.modality == "asr")
    assert asr.timestamp_valid_count == 1
    assert asr.searchable_count == 0
    assert asr.failure_counts == {"provider_timeout": 1, "schema_invalid": 1}


def test_weak_reference_metrics_are_known_only_when_reference_content_exists():
    asr = _asr_result()
    report = build_quality_report(
        [asr],
        weak_references={
            "YT-01": WeakReference(
                sample_id="YT-01",
                subtitle_cues=[WeakReferenceCue(start_sec=10.0, end_sec=15.0, text="hello world")],
            )
        },
        input_bounds={("YT-01", "asr"): TimeRange(start_sec=10.0, end_sec=20.0)},
        expected_sample_ids=["YT-01"],
    )
    modality = report.modalities[0]
    assert modality.weak_reference_status == "known"
    assert modality.weak_reference_metrics["subtitle_segment_overlap_rate"] == 1.0
    assert modality.weak_reference_metrics["subtitle_keyword_recall"] == 1.0

    unknown = build_quality_report(
        [asr],
        weak_references={"YT-01": WeakReference(sample_id="YT-01")},
        expected_sample_ids=["YT-01"],
    )
    assert unknown.modalities[0].weak_reference_status == "unknown"
    assert unknown.modalities[0].weak_reference_metrics == {}


def test_sampling_is_deterministic_and_review_feedback_is_structured():
    candidates = [
        assess_risk(
            sample_id="YT-02",
            modality="asr",
            unit_id="yt-02.asr.high",
            candidate_id="asr-high",
            reasons=["provider_failure"],
        ),
        assess_risk(
            sample_id="YT-01",
            modality="asr",
            unit_id="yt-01.asr.baseline",
            candidate_id="asr-baseline",
        ),
        assess_risk(
            sample_id="YT-01",
            modality="vision",
            unit_id="yt-01.vision.baseline",
            candidate_id="vision-baseline",
        ),
        assess_risk(
            sample_id="YT-02",
            modality="vision",
            unit_id="yt-02.vision.supplement",
            candidate_id="vision-supplement",
            reasons=["proxy_conflict"],
        ),
    ]
    policy = SamplingPolicy(
        fixed_per_modality=1,
        risk_threshold=0.5,
        max_supplement_per_modality=1,
        max_total=3,
    )
    first = select_review_samples(candidates, policy=policy)
    second = select_review_samples(list(reversed(candidates)), policy=policy)
    assert [item.unit_id for item in first.selected] == [item.unit_id for item in second.selected]
    assert first.fixed_count == 2
    assert first.supplemental_count == 1
    assert first.selected[-1].risk_score >= policy.risk_threshold

    updated = apply_review_feedback(
        first.selected,
        [ReviewFeedback(unit_id=first.selected[0].unit_id, status="rejected", defect_codes=["timestamp_invalid"])],
    )
    reviewed = next(item for item in updated if item.unit_id == first.selected[0].unit_id)
    assert reviewed.review_status == "rejected"
    assert reviewed.review_codes == ["timestamp_invalid"]


def test_evaluation_events_keep_numeric_summaries_and_reject_content_or_urls():
    result = _asr_result(usage=Usage(audio_seconds=5.0, input_tokens=11, output_tokens=7))
    event = build_evaluation_event(
        result,
        input_summary=EventInputSummary(
            sample_id="YT-01",
            media_kind="audio_window",
            duration_bucket="lt_10s",
            time_range={"start_sec": 10.0, "end_sec": 15.0},
            has_subtitles=True,
            weak_reference_signal_count=1,
        ),
        scores={"timestamp_valid": 1.0},
    )
    payload = event.as_dict()
    assert payload["usage"]["input_tokens"] == 11
    assert payload["usage"]["output_tokens"] == 7
    assert payload["scores"] == [{"name": "timestamp_valid", "value": 1.0, "numerator": None, "denominator": None, "known": True}]
    assert_event_safe(payload)

    for bad in (
        {"prompt": "secret question"},
        {"answer": "secret answer"},
        {"nested": {"text": "subtitle"}},
        {"endpoint": "https://provider.example/upload"},
        {"provider_response": {"body": "raw response"}},
        {"secret": "sk-proj-1234567890abcdef"},
    ):
        with pytest.raises(EvaluationEventError):
            assert_event_safe(bad)

    with pytest.raises(ValidationError):
        EvaluationScore(name="known-but-missing", value=None, known=True)


def test_failure_event_is_safe_and_has_stable_failure_code():
    result = RecognitionJobResult(
        run_id="run-failed-event",
        candidate_id="asr-failed",
        sample_id="YT-01",
        run_index=1,
        modality="asr",
        terminal_state="provider_incomplete",
        error=stable_error("provider_timeout", phase="provider", retryable=True),
    )
    event = build_evaluation_event(result)
    assert event.prediction.terminal_state == "provider_incomplete"
    assert event.failure_code == "provider_timeout"
    assert event.provider == "unknown"
    assert event.model_version == "unknown"
    assert_event_safe(event.as_dict())


def test_quality_report_cli_is_machine_readable_and_offline(capsys):
    assert main(["--quality-report"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["protocol_version"] == "video-quality-report.v1"
    assert payload["external_calls"] == 0
    assert payload["manual_truth_required_for_report"] is False


def test_quality_report_cli_rejects_conflicting_provider_or_pilot_flags(capsys):
    assert main(["--quality-report", "--run"]) == 2
    assert "quality_report_flags_conflict" in capsys.readouterr().err
    assert main(["--quality-report", "--validate-pilot"]) == 2
    assert "quality_report_flags_conflict" in capsys.readouterr().err


def test_multimodal_proxy_metrics_are_computed_without_manual_truth():
    report = build_quality_report(
        [_vision_result(), _ocr_result()],
        weak_references={
            "YT-01": WeakReference(
                sample_id="YT-01",
                metadata_terms=["code", "python"],
                visible_text=["Applications of Machine Learning"],
            )
        },
        input_bounds={
            ("YT-01", "vision"): InputBounds(sample_id="YT-01", modality="vision", frame_times=[10.0, 12.0, 14.0]),
            ("YT-01", "ocr"): InputBounds(
                sample_id="YT-01",
                modality="ocr",
                time_range=TimeRange(start_sec=10.0, end_sec=10.1),
            ),
        },
        expected_sample_ids=["YT-01"],
    )
    vision = next(item for item in report.modalities if item.modality == "vision")
    ocr = next(item for item in report.modalities if item.modality == "ocr")
    assert vision.weak_reference_status == "known"
    assert vision.weak_reference_metrics["metadata_term_overlap"] == 1.0
    assert ocr.weak_reference_status == "known"
    assert ocr.weak_reference_metrics["visible_text_recall"] == 1.0
