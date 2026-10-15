import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.asr.audio import AUDIO_FORMAT_SELECTOR, DownloadedAudio, download_audio
from app.asr.types import AsrError
from app.connectors.base import TransientFetchError

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&utm_source=share"
YOUTUBE_CANONICAL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
BILIBILI_URL = "https://www.bilibili.com/video/BV1GJ411x7h7?spm_id_from=333"
BILIBILI_CANONICAL = "https://www.bilibili.com/video/BV1GJ411x7h7"
PROXY = "http://127.0.0.1:18080"


def _settings(**overrides):
    values = {
        "asr_max_audio_bytes": 1_000,
        "asr_download_timeout_seconds": 120,
        "youtube_proxy_url": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class Runner:
    """Fake yt-dlp: optionally writes an output file named by the ``-o`` template."""

    def __init__(self, *, returncode=0, stdout="", stderr="", filename="audio.m4a",
                 payload=b"x" * 100, raises=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.filename = filename
        self.payload = payload
        self.raises = raises
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if self.raises is not None:
            raise self.raises
        if self.filename is not None:
            template = args[args.index("-o") + 1]
            directory = template.rsplit("/", 1)[0].replace("%%", "%")
            (Path(directory) / self.filename).write_bytes(self.payload)
        return SimpleNamespace(
            returncode=self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def test_youtube_arguments_and_result(tmp_path):
    runner = Runner()

    audio = download_audio(
        platform="youtube", url=YOUTUBE_URL, dest_dir=tmp_path, settings=_settings(), runner=runner
    )

    assert audio == DownloadedAudio(tmp_path / "audio.m4a", "m4a", 100, "audio/mp4")
    args, kwargs = runner.calls[0]
    assert args[:3] == [sys.executable, "-m", "yt_dlp"]
    pairs = dict(zip(args, args[1:]))
    assert "--ignore-config" in args and "--no-cache-dir" in args and "--no-playlist" in args
    assert pairs["-f"] == AUDIO_FORMAT_SELECTOR == "bestaudio[ext=m4a]/bestaudio"
    assert pairs["--max-filesize"] == "1000"
    assert pairs["--socket-timeout"] == "30.0"
    assert pairs["-o"] == f"{tmp_path}/audio.%(ext)s"
    assert pairs["--extractor-args"] == "youtube:player_client=android_vr"
    # Only the canonical URL (tracking query removed) is passed, and last.
    assert args[-1] == YOUTUBE_CANONICAL
    assert not any(arg.startswith("--cookies") for arg in args)
    assert kwargs["timeout"] == 120.0
    assert kwargs["capture_output"] is True and kwargs["check"] is False
    # Direct: the worker environment is passed on, minus secrets.
    assert "HTTPS_PROXY" not in kwargs["env"]


def test_socket_timeout_never_exceeds_the_total_budget(tmp_path):
    runner = Runner()

    download_audio(
        platform="youtube",
        url=YOUTUBE_URL,
        dest_dir=tmp_path,
        settings=_settings(asr_download_timeout_seconds=10),
        runner=runner,
    )

    args, _ = runner.calls[0]
    assert dict(zip(args, args[1:]))["--socket-timeout"] == "10.0"


def test_bilibili_uses_canonical_url_no_youtube_args_and_no_proxy(tmp_path):
    runner = Runner(filename="audio.webm")

    audio = download_audio(
        platform="bilibili",
        url=BILIBILI_URL,
        dest_dir=tmp_path,
        settings=_settings(youtube_proxy_url=PROXY),
        runner=runner,
    )

    args, kwargs = runner.calls[0]
    assert args[-1] == BILIBILI_CANONICAL
    assert "--extractor-args" not in args
    # A configured YouTube proxy never applies here.
    assert not any("proxy" in name.lower() for name in kwargs["env"])
    assert audio.content_type == "audio/webm"


def test_proxy_environment_is_child_only_and_youtube_only(tmp_path, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/ca/bundle.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/ca/bundle.pem")
    monkeypatch.setenv("ALL_PROXY", "socks5://ambient:1080")
    monkeypatch.setenv("NO_PROXY", ".youtube.com")
    before = dict(os.environ)
    runner = Runner()

    download_audio(
        platform="youtube",
        url=YOUTUBE_URL,
        dest_dir=tmp_path,
        settings=_settings(youtube_proxy_url=PROXY),
        runner=runner,
    )

    env = runner.calls[0][1]["env"]
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert env[name] == PROXY
    assert env["NO_PROXY"] == env["no_proxy"] == "127.0.0.1,localhost,::1"
    assert "ALL_PROXY" not in env and "all_proxy" not in env
    assert env["SSL_CERT_FILE"] == env["REQUESTS_CA_BUNDLE"] == "/ca/bundle.pem"
    assert dict(os.environ) == before  # parent environment is untouched
    assert "HTTPS_PROXY" not in os.environ


SECRET_ENV = {
    "DASHSCOPE_API_KEY": "dashscope-secret-value",
    "OPENAI_API_KEY": "openai-secret-value",
    "ASR_AUDIO_URL_SECRET": "signing-secret-value",
    "DATABASE_URL": "postgresql://user:db-password@host/db",
    "REDIS_URL": "redis://:redis-password@host/0",
    "MINIO_ROOT_PASSWORD": "minio-password-value",
    "TELEGRAM_BOT_TOKEN": "bot-token-value",
}


@pytest.mark.parametrize(
    ("platform", "url", "proxy"),
    [
        ("youtube", YOUTUBE_URL, None),
        ("youtube", YOUTUBE_URL, PROXY),
        ("bilibili", BILIBILI_URL, None),
    ],
)
def test_yt_dlp_child_environment_never_contains_provider_secrets(
    tmp_path, monkeypatch, platform, url, proxy
):
    for name, value in SECRET_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SSL_CERT_FILE", "/ca/bundle.pem")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/ca/bundle.pem")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))
    before = dict(os.environ)
    runner = Runner()

    download_audio(
        platform=platform,
        url=url,
        dest_dir=tmp_path,
        settings=_settings(youtube_proxy_url=proxy),
        runner=runner,
    )

    env = runner.calls[0][1]["env"]
    assert not set(SECRET_ENV) & set(env)
    assert not any(value in env.values() for value in SECRET_ENV.values())
    # The runtime and trusted-CA variables the child needs are kept.
    assert env["SSL_CERT_FILE"] == env["REQUESTS_CA_BUNDLE"] == "/ca/bundle.pem"
    assert env["PATH"] == os.environ["PATH"]
    assert dict(os.environ) == before  # the worker environment is untouched


@pytest.mark.parametrize(
    ("platform", "url"),
    [
        ("youtube", "https://example.com/nothing"),
        ("bilibili", "https://b23.tv/short"),
        ("bilibili", "https://www.bilibili.com/read/cv123"),
        ("vimeo", "https://vimeo.com/1"),
        ("ntu_kaltura", YOUTUBE_URL),
    ],
)
def test_unsupported_urls_and_platforms_never_reach_the_child(tmp_path, platform, url):
    runner = Runner()

    with pytest.raises(AsrError) as caught:
        download_audio(platform=platform, url=url, dest_dir=tmp_path, settings=_settings(), runner=runner)

    assert caught.value.error_code == "asr_audio_unavailable"
    assert runner.calls == []


def test_foreign_host_with_embedded_id_is_rewritten_to_the_canonical_host(tmp_path):
    runner = Runner()

    download_audio(
        platform="youtube",
        url="https://evil.example/?next=youtu.be/dQw4w9WgXcQ",
        dest_dir=tmp_path,
        settings=_settings(),
        runner=runner,
    )

    assert runner.calls[0][0][-1] == YOUTUBE_CANONICAL


def test_dest_dir_percent_signs_are_escaped_for_the_output_template(tmp_path):
    directory = tmp_path / "100%done"
    directory.mkdir()
    runner = Runner()

    audio = download_audio(
        platform="youtube", url=YOUTUBE_URL, dest_dir=directory, settings=_settings(), runner=runner
    )

    template = runner.calls[0][0][runner.calls[0][0].index("-o") + 1]
    assert template == f"{directory}/audio.%(ext)s".replace("%done", "%%done")
    assert audio.path == directory / "audio.m4a"


@pytest.mark.parametrize(
    ("stderr", "proxy", "code"),
    [
        ("ERROR: HTTP Error 429: Too Many Requests", None, "youtube_rate_limited"),
        ("ERROR: unable to connect to proxy http://127.0.0.1:18080", PROXY, "youtube_proxy_unavailable"),
        ("ERROR: HTTP Error 407: Proxy Authentication Required", PROXY, "youtube_proxy_authentication_failed"),
        ("ERROR: proxy timed out", PROXY, "youtube_proxy_timeout"),
    ],
)
def test_youtube_transient_failures_are_classified_without_stderr(tmp_path, stderr, proxy, code):
    secret = "https://signed.example/videoplayback?sig=SUPER-SECRET"
    runner = Runner(returncode=1, stderr=f"{stderr} {secret}", filename=None)

    with pytest.raises(TransientFetchError) as caught:
        download_audio(
            platform="youtube",
            url=YOUTUBE_URL,
            dest_dir=tmp_path,
            settings=_settings(youtube_proxy_url=proxy),
            runner=runner,
        )

    assert str(caught.value) == code
    assert "SUPER-SECRET" not in str(caught.value)
    assert len(runner.calls) == 1  # no retry, in particular none without the proxy


@pytest.mark.parametrize(
    "stderr",
    [
        "ERROR: Video unavailable",
        "ERROR: Sign in to confirm you're not a bot",
        "ERROR: unable to connect to proxy",  # proxy classification needs a proxy
    ],
)
def test_other_youtube_failures_are_terminal_audio_unavailable(tmp_path, stderr):
    runner = Runner(returncode=1, stderr=stderr + " https://x.example/?sig=SECRET", filename=None)

    with pytest.raises(AsrError) as caught:
        download_audio(platform="youtube", url=YOUTUBE_URL, dest_dir=tmp_path, settings=_settings(), runner=runner)

    assert caught.value.error_code == "asr_audio_unavailable"
    assert "SECRET" not in str(caught.value)


def test_bilibili_failure_classification(tmp_path):
    limited = Runner(returncode=1, stderr="ERROR: -352 risk control", filename=None)
    with pytest.raises(TransientFetchError, match="^bilibili_rate_limited$"):
        download_audio(platform="bilibili", url=BILIBILI_URL, dest_dir=tmp_path, settings=_settings(), runner=limited)

    other = Runner(returncode=1, stderr="ERROR: HTTP Error 412", filename=None)
    with pytest.raises(AsrError) as caught:
        download_audio(platform="bilibili", url=BILIBILI_URL, dest_dir=tmp_path, settings=_settings(), runner=other)
    assert caught.value.error_code == "asr_audio_unavailable"


@pytest.mark.parametrize(
    ("platform", "url", "proxy", "code"),
    [
        ("youtube", YOUTUBE_URL, None, "youtube_fetch_timeout"),
        ("youtube", YOUTUBE_URL, PROXY, "youtube_proxy_timeout"),
        ("bilibili", BILIBILI_URL, PROXY, "bilibili_fetch_timeout"),
    ],
)
def test_timeouts_are_transient_with_stable_codes(tmp_path, platform, url, proxy, code):
    runner = Runner(raises=subprocess.TimeoutExpired("yt-dlp", 120), filename=None)

    with pytest.raises(TransientFetchError) as caught:
        download_audio(
            platform=platform,
            url=url,
            dest_dir=tmp_path,
            settings=_settings(youtube_proxy_url=proxy),
            runner=runner,
        )

    assert str(caught.value) == code


def test_spawn_failure_is_terminal_audio_unavailable(tmp_path):
    runner = Runner(raises=OSError("exec failed"), filename=None)

    with pytest.raises(AsrError) as caught:
        download_audio(platform="youtube", url=YOUTUBE_URL, dest_dir=tmp_path, settings=_settings(), runner=runner)

    assert caught.value.error_code == "asr_audio_unavailable"


@pytest.mark.parametrize("returncode", [0, 1])
def test_ytdlp_max_filesize_abort_is_audio_too_large(tmp_path, returncode):
    runner = Runner(
        returncode=returncode,
        stdout="[download] File is larger than max-filesize (5000 bytes > 1000 bytes). Aborting.",
        filename=None,
    )

    with pytest.raises(AsrError) as caught:
        download_audio(platform="youtube", url=YOUTUBE_URL, dest_dir=tmp_path, settings=_settings(), runner=runner)

    assert caught.value.error_code == "asr_audio_too_large"


def test_downloaded_file_over_the_limit_is_audio_too_large(tmp_path):
    runner = Runner(payload=b"x" * 1_001)

    with pytest.raises(AsrError) as caught:
        download_audio(platform="youtube", url=YOUTUBE_URL, dest_dir=tmp_path, settings=_settings(), runner=runner)

    assert caught.value.error_code == "asr_audio_too_large"


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        (None, b""),  # exit 0 but nothing written
        ("audio.m4a", b""),  # empty file
        ("audio.mkv", b"x" * 10),  # container the signed route does not serve
        ("audio.m4a.part", b"x" * 10),  # incomplete download only
        ("audio.f140.m4a.ytdl", b"x" * 10),
    ],
)
def test_missing_empty_or_unsupported_output_is_audio_unavailable(tmp_path, filename, payload):
    runner = Runner(filename=filename, payload=payload)

    with pytest.raises(AsrError) as caught:
        download_audio(platform="youtube", url=YOUTUBE_URL, dest_dir=tmp_path, settings=_settings(), runner=runner)

    assert caught.value.error_code == "asr_audio_unavailable"


def test_part_files_are_ignored_when_a_finished_file_exists(tmp_path):
    (tmp_path / "audio.m4a.part").write_bytes(b"x" * 500)
    runner = Runner(filename="audio.webm", payload=b"y" * 50)

    audio = download_audio(platform="youtube", url=YOUTUBE_URL, dest_dir=tmp_path, settings=_settings(), runner=runner)

    assert audio.path.name == "audio.webm"
    assert audio.size_bytes == 50
