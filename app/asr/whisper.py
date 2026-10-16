"""OpenAI ``whisper-1`` transcription, used as the Fun-ASR fallback.

Audio up to 24 MB is uploaded as is.  Larger audio (and containers OpenAI does
not accept) is transcoded and split into fixed-length mono MP3 chunks with
ffmpeg; each chunk's segment timestamps are shifted by the chunk start offset
reported in ffmpeg's segment list.  Errors carry stable codes only.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.asr._common import (
    client_scope,
    finite_number,
    optional_seconds,
    request_json,
    require_dict,
)
from app.asr.types import (
    AsrCue,
    AsrProtocolError,
    AsrProviderUnavailable,
    AsrTranscript,
)

ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
MODEL = "whisper-1"
PROVIDER = "whisper"
DIRECT_UPLOAD_MAX_BYTES = 24 * 1024 * 1024
SEGMENT_SECONDS = 600
_MAX_SEGMENTS = 200
_ATTEMPTS = 3
_REQUEST_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_FFMPEG_TIMEOUT_SECONDS = 1800.0
_CHUNK_NAME_RE = re.compile(r"chunk-\d{5}\.mp3")
_MEDIA_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
_LANGUAGES = {"chinese": "zh", "zh": "zh", "english": "en", "en": "en"}
# Containers accepted by the OpenAI endpoint, with the upload content type.
_DIRECT_CONTENT_TYPES = {
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "mp4": "audio/mp4",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "webm": "audio/webm",
}
# Ogg Opus files carry an ``.opus`` extension that the endpoint rejects.
_UPLOAD_EXT_ALIASES = {"opus": "ogg"}


def _media_environment() -> dict[str, str]:
    """Give ffmpeg only runtime/TLS settings, never provider credentials."""

    return {key: os.environ[key] for key in _MEDIA_ENV_KEYS if key in os.environ}


class WhisperClient:
    def __init__(
        self,
        *,
        api_key: str,
        client: httpx.Client | None = None,
        ffmpeg_path: str | None = None,
        runner: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("whisper client requires an API key")
        self._api_key = api_key
        self._client = client
        self._ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
        self._runner = runner or subprocess.run
        self._sleep = sleep

    def transcribe_file(self, path: Path, *, work_dir: Path) -> AsrTranscript:
        path = Path(path)
        try:
            size = path.stat().st_size
        except OSError:
            raise AsrProviderUnavailable("audio_unreadable") from None
        if size <= 0:
            raise AsrProviderUnavailable("audio_empty")
        ext = _UPLOAD_EXT_ALIASES.get(
            path.suffix.lstrip(".").lower(), path.suffix.lstrip(".").lower()
        )
        if size <= DIRECT_UPLOAD_MAX_BYTES and ext in _DIRECT_CONTENT_TYPES:
            pieces = [(path, 0.0, ext)]
        else:
            pieces = [(piece, offset, "mp3") for piece, offset in self._split(path, Path(work_dir))]

        cues: list[AsrCue] = []
        language: str | None = None
        audio_seconds: float | None = 0.0
        with client_scope(self._client) as client:
            for piece, offset, piece_ext in pieces:
                payload = self._request(client, piece, piece_ext)
                piece_cues, piece_language, piece_seconds = self._parse(payload, offset)
                cues.extend(piece_cues)
                language = language or piece_language
                if piece_seconds is None or audio_seconds is None:
                    audio_seconds = None
                else:
                    audio_seconds += piece_seconds
        return AsrTranscript(
            cues=tuple(cues),
            language=language,
            provider=PROVIDER,
            model=MODEL,
            audio_seconds=audio_seconds,
            billed_seconds=audio_seconds,
        )

    def _request(self, client: httpx.Client, piece: Path, ext: str) -> Any:
        try:
            audio = piece.read_bytes()
        except OSError:
            raise AsrProviderUnavailable("audio_unreadable") from None
        if len(audio) > DIRECT_UPLOAD_MAX_BYTES:
            raise AsrProviderUnavailable("audio_too_large")
        return request_json(
            client,
            "POST",
            ENDPOINT,
            headers={"Authorization": f"Bearer {self._api_key}"},
            data={
                "model": MODEL,
                "response_format": "verbose_json",
                "timestamp_granularities[]": "segment",
            },
            files={"file": (f"audio.{ext}", audio, _DIRECT_CONTENT_TYPES[ext])},
            timeout=_REQUEST_TIMEOUT,
            max_response_bytes=_MAX_RESPONSE_BYTES,
            attempts=_ATTEMPTS,
            sleep=self._sleep,
        )

    def _split(self, path: Path, work_dir: Path) -> list[tuple[Path, float]]:
        if not self._ffmpeg:
            raise AsrProviderUnavailable("ffmpeg_missing")
        listing = work_dir / "segments.csv"
        args = [
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "48k",
            "-f",
            "segment",
            "-segment_time",
            str(SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            "-segment_list_type",
            "csv",
            "-segment_list",
            str(listing),
            str(work_dir / "chunk-%05d.mp3"),
        ]
        try:
            completed = self._runner(
                args,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=_FFMPEG_TIMEOUT_SECONDS,
                env=_media_environment(),
            )
        except (subprocess.TimeoutExpired, OSError):
            raise AsrProviderUnavailable("ffmpeg_failed") from None
        if completed.returncode:
            raise AsrProviderUnavailable("ffmpeg_failed")
        return self._read_segment_list(listing, work_dir)

    @staticmethod
    def _read_segment_list(listing: Path, work_dir: Path) -> list[tuple[Path, float]]:
        try:
            rows = list(csv.reader(io.StringIO(listing.read_text(encoding="utf-8"))))
        except (OSError, UnicodeDecodeError):
            raise AsrProviderUnavailable("ffmpeg_failed") from None
        pieces: list[tuple[Path, float]] = []
        for row in rows:
            if not row:
                continue
            try:
                name = row[0]
                offset = finite_number(float(row[1]))
            except (IndexError, ValueError, AsrProtocolError):
                raise AsrProviderUnavailable("ffmpeg_failed") from None
            piece = work_dir / name
            if offset < 0 or not _CHUNK_NAME_RE.fullmatch(name) or not piece.is_file():
                raise AsrProviderUnavailable("ffmpeg_failed")
            pieces.append((piece, offset))
        if not pieces or len(pieces) > _MAX_SEGMENTS:
            raise AsrProviderUnavailable("ffmpeg_failed")
        return pieces

    @staticmethod
    def _parse(
        payload: Any, offset: float
    ) -> tuple[list[AsrCue], str | None, float | None]:
        data = require_dict(payload)
        segments = data.get("segments")
        if not isinstance(segments, list):
            raise AsrProtocolError("missing_segments")
        cues: list[AsrCue] = []
        for segment in segments:
            item = require_dict(segment)
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            start = finite_number(item.get("start"))
            end = finite_number(item.get("end"))
            if start < 0 or end < start:
                raise AsrProtocolError("invalid_timing")
            cues.append(AsrCue(offset + start, offset + end, text.strip()))
        if not cues:
            spoken = data.get("text")
            if isinstance(spoken, str) and spoken.strip():
                # Text without timestamped segments cannot become cues.
                raise AsrProtocolError("no_timestamped_segments")
        raw_language = data.get("language")
        language = (
            _LANGUAGES.get(raw_language.strip().lower())
            if isinstance(raw_language, str)
            else None
        )
        return cues, language, optional_seconds(data.get("duration"))
