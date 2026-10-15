"""Shared value types and stable errors for the ASR transcription pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AsrCue:
    start_sec: float
    end_sec: float
    text: str


@dataclass(frozen=True)
class AsrTranscript:
    cues: tuple[AsrCue, ...]
    language: str | None
    provider: str  # "fun_asr" | "whisper"
    model: str
    audio_seconds: float | None
    billed_seconds: float | None


ASR_ERROR_CODES = frozenset(
    {
        "asr_too_long",
        "asr_audio_unavailable",
        "asr_audio_too_large",
        "asr_provider_failed",
        "asr_no_speech",
        "asr_not_configured",
    }
)


class AsrError(Exception):
    """Terminal ASR failure carrying only a stable error code.

    The message is the code itself; provider text, URLs, keys and transcript
    content never enter it.
    """

    def __init__(self, error_code: str) -> None:
        if error_code not in ASR_ERROR_CODES:
            raise ValueError("unknown ASR error code")
        self.error_code = error_code
        super().__init__(error_code)


class AsrProviderUnavailable(Exception):
    """The provider could not serve the request; triggers the fallback."""


class AsrProtocolError(Exception):
    """The provider answered with an unusable payload; triggers the fallback."""
