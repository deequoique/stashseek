"""Opt-in OpenAI pre-annotation for the bounded public-video pilot inputs.

This helper is deliberately separate from the formal benchmark runner.  It
uses a fixed, reviewable acquisition plan, writes only normalized candidates
for a human reviewer, and cannot mutate or unlock the pilot manifest.
"""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from .openai_smoke import (
    DEFAULT_BASE_URL,
    OPENAI_API_KEY_ENV,
    SmokeTransport,
    TransportResponse,
    UrllibTransport,
)
from .redaction import assert_redacted, redact_report
from .text_normalization import TextNormalizationUnavailable, normalize_text


ASR_MODEL = "whisper-1"
VISION_MODEL = "gpt-5.6-luna"
ANNOTATION_CONFIRM_ENV = "VIDEO_RECOGNITION_OPENAI_ANNOTATION_CONFIRM"
ANNOTATION_FAILURE_EXIT_CODE = 3
DEFAULT_REPORT_PATH = Path(
    ".trellis/tasks/08-24-video-recognition-benchmark/research/"
    "openai-annotation-assist-2026-09-05.md"
)

_MARKER_NAME = ".openai-video-annotation-assist"
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_CLIP_BYTES = 80 * 1024 * 1024
_MAX_RESPONSE_BYTES = 1024 * 1024
_REQUEST_TIMEOUT_SECONDS = 120.0
_COMMAND_TIMEOUT_SECONDS = 240.0
_MEDIA_MAX_RETRIES = 2
_BOUNDARY = "video-recognition-annotation-assist"


class AnnotationAssistError(RuntimeError):
    """Stable failure that never carries provider, URL, stderr, or key data."""

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


@dataclass(frozen=True)
class AudioSpec:
    label: str
    start_sec: float
    end_sec: float


@dataclass(frozen=True)
class VisionSpec:
    label: str
    frame_times: tuple[float, ...]
    purpose: str
    vision_assisted_ocr: bool = False


@dataclass(frozen=True)
class ClipSpec:
    sample_id: str
    public_url: str
    label: str
    start_sec: float
    end_sec: float
    audio_specs: tuple[AudioSpec, ...] = ()
    vision_specs: tuple[VisionSpec, ...] = ()

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


ANNOTATION_PLAN: tuple[ClipSpec, ...] = (
    ClipSpec(
        "YT-01",
        "https://www.youtube.com/watch?v=3oAY1j5-KIg",
        "visual-14-24",
        14,
        24,
        vision_specs=(
            VisionSpec(
                "applications-slide",
                (18, 20, 22),
                "Identify the lecturer, display, slide title, and useful search terms.",
                True,
            ),
        ),
    ),
    ClipSpec(
        "YT-01",
        "https://www.youtube.com/watch?v=3oAY1j5-KIg",
        "speech-83-95",
        83,
        95,
        audio_specs=(AudioSpec("computer-vision-speech", 83, 95),),
    ),
    ClipSpec(
        "YT-03",
        "https://www.youtube.com/watch?v=iSpWZzL2i1o",
        "input-map-52-60",
        52,
        60,
        vision_specs=(
            VisionSpec(
                "input-map-ui",
                (54, 55, 56),
                "Determine whether these frames show the same Godot Input Map scene.",
                True,
            ),
        ),
    ),
    ClipSpec(
        "YT-03",
        "https://www.youtube.com/watch?v=iSpWZzL2i1o",
        "combined-326-354",
        326,
        354,
        audio_specs=(AudioSpec("current-object-rest-node", 347, 354),),
        vision_specs=(
            VisionSpec(
                "code-editor-current-object",
                (328, 340, 352),
                "Describe the editor and whether the frames support a code-editor search query.",
                True,
            ),
        ),
    ),
    ClipSpec(
        "BI-01",
        "https://www.bilibili.com/video/BV1bx411M7Zx",
        "handwritten-network-100-110",
        100,
        110,
        audio_specs=(AudioSpec("handwritten-digits-speech", 100, 110),),
        vision_specs=(
            VisionSpec(
                "network-and-hard-subtitles",
                (100, 102, 103),
                "Describe the neural-network visual and read any Chinese/English hard subtitles.",
                True,
            ),
        ),
    ),
    ClipSpec(
        "BI-06",
        "https://www.bilibili.com/video/BV1h64y1f7jz",
        "cake-5-35",
        5,
        35,
        audio_specs=(AudioSpec("cake-description-part-1", 5, 35),),
        vision_specs=(
            VisionSpec(
                "spoon-cake-action",
                (5, 6.5, 8),
                "Determine whether a spoon presses and scoops cake, with frame-level evidence.",
            ),
        ),
    ),
    ClipSpec(
        "BI-06",
        "https://www.bilibili.com/video/BV1h64y1f7jz",
        "cake-35-65",
        35,
        65,
        audio_specs=(AudioSpec("cake-description-part-2", 35, 65),),
    ),
    ClipSpec(
        "BI-06",
        "https://www.bilibili.com/video/BV1h64y1f7jz",
        "cake-65-76",
        65,
        76,
        audio_specs=(AudioSpec("cake-description-part-3", 65, 76),),
    ),
)


