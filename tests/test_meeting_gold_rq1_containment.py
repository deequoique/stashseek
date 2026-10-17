from __future__ import annotations

from evals.meeting_gold.core import Case, MeetingCorpus, Sentence, stable_sentence_id
from evals.meeting_gold.production_path import (
    MAX_CUE_WORDS,
    build_caption_track,
    classify_overlap,
    map_segment_sentences,
    sentence_cue_spans,
    val_meeting_ids,
)


def _sentence(meeting_id: str, turn_index: int, sentence_index: int, text: str, speaker: str = "A") -> Sentence:
    return Sentence(
        id=stable_sentence_id(meeting_id, turn_index, sentence_index),
        meeting_id=meeting_id,
        turn_index=turn_index,
        sentence_index=sentence_index,
        speaker=speaker,
        text=text,
    )


def test_build_caption_track_cue_word_limit_and_provenance() -> None:
    meeting_id = "m1"
    # 13 words: must split into a 12-word cue and a 1-word cue, both tagged
    # with the same sentence id.
    long_sentence = _sentence(meeting_id, 0, 0, " ".join(f"w{i}" for i in range(13)))
    short_sentence = _sentence(meeting_id, 1, 0, "hello world")
    corpus = MeetingCorpus([long_sentence, short_sentence])

    body, cue_sids = build_caption_track(corpus)

    assert len(cue_sids) == 3  # 12-word cue + 1-word cue (turn 0) + 2-word cue (turn 1)
    assert cue_sids[0] == [long_sentence.id]
    assert cue_sids[1] == [long_sentence.id]
    assert cue_sids[2] == [short_sentence.id]
    assert b"events" in body


def test_build_caption_track_strips_disfluency_markers() -> None:
    meeting_id = "m1"
    sentence = _sentence(meeting_id, 0, 0, "um {disfmarker} okay then")
    corpus = MeetingCorpus([sentence])
    _, cue_sids = build_caption_track(corpus)
    # The marker becomes whitespace and disappears as a token, but surrounding
    # words remain tagged with the sentence id.
    assert cue_sids == [[sentence.id]]


def test_sentence_cue_spans_tracks_first_and_last_cue() -> None:
    cue_sids = [["s1"], ["s1", "s2"], ["s2"], ["s3"]]
    spans = sentence_cue_spans(cue_sids)
    assert spans == {"s1": (0, 1), "s2": (1, 2), "s3": (3, 3)}


def test_classify_overlap_full_partial_none() -> None:
    # Fully inside.
    assert classify_overlap((0, 5), (1, 3)) == "full"
    assert classify_overlap((0, 5), (0, 5)) == "full"
    # No overlap at all.
    assert classify_overlap((0, 2), (3, 5)) == "none"
    assert classify_overlap((6, 9), (3, 5)) == "none"
    # Overlaps but is not fully contained.
    assert classify_overlap((0, 2), (1, 3)) == "partial"
    assert classify_overlap((2, 5), (0, 3)) == "partial"


def test_sentence_split_across_cues_is_full_when_chunk_covers_both_cues() -> None:
    # A sentence whose 13 words straddle the 12-word cue boundary spans cue 0
    # and cue 1. A chunk covering both cues fully contains it.
    cue_sids = [["s1"], ["s1"], ["s2"]]
    spans = sentence_cue_spans(cue_sids)
    assert spans["s1"] == (0, 1)

    chunk_covers_both = (0, 2)
    full, partial = map_segment_sentences(chunk_covers_both, spans)
    assert "s1" in full
    assert "s1" not in partial


def test_sentence_split_across_cues_is_partial_when_chunk_covers_only_one_cue() -> None:
    cue_sids = [["s1"], ["s1"], ["s2"]]
    spans = sentence_cue_spans(cue_sids)

    chunk_covers_only_first_cue = (0, 0)
    full, partial = map_segment_sentences(chunk_covers_only_first_cue, spans)
    assert "s1" not in full
    assert "s1" in partial


def test_chunk_overlap_sentence_crossing_boundary_is_partial_in_both_chunks() -> None:
    # Sentence s2 spans cues 1-2, straddling the boundary between chunk A
    # (cues 0-1) and chunk B (cues 2-3). Neither chunk fully contains it.
    cue_sids = [["s1"], ["s1", "s2"], ["s2", "s3"], ["s3"]]
    spans = sentence_cue_spans(cue_sids)
    assert spans["s2"] == (1, 2)

    chunk_a = (0, 1)
    chunk_b = (2, 3)
    full_a, partial_a = map_segment_sentences(chunk_a, spans)
    full_b, partial_b = map_segment_sentences(chunk_b, spans)

    assert "s2" not in full_a and "s2" in partial_a
    assert "s2" not in full_b and "s2" in partial_b
    # s1 and s3 are each fully inside their own chunk.
    assert "s1" in full_a
    assert "s3" in full_b


def test_max_cue_words_constant_matches_design() -> None:
    assert MAX_CUE_WORDS == 12


def _case(meeting_id: str, split: str) -> Case:
    return Case(
        case_id=f"qmsum:{meeting_id}:general:0",
        meeting_id=meeting_id,
        split=split,
        domain="Academic(icsi)",
        query_kind="general",
        query_index=0,
        query="q",
        answer="a",
    )


def test_val_meeting_ids_includes_distractor_meetings_not_in_any_selected_case() -> None:
    # D2: scope B must search all val meetings, including ones no selected
    # case references (distractors), and must exclude other splits.
    bundle = {
        "cases": [
            _case("m1", "val"),
            _case("m2", "val"),
            _case("m2", "val"),  # duplicate meeting via a second case
            _case("m3", "train"),
            _case("m4", "test"),
        ]
    }
    assert val_meeting_ids(bundle) == ["m1", "m2"]
