from __future__ import annotations

import pytest

from evals.meeting_gold.core import MeetingCorpus, Sentence, Turn, stable_sentence_id
from evals.meeting_gold.integration import (
    IntegrationError,
    map_chunk_records,
    map_chunks_to_gold_sentence_ids,
    to_prediction_record,
)


def _corpus() -> dict[str, MeetingCorpus]:
    meeting_id = "meeting-a"
    sentences = [
        Sentence(
            id=stable_sentence_id(meeting_id, 0, 0),
            meeting_id=meeting_id,
            turn_index=0,
            sentence_index=0,
            speaker="Alice",
            text="Alpha one.",
        ),
        Sentence(
            id=stable_sentence_id(meeting_id, 0, 1),
            meeting_id=meeting_id,
            turn_index=0,
            sentence_index=1,
            speaker="Alice",
            text="Beta two.",
        ),
        Sentence(
            id=stable_sentence_id(meeting_id, 1, 0),
            meeting_id=meeting_id,
            turn_index=1,
            sentence_index=0,
            speaker="Bob",
            text="Gamma three.",
        ),
    ]
    return {
        meeting_id: MeetingCorpus(
            sentences,
            qmsum_turns=(
                Turn(meeting_id, 0, "Alice", "Alpha one. Beta two."),
                Turn(meeting_id, 1, "Bob", "Gamma three."),
            ),
            explainmeetsum_turns=(
                Turn(meeting_id, 0, "Alice", "Alpha one. Beta two."),
                Turn(meeting_id, 1, "Bob", "Gamma three."),
            ),
        )
    }


def _id(turn_index: int, sentence_index: int = 0) -> str:
    return stable_sentence_id("meeting-a", turn_index, sentence_index)


def test_explicit_ids_preserve_rank_and_deduplicate_first_occurrence():
    result = map_chunk_records(
        [
            {
                "meeting_id": "meeting-a",
                "chunk_id": "rank-1",
                "rank": 1,
                "score": 0.91,
                "source_sentence_ids": [_id(0, 1), _id(0, 0)],
            },
            {
                "meeting_id": "meeting-a",
                "chunk_id": "rank-2",
                "rank": 2,
                "score": 0.72,
                "source_sentence_ids": [_id(0, 0), _id(1, 0)],
            },
        ],
        _corpus(),
    )

    assert result["retrieved_sentence_ids"] == [_id(0, 1), _id(0, 0), _id(1, 0)]
    assert [chunk["chunk_id"] for chunk in result["chunks"]] == [
        "rank-1",
        "rank-2",
    ]
    assert [chunk["rank"] for chunk in result["chunks"]] == [1, 2]
    assert result["chunks"][1]["source_sentence_ids"] == [_id(0, 0), _id(1, 0)]


def test_character_offsets_require_exact_source_text_and_map_fully_covered_sentences():
    result = map_chunks_to_gold_sentence_ids(
        _corpus(),
        [
            {
                "meeting_id": "meeting-a",
                "turn_index": 0,
                "char_start": 0,
                "char_end": len("Alpha one. Beta two."),
                "text": "Alpha one. Beta two.",
            }
        ],
    )

    assert result["retrieved_sentence_ids"] == [_id(0, 0), _id(0, 1)]
    assert result["chunks"][0]["mapping"] == "character_offsets"


def test_character_offsets_do_not_credit_partially_covered_sentence_boundaries():
    with pytest.raises(IntegrationError, match="offset_matches_no_sentence"):
        map_chunk_records(
            [
                {
                    "meeting_id": "meeting-a",
                    "turn_index": 0,
                    "char_start": 0,
                    "char_end": 1,
                    "text": "A",
                }
            ],
            _corpus(),
        )

    result = map_chunk_records(
        [
            {
                "meeting_id": "meeting-a",
                "turn_index": 0,
                "char_start": 0,
                "char_end": len("Alpha one. B"),
                "text": "Alpha one. B",
            }
        ],
        _corpus(),
    )

    assert result["retrieved_sentence_ids"] == [_id(0, 0)]
    assert _id(0, 1) not in result["retrieved_sentence_ids"]


def test_offsets_can_use_explicit_source_text_when_raw_turn_snapshot_is_absent():
    meeting_id = "meeting-a"
    corpus = {
        meeting_id: [
            Sentence(
                id=stable_sentence_id(meeting_id, 0, 0),
                meeting_id=meeting_id,
                turn_index=0,
                sentence_index=0,
                speaker="Alice",
                text="Alpha one.",
            )
        ]
    }

    result = map_chunk_records(
        [
            {
                "meeting_id": meeting_id,
                "turn_index": 0,
                "source_text": "Alpha one.",
                "char_start": 0,
                "char_end": 10,
                "text": "Alpha one.",
            }
        ],
        corpus,
    )

    assert result["retrieved_sentence_ids"] == [stable_sentence_id(meeting_id, 0, 0)]


