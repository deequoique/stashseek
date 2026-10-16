"""Candidate-pool coverage and scope-B meeting localization (pure functions)."""

from __future__ import annotations

from evals.meeting_gold.retrieval_study import (
    RankedHit,
    meeting_localization,
    pool_coverage,
)

# Segment -> sentence ids fully contained by that chunk, for the case's own
# meeting only (the study never maps other meetings' chunks).
_FULL_IDS = {1: ["s1"], 2: ["s2", "s3"], 3: [], 4: ["s4"]}
_GOLD = frozenset({"s1", "s2", "s3", "s4"})


def test_pool_coverage_is_the_deduplicated_union_of_both_backends() -> None:
    dense = [RankedHit(2, 100, 0.9), RankedHit(3, 100, 0.8)]
    lexical = [RankedHit(2, 100, 5.0), RankedHit(1, 100, 4.0)]

    pool = pool_coverage(lexical, dense, segment_full_ids=_FULL_IDS, gold=_GOLD)

    assert pool["pool_size"] == 3  # segment 2 appears in both lists
    assert pool["pool_hit"] == 1.0
    assert pool["pool_recall"] == 3 / 4  # s1, s2, s3 reachable; s4 is not in the pool


def test_pool_coverage_counts_a_sentence_once_even_if_two_chunks_contain_it() -> None:
    # Overlapping chunks share a sentence; recall is over distinct gold sentences.
    full_ids = {1: ["s1", "s2"], 2: ["s2", "s3"]}
    gold = frozenset({"s1", "s2", "s3"})
    pool = pool_coverage(
        [RankedHit(1, 100, 1.0)], [RankedHit(2, 100, 1.0)], segment_full_ids=full_ids, gold=gold
    )
    assert pool["pool_recall"] == 1.0


def test_pool_coverage_ignores_other_meetings_chunks_and_misses_cleanly() -> None:
    # Scope B candidates from a distractor meeting are absent from the case's
    # segment map and contribute nothing, but still count toward the pool size.
    pool = pool_coverage(
        [RankedHit(900, 200, 1.0)],
        [RankedHit(901, 200, 1.0), RankedHit(3, 100, 0.5)],
        segment_full_ids=_FULL_IDS,
        gold=_GOLD,
    )
    assert pool["pool_size"] == 3
    assert pool["pool_hit"] == 0.0
    assert pool["pool_recall"] == 0.0


def test_pool_coverage_with_empty_pools_has_zero_size_and_zero_recall() -> None:
    pool = pool_coverage([], [], segment_full_ids=_FULL_IDS, gold=_GOLD)
    assert pool == {"pool_size": 0, "pool_hit": 0.0, "pool_recall": 0.0}


def test_pool_coverage_without_gold_reports_only_the_size() -> None:
    pool = pool_coverage(
        [RankedHit(1, 100, 1.0)], [RankedHit(2, 100, 1.0)], segment_full_ids=_FULL_IDS, gold=frozenset()
    )
    assert pool == {"pool_size": 2, "pool_hit": None, "pool_recall": None}


def test_a_wider_lexical_pool_can_only_add_coverage() -> None:
    dense = [RankedHit(1, 100, 0.9)]
    lex_and = [RankedHit(2, 100, 3.0)]
    lex_or = [RankedHit(2, 100, 3.0), RankedHit(4, 100, 1.0)]
    narrow = pool_coverage(lex_and, dense, segment_full_ids=_FULL_IDS, gold=_GOLD)
    wide = pool_coverage(lex_or, dense, segment_full_ids=_FULL_IDS, gold=_GOLD)
    assert wide["pool_recall"] >= narrow["pool_recall"]
    assert (narrow["pool_recall"], wide["pool_recall"]) == (3 / 4, 1.0)


# --------------------------------------------------------- localization
_ITEM_MEETINGS = {10: "m_case", 20: "m_same", 30: "m_other"}
# Source groups are read from case metadata, not from the meeting id prefix.
_DOMAINS = {"m_case": "Product(ami)", "m_same": "Product(ami)", "m_other": "Committee"}


def _localize(ranked: list[RankedHit]) -> dict:
    return meeting_localization(
        ranked,
        case_item_id=10,
        case_meeting_id="m_case",
        item_meeting_ids=_ITEM_MEETINGS,
        meeting_domains=_DOMAINS,
    )


def test_localization_top1_in_the_right_meeting() -> None:
    result = _localize([RankedHit(1, 10, 0.9), RankedHit(2, 20, 0.8)])
    assert result == {
        "top1_empty": False,
        "top1_meeting_correct": 1.0,
        "wrong_top1_meeting_id": None,
        "wrong_top1_same_source": None,
    }


def test_localization_wrong_top1_from_a_same_source_meeting() -> None:
    # The right meeting is second: top-1 is wrong but it is a look-alike meeting
    # from the same source domain.
    result = _localize([RankedHit(2, 20, 0.9), RankedHit(1, 10, 0.8)])
    assert result["top1_meeting_correct"] == 0.0
    assert result["wrong_top1_meeting_id"] == "m_same"
    assert result["wrong_top1_same_source"] is True


def test_localization_wrong_top1_from_a_different_source() -> None:
    result = _localize([RankedHit(3, 30, 0.9)])
    assert result["wrong_top1_meeting_id"] == "m_other"
    assert result["wrong_top1_same_source"] is False


def test_localization_empty_ranking_has_no_first_place() -> None:
    result = _localize([])
    assert result["top1_empty"] is True
    assert result["top1_meeting_correct"] == 0.0
    assert result["wrong_top1_meeting_id"] is None
    assert result["wrong_top1_same_source"] is None


def test_localization_unknown_wrong_meeting_leaves_source_undetermined() -> None:
    result = _localize([RankedHit(7, 99, 0.9)])
    assert result["top1_meeting_correct"] == 0.0
    assert result["wrong_top1_meeting_id"] is None
    assert result["wrong_top1_same_source"] is None
