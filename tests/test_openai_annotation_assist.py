from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from evals.video_recognition import annotation_assist
from evals.video_recognition.__main__ import main
from evals.video_recognition.openai_smoke import TransportResponse
from evals.video_recognition.pilot_schema import load_pilot_manifest, pilot_execution_blockers


class FakePreparer:
    def prepare(self, root):
        (root / "bounded-private-media.bin").write_bytes(b"temporary")
        return annotation_assist.PreparedInputs(
            audio=(
                annotation_assist.PreparedAudio(
                    "BI-06",
                    annotation_assist.AudioSpec("cake-description-part-1", 5, 65),
                    b"RIFFfake-wav",
                ),
            ),
            vision=(
                annotation_assist.PreparedVision(
                    "YT-01",
                    annotation_assist.VisionSpec(
                        "applications-slide",
                        (18, 20, 22),
                        "read the slide",
                        True,
                    ),
                    (b"jpeg-one", b"jpeg-two", b"jpeg-three"),
                ),
            ),
        )


class FakeTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, path, *, headers, body, timeout_seconds):
        self.calls.append(
            {
                "method": method,
                "path": path,
                "headers": headers,
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        if path == "/v1/audio/transcriptions":
            return TransportResponse(
                200,
                json.dumps(
                    {
                        "text": "外皮焦焦的，像冰淇淋 https://signed.example/private",
                        "segments": [{"start": 2.0, "end": 4.5, "text": "外皮焦焦的，像冰淇淋"}],
                        "words": [{"start": 2.0, "end": 2.5, "word": "外皮"}],
                        "raw_response": "must-not-survive",
                    }
                ).encode(),
            )
        candidate = {
            "summary": "讲师在 Applications of Machine Learning 幻灯片旁",
            "same_scene": True,
            "screen_text_present": True,
            "screen_text": ["Applications of Machine Learning"],
            "actions": [],
            "search_terms": ["lecturer", "slide"],
            "frame_observations": [
                {
                    "frame_index": 1,
                    "timestamp_sec": 18,
                    "observation": "lecturer and display",
                    "screen_text": ["Applications of Machine Learning"],
                }
            ],
            "uncertainties": [],
        }
        return TransportResponse(
            200,
            json.dumps(
                {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": json.dumps(candidate)}],
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                    "provider_body": "must-not-survive",
                }
            ).encode(),
        )


def test_annotation_assist_requires_dedicated_confirmation():
    with pytest.raises(annotation_assist.AnnotationAssistError) as exc_info:
        annotation_assist.run_annotation_assist(
            api_key="test-key",
            transport=FakeTransport(),
            media_preparer=FakePreparer(),
        )
    assert exc_info.value.error_code == "confirmation_required"


def test_plan_is_fixed_to_four_public_samples_and_sixty_second_sections():
    annotation_assist.validate_annotation_plan()
    assert {clip.sample_id for clip in annotation_assist.ANNOTATION_PLAN} == {
        "YT-01",
        "YT-03",
        "BI-01",
        "BI-06",
    }
    assert all(0 < clip.duration_sec < 60 for clip in annotation_assist.ANNOTATION_PLAN)
    invalid = [replace(annotation_assist.ANNOTATION_PLAN[0], public_url="https://example.com/video")]
    with pytest.raises(annotation_assist.AnnotationAssistError) as exc_info:
        annotation_assist.validate_annotation_plan(invalid)
    assert exc_info.value.error_code == "annotation_plan_invalid"


def test_fake_run_preserves_candidates_but_redacts_urls_and_cleans_media(tmp_path):
    transport = FakeTransport()
    report_path = tmp_path / "review.md"
    media_root = tmp_path / "media"
    report = annotation_assist.run_annotation_assist(
        confirm=True,
        api_key="test-key",
        transport=transport,
        media_preparer=FakePreparer(),
        temp_base_dir=media_root,
        report_path=report_path,
    )
    assert report.success
    assert report.cleanup_removed
    assert not list(media_root.iterdir())
    text = report_path.read_text()
    assert "外皮焦焦的" in text
    assert "Applications of Machine Learning" in text
    assert "7.0" in text and "9.5" in text
    for forbidden in (
        "test-key",
        "signed.example",
        "must-not-survive",
        "data:image",
        "youtube.com",
        "bilibili.com",
        "bounded-private-media",
    ):
        assert forbidden not in text
    assert "[REDACTED_URL]" in text


def test_asr_normalization_converts_all_text_levels_without_changing_timestamps():
    payload = {
        "text": "隔熱水把它攪軟，吃起來像冰淇淋",
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "隔熱水把它攪軟"},
        ],
        "words": [
            {"start": 0.0, "end": 0.4, "word": "隔熱水"},
        ],
    }

    normalized = annotation_assist._normalize_asr(payload, 35.0)

    assert normalized["text"] == "隔热水把它搅软,吃起来像冰淇淋"
    assert normalized["segments"] == [
        {"start_sec": 35.0, "end_sec": 36.5, "text": "隔热水把它搅软"}
    ]
    assert normalized["words"] == [
        {"start_sec": 35.0, "end_sec": 35.4, "word": "隔热水"}
    ]


