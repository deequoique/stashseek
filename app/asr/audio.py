"""Audio-only acquisition of public YouTube/Bilibili videos through yt-dlp.

The child process writes into a caller-owned private directory.  YouTube
reuses the connector's child-only proxy/CA environment and stable error
classification; Bilibili never uses a proxy.  No cookies, tokens or accounts
are supplied, and raw yt-dlp output never enters an exception or a log.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.asr.signing import ALLOWED_AUDIO_EXTS
from app.asr.types import AsrError
from app.connectors.base import TransientFetchError
from app.connectors.bilibili import BilibiliConnector
from app.connectors.youtube import YouTubeConnector

AUDIO_FORMAT_SELECTOR = "bestaudio[ext=m4a]/bestaudio"
_MAX_SOCKET_TIMEOUT_SECONDS = 30.0
_TOO_LARGE_MARKER = "larger than max-filesize"
_INCOMPLETE_SUFFIXES = frozenset({"part", "ytdl", "temp"})
_CONTENT_TYPES = {
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "webm": "audio/webm",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "aac": "audio/aac",
    "flac": "audio/flac",
}
# Variables the yt-dlp child must never see: provider keys, signing secrets and
# connection strings.  Names follow the launcher's redaction rule so a new
# ``*_API_KEY`` / ``*_SECRET`` setting is excluded without touching this file.
_SECRET_NAME_MARKERS = ("API_KEY", "SECRET", "PASSWORD", "TOKEN", "CREDENTIAL")
_SECRET_URL_NAMES = frozenset({"DATABASE_URL", "MIGRATION_DATABASE_URL", "REDIS_URL"})
# yt-dlp failures the connectors already treat as retryable for one item.
_TRANSIENT_YOUTUBE_CODES = frozenset(
    {
        "youtube_rate_limited",
        "youtube_proxy_unavailable",
        "youtube_proxy_timeout",
        "youtube_proxy_authentication_failed",
    }
)


@dataclass(frozen=True)
class DownloadedAudio:
    path: Path
    ext: str
    size_bytes: int
    content_type: str


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return upper in _SECRET_URL_NAMES or any(
        marker in upper for marker in _SECRET_NAME_MARKERS
    )


def _child_environment_without_secrets(
    base: Mapping[str, str] | None,
) -> dict[str, str]:
    """Copy ``base`` (default: the worker environment) minus secret settings.

    The trusted-CA, proxy and locale variables survive; the worker's own
    environment is never mutated.
    """

    source = os.environ if base is None else base
    return {
        name: value for name, value in source.items() if not _is_secret_name(name)
    }


def download_audio(
    *,
    platform: str,
    url: str,
    dest_dir: Path,
    settings: Any,
    runner: Callable[..., Any] | None = None,
) -> DownloadedAudio:
    """Download the best audio stream of one public video into ``dest_dir``.

    Raises :class:`TransientFetchError` for rate limits, proxy failures and
    timeouts (retryable) and :class:`AsrError` for every terminal condition.
    """

    run = runner or subprocess.run
    dest_dir = Path(dest_dir)
    max_bytes = int(settings.asr_max_audio_bytes)
    timeout = float(settings.asr_download_timeout_seconds)

    extra_args: list[str] = []
    proxy_environment: dict[str, str] | None = None
    proxy_url = getattr(settings, "youtube_proxy_url", None)
    youtube: YouTubeConnector | None = None
    if platform == "youtube":
        # Only the connector's own helpers are used: they build the child-only
        # proxy environment and classify failures without exposing stderr.
        youtube = YouTubeConnector(proxy_url=proxy_url)
        video_id = youtube.match(url)
        if video_id is None:
            raise AsrError("asr_audio_unavailable")
        target = f"https://www.youtube.com/watch?v={video_id}"
        extra_args = ["--extractor-args", "youtube:player_client=android_vr"]
        proxy_environment = youtube._child_environment()
    elif platform == "bilibili":
        bilibili = BilibiliConnector()
        platform_id = bilibili.match(url)
        if platform_id is None:
            raise AsrError("asr_audio_unavailable")
        target = bilibili.canonical_url(platform_id)
    else:
        raise AsrError("asr_audio_unavailable")

    args = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-cache-dir",
        "--no-playlist",
        "--no-progress",
        "--socket-timeout",
        str(min(_MAX_SOCKET_TIMEOUT_SECONDS, timeout)),
        "-f",
        AUDIO_FORMAT_SELECTOR,
        "--max-filesize",
        str(max_bytes),
        "-o",
        str(dest_dir).replace("%", "%%") + "/audio.%(ext)s",
        *extra_args,
        target,
    ]
    run_kwargs: dict[str, Any] = {
        "text": True,
        "capture_output": True,
        "check": False,
        "timeout": timeout,
        "stdin": subprocess.DEVNULL,
    }
    # The child always gets an explicit environment: the connector's
    # proxy/CA copy for YouTube, the worker's own for Bilibili, in both cases
    # without provider keys or other secrets.
    run_kwargs["env"] = _child_environment_without_secrets(proxy_environment)
    try:
        result = run(args, **run_kwargs)
    except subprocess.TimeoutExpired:
        raise TransientFetchError(_timeout_code(platform, proxy_url)) from None
    except OSError:
        raise AsrError("asr_audio_unavailable") from None

    output = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    if _TOO_LARGE_MARKER in output:
        raise AsrError("asr_audio_too_large")
    if result.returncode:
        _raise_for_failure(platform, youtube, result.stderr)
    return _collect_audio(dest_dir, max_bytes)


def _timeout_code(platform: str, proxy_url: str | None) -> str:
    if platform == "bilibili":
        return "bilibili_fetch_timeout"
    if proxy_url is not None:
        return "youtube_proxy_timeout"
    return "youtube_fetch_timeout"


def _raise_for_failure(
    platform: str, youtube: YouTubeConnector | None, stderr: object
) -> None:
    if platform == "bilibili":
        code = BilibiliConnector._classify_failure(stderr)
        if code == "bilibili_rate_limited":
            raise TransientFetchError(code)
    elif youtube is not None:
        code = youtube._classify_yt_dlp_failure(stderr)
        if code in _TRANSIENT_YOUTUBE_CODES:
            raise TransientFetchError(code)
    raise AsrError("asr_audio_unavailable")


def _collect_audio(dest_dir: Path, max_bytes: int) -> DownloadedAudio:
    candidates = [
        entry
        for entry in dest_dir.glob("audio.*")
        if entry.is_file()
        and entry.suffix.lstrip(".").lower() not in _INCOMPLETE_SUFFIXES
    ]
    if not candidates:
        raise AsrError("asr_audio_unavailable")
    chosen = max(candidates, key=lambda entry: entry.stat().st_size)
    ext = chosen.suffix.lstrip(".").lower()
    size = chosen.stat().st_size
    if ext not in ALLOWED_AUDIO_EXTS or size <= 0:
        raise AsrError("asr_audio_unavailable")
    if size > max_bytes:
        # Formats without a declared size are only bounded after download.
        raise AsrError("asr_audio_too_large")
    return DownloadedAudio(
        path=chosen,
        ext=ext,
        size_bytes=size,
        content_type=_CONTENT_TYPES.get(ext, "application/octet-stream"),
    )