def test_missing_source_identity_is_rejected_by_default_and_reported_in_partial_mode():
    record = {"meeting_id": "meeting-a", "chunk_id": "unbound", "text": "Alpha one."}

    with pytest.raises(IntegrationError, match="source_identity_required"):
        map_chunk_records([record], _corpus())

    result = map_chunk_records([record], _corpus(), allow_unmapped=True)
    assert result["status"] == "partial"
    assert result["retrieved_sentence_ids"] == []
    assert result["coverage"] == {
        "input_chunks": 1,
        "mapped_chunks": 0,
        "unmapped_chunks": 1,
        "mapped_sentence_ids": 0,
    }
    assert result["unmapped_chunks"] == [
        {
            "input_index": 0,
            "chunk_id": "unbound",
            "rank": 1,
            "reason": "source_identity_required",
        }
    ]


def test_partial_retrieval_coverage_does_not_invent_unretrieved_ids():
    result = map_chunk_records(
        [
            {
                "meeting_id": "meeting-a",
                "source_sentence_ids": [_id(0, 0)],
            }
        ],
        _corpus(),
    )

    assert result["retrieved_sentence_ids"] == [_id(0, 0)]
    assert result["coverage"]["mapped_sentence_ids"] == 1
    assert _id(0, 1) not in result["retrieved_sentence_ids"]
    assert _id(1, 0) not in result["retrieved_sentence_ids"]


def test_unknown_and_cross_meeting_sentence_ids_are_rejected():
    other_id = stable_sentence_id("meeting-b", 0, 0)
    corpus = {
        **_corpus(),
        "meeting-b": MeetingCorpus(
            [
                Sentence(
                    id=other_id,
                    meeting_id="meeting-b",
                    turn_index=0,
                    sentence_index=0,
                    speaker="Carol",
                    text="Other meeting.",
                )
            ]
        ),
    }

    with pytest.raises(IntegrationError, match="wrong_meeting"):
        map_chunk_records(
            [{"meeting_id": "meeting-a", "source_sentence_ids": [other_id]}],
            corpus,
        )
    with pytest.raises(IntegrationError, match="unknown_sentence_id"):
        map_chunk_records(
            [{"meeting_id": "meeting-a", "source_sentence_ids": ["not-frozen"]}],
            corpus,
        )


def test_ambiguous_offsets_and_text_mismatch_fail_without_fuzzy_matching():
    meeting_id = "meeting-a"
    repeated = MeetingCorpus(
        [
            Sentence(
                id=stable_sentence_id(meeting_id, 0, 0),
                meeting_id=meeting_id,
                turn_index=0,
                sentence_index=0,
                speaker="Alice",
                text="Same.",
            )
        ],
        explainmeetsum_turns=(Turn(meeting_id, 0, "Alice", "Same. Same."),),
    )

    with pytest.raises(IntegrationError, match="ambiguous_character_offsets"):
        map_chunk_records(
            [
                {
                    "meeting_id": meeting_id,
                    "turn_index": 0,
                    "char_start": 0,
                    "char_end": 5,
                }
            ],
            {meeting_id: repeated},
        )

    with pytest.raises(IntegrationError, match="chunk_text_offset_mismatch"):
        map_chunk_records(
            [
                {
                    "meeting_id": "meeting-a",
                    "turn_index": 0,
                    "char_start": 0,
                    "char_end": 10,
                    "text": "Alpha one Beta",
                }
            ],
            _corpus(),
        )


def test_conflicting_mapping_authorities_and_wrong_meeting_are_not_silently_resolved():
    with pytest.raises(IntegrationError, match="mapping_authority_mismatch"):
        map_chunk_records(
            [
                {
                    "meeting_id": "meeting-a",
                    "source_sentence_ids": [_id(0, 0)],
                    "turn_index": 1,
                    "char_start": 0,
                    "char_end": len("Gamma three."),
                    "text": "Gamma three.",
                }
            ],
            _corpus(),
        )

    with pytest.raises(IntegrationError, match="wrong_meeting"):
        map_chunk_records(
            [{"meeting_id": "meeting-b", "source_sentence_ids": [_id(0, 0)]}],
            _corpus(),
            meeting_id="meeting-a",
        )


def test_result_and_prediction_projection_do_not_leak_gold_labels():
    result = map_chunk_records(
        [{"meeting_id": "meeting-a", "source_sentence_ids": [_id(0, 0)]}],
        _corpus(),
    )
    prediction = to_prediction_record(result, case_id="qmsum:meeting-a:general:0")

    serialized_keys = repr(result) + repr(prediction)
    assert "evidence" not in serialized_keys
    assert "answer" not in serialized_keys
    assert "eligibility" not in serialized_keys
    assert "gold_sentence_ids" not in serialized_keys
    assert prediction == {
        "case_id": "qmsum:meeting-a:general:0",
        "retrieved_sentence_ids": [_id(0, 0)],
        "cited_sentence_ids": [],
    }

    with pytest.raises(IntegrationError, match="gold_label_field"):
        map_chunk_records(
            [
                {
                    "meeting_id": "meeting-a",
                    "source_sentence_ids": [_id(0, 0)],
                    "evidence_types": ["CES"],
                }
            ],
            _corpus(),
        )
