from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import evals.meeting_gold.retrieval_study as rs
from app.retrieval import fusion as app_fusion
from evals.meeting_gold.retrieval_study import (
    ONLINE_PER_ITEM_CAP,
    POOL_LIMIT,
    RQ1_CASE_IDS,
    RRF_K,
    FusionConfig,
    RankedHit,
    SettingSpec,
    StudyError,
    build_comparisons,
    compute_metrics,
    covered_sentences,
    enumerate_settings,
    fuse_for_method,
    fuse_minmax,
    fuse_raw,
    fuse_rrf,
    fuse_single,
    gold_bearing_chunks,
    production_spec_by_scope,
    r2_equivalence_check,
    rank_for_setting,
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
    # 9 method/variant combinations x 2 diversify x 2 scope, plus the capped
    # RRF window for AND/OR x 2 scope.
    assert len(specs) == 36 + 4
    assert len({spec.setting_id for spec in specs}) == 36 + 4
    baselines = [spec for spec in specs if spec.is_baseline]
    assert len(baselines) == 1
    assert baselines[0].setting_id == "raw-AND-div-on-scope-B"


def test_matrix_contains_the_capped_rrf_settings_for_both_scopes() -> None:
    specs = {spec.setting_id: spec for spec in enumerate_settings()}
    for scope in ("A", "B"):
        for variant in ("AND", "OR"):
            spec = specs[f"rrf-{variant}-cap3-scope-{scope}"]
            assert spec.per_item_cap == 3
            assert spec.diversify is False
            assert spec.method == "rrf"
            assert spec.lexical_variant == variant
    # No diversify twin exists for a capped setting, and no uncapped setting
    # gained a cap.
    capped = [spec for spec in specs.values() if spec.per_item_cap is not None]
    assert len(capped) == 4
    online = [spec for spec in specs.values() if spec.is_online_candidate]
    assert sorted(spec.setting_id for spec in online) == [
        "rrf-AND-cap3-scope-A",
        "rrf-AND-cap3-scope-B",
    ]


def test_online_cap_and_pool_match_the_production_defaults() -> None:
    # The study's window constants must equal the production defaults, or the
    # R2 equivalence check for the new online setting cannot hold.
    default = FusionConfig()
    assert ONLINE_PER_ITEM_CAP == default.per_item_cap
    assert POOL_LIMIT == default.candidate_pool_limit
    assert RRF_K == default.rrf_k
    assert default.lexical_weight == default.vector_weight == 1.0
    assert default.method == "rrf"


def test_fuse_single_keeps_best_score_per_segment() -> None:
    hits = [RankedHit(1, 100, 0.2), RankedHit(1, 100, 0.9), RankedHit(2, 100, 0.5)]
    fused = fuse_single(hits)
    by_id = {hit.segment_id: hit.score for hit in fused}
    assert by_id == {1: 0.9, 2: 0.5}


def test_fusion_functions_are_the_production_implementations() -> None:
    # The study must not keep a second copy of the fusion rules.
    assert fuse_rrf is app_fusion.fuse_rrf
    assert fuse_raw is app_fusion.fuse_raw
    assert rs.select_window is app_fusion.select_window


def test_fuse_raw_concatenates_and_keeps_max_score() -> None:
    lexical = [RankedHit(1, 100, 0.9), RankedHit(2, 100, 0.4)]
    dense = [RankedHit(2, 100, 0.95), RankedHit(3, 200, 0.7)]
    fused = fuse_raw([lexical, dense])
    by_id = {hit.segment_id: hit.score for hit in fused}
    assert by_id == {1: 0.9, 2: 0.95, 3: 0.7}


def test_fuse_rrf_scores_match_reciprocal_rank_formula() -> None:
    lexical = [RankedHit(1, 100, 0.9), RankedHit(2, 100, 0.4)]
    dense = [RankedHit(2, 100, 0.95), RankedHit(3, 200, 0.7)]
    fused = {
        hit.segment_id: hit.score
        for hit in fuse_rrf([lexical, dense], k=60, weights=[1.0, 1.0])
    }
    # segment 1: lexical rank 1 only -> 1/(60+1)
    assert fused[1] == 1 / 61
    # segment 2: lexical rank 2, dense rank 1 -> 1/62 + 1/61
    assert abs(fused[2] - (1 / 62 + 1 / 61)) < 1e-12
    # segment 3: dense rank 2 only -> 1/62
    assert fused[3] == 1 / 62


def test_fuse_for_method_dispatches_to_the_production_fusion() -> None:
    lex_and = [RankedHit(1, 100, 3.0), RankedHit(2, 100, 1.0)]
    lex_or = [RankedHit(1, 100, 3.0), RankedHit(2, 100, 1.0), RankedHit(4, 300, 0.5)]
    dense = [RankedHit(2, 100, 0.95), RankedHit(3, 200, 0.7)]

    def fused(method: str, variant: str | None) -> list[RankedHit]:
        return fuse_for_method(
            method, lex_and=lex_and, lex_or=lex_or, dense=dense, lexical_variant=variant
        )

    assert fused("rrf", "AND") == app_fusion.fuse_rrf(
        [lex_and, dense], k=RRF_K, weights=[1.0, 1.0]
    )
    assert fused("rrf", "OR") == app_fusion.fuse_rrf(
        [lex_or, dense], k=RRF_K, weights=[1.0, 1.0]
    )
    assert fused("raw", "AND") == app_fusion.fuse_raw([lex_and, dense])
    assert fused("raw", "OR") == app_fusion.fuse_raw([lex_or, dense])
    with pytest.raises(StudyError, match="unknown_method:nope"):
        fused("nope", "AND")


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


def test_rank_setting_per_item_cap_limits_each_video_in_fused_order() -> None:
    hits = [
        RankedHit(1, 100, 0.9),
        RankedHit(2, 100, 0.8),
        RankedHit(3, 100, 0.7),
        RankedHit(4, 100, 0.6),
        RankedHit(5, 200, 0.5),
        RankedHit(6, 300, 0.4),
    ]
    ranked = rank_setting(hits, diversify=False, limit=10, per_item_cap=3)
    # Item 100 keeps its three best segments in the first pass; its fourth is
    # held back and only fills the window after the other videos' hits.
    assert [hit.segment_id for hit in ranked] == [1, 2, 3, 5, 6, 4]
    # With a limit the first pass fills, the held-back hit never appears.
    short = rank_setting(hits, diversify=False, limit=5, per_item_cap=3)
    assert [hit.segment_id for hit in short] == [1, 2, 3, 5, 6]


def test_rank_setting_per_item_cap_sorts_by_score_then_segment_id() -> None:
    # Input order must not matter; ties break by segment id like ``fuse_rrf``.
    hits = [RankedHit(9, 100, 0.5), RankedHit(3, 200, 0.5), RankedHit(7, 100, 0.9)]
    ranked = rank_setting(hits, diversify=False, limit=10, per_item_cap=3)
    assert [hit.segment_id for hit in ranked] == [7, 3, 9]


def test_rank_setting_per_item_cap_respects_the_total_limit() -> None:
    hits = [RankedHit(i, 100 + i, 1.0 - i / 100) for i in range(1, 8)]
    ranked = rank_setting(hits, diversify=False, limit=4, per_item_cap=3)
    assert [hit.segment_id for hit in ranked] == [1, 2, 3, 4]


def test_rank_setting_cap_equal_to_limit_is_a_plain_score_sort() -> None:
    hits = [RankedHit(1, 100, 0.2), RankedHit(2, 100, 0.9), RankedHit(3, 100, 0.5)]
    capped = rank_setting(hits, diversify=False, limit=10, per_item_cap=10)
    plain = rank_setting(hits, diversify=False, limit=10)
    assert [hit.segment_id for hit in capped] == [hit.segment_id for hit in plain] == [2, 3, 1]


def test_rank_setting_rejects_a_cap_together_with_diversify() -> None:
    with pytest.raises(StudyError, match="per_item_cap_requires_diversify_off"):
        rank_setting([RankedHit(1, 100, 0.5)], diversify=True, per_item_cap=3)


def test_rank_for_setting_applies_the_cap_to_the_rrf_ranking() -> None:
    # Five segments of item 100 lead both lists; RRF keeps them ahead of the
    # other videos, and the cap then trims item 100 to three.
    dense = tuple(RankedHit(i, 100, 0.9 - i / 100) for i in range(1, 6)) + (
        RankedHit(6, 200, 0.4),
    )
    lexical = tuple(RankedHit(i, 100, 5.0 - i) for i in range(1, 6)) + (
        RankedHit(7, 300, 0.1),
    )
    pools = rs.CandidatePools(lex_and=lexical, lex_or=lexical, dense=dense)
    specs = {spec.setting_id: spec for spec in enumerate_settings()}

    capped = rank_for_setting(pools, specs["rrf-AND-cap3-scope-B"])
    uncapped = rank_for_setting(pools, specs["rrf-AND-div-off-scope-B"])

    assert [hit.segment_id for hit in uncapped][:5] == [1, 2, 3, 4, 5]
    assert [hit.segment_id for hit in capped][:3] == [1, 2, 3]
    # The first pass holds three hits of item 100 and one each of the other
    # videos; the held-back hits of item 100 follow it.
    assert [hit.item_id for hit in capped[:5]].count(100) == 3
    assert {hit.item_id for hit in capped[:5]} == {100, 200, 300}
    assert len(capped) == len(uncapped) == 7


def test_rank_for_setting_cap_equals_the_uncapped_ranking_for_a_single_video() -> None:
    # Scope A (one video): the soft cap fills by ranking, so the capped window
    # is the uncapped ranking.
    dense = tuple(RankedHit(i, 100, 0.9 - i / 100) for i in range(1, 13))
    lexical = tuple(RankedHit(i, 100, 5.0 - i / 10) for i in range(3, 9))
    pools = rs.CandidatePools(lex_and=lexical, lex_or=lexical, dense=dense)
    specs = {spec.setting_id: spec for spec in enumerate_settings()}

    capped = rank_for_setting(pools, specs["rrf-AND-cap3-scope-A"])
    uncapped = rank_for_setting(pools, specs["rrf-AND-div-off-scope-A"])

    assert [hit.segment_id for hit in capped] == [hit.segment_id for hit in uncapped]
    assert len(capped) == 10


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


def test_build_comparisons_pairs_the_capped_settings_with_the_scope_baseline() -> None:
    specs = enumerate_settings()
    metrics_by_setting = {
        "raw-AND-div-on-scope-B": {"recall@5": [0.0, 0.2], "hit@5": [0.0, 1.0]},
        "raw-AND-div-on-scope-A": {"recall@5": [0.4, 0.6], "hit@5": [1.0, 1.0]},
        "rrf-AND-cap3-scope-B": {"recall@5": [0.2, 0.4], "hit@5": [1.0, 1.0]},
        "rrf-AND-cap3-scope-A": {"recall@5": [0.2, 0.6], "hit@5": [1.0, 1.0]},
    }

    comparisons, _ = build_comparisons(metrics_by_setting, specs)

    b = comparisons["rrf-AND-cap3-scope-B"]
    assert abs(b["recall@5"]["bootstrap"]["mean_difference"] - 0.2) < 1e-12
    assert b["hit@5"]["mcnemar"]["p_value"] is not None
    a = comparisons["rrf-AND-cap3-scope-A"]
    # Scope A is paired with the scope-A baseline: (-0.2 + 0.0) / 2.
    assert abs(a["recall@5"]["bootstrap"]["mean_difference"] + 0.1) < 1e-12


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


# ------------------------------------------------- R2 equivalence (fake services)
class _FakeServices:
    """Stands in for ``KnowledgeServices``: records the fusion config it got."""

    instances: list["_FakeServices"] = []
    returned_segment_ids: list[int] = []

    def __init__(self, tenant: Any, factory: Any, *, embedder: Any, fusion: Any = None) -> None:
        self.fusion = fusion
        self.calls: list[tuple[str, int, int | None]] = []
        _FakeServices.instances.append(self)

    def search_segments(self, query: str, *, limit: int, item_id: int | None) -> list[Any]:
        self.calls.append((query, limit, item_id))
        return [SimpleNamespace(segment_id=value) for value in _FakeServices.returned_segment_ids]


@pytest.fixture
def fake_services(monkeypatch: pytest.MonkeyPatch) -> type[_FakeServices]:
    _FakeServices.instances = []
    _FakeServices.returned_segment_ids = []
    monkeypatch.setattr("app.agent.services.KnowledgeServices", _FakeServices)
    return _FakeServices


def _check(fusion: FusionConfig | None, expected: list[int], *, item_id: int | None = None):
    return r2_equivalence_check(
        lambda: None,
        tenant=object(),
        cached_embedder=object(),  # type: ignore[arg-type]
        query="q",
        item_id=item_id,
        expected_ranked=[RankedHit(value, 1, 0.5) for value in expected],
        fusion=fusion,
    )


def test_r2_check_passes_and_forwards_the_fusion_config(
    fake_services: type[_FakeServices],
) -> None:
    fake_services.returned_segment_ids = [5, 3, 9]

    equal, expected, actual = _check(FusionConfig(method="raw"), [5, 3, 9], item_id=7)

    assert (equal, expected, actual) == (True, [5, 3, 9], [5, 3, 9])
    (service,) = fake_services.instances
    assert service.fusion == FusionConfig(method="raw")
    assert service.calls == [("q", rs.SEARCH_RESULT_LIMIT, 7)]


def test_r2_check_defaults_to_the_default_service_config(
    fake_services: type[_FakeServices],
) -> None:
    fake_services.returned_segment_ids = [1]

    assert _check(None, [1])[0] is True

    # ``None`` is passed through; ``KnowledgeServices`` then builds the default.
    assert fake_services.instances[0].fusion is None


def test_r2_check_fails_on_a_different_ranking_or_a_different_length(
    fake_services: type[_FakeServices],
) -> None:
    fake_services.returned_segment_ids = [5, 9, 3]
    assert _check(FusionConfig(), [5, 3, 9]) == (False, [5, 3, 9], [5, 9, 3])

    # The capped window returns fewer rows than an uncapped ranking: not equal.
    fake_services.returned_segment_ids = [5, 3]
    assert _check(FusionConfig(), [5, 3, 9]) == (False, [5, 3, 9], [5, 3])


@pytest.mark.parametrize("scope", ["A", "B"])
def test_equivalence_config_per_setting(scope: str) -> None:
    by_id = {spec.setting_id: spec for spec in enumerate_settings()}

    legacy = rs._equivalence_fusion(by_id[f"raw-AND-div-on-scope-{scope}"])
    online = rs._equivalence_fusion(by_id[f"rrf-AND-cap3-scope-{scope}"])

    assert legacy == FusionConfig(method="raw")
    assert online == FusionConfig()
    others = [
        spec
        for spec in by_id.values()
        if spec.setting_id
        not in {f"raw-AND-div-on-scope-{scope}", f"rrf-AND-cap3-scope-{scope}"}
        and spec.scope == scope
    ]
    assert all(rs._equivalence_fusion(spec) is None for spec in others)


def test_setting_spec_without_a_cap_keeps_its_legacy_id() -> None:
    assert SettingSpec("raw", "AND", True, "B").setting_id == "raw-AND-div-on-scope-B"
    assert SettingSpec("dense", None, False, "A").setting_id == "dense-div-off-scope-A"
    assert (
        SettingSpec("rrf", "AND", False, "B", per_item_cap=3).setting_id
        == "rrf-AND-cap3-scope-B"
    )
