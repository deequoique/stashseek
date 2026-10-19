"""Fixed registry of ingest failure reasons.

Pure data: no web, database or worker imports.  Every reason code the public
API may show is listed here with a category (one per user-facing explanation)
and whether re-running ingestion can plausibly change the outcome.  A code that
is not in the registry never leaves the server; it is shown as
``ingestion_failed``.

``item.fail_reason`` stores the code of the last unsuccessful attempt: failed
items keep the codes of ``app.web.library.SAFE_ERROR_CODES``; ``needs_*`` items
carry the codes written by ``process_item`` (``asr_disabled`` and friends).
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = (
    "no_speech",
    "no_transcription",
    "fetch_failed",
    "asr_failed",
    "quota",
    "too_large",
    "login_required",
    "error",
)
FALLBACK_CODE = "ingestion_failed"
LOGIN_REQUIRED = "login_required"

# Default reasons for ``needs_*`` rows written before reasons were recorded.
DEFAULT_NEEDS_ASR_REASON = "asr_unavailable"
DEFAULT_NEEDS_EXTENSION_REASON = "bilibili_login_required"


@dataclass(frozen=True)
class IngestReason:
    code: str
    category: str
    # Whether re-running ingestion can plausibly change the outcome.  It only
    # steers the wording of the UI; ``login_required`` reasons never offer a
    # server-side retry at all.
    retry_useful: bool


_REGISTRY: dict[str, IngestReason] = {
    reason.code: reason
    for reason in (
        IngestReason("asr_no_speech", "no_speech", False),
        IngestReason("asr_disabled", "no_transcription", True),
        IngestReason("asr_unsupported_platform", "no_transcription", False),
        IngestReason("asr_unavailable", "no_transcription", True),
        IngestReason("asr_not_configured", "no_transcription", True),
        IngestReason("transient_fetch_failed", "fetch_failed", True),
        IngestReason("asr_audio_unavailable", "fetch_failed", True),
        IngestReason("asr_provider_failed", "asr_failed", True),
        IngestReason("asr_handoff_failed", "asr_failed", True),
        # Reserved: nothing writes it until quota-aware ingestion exists.
        IngestReason("quota_exceeded", "quota", True),
        IngestReason("ingest_too_large", "too_large", False),
        IngestReason("asr_too_long", "too_large", False),
        IngestReason("asr_audio_too_large", "too_large", False),
        IngestReason("bilibili_login_required", LOGIN_REQUIRED, False),
        IngestReason("bilibili_capture_required", LOGIN_REQUIRED, False),
        IngestReason("browser_capture_required", LOGIN_REQUIRED, False),
        IngestReason("queue_unavailable", "error", True),
        IngestReason(FALLBACK_CODE, "error", True),
        IngestReason("missing_dispatch", "error", True),
        IngestReason("item_missing", "error", True),
        IngestReason("item_deleted", "error", True),
        IngestReason("capture_upload_failed", "error", True),
    )
}


def is_known_reason(code: str | None) -> bool:
    return code in _REGISTRY


def reason_for(code: str | None) -> IngestReason:
    """Return the registry entry for ``code``; unknown codes collapse to
    ``ingestion_failed`` so provider text can never reach a client."""

    return _REGISTRY.get(code) or _REGISTRY[FALLBACK_CODE]  # type: ignore[arg-type]
