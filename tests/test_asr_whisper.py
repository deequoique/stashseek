import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.asr import whisper as whisper_module
from app.asr.types import AsrProtocolError, AsrProviderUnavailable
from app.asr.whisper import DIRECT_UPLOAD_MAX_BYTES, WhisperClient

API_KEY = "sk-openai-test-key"


def _segments(*items, language="english", duration=10.0, text="spoken"):
    return {
        "language": language,
        "duration": duration,
        "text": text,
        "segments": [
            {"id": index, "start": start, "end": end, "text": words}
            for index, (start, end, words) in enumerate(items)
        ],
    }


class Provider:
    def __init__(self, responses=None):
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.responses = list(responses or [_segments((0.0, 1.0, "hi"))])

    def handler(self, request):
        request.read()
        self.requests.append(request)
        self.bodies.append(request.content)
        item = self.responses[min(len(self.requests) - 1, len(self.responses) - 1)]
        if isinstance(item, httpx.Response):
            return item
        if callable(item):
            return item(request)
        return httpx.Response(200, json=item)


def _make(provider, *, runner=None, ffmpeg_path="/usr/bin/ffmpeg", sleeps=None):
    return WhisperClient(
        api_key=API_KEY,
        client=httpx.Client(transport=httpx.MockTransport(provider.handler)),
        ffmpeg_path=ffmpeg_path,
        runner=runner,
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
    )


