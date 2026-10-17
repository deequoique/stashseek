from __future__ import annotations

import json
from pathlib import Path

from evals.meeting_gold.retrieval_study import (
    RQ1_CASE_IDS,
    RankedHit,
    build_comparisons,
    compute_metrics,
    covered_sentences,
    enumerate_settings,
    fuse_minmax,
    fuse_raw,
    fuse_rrf,
    fuse_single,
    gold_bearing_chunks,
    production_spec_by_scope,
    rank_setting,
)

_RESEARCH_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "meeting_gold"
    / "prodpath-rerun-20261006.cases.json"
)


def test_enumerate_settings_matrix_shape() -> None:
    specs = enumerate_settings()
    # 9 method/variant combinations x 2 diversify x 2 scope.
    assert len(specs) == 36
    assert len({spec.setting_id for spec in specs}) == 36
    baselines = [spec for spec in specs if spec.is_baseline]
    assert len(baselines) == 1
    assert baselines[0].setting_id == "raw-AND-div-on-scope-B"


def test_fuse_single_keeps_best_score_per_segment() -> None:
    hits = [RankedHit(1, 100, 0.2), RankedHit(1, 100, 0.9), RankedHit(2, 100, 0.5)]
    fused = fuse_single(hits)
    by_id = {hit.segment_id: hit.score for hit in fused}
    assert by_id == {1: 0.9, 2: 0.5}


def test_fuse_raw_concatenates_and_keeps_max_score() -> None:
    lexical = [RankedHit(1, 100, 0.9), RankedHit(2, 100, 0.4)]
    dense = [RankedHit(2, 100, 0.95), RankedHit(3, 200, 0.7)]
    fused = fuse_raw(lexical, dense)
    by_id = {hit.segment_id: hit.score for hit in fused}
    assert by_id == {1: 0.9, 2: 0.95, 3: 0.7}


def test_fuse_rrf_scores_match_reciprocal_rank_formula() -> None:
    lexical = [RankedHit(1, 100, 0.9), RankedHit(2, 100, 0.4)]
    dense = [RankedHit(2, 100, 0.95), RankedHit(3, 200, 0.7)]
    fused = {hit.segment_id: hit.score for hit in fuse_rrf(lexical, dense, k=60)}
    # segment 1: lexical rank 1 only -> 1/(60+1)
    assert fused[1] == 1 / 61
    # segment 2: lexical rank 2, dense rank 1 -> 1/62 + 1/61
    assert abs(fused[2] - (1 / 62 + 1 / 61)) < 1e-12
    # segment 3: dense rank 2 only -> 1/62
    assert fused[3] == 1 / 62


def test_fuse_minmax_normalizes_each_list_independently() -> None:
    lexical = [RankedHit(1, 100, 1.0), RankedHit(2, 100, 0.0)]
    dense = [RankedHit(2, 100, 2.0), RankedHit(3, 200, 0.0)]
    fused = {hit.segment_id: hit.score for hit in fuse_minmax(lexical, dense, alpha=0.5)}
    # segment 1: lexical-normalized 1.0, absent from dense -> 0.0: 0.5*1 + 0.5*0
    assert fused[1] == 0.5
    # segment 2: lexical-normalized 0.0, dense-normalized 1.0: 0.5*0 + 0.5*1
    assert fused[2] == 0.5
    # segment 3: absent from lexical -> 0.0, dense-normalized 0.0
    assert fused[3] == 0.0


def test_fuse_minmax_constant_scores_normalize_to_one() -> None:
    lexical = [RankedHit(1, 100, 0.5), RankedHit(2, 100, 0.5)]
    fused = {hit.segment_id: hit.score for hit in fuse_minmax(lexical, [], alpha=0.5)}
    assert fused == {1: 0.5, 2: 0.5}


def test_rank_setting_diversify_on_reserves_item_slots() -> None:
    hits = [
        RankedHit(1, 100, 0.9),
        RankedHit(2, 100, 0.8),
        RankedHit(3, 100, 0.7),
        RankedHit(4, 200, 0.6),
    ]
    ranked = rank_setting(hits, diversify=True, limit=3)
    # Item 100 dominates by raw score but diversification must surface item
    # 200's representative within the top slots once more than one item exists.
    assert {hit.item_id for hit in ranked} == {100, 200}


def test_rank_setting_diversify_off_is_a_plain_score_sort() -> None:
    hits = [RankedHit(1, 100, 0.2), RankedHit(2, 100, 0.9), RankedHit(3, 200, 0.5)]
    ranked = rank_setting(hits, diversify=False, limit=10)
    assert [hit.segment_id for hit in ranked] == [2, 3, 1]