@dataclass(frozen=True)
class PreparedAudio:
    sample_id: str
    spec: AudioSpec
    payload: bytes


@dataclass(frozen=True)
class PreparedVision:
    sample_id: str
    spec: VisionSpec
    payloads: tuple[bytes, ...]


@dataclass(frozen=True)
class PreparationFailure:
    sample_id: str
    label: str
    error_code: str


@dataclass(frozen=True)
class PreparedInputs:
    audio: tuple[PreparedAudio, ...]
    vision: tuple[PreparedVision, ...]
    failures: tuple[PreparationFailure, ...] = ()


class MediaPreparer(Protocol):
    def prepare(self, root: Path) -> PreparedInputs: ...


class CommandRunner(Protocol):
    def run(self, args: Sequence[str], *, timeout_seconds: float, env: Mapping[str, str]) -> int: ...


class SubprocessCommandRunner:
    """Run media commands without ever emitting child output."""

    def run(self, args: Sequence[str], *, timeout_seconds: float, env: Mapping[str, str]) -> int:
        try:
            completed = subprocess.run(
                list(args),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
                env=dict(env),
            )
        except subprocess.TimeoutExpired:
            return 124
        except OSError:
            return 127
        return int(completed.returncode)


def _safe_media_environment() -> dict[str, str]:
    """Give media children only runtime/TLS settings, never provider secrets."""

    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "TLS_CA_BUNDLE",
    }
    return {key: value for key, value in os.environ.items() if key in allowed and isinstance(value, str)}


def validate_annotation_plan(plan: Sequence[ClipSpec] = ANNOTATION_PLAN) -> None:
    allowed_urls = {
        "YT-01": "https://www.youtube.com/watch?v=3oAY1j5-KIg",
        "YT-03": "https://www.youtube.com/watch?v=iSpWZzL2i1o",
        "BI-01": "https://www.bilibili.com/video/BV1bx411M7Zx",
        "BI-06": "https://www.bilibili.com/video/BV1h64y1f7jz",
    }
    if not plan:
        raise AnnotationAssistError("annotation_plan_invalid")
    for clip in plan:
        if allowed_urls.get(clip.sample_id) != clip.public_url:
            raise AnnotationAssistError("annotation_plan_invalid")
        parts = urlsplit(clip.public_url)
        if parts.scheme != "https" or parts.username or parts.password or parts.fragment:
            raise AnnotationAssistError("annotation_plan_invalid")
        if clip.start_sec < 0 or clip.end_sec <= clip.start_sec or clip.duration_sec > 60:
            raise AnnotationAssistError("annotation_plan_invalid")
        for audio in clip.audio_specs:
            if audio.start_sec < clip.start_sec or audio.end_sec > clip.end_sec or audio.end_sec <= audio.start_sec:
                raise AnnotationAssistError("annotation_plan_invalid")
            if audio.end_sec - audio.start_sec > 60:
                raise AnnotationAssistError("annotation_plan_invalid")
        for vision in clip.vision_specs:
            if not 3 <= len(vision.frame_times) <= 6:
                raise AnnotationAssistError("annotation_plan_invalid")
            if tuple(sorted(set(vision.frame_times))) != vision.frame_times:
                raise AnnotationAssistError("annotation_plan_invalid")
            if any(value < clip.start_sec or value > clip.end_sec for value in vision.frame_times):
                raise AnnotationAssistError("annotation_plan_invalid")


