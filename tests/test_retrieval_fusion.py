"""Rank fusion (RRF / raw) and the per-item candidate window.

Pure-function tests: no database and no embedding provider.
"""

from __future__ import annotations

import pytest

from app.retrieval.fusion import (
    FusionConfig,
    fuse_raw,
    fuse_rrf,
    select_window,
)
from app.retrieval.search import Hit


def hit(item_id: int, segment_id: int, score: float) -> Hit:
    return Hit(
        item_id=item_id,
        title=f"video-{item_id}",
        platform_id=f"platform-{item_id}",
        segment_id=segment_id,
        text=f"evidence-{segment_id}",
        start_sec=float(segment_id),
        score=score,
    )


def ids(hits) -> list[int]:
    return [h.segment_id for h in hits]


def test_fusion_config_defaults_match_the_design():
    config = FusionConfig()
    assert config.method == "rrf"
    assert config.rrf_k == 60
    assert config.lexical_weight == 1.0
    assert config.vector_weight == 1.0
    assert config.candidate_pool_limit == 50
    assert config.per_item_cap == 3


def test_rrf_uses_ranks_so_a_tiny_lexical_score_still_competes():
    lexical = [hit(1, 100, 0.009)]  # trigram-scale score
    vector = [hit(2, 200, 0.50), hit(3, 300, 0.49), hit(4, 400, 0.48)]

    fused = fuse_rrf([lexical, vector], k=60, weights=[1.0, 1.0])

    # rank 1 in each list ties on 1/61; the segment_id tiebreak decides.
    assert ids(fused)[:2] == [100, 200]
    assert fused[0].score == pytest.approx(1 / 61)
    assert fused[1].score == pytest.approx(1 / 61)
    assert fused[2].score == pytest.approx(1 / 62)


def test_segment_in_both_lists_adds_scores_and_ranks_first():
    lexical = [hit(1, 10, 0.9), hit(2, 20, 0.8)]
    vector = [hit(3, 30, 0.7), hit(2, 20, 0.6)]

    fused = fuse_rrf([lexical, vector], k=60, weights=[1.0, 1.0])

    assert ids(fused) == [20, 10, 30]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 62)
    assert fused[1].score == pytest.approx(1 / 61)
    assert fused[2].score == pytest.approx(1 / 61)
    # 10 and 30 tie on 1/61 and 1/61: segment_id ascending.
    assert fused[1].segment_id < fused[2].segment_id


def test_rrf_returns_replaced_hits_with_fused_score_and_original_fields():
    lexical = [hit(1, 10, 0.9)]
    vector = [hit(1, 10, 0.4)]

    (fused,) = fuse_rrf([lexical, vector], k=60, weights=[1.0, 1.0])

    assert fused.score == pytest.approx(2 / 61)
    assert (fused.item_id, fused.segment_id, fused.text) == (1, 10, "evidence-10")
    # The inputs are frozen dataclasses and must stay untouched.
    assert lexical[0].score == 0.9 and vector[0].score == 0.4


def test_rrf_dedupes_a_list_by_segment_keeping_the_best_score():
    lexical = [hit(1, 10, 0.2), hit(2, 20, 0.5), hit(1, 10, 0.9)]

    fused = fuse_rrf([lexical], k=60, weights=[1.0])

    # Segment 10 (best score 0.9) is rank 1, segment 20 rank 2, no double count.
    assert ids(fused) == [10, 20]
    assert fused[0].score == pytest.approx(1 / 61)
    assert fused[1].score == pytest.approx(1 / 62)


def test_rrf_ranks_inside_a_list_by_score_not_input_position():
    vector = [hit(1, 10, 0.3), hit(2, 20, 0.8)]

    fused = fuse_rrf([vector], k=60, weights=[1.0])

    assert ids(fused) == [20, 10]


def test_empty_lists_are_skipped_without_affecting_the_others():
    vector = [hit(1, 10, 0.8), hit(2, 20, 0.7)]

    both = fuse_rrf([[], vector], k=60, weights=[1.0, 1.0])
    only_vector = fuse_rrf([vector], k=60, weights=[1.0])

    assert ids(both) == ids(only_vector) == [10, 20]
    assert [h.score for h in both] == [h.score for h in only_vector]
    assert fuse_rrf([[], []], k=60, weights=[1.0, 1.0]) == []


