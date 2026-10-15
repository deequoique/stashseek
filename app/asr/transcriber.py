"""Orchestrates audio download, Fun-ASR, the whisper fallback and cleanup.

One ``transcribe`` call:

1. checks admission (configuration and duration) before any download;
2. downloads audio into a private temporary directory;
3. stages the audio as a temporary object behind a short-lived signed URL and
   asks Fun-ASR, falling back to whisper only when Fun-ASR is unavailable or
   answers with an unusable payload (a valid but empty transcript does not
   fall back);
4. always deletes the temporary object and directory;
5. emits one allow-listed ``asr_transcription`` diagnostic record.
"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.asr.audio import download_audio
from app.asr.funasr import FunAsrClient
from app.asr.signing import audio_object_key, new_audio_object_name, sign_audio_url
from app.asr.types import (
    AsrCue,
    AsrError,
    AsrProtocolError,
    AsrProviderUnavailable,
    AsrTranscript,
)
from app.asr.whisper import MODEL as WHISPER_MODEL
from app.asr.whisper import WhisperClient
from app.browser_capture import CaptureCaption, CaptureCue, canonical_transcript_bytes
from app.connectors.base import Cue, TextResult, TransientFetchError
from app.ingest.validate import IngestLimitExceeded
from app.object_store import ObjectStoreError

_LOGGER = logging.getLogger("notebook_agent.runtime")
# Provider list prices used only for the estimate in the diagnostic record.
_COST_PER_SECOND = {"fun_asr": (0.00026, "CNY"), "whisper": (0.0001, "USD")}
_MAX_CUE_TEXT_CHARS = 10_000
_MIN_CUE_SECONDS = 0.01
_CJK_RATIO_THRESHOLD = 0.3
_CJK_RE = re.compile(r"[㐀-鿿]")
_SAFE_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_LOG_FIELDS = (
    "provider",
    "model",
    "audio_seconds",
    "billed_seconds",
    "estimated_cost",
    "currency",
    "duration_ms",
    "fallback_used",
    "cue_count",
    "error_code",
)


def _normalize_cues(cues: Any) -> list[AsrCue]:
    """NFKC-normalize, fold whitespace, drop blanks and order by start."""

    normalized: list[AsrCue] = []
    for cue in cues:
        text = " ".join(unicodedata.normalize("NFKC", cue.text).split())
        text = text[:_MAX_CUE_TEXT_CHARS].strip()
        if not text:
            continue
        start = round(cue.start_sec, 3)
        end = round(max(cue.end_sec, start + _MIN_CUE_SECONDS), 3)
        normalized.append(AsrCue(start, end, text))
    normalized.sort(key=lambda cue: cue.start_sec)
    return normalized


def _infer_language(cues: list[AsrCue]) -> str:
    """``zh`` when CJK ideographs are >= 30% of letters/digits, else ``en``."""

    total = cjk = 0
    for cue in cues:
        for character in cue.text:
            if character.isalnum():
                total += 1
                if _CJK_RE.match(character):
                    cjk += 1
    return "zh" if total and cjk / total >= _CJK_RATIO_THRESHOLD else "en"


def transcript_to_text_result(transcript: AsrTranscript) -> TextResult:
    """Build the canonical ``capture-transcript.v1`` result for ``process_item``."""

    cues = _normalize_cues(transcript.cues)
    if not cues:
        raise AsrError("asr_no_speech")
    language = transcript.language or _infer_language(cues)
    try:
        caption = CaptureCaption(
            status="available",
            source="auto_caption",
            language=language,
            cues=[
                CaptureCue(start_sec=cue.start_sec, end_sec=cue.end_sec, text=cue.text)
                for cue in cues
            ],
        )
        raw_body = canonical_transcript_bytes(caption)
    except ValidationError:
        # Cue-count and text-size ceilings of the canonical transcript.
        raise IngestLimitExceeded() from None
    return TextResult(
        raw_body=raw_body,
        cues=[Cue(cue.start_sec, cue.end_sec, cue.text) for cue in cues],
        source="asr_whisper",
        lang=language,
        format="capture_v1",
    )


class AsrTranscriber:
    def __init__(
        self,
        *,
        settings: Any,
        object_store: Any,
        funasr: FunAsrClient | None = None,
        whisper: WhisperClient | None = None,
        downloader: Callable[..., Any] = download_audio,
    ) -> None:
        self._settings = settings
        self._object_store = object_store
        if funasr is None and getattr(settings, "dashscope_api_key", None):
            funasr = FunAsrClient(
                api_key=settings.dashscope_api_key,
                base_url=settings.dashscope_base_url,
                model=settings.fun_asr_model,
                timeout_seconds=settings.asr_provider_timeout_seconds,
                poll_interval_seconds=settings.asr_poll_interval_seconds,
            )
        if (
            whisper is None
            and getattr(settings, "asr_whisper_fallback_enabled", False)
            and getattr(settings, "openai_api_key", None)
        ):
            whisper = WhisperClient(api_key=settings.openai_api_key)
        self._funasr = funasr
        self._whisper = whisper
        self._downloader = downloader

    def transcribe(
        self, *, platform: str, url: str, duration_sec: int | None
    ) -> AsrTranscript:
        started = time.monotonic()
        record: dict[str, Any] = {}
        try:
            transcript = self._transcribe(platform, url, duration_sec, record)
        except AsrError as exc:
            record["error_code"] = exc.error_code
            raise
        except TransientFetchError as exc:
            code = str(exc)
            record["error_code"] = (
                code if _SAFE_CODE_RE.fullmatch(code) else "transient_fetch_failed"
            )
            raise
        except BaseException:
            record["error_code"] = "asr_unexpected_error"
            raise
        else:
            self._record_success(record, transcript)
            return transcript
        finally:
            record["duration_ms"] = int((time.monotonic() - started) * 1000)
            self._emit(record)

    def _transcribe(
        self,
        platform: str,
        url: str,
        duration_sec: int | None,
        record: dict[str, Any],
    ) -> AsrTranscript:
        settings = self._settings
        if self._funasr is None and self._whisper is None:
            raise AsrError("asr_not_configured")
        if duration_sec is not None and duration_sec > settings.asr_max_duration_seconds:
            raise AsrError("asr_too_long")

        work_dir = Path(tempfile.mkdtemp(prefix="asr-"))
        object_key: str | None = None
        try:
            audio = self._downloader(
                platform=platform, url=url, dest_dir=work_dir, settings=settings
            )
            transcript: AsrTranscript | None = None
            if self._funasr is not None:
                record["provider"] = "fun_asr"
                record["model"] = settings.fun_asr_model
                signed_url: str | None
                try:
                    name = new_audio_object_name(audio.ext)
                    object_key = audio_object_key(name)
                    self._object_store.put_file(
                        object_key, audio.path, audio.content_type
                    )
                    signed_url = sign_audio_url(
                        base_url=settings.asr_public_base_url,
                        name=name,
                        secret=settings.asr_audio_url_secret,
                        ttl_seconds=settings.asr_audio_url_ttl_seconds,
                    )
                except (ObjectStoreError, ValueError):
                    # Staging failed: Fun-ASR cannot fetch the audio.
                    signed_url = None
                if signed_url is not None:
                    try:
                        transcript = self._funasr.transcribe_url(signed_url)
                    except (AsrProviderUnavailable, AsrProtocolError):
                        transcript = None
            if transcript is None:
                record["fallback_used"] = self._funasr is not None
                if self._whisper is None:
                    raise AsrError("asr_provider_failed")
                record["provider"] = "whisper"
                record["model"] = WHISPER_MODEL
                try:
                    transcript = self._whisper.transcribe_file(
                        audio.path, work_dir=work_dir
                    )
                except (AsrProviderUnavailable, AsrProtocolError):
                    raise AsrError("asr_provider_failed") from None
            cues = _normalize_cues(transcript.cues)
            if not cues:
                self._record_success(record, transcript)
                raise AsrError("asr_no_speech")
            return replace(transcript, cues=tuple(cues))
        finally:
            self._cleanup(object_key, work_dir)

    def _cleanup(self, object_key: str | None, work_dir: Path) -> None:
        if object_key is not None:
            try:
                self._object_store.delete_object(object_key)
            except Exception:
                # Best effort: the bucket lifecycle rule expires leftovers.
                pass
        shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _record_success(record: dict[str, Any], transcript: AsrTranscript) -> None:
        record["provider"] = transcript.provider
        record["model"] = transcript.model
        record["audio_seconds"] = transcript.audio_seconds
        record["billed_seconds"] = transcript.billed_seconds
        record["cue_count"] = len(transcript.cues)
        billable = (
            transcript.billed_seconds
            if transcript.billed_seconds is not None
            else transcript.audio_seconds
        )
        rate = _COST_PER_SECOND.get(transcript.provider)
        if billable is not None and rate is not None:
            record["estimated_cost"] = round(billable * rate[0], 6)
            record["currency"] = rate[1]

    @staticmethod
    def _emit(record: dict[str, Any]) -> None:
        payload: dict[str, Any] = {"event": "asr_transcription"}
        for name in _LOG_FIELDS:
            value = record.get(name)
            if value is not None:
                payload[name] = value
        payload.setdefault("fallback_used", False)
        try:
            _LOGGER.info("diagnostic", extra={"diagnostic_payload": payload})
        except Exception:
            # Observability must never alter ingestion semantics.
            return