class YtDlpMediaPreparer:
    """Acquire only the hard-coded sections and project them to WAV/JPEG bytes."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        python_executable: str | None = None,
        ffmpeg_executable: str | None = None,
    ) -> None:
        self._runner = command_runner or SubprocessCommandRunner()
        self._python = python_executable or sys.executable
        self._ffmpeg = ffmpeg_executable or shutil.which("ffmpeg") or ""

    def _preflight(self) -> None:
        if not self._ffmpeg or not Path(self._ffmpeg).is_file():
            raise AnnotationAssistError("ffmpeg_missing")
        if not self._python or not Path(self._python).is_file():
            raise AnnotationAssistError("yt_dlp_missing")

    def _download_clip(self, clip: ClipSpec, root: Path, index: int, env: Mapping[str, str]) -> Path:
        output_template = root / f"clip-{index:02d}.%(ext)s"
        args = [
            self._python,
            "-m",
            "yt_dlp",
            "--ignore-config",
            "--socket-timeout",
            "30",
        ]
        if clip.sample_id.startswith("YT-"):
            # The public mweb player currently exposes a section-downloadable
            # URL to ffmpeg.  This is scoped to this offline annotation helper;
            # it does not change the production YouTube connector contract.
            args.extend(["--extractor-args", "youtube:player_client=mweb"])
        args.extend(
            [
            "--no-playlist",
            "--no-part",
            "--no-warnings",
            "--quiet",
            "--force-overwrites",
            "--download-sections",
            f"*{clip.start_sec:g}-{clip.end_sec:g}",
            "--force-keyframes-at-cuts",
            "--merge-output-format",
            "mp4",
            "--format",
            "bv*[height<=720]+ba/b[height<=720]/b",
            "--output",
            str(output_template),
            clip.public_url,
            ]
        )
        code = self._runner.run(args, timeout_seconds=_COMMAND_TIMEOUT_SECONDS, env=env)
        if code != 0:
            raise AnnotationAssistError("bounded_media_acquisition_failed")
        candidates = [
            path
            for path in root.glob(f"clip-{index:02d}.*")
            if path.is_file() and path.suffix not in {".part", ".ytdl"}
        ]
        if len(candidates) != 1:
            raise AnnotationAssistError("bounded_media_acquisition_failed")
        path = candidates[0]
        try:
            size = path.stat().st_size
        except OSError:
            raise AnnotationAssistError("bounded_media_acquisition_failed") from None
        if size <= 0 or size > _MAX_CLIP_BYTES:
            raise AnnotationAssistError("bounded_media_size_invalid")
        return path

    def _extract_audio(
        self,
        clip: ClipSpec,
        audio: AudioSpec,
        clip_path: Path,
        root: Path,
        serial: int,
        env: Mapping[str, str],
    ) -> bytes:
        output = root / f"audio-{serial:02d}.wav"
        args = [
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{audio.start_sec - clip.start_sec:g}",
            "-t",
            f"{audio.end_sec - audio.start_sec:g}",
            "-i",
            str(clip_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(output),
        ]
        if self._runner.run(args, timeout_seconds=_COMMAND_TIMEOUT_SECONDS, env=env) != 0:
            raise AnnotationAssistError("audio_extraction_failed")
        return _read_bounded_file(output, _MAX_AUDIO_BYTES, "audio_input_invalid")

    def _extract_frame(
        self,
        clip: ClipSpec,
        timestamp: float,
        clip_path: Path,
        root: Path,
        serial: int,
        env: Mapping[str, str],
    ) -> bytes:
        output = root / f"frame-{serial:02d}.jpg"
        args = [
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp - clip.start_sec:g}",
            "-i",
            str(clip_path),
            "-frames:v",
            "1",
            "-vf",
            "scale='min(1280,iw)':-2",
            "-q:v",
            "3",
            "-y",
            str(output),
        ]
        if self._runner.run(args, timeout_seconds=_COMMAND_TIMEOUT_SECONDS, env=env) != 0:
            raise AnnotationAssistError("frame_extraction_failed")
        return _read_bounded_file(output, _MAX_IMAGE_BYTES, "image_input_invalid")

    def prepare(self, root: Path) -> PreparedInputs:
        validate_annotation_plan()
        self._preflight()
        env = _safe_media_environment()
        prepared_audio: list[PreparedAudio] = []
        prepared_vision: list[PreparedVision] = []
        audio_serial = 0
        frame_serial = 0
        failures: list[PreparationFailure] = []
        for clip_index, clip in enumerate(ANNOTATION_PLAN):
            try:
                clip_path: Path | None = None
                last_error: AnnotationAssistError | None = None
                for attempt in range(_MEDIA_MAX_RETRIES + 1):
                    try:
                        clip_path = self._download_clip(clip, root, clip_index, env)
                        break
                    except AnnotationAssistError as exc:
                        last_error = exc
                        if exc.error_code != "bounded_media_acquisition_failed" or attempt >= _MEDIA_MAX_RETRIES:
                            raise
                if clip_path is None:
                    raise last_error or AnnotationAssistError("bounded_media_acquisition_failed")
                for audio in clip.audio_specs:
                    prepared_audio.append(
                        PreparedAudio(
                            clip.sample_id,
                            audio,
                            self._extract_audio(clip, audio, clip_path, root, audio_serial, env),
                        )
                    )
                    audio_serial += 1
                for vision in clip.vision_specs:
                    payloads: list[bytes] = []
                    for timestamp in vision.frame_times:
                        payloads.append(
                            self._extract_frame(clip, timestamp, clip_path, root, frame_serial, env)
                        )
                        frame_serial += 1
                    prepared_vision.append(PreparedVision(clip.sample_id, vision, tuple(payloads)))
            except AnnotationAssistError as exc:
                failures.append(PreparationFailure(clip.sample_id, clip.label, exc.error_code))
        return PreparedInputs(tuple(prepared_audio), tuple(prepared_vision), tuple(failures))


def _read_bounded_file(path: Path, limit: int, error_code: str) -> bytes:
    try:
        size = path.stat().st_size
        if size <= 0 or size > limit:
            raise AnnotationAssistError(error_code)
        payload = path.read_bytes()
    except OSError:
        raise AnnotationAssistError(error_code) from None
    if len(payload) != size:
        raise AnnotationAssistError(error_code)
    return payload


@contextmanager
def _temporary_media(base_dir: str | Path | None = None) -> Iterator[Path]:
    base = Path(base_dir) if base_dir is not None else None
    if base is not None:
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise AnnotationAssistError("invalid_temp_base_dir") from None
        if not base.is_dir():
            raise AnnotationAssistError("invalid_temp_base_dir")
    try:
        root = Path(tempfile.mkdtemp(prefix="openai-video-annotation-", dir=str(base) if base else None))
    except OSError:
        raise AnnotationAssistError("invalid_temp_base_dir") from None
    try:
        (root / _MARKER_NAME).write_text("owned-by-openai-video-annotation-assist\n", encoding="utf-8")
        yield root
    finally:
        try:
            shutil.rmtree(root)
        except OSError:
            raise AnnotationAssistError("cleanup_failed") from None
        if root.exists():
            raise AnnotationAssistError("cleanup_failed")


@dataclass(frozen=True)
class AnnotationRecord:
    sample_id: str
    label: str
    modality: str
    model: str
    status: str
    latency_ms: float
    usage: dict[str, object]
    candidate: dict[str, object] | None = None
    error_code: str | None = None
    http_status: int | None = None

    @property
    def success(self) -> bool:
        return self.status == "completed"


@dataclass(frozen=True)
class AnnotationAssistReport:
    records: tuple[AnnotationRecord, ...]
    cleanup_removed: bool
    report_path: Path

    @property
    def success(self) -> bool:
        return bool(self.records) and all(record.success for record in self.records)

    def safe_summary(self) -> dict[str, object]:
        return {
            "status": "completed" if self.success else "completed_with_errors",
            "record_count": len(self.records),
            "successful_records": sum(record.success for record in self.records),
            "failed_records": sum(not record.success for record in self.records),
            "cleanup": {"marked_temp_dir": True, "removed": self.cleanup_removed},
            "report_written": self.report_path.is_file(),
        }


def confirmation_from_environment(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return values.get(ANNOTATION_CONFIRM_ENV, "").strip().casefold() in {"1", "true", "yes"}


def _validate_key(value: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnnotationAssistError("credentials_missing")
    key = value.strip()
    if any(character.isspace() or not 33 <= ord(character) <= 126 for character in key):
        raise AnnotationAssistError("credentials_invalid")
    return key


def _multipart_audio(payload: bytes) -> tuple[bytes, str]:
    if not payload or len(payload) > _MAX_AUDIO_BYTES:
        raise AnnotationAssistError("audio_input_invalid")
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{_BOUNDARY}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )

    field("model", ASR_MODEL)
    field("response_format", "verbose_json")
    field("timestamp_granularities[]", "segment")
    field("timestamp_granularities[]", "word")
    chunks.extend(
        [
            f"--{_BOUNDARY}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="bounded.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            payload,
            b"\r\n",
            f"--{_BOUNDARY}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={_BOUNDARY}"


_VISION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "same_scene": {"type": "boolean"},
        "screen_text_present": {"type": "boolean"},
        "screen_text": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "search_terms": {"type": "array", "items": {"type": "string"}},
        "frame_observations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "frame_index": {"type": "integer"},
                    "timestamp_sec": {"type": "number"},
                    "observation": {"type": "string"},
                    "screen_text": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["frame_index", "timestamp_sec", "observation", "screen_text"],
            },
        },
        "uncertainties": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "same_scene",
        "screen_text_present",
        "screen_text",
        "actions",
        "search_terms",
        "frame_observations",
        "uncertainties",
    ],
}


def _vision_payload(prepared: PreparedVision) -> bytes:
    if len(prepared.payloads) != len(prepared.spec.frame_times):
        raise AnnotationAssistError("image_input_invalid")
    timeline = ", ".join(
        f"image {index + 1}={timestamp:g}s"
        for index, timestamp in enumerate(prepared.spec.frame_times)
    )
    prompt = (
        "You are producing a tentative annotation for human review, not benchmark truth. "
        f"Sample ID: {prepared.sample_id}. {timeline}. Purpose: {prepared.spec.purpose} "
        "Describe only visible evidence. Copy screen text only when legible; record uncertainty explicitly."
    )
    content: list[dict[str, object]] = [{"type": "input_text", "text": prompt}]
    for payload in prepared.payloads:
        if not payload or len(payload) > _MAX_IMAGE_BYTES:
            raise AnnotationAssistError("image_input_invalid")
        encoded = base64.b64encode(payload).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{encoded}",
                "detail": "original",
            }
        )
    request = {
        "model": VISION_MODEL,
        "store": False,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 1200,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "video_annotation_candidate",
                "strict": True,
                "schema": _VISION_SCHEMA,
            }
        },
        "input": [{"role": "user", "content": content}],
    }
    return json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _headers(key: str, content_type: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": content_type,
        "Accept": "application/json",
    }


def _parse_response(response: TransportResponse) -> Mapping[str, Any]:
    if not 200 <= response.status_code < 300:
        raise AnnotationAssistError(_http_error_code(response.status_code))
    if len(response.body) > _MAX_RESPONSE_BYTES:
        raise AnnotationAssistError("provider_response_too_large")
    try:
        parsed = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AnnotationAssistError("provider_malformed_response") from None
    if not isinstance(parsed, Mapping):
        raise AnnotationAssistError("provider_malformed_response")
    return parsed


def _http_error_code(status: int) -> str:
    if status in {401, 403}:
        return "provider_auth_failed"
    if status in {408, 425, 429} or status >= 500:
        return "provider_transient"
    if 400 <= status < 500:
        return "provider_rejected"
    return "provider_unexpected_status"


def _usage(value: Any) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {"reported": False}
    result: dict[str, object] = {"reported": False}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        number = value.get(key)
        if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
            result[key] = number
    if len(result) > 1:
        result["reported"] = True
    return result


def _finite_time(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise AnnotationAssistError("provider_malformed_response")
    return float(value)


def _bounded_text(value: Any, *, maximum: int = 4000) -> str:
    if not isinstance(value, str):
        raise AnnotationAssistError("provider_malformed_response")
    normalized = " ".join(value.replace("\x00", "").split())
    return normalized[:maximum]


def _bounded_asr_text(value: Any, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise AnnotationAssistError("provider_malformed_response")
    try:
        normalized = normalize_text(value)
    except TextNormalizationUnavailable:
        raise AnnotationAssistError("text_normalization_unavailable") from None
    return normalized[:maximum]


def _normalize_asr(payload: Mapping[str, Any], offset: float) -> dict[str, object]:
    text = _bounded_asr_text(payload.get("text", ""), maximum=12000)
    segments_value = payload.get("segments", [])
    words_value = payload.get("words", [])
    if not isinstance(segments_value, list) or not isinstance(words_value, list):
        raise AnnotationAssistError("provider_malformed_response")
    segments: list[dict[str, object]] = []
    for item in segments_value[:200]:
        if not isinstance(item, Mapping):
            raise AnnotationAssistError("provider_malformed_response")
        start = _finite_time(item.get("start")) + offset
        end = _finite_time(item.get("end")) + offset
        if start < offset or end < start:
            raise AnnotationAssistError("provider_malformed_response")
        segments.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "text": _bounded_asr_text(item.get("text", ""), maximum=2000),
            }
        )
    words: list[dict[str, object]] = []
    for item in words_value[:1000]:
        if not isinstance(item, Mapping):
            raise AnnotationAssistError("provider_malformed_response")
        start = _finite_time(item.get("start")) + offset
        end = _finite_time(item.get("end")) + offset
        if start < offset or end < start:
            raise AnnotationAssistError("provider_malformed_response")
        words.append(
            {
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "word": _bounded_asr_text(item.get("word", ""), maximum=200),
            }
        )
    return {"text": text, "segments": segments, "words": words}


def _response_output_text(payload: Mapping[str, Any]) -> str:
    if payload.get("status") != "completed" or not isinstance(payload.get("output"), list):
        raise AnnotationAssistError("provider_malformed_response")
    texts: list[str] = []
    for item in payload["output"]:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if not texts:
        raise AnnotationAssistError("provider_malformed_response")
    return "".join(texts)


def _normalize_vision(payload: Mapping[str, Any]) -> dict[str, object]:
    try:
        candidate = json.loads(_response_output_text(payload))
    except json.JSONDecodeError:
        raise AnnotationAssistError("provider_malformed_response") from None
    if not isinstance(candidate, dict):
        raise AnnotationAssistError("provider_malformed_response")
    # Re-serialize through JSON to reject custom or non-JSON provider objects.
    encoded = json.dumps(candidate, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise AnnotationAssistError("provider_response_too_large")
    return candidate


def _latency_ms(started: float) -> float:
    return round(max(0.0, (time.perf_counter() - started) * 1000), 2)


def _failed_record(
    *,
    sample_id: str,
    label: str,
    modality: str,
    model: str,
    started: float,
    error_code: str,
    http_status: int | None = None,
) -> AnnotationRecord:
    return AnnotationRecord(
        sample_id,
        label,
        modality,
        model,
        "failed",
        _latency_ms(started),
        {"reported": False},
        error_code=error_code,
        http_status=http_status,
    )


def _call_asr(transport: SmokeTransport, key: str, item: PreparedAudio) -> AnnotationRecord:
    started = time.perf_counter()
    try:
        body, content_type = _multipart_audio(item.payload)
        response = transport.request(
            "POST",
            "/v1/audio/transcriptions",
            headers=_headers(key, content_type),
            body=body,
            timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        )
        if not 200 <= response.status_code < 300:
            return _failed_record(
                sample_id=item.sample_id,
                label=item.spec.label,
                modality="asr",
                model=ASR_MODEL,
                started=started,
                error_code=_http_error_code(response.status_code),
                http_status=response.status_code,
            )
        payload = _parse_response(response)
        return AnnotationRecord(
            item.sample_id,
            item.spec.label,
            "asr",
            ASR_MODEL,
            "completed",
            _latency_ms(started),
            _usage(payload.get("usage")),
            candidate=redact_report(
                _normalize_asr(payload, item.spec.start_sec),
                secrets_to_remove=(key,),
            ),
        )
    except AnnotationAssistError as exc:
        return _failed_record(
            sample_id=item.sample_id,
            label=item.spec.label,
            modality="asr",
            model=ASR_MODEL,
            started=started,
            error_code=exc.error_code,
        )
    except Exception:
        return _failed_record(
            sample_id=item.sample_id,
            label=item.spec.label,
            modality="asr",
            model=ASR_MODEL,
            started=started,
            error_code="provider_unavailable",
        )


def _call_vision(transport: SmokeTransport, key: str, item: PreparedVision) -> AnnotationRecord:
    started = time.perf_counter()
    try:
        response = transport.request(
            "POST",
            "/v1/responses",
            headers=_headers(key, "application/json"),
            body=_vision_payload(item),
            timeout_seconds=_REQUEST_TIMEOUT_SECONDS,
        )
        if not 200 <= response.status_code < 300:
            return _failed_record(
                sample_id=item.sample_id,
                label=item.spec.label,
                modality="vision+ocr-assist" if item.spec.vision_assisted_ocr else "vision",
                model=VISION_MODEL,
                started=started,
                error_code=_http_error_code(response.status_code),
                http_status=response.status_code,
            )
        payload = _parse_response(response)
        return AnnotationRecord(
            item.sample_id,
            item.spec.label,
            "vision+ocr-assist" if item.spec.vision_assisted_ocr else "vision",
            VISION_MODEL,
            "completed",
            _latency_ms(started),
            _usage(payload.get("usage")),
            candidate=redact_report(_normalize_vision(payload), secrets_to_remove=(key,)),
        )
    except AnnotationAssistError as exc:
        return _failed_record(
            sample_id=item.sample_id,
            label=item.spec.label,
            modality="vision+ocr-assist" if item.spec.vision_assisted_ocr else "vision",
            model=VISION_MODEL,
            started=started,
            error_code=exc.error_code,
        )
    except Exception:
        return _failed_record(
            sample_id=item.sample_id,
            label=item.spec.label,
            modality="vision+ocr-assist" if item.spec.vision_assisted_ocr else "vision",
            model=VISION_MODEL,
            started=started,
            error_code="provider_unavailable",
        )


def _json_for_markdown(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).replace("````", "``` `")


def render_review_report(records: Sequence[AnnotationRecord]) -> str:
    lines = [
        "# OpenAI 视频预标注候选（待人工审核）",
        "",
        "> 这些结果只是模型候选，不是 benchmark truth。它们不会修改 `pilot_catalog.yaml`、不会计分，也不会打开正式 provider execution gate。",
        "> OCR 字段由多模态视觉模型辅助读取，不代表正式 OCR provider 的结果。",
        "",
        f"- ASR 模型：`{ASR_MODEL}`",
        f"- 视觉模型：`{VISION_MODEL}`",
        f"- 记录数：{len(records)}",
        "",
    ]
    for record in records:
        lines.extend(
            [
                f"## {record.sample_id} / {record.label}",
                "",
                f"- 模态：`{record.modality}`",
                f"- 状态：`{record.status}`",
                f"- 延迟：{record.latency_ms:.2f} ms",
                f"- 用量：`{_json_for_markdown(record.usage).replace(chr(10), ' ')}`",
            ]
        )
        if record.error_code:
            lines.append(f"- 错误类别：`{record.error_code}`")
        if record.http_status is not None:
            lines.append(f"- HTTP 状态：`{record.http_status}`")
        if record.candidate is not None:
            lines.extend(["", "````json", _json_for_markdown(record.candidate), "````"])
        lines.append("")
    lines.extend(
        [
            "## 人工审核后续",
            "",
            "只把人工确认过的时间范围、屏幕文字和画面判断写回 pilot truth；模型候选本身不能作为冻结依据。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_report(path: Path, records: Sequence[AnnotationRecord]) -> None:
    # Usage field names intentionally contain ``tokens`` and are valid numeric
    # counters, so scan only content-bearing candidates with the shared strict
    # sentinel checker.
    assert_redacted([record.candidate for record in records])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(render_review_report(records), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        raise AnnotationAssistError("report_write_failed") from None


def run_annotation_assist(
    *,
    confirm: bool = False,
    api_key: str | None = None,
    environ: Mapping[str, str] | None = None,
    transport: SmokeTransport | None = None,
    media_preparer: MediaPreparer | None = None,
    base_url: str = DEFAULT_BASE_URL,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    temp_base_dir: str | Path | None = None,
) -> AnnotationAssistReport:
    if not confirm:
        raise AnnotationAssistError("confirmation_required")
    values = os.environ if environ is None else environ
    key = _validate_key(api_key if api_key is not None else values.get(OPENAI_API_KEY_ENV))
    validate_annotation_plan()
    client = transport or UrllibTransport(base_url)
    preparer = media_preparer or YtDlpMediaPreparer()
    root: Path | None = None
    records: list[AnnotationRecord] = []
    with _temporary_media(temp_base_dir) as temp_root:
        root = temp_root
        prepared = preparer.prepare(temp_root)
        for failure in prepared.failures:
            records.append(
                AnnotationRecord(
                    failure.sample_id,
                    failure.label,
                    "media",
                    "yt-dlp+ffmpeg",
                    "failed",
                    0.0,
                    {"reported": False},
                    error_code=failure.error_code,
                )
            )
        for item in prepared.audio:
            records.append(_call_asr(client, key, item))
        for item in prepared.vision:
            records.append(_call_vision(client, key, item))
    output = Path(report_path)
    _write_report(output, records)
    return AnnotationAssistReport(tuple(records), root is not None and not root.exists(), output)


def safe_blocked_report(error_code: str) -> dict[str, object]:
    allowed = {
        "confirmation_required",
        "credentials_missing",
        "credentials_invalid",
        "annotation_plan_invalid",
        "invalid_temp_base_dir",
        "ffmpeg_missing",
        "yt_dlp_missing",
        "bounded_media_acquisition_failed",
        "bounded_media_size_invalid",
        "audio_extraction_failed",
        "frame_extraction_failed",
        "audio_input_invalid",
        "image_input_invalid",
        "text_normalization_unavailable",
        "cleanup_failed",
        "report_write_failed",
        "tls_configuration_invalid",
    }
    return {
        "status": "blocked",
        "error_code": error_code if error_code in allowed else "annotation_assist_unavailable",
    }


__all__ = [
    "ANNOTATION_CONFIRM_ENV",
    "ANNOTATION_FAILURE_EXIT_CODE",
    "ANNOTATION_PLAN",
    "ASR_MODEL",
    "DEFAULT_REPORT_PATH",
    "VISION_MODEL",
    "AnnotationAssistError",
    "AnnotationAssistReport",
    "AnnotationRecord",
    "AudioSpec",
    "ClipSpec",
    "MediaPreparer",
    "PreparedAudio",
    "PreparedInputs",
    "PreparedVision",
    "VisionSpec",
    "YtDlpMediaPreparer",
    "confirmation_from_environment",
    "render_review_report",
    "run_annotation_assist",
    "safe_blocked_report",
    "validate_annotation_plan",
]