def test_requests_use_whisper_and_luna_without_formal_provider_gate(tmp_path):
    transport = FakeTransport()
    report = annotation_assist.run_annotation_assist(
        confirm=True,
        api_key="test-key",
        transport=transport,
        media_preparer=FakePreparer(),
        temp_base_dir=tmp_path / "media",
        report_path=tmp_path / "review.md",
    )
    assert report.success
    assert [call["path"] for call in transport.calls] == [
        "/v1/audio/transcriptions",
        "/v1/responses",
    ]
    audio_body = transport.calls[0]["body"]
    assert b"whisper-1" in audio_body
    assert audio_body.count(b'timestamp_granularities[]') == 2
    payload = json.loads(transport.calls[1]["body"])
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["store"] is False
    images = [part for part in payload["input"][0]["content"] if part["type"] == "input_image"]
    assert len(images) == 3
    assert all(image["detail"] == "original" for image in images)
    assert [base64.b64decode(image["image_url"].split(",", 1)[1]) for image in images] == [
        b"jpeg-one",
        b"jpeg-two",
        b"jpeg-three",
    ]
    blockers = pilot_execution_blockers(load_pilot_manifest())
    assert "pilot_manifest_not_frozen" in blockers
    assert "pilot_query_truth_pending" in blockers


def test_cli_annotation_branch_isolated_from_catalog_and_smoke(monkeypatch, capsys, tmp_path):
    output = tmp_path / "review.md"
    output.write_text("review", encoding="utf-8")
    fake_report = annotation_assist.AnnotationAssistReport((), True, output)
    monkeypatch.setattr(annotation_assist, "run_annotation_assist", lambda **kwargs: fake_report)
    monkeypatch.setattr(
        "evals.video_recognition.__main__.load_catalog",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("catalog must not load")),
    )
    assert main(["--openai-annotation-assist", "--confirm-openai-annotation-assist"]) == 3
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "completed_with_errors"
    assert main(
        [
            "--openai-annotation-assist",
            "--confirm-openai-annotation-assist",
            "--openai-smoke",
        ]
    ) == 2
    assert "annotation_assist_flags_conflict" in capsys.readouterr().err


def test_media_child_environment_excludes_provider_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("ANOTHER_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("SSL_CERT_FILE", "/safe/ca.pem")
    child = annotation_assist._safe_media_environment()
    assert "OPENAI_API_KEY" not in child
    assert "ANOTHER_ACCESS_TOKEN" not in child
    assert child["SSL_CERT_FILE"] == "/safe/ca.pem"


def test_partial_media_failure_still_produces_review_report(tmp_path):
    preparer = FakePreparer()
    original_prepare = preparer.prepare

    def prepare_with_failure(root):
        prepared = original_prepare(root)
        return annotation_assist.PreparedInputs(
            prepared.audio,
            prepared.vision,
            (annotation_assist.PreparationFailure("BI-06", "cake-35-65", "bounded_media_acquisition_failed"),),
        )

    preparer.prepare = prepare_with_failure
    report_path = tmp_path / "partial.md"
    report = annotation_assist.run_annotation_assist(
        confirm=True,
        api_key="test-key",
        transport=FakeTransport(),
        media_preparer=preparer,
        temp_base_dir=tmp_path / "media",
        report_path=report_path,
    )
    assert not report.success
    assert report_path.is_file()
    assert "bounded_media_acquisition_failed" in report_path.read_text()