def _audio(tmp_path, name="audio.m4a", content=b"audio-bytes"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_direct_upload_request_format_and_cues(tmp_path):
    provider = Provider(
        [_segments((0.0, 2.5, " hello "), (2.5, 4.0, "world"), (4.0, 5.0, "  "), duration=5.0)]
    )
    client = _make(provider)

    transcript = client.transcribe_file(_audio(tmp_path), work_dir=tmp_path)

    request = provider.requests[0]
    assert (request.method, str(request.url)) == (
        "POST",
        "https://api.openai.com/v1/audio/transcriptions",
    )
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["content-type"].startswith("multipart/form-data")
    body = provider.bodies[0]
    for expected in (
        b'name="model"\r\n\r\nwhisper-1',
        b'name="response_format"\r\n\r\nverbose_json',
        b'name="timestamp_granularities[]"\r\n\r\nsegment',
        b'name="file"; filename="audio.m4a"',
        b"audio-bytes",
    ):
        assert expected in body
    assert b"word" not in body.split(b'name="file"')[0]
    assert [(c.start_sec, c.end_sec, c.text) for c in transcript.cues] == [
        (0.0, 2.5, "hello"),
        (2.5, 4.0, "world"),
    ]
    assert transcript.provider == "whisper"
    assert transcript.model == "whisper-1"
    assert transcript.language == "en"
    assert transcript.audio_seconds == 5.0
    assert transcript.billed_seconds == 5.0


@pytest.mark.parametrize(
    ("language", "expected"),
    [("chinese", "zh"), ("English", "en"), ("japanese", None), (None, None)],
)
def test_language_mapping(tmp_path, language, expected):
    provider = Provider([_segments((0.0, 1.0, "x"), language=language)])

    transcript = _make(provider).transcribe_file(_audio(tmp_path), work_dir=tmp_path)

    assert transcript.language == expected


def test_opus_file_is_uploaded_as_ogg(tmp_path):
    provider = Provider()

    _make(provider).transcribe_file(_audio(tmp_path, "audio.opus"), work_dir=tmp_path)

    assert b'filename="audio.ogg"' in provider.bodies[0]


class FfmpegRunner:
    """Fake ffmpeg that writes chunk files and a segment list like the real one."""

    def __init__(self, chunks, *, returncode=0, raises=None, listing=None):
        self.chunks = chunks
        self.returncode = returncode
        self.raises = raises
        self.listing = listing
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if self.raises:
            raise self.raises
        if self.returncode:
            return SimpleNamespace(returncode=self.returncode, stdout=b"", stderr=b"boom")
        listing_path = args[args.index("-segment_list") + 1]
        pattern = args[-1]
        rows = []
        for index, (start, end, payload) in enumerate(self.chunks):
            name = f"chunk-{index:05d}.mp3"
            (Path(pattern).parent / name).write_bytes(payload)
            rows.append(f"{name},{start:.6f},{end:.6f}")
        with open(listing_path, "w", encoding="utf-8") as handle:
            handle.write(self.listing if self.listing is not None else "\n".join(rows) + "\n")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")


def _large_file(tmp_path, name="audio.webm", extra=1):
    path = tmp_path / name
    with open(path, "wb") as handle:
        handle.truncate(DIRECT_UPLOAD_MAX_BYTES + extra)  # sparse: no real 24 MB
    return path


def test_large_audio_is_segmented_and_timestamps_are_offset(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    runner = FfmpegRunner(
        [(0.0, 600.0, b"CHUNK-A"), (600.0, 1200.0, b"CHUNK-B"), (1200.0, 1500.5, b"CHUNK-C")]
    )

    def by_chunk(request):
        if b"CHUNK-A" in request.content:
            return httpx.Response(200, json=_segments((0.0, 3.0, "a1"), (590.0, 599.0, "a2"), duration=600.0, language="chinese"))
        if b"CHUNK-B" in request.content:
            return httpx.Response(200, json=_segments((1.5, 4.0, "b1"), duration=600.0, language="english"))
        return httpx.Response(200, json=_segments((10.25, 12.0, "c1"), duration=300.5))

    provider = Provider([by_chunk, by_chunk, by_chunk])
    client = _make(provider, runner=runner)

    transcript = client.transcribe_file(_large_file(tmp_path), work_dir=work)

    assert [(c.start_sec, c.end_sec, c.text) for c in transcript.cues] == [
        (0.0, 3.0, "a1"),
        (590.0, 599.0, "a2"),
        (601.5, 604.0, "b1"),
        (1210.25, 1212.0, "c1"),
    ]
    assert len(provider.requests) == 3
    assert transcript.language == "zh"  # first chunk that reports one
    assert transcript.audio_seconds == pytest.approx(1500.5)
    assert all(b'filename="audio.mp3"' in body for body in provider.bodies)

    args, kwargs = runner.calls[0]
    assert args[0] == "/usr/bin/ffmpeg"
    pairs = dict(zip(args, args[1:]))
    assert pairs["-segment_time"] == "600"
    assert pairs["-reset_timestamps"] == "1"
    assert pairs["-segment_list_type"] == "csv"
    assert pairs["-segment_list"] == str(work / "segments.csv")
    assert pairs["-ac"] == "1" and pairs["-ar"] == "16000" and pairs["-b:a"] == "48k"
    assert "-vn" in args and args[-1] == str(work / "chunk-%05d.mp3")
    # ffmpeg never sees provider credentials.
    assert API_KEY not in " ".join(args)
    assert set(kwargs["env"]) <= {"PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
    assert kwargs["timeout"] > 0


def test_large_audio_without_ffmpeg_is_unavailable_and_makes_no_request(tmp_path, monkeypatch):
    monkeypatch.setattr(whisper_module.shutil, "which", lambda name: None)
    provider = Provider()
    client = _make(provider, ffmpeg_path=None)

    with pytest.raises(AsrProviderUnavailable, match="ffmpeg_missing"):
        client.transcribe_file(_large_file(tmp_path), work_dir=tmp_path)

    assert provider.requests == []


def test_aac_needs_transcoding_even_when_small(tmp_path):
    runner = FfmpegRunner([(0.0, 5.0, b"CHUNK-A")])
    provider = Provider()

    _make(provider, runner=runner).transcribe_file(
        _audio(tmp_path, "audio.aac"), work_dir=tmp_path
    )

    assert len(runner.calls) == 1
    assert b'filename="audio.mp3"' in provider.bodies[0]


@pytest.mark.parametrize(
    "runner",
    [
        FfmpegRunner([], returncode=1),
        FfmpegRunner([], raises=subprocess.TimeoutExpired("ffmpeg", 1)),
        FfmpegRunner([], raises=OSError("no exec")),
        FfmpegRunner([(0.0, 1.0, b"x")], listing=""),
        FfmpegRunner([(0.0, 1.0, b"x")], listing="../../evil.mp3,0.0,1.0\n"),
        FfmpegRunner([(0.0, 1.0, b"x")], listing="chunk-00000.mp3,not-a-number,1.0\n"),
        FfmpegRunner([(0.0, 1.0, b"x")], listing="chunk-00000.mp3,nan,1.0\n"),
        FfmpegRunner([(0.0, 1.0, b"x")], listing="chunk-00009.mp3,0.0,1.0\n"),
    ],
)
def test_ffmpeg_failures_are_unavailable(tmp_path, runner):
    provider = Provider()
    client = _make(provider, runner=runner)

    with pytest.raises(AsrProviderUnavailable):
        client.transcribe_file(_large_file(tmp_path), work_dir=tmp_path)

    assert provider.requests == []


def test_oversized_chunk_is_unavailable(tmp_path):
    runner = FfmpegRunner([(0.0, 600.0, b"x")])

    def runner_with_big_chunk(args, **kwargs):
        result = runner(args, **kwargs)
        with open(tmp_path / "chunk-00000.mp3", "wb") as handle:
            handle.truncate(DIRECT_UPLOAD_MAX_BYTES + 1)
        return result

    provider = Provider()
    client = _make(provider, runner=runner_with_big_chunk)

    with pytest.raises(AsrProviderUnavailable, match="audio_too_large"):
        client.transcribe_file(_large_file(tmp_path), work_dir=tmp_path)

    assert provider.requests == []


@pytest.mark.parametrize("status", [401, 403, 400, 404])
def test_non_retryable_http_errors_are_unavailable(tmp_path, status):
    provider = Provider([httpx.Response(status, json={"error": {"message": API_KEY}})])
    sleeps: list[float] = []

    with pytest.raises(AsrProviderUnavailable) as caught:
        _make(provider, sleeps=sleeps).transcribe_file(_audio(tmp_path), work_dir=tmp_path)

    assert len(provider.requests) == 1
    assert sleeps == []
    assert API_KEY not in str(caught.value)


def test_429_and_5xx_are_retried_then_succeed(tmp_path):
    provider = Provider(
        [httpx.Response(429, json={}), httpx.Response(502, json={}), _segments((0.0, 1.0, "ok"))]
    )
    sleeps: list[float] = []

    transcript = _make(provider, sleeps=sleeps).transcribe_file(
        _audio(tmp_path), work_dir=tmp_path
    )

    assert [c.text for c in transcript.cues] == ["ok"]
    assert len(provider.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_retries_are_bounded_to_three_attempts(tmp_path):
    provider = Provider([httpx.Response(503, json={})])
    sleeps: list[float] = []

    with pytest.raises(AsrProviderUnavailable):
        _make(provider, sleeps=sleeps).transcribe_file(_audio(tmp_path), work_dir=tmp_path)

    assert len(provider.requests) == 3
    assert sleeps == [1.0, 2.0]


def test_empty_valid_transcript_returns_no_cues(tmp_path):
    provider = Provider([_segments(text="", duration=3.0)])

    transcript = _make(provider).transcribe_file(_audio(tmp_path), work_dir=tmp_path)

    assert transcript.cues == ()
    assert transcript.audio_seconds == 3.0


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "no segments key"},
        {"segments": "nope"},
        {"segments": [], "text": "text without timestamps"},
        {"segments": ["not-an-object"]},
        {"segments": [{"start": 5.0, "end": 1.0, "text": "backwards"}]},
        {"segments": [{"start": -1.0, "end": 1.0, "text": "negative"}]},
        {"segments": [{"start": "0", "end": 1.0, "text": "stringly"}]},
        [],
    ],
)
def test_protocol_errors(tmp_path, payload):
    provider = Provider([payload])

    with pytest.raises(AsrProtocolError):
        _make(provider).transcribe_file(_audio(tmp_path), work_dir=tmp_path)


def test_non_json_and_oversized_responses_are_protocol_errors(tmp_path):
    for response in (
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, content=b"{}", headers={"content-length": str(30 * 1024 * 1024)}),
    ):
        with pytest.raises(AsrProtocolError):
            _make(Provider([response])).transcribe_file(_audio(tmp_path), work_dir=tmp_path)


def test_missing_or_empty_audio_file_is_unavailable(tmp_path):
    client = _make(Provider())

    with pytest.raises(AsrProviderUnavailable):
        client.transcribe_file(tmp_path / "missing.m4a", work_dir=tmp_path)
    with pytest.raises(AsrProviderUnavailable):
        client.transcribe_file(_audio(tmp_path, "empty.m4a", b""), work_dir=tmp_path)


def test_constructor_requires_api_key():
    with pytest.raises(ValueError):
        WhisperClient(api_key="")
