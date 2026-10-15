from types import SimpleNamespace

import pytest

from app.asr.connector import AsrFallbackConnector
from app.asr.transcriber import AsrTranscriber
from app.asr.types import AsrCue, AsrError, AsrTranscript
from app.browser_capture import parse_canonical_transcript
from app.connectors.base import (
    Cue,
    ItemMeta,
    NeedsASR,
    NeedsExtension,
    TextResult,
    TransientFetchError,
)

CANONICAL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _meta(**overrides):
    values = {"platform_id": "dQw4w9WgXcQ", "url": CANONICAL, "duration_sec": 321,
              "title": "A talk"}
    values.update(overrides)
    return ItemMeta(**values)


class Inner:
    platform = "youtube"

    def __init__(self, *, text=None, meta=None):
        self.text = text if text is not None else NeedsASR()
        self.meta = meta if meta is not None else _meta()
        self.calls: list[tuple[str, str]] = []

    def match(self, url):
        self.calls.append(("match", url))
        return "dQw4w9WgXcQ" if "youtube.com" in url else None

    def fetch_meta(self, platform_id):
        self.calls.append(("fetch_meta", platform_id))
        return self.meta

    def fetch_text(self, platform_id):
        self.calls.append(("fetch_text", platform_id))
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


class FakeTranscriber:
    def __init__(self, transcript=None, error=None):
        self.transcript = transcript or AsrTranscript(
            cues=(AsrCue(0.0, 1.5, "hello there"), AsrCue(1.5, 3.0, "general kenobi")),
            language="en",
            provider="fun_asr",
            model="fun-asr",
            audio_seconds=3.0,
            billed_seconds=3.0,
        )
        self.error = error
        self.calls: list[dict] = []

    def transcribe(self, *, platform, url, duration_sec):
        self.calls.append({"platform": platform, "url": url, "duration_sec": duration_sec})
        if self.error:
            raise self.error
        return self.transcript


def test_needs_asr_is_transcribed_using_cached_meta():
    inner, transcriber = Inner(), FakeTranscriber()
    connector = AsrFallbackConnector(inner, transcriber, platform="youtube")

    meta = connector.fetch_meta("dQw4w9WgXcQ")
    result = connector.fetch_text("dQw4w9WgXcQ")

    assert meta is inner.meta
    assert transcriber.calls == [
        {"platform": "youtube", "url": CANONICAL, "duration_sec": 321}
    ]
    assert isinstance(result, TextResult)
    assert result.source == "asr_whisper"
    assert result.format == "capture_v1"
    assert result.lang == "en"
    assert result.cues == [Cue(0.0, 1.5, "hello there"), Cue(1.5, 3.0, "general kenobi")]
    parsed = parse_canonical_transcript(result.raw_body, source="auto_caption", language="en")
    assert parsed.cues == result.cues
    # The inner connector is not asked for metadata twice.
    assert [call[0] for call in inner.calls].count("fetch_meta") == 1


def test_fetch_text_without_prior_meta_fetches_it_once():
    inner, transcriber = Inner(), FakeTranscriber()
    connector = AsrFallbackConnector(inner, transcriber, platform="youtube")

    connector.fetch_text("dQw4w9WgXcQ")
    connector.fetch_text("dQw4w9WgXcQ")

    assert [call[0] for call in inner.calls].count("fetch_meta") == 1
    assert transcriber.calls[0]["url"] == CANONICAL


def test_unknown_duration_is_passed_through_as_none():
    inner = Inner(meta=_meta(duration_sec=None))
    transcriber = FakeTranscriber()

    AsrFallbackConnector(inner, transcriber, platform="youtube").fetch_text("dQw4w9WgXcQ")

    assert transcriber.calls[0]["duration_sec"] is None


def test_text_result_from_the_inner_connector_passes_through_untouched():
    existing = TextResult(b"{}", [Cue(0.0, 1.0, "caption")], "official_cc", "en")
    inner, transcriber = Inner(text=existing), FakeTranscriber()
    connector = AsrFallbackConnector(inner, transcriber, platform="youtube")

    assert connector.fetch_text("dQw4w9WgXcQ") is existing
    assert transcriber.calls == []