def test_covered_sentences_and_compute_metrics_full_containment() -> None:
    segment_full_ids = {1: ["s1"], 2: ["s2", "s3"], 3: [], 4: ["s4"]}
    gold = frozenset({"s1", "s2", "s3", "s4"})
    ranked = [RankedHit(2, 100, 1.0), RankedHit(1, 100, 0.9), RankedHit(4, 300, 0.8)]

    assert covered_sentences([2, 1, 4], segment_full_ids, 2) == frozenset({"s2", "s3", "s1"})

    metrics = compute_metrics(
        ranked, segment_full_ids=segment_full_ids, gold=gold, gold_chunk_count=3
    )
    assert metrics["hit@1"] == 1.0
    assert metrics["recall@1"] == 0.5  # segment 2 alone covers s2, s3
    assert metrics["recall@3"] == 1.0
    assert metrics["rr"] == 1.0  # first ranked chunk already covers gold


def test_gold_bearing_chunks_restricts_to_gold_intersection() -> None:
    segments = [
        {"full_sentence_ids": ["s1"]},
        {"full_sentence_ids": ["s2", "s3"]},
        {"full_sentence_ids": ["s4"]},
        {"full_sentence_ids": []},
    ]
    gold = frozenset({"s1", "s2", "s3"})
    chunks = gold_bearing_chunks(segments, gold)
    assert sorted(frozenset().union(*chunks)) == ["s1", "s2", "s3"]
    assert len(chunks) == 2  # the s4-only and empty chunks are excluded


def test_production_spec_by_scope_returns_raw_and_diversify_on_per_scope() -> None:
    specs = enumerate_settings()
    by_scope = production_spec_by_scope(specs)
    assert set(by_scope) == {"A", "B"}
    assert by_scope["A"].setting_id == "raw-AND-div-on-scope-A"
    assert by_scope["B"].setting_id == "raw-AND-div-on-scope-B"


def test_build_comparisons_pairs_each_scope_with_its_own_production_setting() -> None:
    # Regression for the bug where every scope-A row was compared against the
    # scope-B baseline, so a scope-A delta mostly reported the scope-A
    # "known meeting" effect rather than the method/diversification change.
    # Here, lexical-AND in scope A has *identical* per-case metrics to the
    # scope-A production setting (no method effect at all), so its within-
    # scope delta must be exactly zero -- not the ~+0.4 scope effect you would
    # get by diffing against the scope-B baseline.
    specs = enumerate_settings()
    metrics_by_setting = {
        "raw-AND-div-on-scope-B": {"recall@5": [0.0, 0.2], "hit@5": [0.0, 1.0]},
        "raw-AND-div-on-scope-A": {"recall@5": [0.4, 0.6], "hit@5": [1.0, 1.0]},
        "lexical-AND-div-on-scope-A": {"recall@5": [0.4, 0.6], "hit@5": [1.0, 1.0]},
        "rrf-AND-div-on-scope-B": {"recall@5": [0.0, 0.4], "hit@5": [0.0, 1.0]},
    }

    comparisons, cross_scope = build_comparisons(metrics_by_setting, specs)

    # Each scope's own production setting is never compared to itself.
    assert "raw-AND-div-on-scope-A" not in comparisons
    assert "raw-AND-div-on-scope-B" not in comparisons

    lexical_a = comparisons["lexical-AND-div-on-scope-A"]["recall@5"]["bootstrap"]
    assert lexical_a["mean_difference"] == 0.0

    rrf_b = comparisons["rrf-AND-div-on-scope-B"]["recall@5"]["bootstrap"]
    assert abs(rrf_b["mean_difference"] - 0.1) < 1e-12

    # The one explicit cross-scope comparison still reports the scope effect.
    cross_recall5 = cross_scope["recall@5"]["bootstrap"]
    assert abs(cross_recall5["mean_difference"] - 0.4) < 1e-12


def test_rq1_case_ids_match_the_frozen_research_selection() -> None:
    assert len(RQ1_CASE_IDS) == 30
    assert len(set(RQ1_CASE_IDS)) == 30
    assert list(RQ1_CASE_IDS) == sorted(RQ1_CASE_IDS)

    rows = json.loads(_RESEARCH_FIXTURE.read_text(encoding="utf-8"))
    assert sorted(row["case_id"] for row in rows) == list(RQ1_CASE_IDS)