def test_weight_zero_is_equivalent_to_disabling_the_path():
    lexical = [hit(1, 10, 0.9), hit(3, 30, 0.8)]
    vector = [hit(2, 20, 0.7), hit(1, 10, 0.6)]

    disabled = fuse_rrf([vector], k=60, weights=[1.0])
    zeroed = fuse_rrf([lexical, vector], k=60, weights=[0.0, 1.0])

    assert zeroed == disabled
    # A segment that only the zero-weight path returns is absent entirely.
    assert 30 not in ids(zeroed)


def test_weights_scale_each_path_contribution():
    lexical = [hit(1, 10, 0.9)]
    vector = [hit(2, 20, 0.9)]

    fused = fuse_rrf([lexical, vector], k=60, weights=[1.0, 3.0])

    assert ids(fused) == [20, 10]
    assert fused[0].score == pytest.approx(3 / 61)
    assert fused[1].score == pytest.approx(1 / 61)


def test_k_changes_the_scale_but_not_the_order():
    lexical = [hit(1, 10, 0.9), hit(2, 20, 0.8), hit(3, 30, 0.7)]
    vector = [hit(3, 30, 0.9), hit(4, 40, 0.8), hit(1, 10, 0.7)]

    orders = {
        k: ids(fuse_rrf([lexical, vector], k=k, weights=[1.0, 1.0]))
        for k in (1, 10, 60, 500)
    }

    assert len({tuple(order) for order in orders.values()}) == 1
    small = fuse_rrf([lexical, vector], k=1, weights=[1.0, 1.0])[0].score
    large = fuse_rrf([lexical, vector], k=500, weights=[1.0, 1.0])[0].score
    assert small > large


def test_rrf_supports_a_third_path_without_rule_changes():
    lexical = [hit(1, 10, 0.9)]
    vector = [hit(2, 20, 0.9)]
    media = [hit(2, 20, 0.5), hit(3, 30, 0.4)]

    fused = fuse_rrf([lexical, vector, media], k=60, weights=[1.0, 1.0, 1.0])

    assert ids(fused) == [20, 10, 30]
    assert fused[0].score == pytest.approx(2 / 61)


def test_rrf_ties_break_by_ascending_segment_id():
    vector_a = [hit(1, 50, 0.5)]
    vector_b = [hit(2, 7, 0.5)]
    vector_c = [hit(3, 30, 0.5)]

    fused = fuse_rrf([vector_a, vector_b, vector_c], k=60, weights=[1, 1, 1])

    assert ids(fused) == [7, 30, 50]


def test_rrf_rejects_invalid_arguments():
    with pytest.raises(ValueError):
        fuse_rrf([[hit(1, 1, 1.0)]], k=0, weights=[1.0])
    with pytest.raises(ValueError):
        fuse_rrf([[hit(1, 1, 1.0)]], k=60, weights=[1.0, 1.0])
    with pytest.raises(ValueError):
        fuse_rrf([[hit(1, 1, 1.0)]], k=60, weights=[-1.0])


def test_fuse_raw_compares_raw_scores_and_keeps_the_best_per_segment():
    lexical = [hit(1, 10, 0.009), hit(2, 20, 0.004)]
    vector = [hit(3, 30, 0.50), hit(1, 10, 0.45)]

    fused = fuse_raw([lexical, vector])

    # The legacy behaviour: a trigram-scale lexical hit ranks below every vector hit.
    assert ids(fused) == [30, 10, 20]
    assert fused[1].score == 0.45  # best of 0.009 and 0.45
    assert fuse_raw([]) == []


def test_fuse_raw_breaks_ties_by_segment_id():
    fused = fuse_raw([[hit(1, 9, 0.5)], [hit(2, 3, 0.5)]])

    assert ids(fused) == [3, 9]


# ------------------------------------------------------------- select_window


def _fused(spec: list[tuple[int, int]]) -> list[Hit]:
    """Ranked hits from (item_id, segment_id) pairs, best first."""

    return [hit(item, segment, 1.0 - index * 0.01) for index, (item, segment) in enumerate(spec)]