def test_needs_extension_passes_through_untouched():
    needs = NeedsExtension("login required")
    inner, transcriber = Inner(text=needs), FakeTranscriber()
    connector = AsrFallbackConnector(inner, transcriber, platform="youtube")

    assert connector.fetch_text("dQw4w9WgXcQ") is needs
    assert transcriber.calls == []


def test_transcriber_errors_propagate_unchanged():
    for error in (AsrError("asr_too_long"), TransientFetchError("youtube_rate_limited")):
        connector = AsrFallbackConnector(
            Inner(), FakeTranscriber(error=error), platform="youtube"
        )
        with pytest.raises(type(error)) as caught:
            connector.fetch_text("dQw4w9WgXcQ")
        assert caught.value is error


def test_inner_errors_propagate_and_no_transcription_starts():
    inner = Inner(text=TransientFetchError("youtube_fetch_failed"))
    transcriber = FakeTranscriber()
    connector = AsrFallbackConnector(inner, transcriber, platform="youtube")

    with pytest.raises(TransientFetchError):
        connector.fetch_text("dQw4w9WgXcQ")

    assert transcriber.calls == []


def test_missing_url_is_audio_unavailable():
    inner = Inner(meta=_meta(url=""))
    transcriber = FakeTranscriber()
    connector = AsrFallbackConnector(inner, transcriber, platform="youtube")

    with pytest.raises(AsrError) as caught:
        connector.fetch_text("dQw4w9WgXcQ")

    assert caught.value.error_code == "asr_audio_unavailable"
    assert transcriber.calls == []


def test_platform_and_match_are_delegated():
    inner = Inner()
    connector = AsrFallbackConnector(inner, FakeTranscriber(), platform="youtube")

    assert connector.platform == "youtube"
    assert connector.match(CANONICAL) == "dQw4w9WgXcQ"
    assert connector.match("https://example.com") is None


def test_no_speech_transcript_surfaces_as_asr_no_speech():
    empty = AsrTranscript((AsrCue(0, 1, "   "),), None, "fun_asr", "fun-asr", None, None)
    connector = AsrFallbackConnector(
        Inner(), FakeTranscriber(transcript=empty), platform="youtube"
    )

    with pytest.raises(AsrError) as caught:
        connector.fetch_text("dQw4w9WgXcQ")

    assert caught.value.error_code == "asr_no_speech"


def test_end_to_end_with_a_real_transcriber_and_fake_providers(tmp_path):
    from pathlib import Path

    from app.asr.audio import DownloadedAudio

    class Store:
        def __init__(self):
            self.deleted = []

        def put_file(self, key, path, content_type):
            return 1

        def delete_object(self, key, **kwargs):
            self.deleted.append(key)

    class FunAsr:
        def transcribe_url(self, audio_url):
            return AsrTranscript(
                cues=(AsrCue(0.0, 2.0, "你好，世界"),),
                language=None,
                provider="fun_asr",
                model="fun-asr",
                audio_seconds=2.0,
                billed_seconds=2.0,
            )

    def downloader(*, platform, url, dest_dir, settings, runner=None):
        path = Path(dest_dir) / "audio.m4a"
        path.write_bytes(b"a")
        return DownloadedAudio(path, "m4a", 1, "audio/mp4")

    settings = SimpleNamespace(
        asr_max_duration_seconds=14_400,
        asr_public_base_url="https://notebookai.example.com",
        asr_audio_url_secret="s" * 40,
        asr_audio_url_ttl_seconds=7_200,
        fun_asr_model="fun-asr",
        dashscope_api_key=None,
        openai_api_key=None,
        asr_whisper_fallback_enabled=False,
    )
    store = Store()
    transcriber = AsrTranscriber(
        settings=settings, object_store=store, funasr=FunAsr(), downloader=downloader
    )
    connector = AsrFallbackConnector(Inner(), transcriber, platform="youtube")

    connector.fetch_meta("dQw4w9WgXcQ")
    result = connector.fetch_text("dQw4w9WgXcQ")

    assert result.lang == "zh"  # inferred from CJK ratio
    assert result.source == "asr_whisper" and result.format == "capture_v1"
    assert result.cues[0].text == "你好,世界"  # NFKC folds the full-width comma
    assert len(store.deleted) == 1
