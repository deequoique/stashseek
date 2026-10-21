import pytest

from app.asr.types import ASR_ERROR_CODES
from app.ingest import reasons
from app.ingest.reasons import (
    CATEGORIES,
    FALLBACK_CODE,
    IngestReason,
    is_known_reason,
    reason_for,
)
from app.web.library import SAFE_ERROR_CODES


EXPECTED = {
    "asr_no_speech": ("no_speech", False),
    "asr_disabled": ("no_transcription", True),
    "asr_unsupported_platform": ("no_transcription", False),
    "asr_unavailable": ("no_transcription", True),
    "part_transcription_unsupported": ("no_transcription", False),
    "asr_not_configured": ("no_transcription", True),
    "transient_fetch_failed": ("fetch_failed", True),
    "asr_audio_unavailable": ("fetch_failed", True),
    "asr_provider_failed": ("asr_failed", True),
    "asr_handoff_failed": ("asr_failed", True),
    "quota_exceeded": ("quota", True),
    "ingest_too_large": ("too_large", False),
    "asr_too_long": ("too_large", False),
    "asr_audio_too_large": ("too_large", False),
    "bilibili_login_required": ("login_required", False),
    "bilibili_capture_required": ("login_required", False),
    "browser_capture_required": ("login_required", False),
    "queue_unavailable": ("error", True),
    "ingestion_failed": ("error", True),
    "missing_dispatch": ("error", True),
    "item_missing": ("error", True),
    "item_deleted": ("error", True),
    "capture_upload_failed": ("error", True),
}


@pytest.mark.parametrize(("code", "expected"), sorted(EXPECTED.items()))
def test_every_listed_code_has_its_category_and_retry_hint(code, expected):
    assert reason_for(code) == IngestReason(code, *expected)
    assert is_known_reason(code)


def test_the_registry_is_exactly_the_listed_codes():
    assert set(reasons._REGISTRY) == set(EXPECTED)


def test_every_safe_error_code_and_asr_code_resolves_to_a_category():
    for code in sorted(SAFE_ERROR_CODES | ASR_ERROR_CODES):
        assert reason_for(code).category in CATEGORIES


def test_the_eight_categories_are_all_in_use():
    assert {value.category for value in reasons._REGISTRY.values()} == set(CATEGORIES)
    assert len(CATEGORIES) == 8


def test_the_response_schema_lists_exactly_the_registry_categories_and_statuses():
    # A category or status the schema does not list would make every library
    # response that carries it fail validation, so the two lists must agree.
    from typing import get_args

    from app.api.library_schemas import IngestReasonResponse, IngestStageResponse
    from app.ingest.stages import STAGE_STATUSES

    categories = IngestReasonResponse.model_fields["category"].annotation
    statuses = IngestStageResponse.model_fields["status"].annotation

    assert set(get_args(categories)) == set(CATEGORIES)
    assert set(get_args(statuses)) == set(STAGE_STATUSES)


@pytest.mark.parametrize(
    "code",
    [
        None,
        "",
        "youtube_rate_limited",
        "bilibili_rate_limited",
        "Traceback (most recent call last): secret",
        "transcript_unavailable",
    ],
)
def test_unknown_codes_collapse_to_the_generic_error(code):
    result = reason_for(code)

    assert result.code == FALLBACK_CODE == "ingestion_failed"
    assert result.category == "error"
    assert result.retry_useful is True
    assert not is_known_reason(code)


def test_login_required_reasons_never_suggest_a_server_retry():
    login = [v for v in reasons._REGISTRY.values() if v.category == "login_required"]

    assert login
    assert all(value.retry_useful is False for value in login)