def test_window_caps_hits_per_item_and_preserves_rank_order():
    # The first pass takes at most two hits per item while the window is
    # filled; the window is long enough here, so nothing is skipped.
    fused = _fused([(1, 1), (1, 2), (1, 3), (1, 4), (2, 5), (3, 6)])

    window = select_window(fused, limit=4, per_item_cap=2)

    assert ids(window) == [1, 2, 5, 6]


def test_window_single_item_pool_is_filled_to_the_limit_by_rank():
    # A search narrowed to one video: the cap must not shorten the window.
    fused = _fused([(7, n) for n in range(1, 16)])

    window = select_window(fused, limit=10, per_item_cap=3)

    assert ids(window) == list(range(1, 11))


def test_window_single_item_pool_smaller_than_the_limit_returns_everything():
    fused = _fused([(7, n) for n in range(1, 5)])

    assert ids(select_window(fused, limit=10, per_item_cap=3)) == [1, 2, 3, 4]


def test_window_fills_a_short_first_pass_by_rank_with_the_first_pass_first():
    # Item 1 dominates the ranking; with cap 2 the first pass keeps
    # [1, 2, 6, 7, 8, 9] and skips [3, 4, 5]. When the limit asks for more than
    # the first pass holds, the skipped hits follow it in ranking order.
    fused = _fused(
        [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (2, 6), (2, 7), (3, 8), (3, 9)]
    )

    window = select_window(fused, limit=6, per_item_cap=2)

    # The first pass already fills the six slots, so no filler is used.
    assert ids(window) == [1, 2, 6, 7, 8, 9]

    window = select_window(fused, limit=8, per_item_cap=2)

    # First pass holds 6 hits (two per item); two skipped hits fill the rest in
    # ranking order after them.
    assert ids(window) == [1, 2, 6, 7, 8, 9, 3, 4]
    assert [h.segment_id for h in window[:6]] == [1, 2, 6, 7, 8, 9]


def test_window_filler_never_repeats_a_hit_and_stays_within_the_pool():
    fused = _fused([(1, 1), (1, 2), (1, 3), (2, 4)])

    window = select_window(fused, limit=10, per_item_cap=1)

    assert ids(window) == [1, 4, 2, 3]
    assert len(set(ids(window))) == len(window)


def test_window_cap_at_or_above_limit_equals_a_plain_ranking_cut():
    fused = _fused([(1, 1), (1, 2), (2, 3), (1, 4), (3, 5), (1, 6), (2, 7)])

    for cap in (4, 10, 99):
        assert ids(select_window(fused, limit=4, per_item_cap=cap)) == ids(fused[:4])
    # A smaller cap that never binds before the limit also equals the cut.
    assert ids(select_window(fused, limit=3, per_item_cap=2)) == ids(fused[:3])


def test_window_stops_at_the_total_limit():
    fused = _fused([(i, i) for i in range(1, 21)])

    window = select_window(fused, limit=10, per_item_cap=3)

    assert ids(window) == list(range(1, 11))


def test_window_can_hold_more_than_five_distinct_items():
    fused = _fused([(i, i * 10) for i in range(1, 9)])

    window = select_window(fused, limit=10, per_item_cap=3)

    assert len({h.item_id for h in window}) == 8


def test_window_with_cap_at_or_above_limit_is_unrestricted():
    fused = _fused([(1, n) for n in range(1, 15)])

    assert ids(select_window(fused, limit=10, per_item_cap=10)) == list(range(1, 11))
    assert ids(select_window(fused, limit=10, per_item_cap=99)) == list(range(1, 11))


def test_window_with_cap_three_fits_at_least_four_items_in_ten_slots():
    fused = _fused(
        [(1, 1), (1, 2), (1, 3), (1, 4), (2, 5), (2, 6), (2, 7), (2, 8), (3, 9), (3, 10), (3, 11), (4, 12)]
    )

    window = select_window(fused, limit=10, per_item_cap=3)

    assert len(window) == 10
    assert {h.item_id for h in window} == {1, 2, 3, 4}


def test_window_edge_cases():
    fused = _fused([(1, 1), (2, 2)])

    assert select_window([], limit=10, per_item_cap=3) == []
    assert select_window(fused, limit=0, per_item_cap=3) == []
    assert ids(select_window(fused, limit=1, per_item_cap=1)) == [1]
    with pytest.raises(ValueError):
        select_window(fused, limit=10, per_item_cap=0)
