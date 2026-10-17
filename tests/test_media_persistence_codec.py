from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.types import Citation, MediaCitation
from app.api.conversation_routes import ConversationMediaCitationResponse
from app.channels.service import _answer_from_turn
from app.channels.source_codec import (
    decode_sources,
    encode_sources,
    text_source_payloads,
    validated_text_citations,
)


def _media(**overrides) -> MediaCitation:
    values = {
        "evidence_id": "Mmedia-1",
        "media_id": "media-1",
        "item_id": 7,
        "title": "A clip",
        "location": "https://example.test/media/1",
        "start_sec": 3.0,
        "end_sec": 8.0,
        "evidence": ["a document opens"],
    }
    values.update(overrides)
    return MediaCitation(**values)


def test_encode_decode_preserves_legacy_text_and_tagged_media_shape():
    text = {
        "item_id": 7,
        "segment_id": 11,
        "title": "Text source",
        "excerpt": "text evidence",
        "url": "https://example.test/text",
        "start_sec": 2.0,
    }

    encoded = encode_sources([text], [_media()])
    decoded = decode_sources(encoded)

    assert encoded[0] == text
    assert encoded[1]["kind"] == "media"
    assert decoded.text == (text,)
    assert decoded.media == (_media(),)
    assert text_source_payloads(encoded) == [text]
    assert validated_text_citations(encoded) == [Citation(**text)]


def test_malformed_media_is_dropped_without_becoming_text_or_api_source():
    malformed = [
        {"kind": "media", "evidence_id": "M1", "media_id": "media-1", "item_id": 0},
        {
            "kind": "media",
            "evidence_id": "S1",
            "media_id": "media-2",
            "item_id": 7,
        },
        {
            "evidence_id": "M3",
            "media_id": "media-3",
            "item_id": 7,
            "evidence": ["not tagged"],
        },
        {"kind": "media", "evidence_id": "M4", "media_id": "media-4", "item_id": 7, "location": "javascript:bad"},
    ]

    decoded = decode_sources(malformed)

    assert decoded.text == ()
    assert decoded.media == ()
    assert text_source_payloads(malformed) == []


@pytest.mark.parametrize(
    "value",
    [
        {"evidence_id": "M1", "media_id": "media-1", "item_id": 0},
        {"evidence_id": "S1", "media_id": "media-1", "item_id": 7},
        {"evidence_id": "M1", "media_id": "media/1", "item_id": 7},
        {"evidence_id": "M1", "media_id": "media-1", "item_id": True},
        {"evidence_id": "M1", "media_id": "media-1", "item_id": 7, "location": "https://user:pass@example.test/x"},
    ],
)
def test_media_citation_requires_strict_identity_and_safe_http_source(value):
    with pytest.raises(ValueError):
        MediaCitation(**value)


def test_encode_sources_skips_invalid_media_but_keeps_valid_text():
    text = {"title": "legacy", "excerpt": "evidence", "url": "https://example.test"}
    invalid = SimpleNamespace(model_dump=lambda **_kwargs: {"evidence_id": "M1", "media_id": "m", "item_id": 0})

    assert encode_sources([text], [invalid]) == [text]


def test_answer_replay_projects_text_and_media_sources_separately():
    text = {
        "item_id": 7,
        "segment_id": 11,
        "title": "Text source",
        "excerpt": "text evidence",
        "url": "https://example.test/text",
    }
    media = _media()
    turn = SimpleNamespace(
        sources=encode_sources([text], [media]),
        answer_status="ok",
        error_code=None,
        assistant_text="Answer [S11] [M1]",
        action_results=[],
    )

    answer = _answer_from_turn(turn, "thread-1")

    assert answer.status == "ok"
    assert answer.citations == [Citation(**text)]
    assert answer.media_citations == [media]
    assert answer.thread_id == "thread-1"


def test_legacy_text_only_replay_keeps_media_field_empty():
    text = {
        "item_id": 7,
        "segment_id": 11,
        "title": "Text source",
        "excerpt": "text evidence",
        "url": "https://example.test/text",
    }
    turn = SimpleNamespace(
        sources=[text],
        answer_status="legacy",
        error_code="old_error",
        assistant_text="Legacy answer",
        action_results=None,
    )

    answer = _answer_from_turn(turn, "thread-legacy")

    assert answer.status == "ok"
    assert answer.error_code is None
    assert answer.citations == [Citation(**text)]
    assert answer.media_citations == []


def test_api_media_projection_accepts_only_codec_validated_fields():
    projected = ConversationMediaCitationResponse(**_media().model_dump(mode="json"))

    assert projected.item_id == 7
    assert projected.location == "https://example.test/media/1"
    assert "kind" not in projected.model_dump()
