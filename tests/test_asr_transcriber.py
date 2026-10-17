import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.asr.audio import DownloadedAudio
from app.asr.signing import verify_audio_signature
from app.asr.transcriber import (
    AsrTranscriber,
    _infer_language,
    transcript_to_text_result,
)
from app.asr.types import (
    AsrCue,
    AsrError,
    AsrProtocolError,
    AsrProviderUnavailable,
    AsrTranscript,
)
from app.browser_capture import parse_canonical_transcript
from app.connectors.base import Cue, TransientFetchError
from app.ingest.validate import IngestLimitExceeded
from app.object_store import ObjectStoreError

SECRET = "s" * 40
BASE_URL = "https://notebookai.example.com"
SPOKEN = "PRIVATE-TRANSCRIPT-WORDS"


def _settings(**overrides):
    values = {
        "asr_max_duration_seconds": 14_400,
        "asr_max_audio_bytes": 10_000,
        "asr_download_timeout_seconds": 900,
        "asr_public_base_url": BASE_URL,
        "asr_audio_url_secret": SECRET,
        "asr_audio_url_ttl_seconds": 7_200,
        "fun_asr_model": "fun-asr",
        "dashscope_api_key": None,
        "openai_api_key": None,
        "asr_whisper_fallback_enabled": True,
        "us_video_proxy_url": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _transcript(provider="fun_asr", model="fun-asr", cues=None, **overrides):
    values = {
        "cues": tuple(cues if cues is not None else [AsrCue(0.0, 1.0, SPOKEN)]),
        "language": None,
        "provider": provider,
        "model": model,
        "audio_seconds": 100.0,
        "billed_seconds": 100.0,
    }
    values.update(overrides)
    return AsrTranscript(**values)


class FakeStore:
    def __init__(self, *, put_error=None, delete_error=None):
        self.puts: list[dict] = []
        self.deleted: list[str] = []
        self.put_error = put_error
        self.delete_error = delete_error

    def put_file(self, key, path, content_type):
        self.puts.append(
            {"key": key, "path": Path(path), "content_type": content_type,
             "existed": Path(path).is_file()}
        )
        if self.put_error:
            raise self.put_error
        return Path(path).stat().st_size

    def delete_object(self, key, **kwargs):
        self.deleted.append(key)
        if self.delete_error:
            raise self.delete_error


class FakeDownloader:
    def __init__(self, *, ext="m4a", raises=None, after_write=None):
        self.ext = ext
        self.raises = raises
        self.calls: list[dict] = []
        self.dirs: list[Path] = []
        self.after_write = after_write

    def __call__(self, *, platform, url, dest_dir, settings, runner=None):
        self.calls.append({"platform": platform, "url": url})
        self.dirs.append(Path(dest_dir))
        path = Path(dest_dir) / f"audio.{self.ext}"
        path.write_bytes(b"audio-bytes")
        if self.raises:
            raise self.raises
        return DownloadedAudio(path, self.ext, 11, "audio/mp4")


class FakeFunAsr:
    def __init__(self, result=None, raises=None):
        self.result = result if result is not None else _transcript()
        self.raises = raises
        self.urls: list[str] = []

    def transcribe_url(self, audio_url):
        self.urls.append(audio_url)
        if self.raises:
            raise self.raises
        return self.result


class FakeWhisper:
    def __init__(self, result=None, raises=None):
        self.result = result if result is not None else _transcript(
            "whisper", "whisper-1", [AsrCue(0.0, 2.0, SPOKEN)]
        )
        self.raises = raises
        self.calls: list[dict] = []

    def transcribe_file(self, path, *, work_dir):
        self.calls.append({"path": Path(path), "work_dir": Path(work_dir),
                           "existed": Path(path).is_file()})
        if self.raises:
            raise self.raises
        return self.result


def _transcriber(*, settings=None, store=None, funasr=None, whisper=None, downloader=None):
    store = store if store is not None else FakeStore()
    downloader = downloader if downloader is not None else FakeDownloader()
    return (
        AsrTranscriber(
            settings=settings or _settings(),
            object_store=store,
            funasr=funasr,
            whisper=whisper,
            downloader=downloader,
        ),
        store,
        downloader,
    )


def _run(transcriber, *, duration_sec=600):
    return transcriber.transcribe(
        platform="youtube",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        duration_sec=duration_sec,
    )


@pytest.fixture
def diagnostics():
    logger = logging.getLogger("notebook_agent.runtime")
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture(level=logging.DEBUG)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(previous_level)


def _payloads(records):
    return [
        record.diagnostic_payload
        for record in records
        if getattr(record, "diagnostic_payload", {}).get("event") == "asr_transcription"
    ]


# --- happy path ------------------------------------------------------------


def test_funasr_success_stages_a_signed_url_and_cleans_up(diagnostics):
    funasr, whisper = FakeFunAsr(), FakeWhisper()
    transcriber, store, downloader = _transcriber(funasr=funasr, whisper=whisper)

    transcript = _run(transcriber)

    assert transcript.provider == "fun_asr"
    assert [cue.text for cue in transcript.cues] == [SPOKEN]
    assert whisper.calls == []
    # The audio was staged under the private temporary prefix with a uuid name.
    (put,) = store.puts
    assert re.fullmatch(r"asr-tmp/[0-9a-f]{32}\.m4a", put["key"])
    assert put["existed"] and put["content_type"] == "audio/mp4"
    # Fun-ASR received a signed URL for exactly that object name.
    (signed,) = funasr.urls
    parts = urlsplit(signed)
    name = put["key"].removeprefix("asr-tmp/")
    assert f"{parts.scheme}://{parts.netloc}" == BASE_URL
    assert parts.path == f"/api/v1/asr-audio/{name}"
    query = parse_qs(parts.query)
    assert verify_audio_signature(
        name=name,
        expires=query["expires"][0],
        sig=query["sig"][0],
        secret=SECRET,
        max_ttl_seconds=7_200,
    )
    # The temporary object and directory are gone.
    assert store.deleted == [put["key"]]
    assert not downloader.dirs[0].exists()
    assert downloader.calls == [
        {"platform": "youtube", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
    ]


def test_cues_are_normalized_ordered_and_blank_cues_dropped():
    cues = [
        AsrCue(5.0, 6.0, "second　line  "),
        AsrCue(1.0, 1.0, "ＡＢＣ　１２３"),
        AsrCue(3.0, 4.0, "   \n "),
    ]
    funasr = FakeFunAsr(_transcript(cues=cues))
    transcriber, _, _ = _transcriber(funasr=funasr)

    transcript = _run(transcriber)

    assert [(c.start_sec, c.end_sec, c.text) for c in transcript.cues] == [
        (1.0, 1.01, "ABC 123"),
        (5.0, 6.0, "second line"),
    ]


# --- fallback --------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [AsrProviderUnavailable("down"), AsrProtocolError("bad payload")]
)
def test_fallback_to_whisper_on_unavailable_or_protocol_error(error, diagnostics):
    funasr, whisper = FakeFunAsr(raises=error), FakeWhisper()
    transcriber, store, downloader = _transcriber(funasr=funasr, whisper=whisper)

    transcript = _run(transcriber)

    assert transcript.provider == "whisper"
    (call,) = whisper.calls
    assert call["existed"] and call["path"].name == "audio.m4a"
    assert call["work_dir"] == downloader.dirs[0]
    assert store.deleted == [store.puts[0]["key"]]
    (payload,) = _payloads(diagnostics)
    assert payload["fallback_used"] is True
    assert payload["provider"] == "whisper"


def test_object_store_failure_falls_back_to_whisper_and_still_deletes():
    funasr = FakeFunAsr()
    whisper = FakeWhisper()
    store = FakeStore(put_error=ObjectStoreError("object upload failed"))
    transcriber, _, _ = _transcriber(store=store, funasr=funasr, whisper=whisper)

    transcript = _run(transcriber)

    assert transcript.provider == "whisper"
    assert funasr.urls == []
    assert store.deleted == [store.puts[0]["key"]]  # a partial upload is removed too


def test_empty_valid_funasr_transcript_does_not_fall_back(diagnostics):
    funasr = FakeFunAsr(_transcript(cues=[]))
    whisper = FakeWhisper()
    transcriber, store, downloader = _transcriber(funasr=funasr, whisper=whisper)

    with pytest.raises(AsrError) as caught:
        _run(transcriber)

    assert caught.value.error_code == "asr_no_speech"
    assert whisper.calls == []
    assert store.deleted == [store.puts[0]["key"]]
    assert not downloader.dirs[0].exists()
    (payload,) = _payloads(diagnostics)
    assert payload["error_code"] == "asr_no_speech"
    assert payload["fallback_used"] is False


def test_blank_only_funasr_transcript_is_no_speech_without_fallback():
    funasr = FakeFunAsr(_transcript(cues=[AsrCue(0, 1, "  \t ")]))
    whisper = FakeWhisper()
    transcriber, _, _ = _transcriber(funasr=funasr, whisper=whisper)

    with pytest.raises(AsrError) as caught:
        _run(transcriber)

    assert caught.value.error_code == "asr_no_speech"
    assert whisper.calls == []


def test_empty_whisper_transcript_is_no_speech():
    whisper = FakeWhisper(_transcript("whisper", "whisper-1", cues=[]))
    transcriber, store, _ = _transcriber(
        funasr=FakeFunAsr(raises=AsrProviderUnavailable("x")), whisper=whisper
    )

    with pytest.raises(AsrError) as caught:
        _run(transcriber)

    assert caught.value.error_code == "asr_no_speech"
    assert store.deleted


def test_both_providers_failing_is_provider_failed():
    transcriber, store, downloader = _transcriber(
        funasr=FakeFunAsr(raises=AsrProviderUnavailable("down")),
        whisper=FakeWhisper(raises=AsrProtocolError("bad")),
    )

    with pytest.raises(AsrError) as caught:
        _run(transcriber)

    assert caught.value.error_code == "asr_provider_failed"
    assert caught.value.__cause__ is None
    assert store.deleted and not downloader.dirs[0].exists()


def test_funasr_failure_without_whisper_is_provider_failed():
    transcriber, store, _ = _transcriber(
        funasr=FakeFunAsr(raises=AsrProviderUnavailable("down"))
    )

    with pytest.raises(AsrError) as caught:
        _run(transcriber)

    assert caught.value.error_code == "asr_provider_failed"
    assert store.deleted


def test_whisper_only_when_funasr_is_not_configured():
    whisper = FakeWhisper()
    transcriber, store, _ = _transcriber(whisper=whisper)

    transcript = _run(transcriber)

    assert transcript.provider == "whisper"
    assert store.puts == [] and store.deleted == []  # nothing to stage


def test_default_clients_are_built_from_settings():
    transcriber, _, _ = _transcriber(
        settings=_settings(dashscope_api_key="dk", openai_api_key="ok",
                           dashscope_base_url="https://dashscope-intl.aliyuncs.com",
                           asr_provider_timeout_seconds=60, asr_poll_interval_seconds=2.0)
    )

    assert transcriber._funasr is not None and transcriber._whisper is not None

    no_fallback, _, _ = _transcriber(
        settings=_settings(dashscope_api_key="dk", openai_api_key="ok",
                           asr_whisper_fallback_enabled=False,
                           dashscope_base_url="https://dashscope-intl.aliyuncs.com",
                           asr_provider_timeout_seconds=60, asr_poll_interval_seconds=2.0)
    )
    assert no_fallback._whisper is None


def test_nothing_configured_fails_before_downloading():
    transcriber, store, downloader = _transcriber()

    with pytest.raises(AsrError) as caught:
        _run(transcriber)

    assert caught.value.error_code == "asr_not_configured"
    assert downloader.calls == [] and store.puts == []


# --- admission -------------------------------------------------------------


def test_too_long_is_rejected_before_any_download_or_provider_call(diagnostics):
    funasr, whisper = FakeFunAsr(), FakeWhisper()
    transcriber, store, downloader = _transcriber(funasr=funasr, whisper=whisper)

    with pytest.raises(AsrError) as caught:
        _run(transcriber, duration_sec=14_401)

    assert caught.value.error_code == "asr_too_long"
    assert downloader.calls == [] and store.puts == []
    assert funasr.urls == [] and whisper.calls == []
    (payload,) = _payloads(diagnostics)
    assert payload["error_code"] == "asr_too_long"


@pytest.mark.parametrize("duration", [14_400, 1, None])
def test_duration_at_limit_or_unknown_is_admitted(duration):
    transcriber, _, downloader = _transcriber(funasr=FakeFunAsr())

    _run(transcriber, duration_sec=duration)

    assert len(downloader.calls) == 1


# --- cleanup ---------------------------------------------------------------


def test_temp_object_and_directory_are_removed_when_the_download_fails():
    downloader = FakeDownloader(raises=TransientFetchError("youtube_rate_limited"))
    transcriber, store, _ = _transcriber(funasr=FakeFunAsr(), downloader=downloader)

    with pytest.raises(TransientFetchError):
        _run(transcriber)

    assert store.puts == []
    assert not downloader.dirs[0].exists()


def test_temp_object_and_directory_are_removed_on_unexpected_exception():
    transcriber, store, downloader = _transcriber(
        funasr=FakeFunAsr(raises=RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="boom"):
        _run(transcriber)

    assert store.deleted == [store.puts[0]["key"]]
    assert not downloader.dirs[0].exists()


def test_temp_object_is_removed_on_base_exception():
    transcriber, store, downloader = _transcriber(
        funasr=FakeFunAsr(raises=KeyboardInterrupt())
    )

    with pytest.raises(KeyboardInterrupt):
        _run(transcriber)

    assert store.deleted == [store.puts[0]["key"]]
    assert not downloader.dirs[0].exists()


def test_delete_failure_does_not_mask_the_result():
    store = FakeStore(delete_error=ObjectStoreError("object delete failed"))
    transcriber, _, downloader = _transcriber(store=store, funasr=FakeFunAsr())

    transcript = _run(transcriber)

    assert transcript.provider == "fun_asr"
    assert not downloader.dirs[0].exists()


def test_delete_failure_does_not_mask_the_original_error():
    store = FakeStore(delete_error=OSError("down"))
    transcriber, _, _ = _transcriber(
        store=store, funasr=FakeFunAsr(raises=RuntimeError("original"))
    )

    with pytest.raises(RuntimeError, match="original"):
        _run(transcriber)


def test_unsupported_extension_is_not_staged_and_uses_whisper_only():
    downloader = FakeDownloader(ext="mkv")
    whisper = FakeWhisper()
    transcriber, store, _ = _transcriber(
        funasr=FakeFunAsr(), whisper=whisper, downloader=downloader
    )

    transcript = _run(transcriber)

    assert transcript.provider == "whisper"
    assert store.puts == [] and store.deleted == []


# --- logging ---------------------------------------------------------------


_ALLOWED_LOG_FIELDS = {
    "event", "provider", "model", "audio_seconds", "billed_seconds",
    "estimated_cost", "currency", "duration_ms", "fallback_used", "cue_count",
    "error_code",
}


def _assert_log_is_clean(records, store=None):
    blob = json.dumps(_payloads(records)) + " ".join(r.getMessage() for r in records)
    forbidden = [SPOKEN, SECRET, "asr-tmp/", "/api/v1/asr-audio/", "sig=", "youtube.com",
                 "dk-key", "ok-key", "audio.m4a", "/tmp", "/var/"]
    for item in forbidden:
        assert item not in blob


def test_success_log_has_only_allow_listed_fields_and_no_sensitive_data(diagnostics):
    funasr = FakeFunAsr(_transcript(audio_seconds=120.0, billed_seconds=100.0))
    transcriber, _, _ = _transcriber(funasr=funasr, whisper=FakeWhisper())

    _run(transcriber)

    (payload,) = _payloads(diagnostics)
    assert set(payload) <= _ALLOWED_LOG_FIELDS
    assert payload["provider"] == "fun_asr"
    assert payload["model"] == "fun-asr"
    assert payload["audio_seconds"] == 120.0
    assert payload["billed_seconds"] == 100.0
    assert payload["estimated_cost"] == pytest.approx(0.026)
    assert payload["currency"] == "CNY"
    assert payload["fallback_used"] is False
    assert payload["cue_count"] == 1
    assert isinstance(payload["duration_ms"], int) and payload["duration_ms"] >= 0
    assert "error_code" not in payload
    assert len(_payloads(diagnostics)) == 1
    _assert_log_is_clean(diagnostics)


def test_whisper_cost_estimate_uses_audio_seconds(diagnostics):
    whisper = FakeWhisper(
        _transcript("whisper", "whisper-1", audio_seconds=100.0, billed_seconds=100.0)
    )
    transcriber, _, _ = _transcriber(whisper=whisper)

    _run(transcriber)

    (payload,) = _payloads(diagnostics)
    assert payload["estimated_cost"] == pytest.approx(0.01)
    assert payload["currency"] == "USD"
    assert payload["fallback_used"] is False


def test_failure_logs_are_clean(diagnostics):
    secret_error = AsrProviderUnavailable(f"{SECRET} https://x.example/?sig=abc dk-key")
    transcriber, _, _ = _transcriber(
        funasr=FakeFunAsr(raises=secret_error), whisper=FakeWhisper(raises=secret_error)
    )

    with pytest.raises(AsrError):
        _run(transcriber)

    (payload,) = _payloads(diagnostics)
    assert payload["error_code"] == "asr_provider_failed"
    assert set(payload) <= _ALLOWED_LOG_FIELDS
    _assert_log_is_clean(diagnostics)


def test_transient_and_unexpected_errors_log_stable_codes_only(diagnostics):
    downloader = FakeDownloader(raises=TransientFetchError("youtube_rate_limited"))
    transcriber, _, _ = _transcriber(funasr=FakeFunAsr(), downloader=downloader)
    with pytest.raises(TransientFetchError):
        _run(transcriber)

    leaky = FakeDownloader(raises=TransientFetchError("raw text with SECRET url https://x"))
    transcriber, _, _ = _transcriber(funasr=FakeFunAsr(), downloader=leaky)
    with pytest.raises(TransientFetchError):
        _run(transcriber)

    crash = FakeFunAsr(raises=RuntimeError(f"{SECRET}"))
    transcriber, _, _ = _transcriber(funasr=crash)
    with pytest.raises(RuntimeError):
        _run(transcriber)

    codes = [p["error_code"] for p in _payloads(diagnostics)]
    assert codes == ["youtube_rate_limited", "transient_fetch_failed", "asr_unexpected_error"]
    _assert_log_is_clean(diagnostics)


# --- transcript_to_text_result / language ---------------------------------


def test_text_result_is_a_canonical_capture_transcript():
    transcript = _transcript(
        cues=[AsrCue(0.0, 2.5, "hello world"), AsrCue(2.5, 5.0, "second line")],
        language="en",
    )

    result = transcript_to_text_result(transcript)

    assert result.source == "asr_whisper"
    assert result.format == "capture_v1"
    assert result.lang == "en"
    assert result.cues == [Cue(0.0, 2.5, "hello world"), Cue(2.5, 5.0, "second line")]
    payload = json.loads(result.raw_body)
    assert payload["schema_version"] == "capture-transcript.v1"
    assert [c["text"] for c in payload["cues"]] == ["hello world", "second line"]
    parsed = parse_canonical_transcript(result.raw_body, source="auto_caption", language="en")
    assert parsed.cues == result.cues


def test_text_result_with_no_speech_raises():
    with pytest.raises(AsrError) as caught:
        transcript_to_text_result(_transcript(cues=[AsrCue(0, 1, " ")]))

    assert caught.value.error_code == "asr_no_speech"


def test_text_result_over_canonical_limits_is_ingest_too_large():
    cues = [AsrCue(float(i), float(i) + 0.5, "x") for i in range(50_001)]

    with pytest.raises(IngestLimitExceeded):
        transcript_to_text_result(_transcript(cues=cues, language="en"))


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["今天我们来讨论一下这个问题"], "zh"),
        (["Today we talk about the problem"], "en"),
        (["这是一个 test of mixed 语言 speech 中文很多很多"], "zh"),  # >= 30% CJK
        (["machine learning algorithm 今天"], "en"),  # < 30% CJK
        (["12345 !!!"], "en"),
        ([], "en"),
    ],
)
def test_language_inference(texts, expected):
    cues = [AsrCue(float(i), float(i) + 1, text) for i, text in enumerate(texts)]

    assert _infer_language(cues) == expected


def test_provider_language_wins_over_inference():
    transcript = _transcript(cues=[AsrCue(0, 1, "今天我们来讨论")], language="en")

    assert transcript_to_text_result(transcript).lang == "en"
    inferred = transcript_to_text_result(_transcript(cues=[AsrCue(0, 1, "今天我们来讨论")]))
    assert inferred.lang == "zh"
