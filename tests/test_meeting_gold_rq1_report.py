from __future__ import annotations

from evals.meeting_gold.core import Case, MeetingCorpus, Sentence, stable_sentence_id
from evals.meeting_gold.report import (
    _default_diagnosis,
    _global_segment_index,
    _r7_summary,
    attach_case_metric_lookup,
    dense_candidate_stats,
    render_ablation,
    render_attribution,
    render_failure_cases,
    render_setup_md,
    select_failure_cases,
    top_settings_by_recall5,
)
from evals.meeting_gold.retrieval_study import (
    build_comparisons,
    enumerate_settings,
    production_spec_by_scope,
)


def _synthetic_result(metrics_by_setting: dict[str, dict[str, list[float]]]) -> dict:
    """Build a minimal but real ``run_rq1``-shaped result from per-case metrics.

    Exercises the real ``build_comparisons``/``production_spec_by_scope``
    helpers rather than hand-rolled comparison dicts, so a regression in how
    ``report.py`` consumes them is caught here too.
    """

    specs = enumerate_settings()
    comparisons, cross_scope = build_comparisons(metrics_by_setting, specs)
    aggregates = {
        setting_id: {
            metric_name: {"mean": sum(values) / len(values), "n": len(values)}
            for metric_name, values in metrics.items()
        }
        for setting_id, metrics in metrics_by_setting.items()
    }
    return {
        "settings": [
            {
                "setting_id": spec.setting_id,
                "method": spec.method,
                "lexical_variant": spec.lexical_variant,
                "diversify": spec.diversify,
                "scope": spec.scope,
            }
            for spec in specs
            if spec.setting_id in metrics_by_setting
        ],
        "baseline_setting_id": "raw-AND-div-on-scope-B",
        "scope_production_setting_ids": {
            scope: spec.setting_id for scope, spec in production_spec_by_scope(specs).items()
        },
        "aggregates": aggregates,
        "comparisons": comparisons,
        "cross_scope_comparison": cross_scope,
        "per_case": [],
    }


def _ablation_row_columns(ablation_md: str, *, label_prefix: str) -> list[str]:
    line = next(line for line in ablation_md.splitlines() if label_prefix in line)
    return [cell.strip() for cell in line.split("|")]


def test_render_ablation_compares_scope_a_rows_to_the_scope_a_baseline() -> None:
    # Regression for the bug where every row's delta came from the scope-B
    # baseline: lexical-AND in scope A has identical per-case metrics to the
    # scope-A production setting, so its displayed delta must be ~0, not the
    # ~+0.4 scope effect that diffing against scope B would show.
    result = _synthetic_result(
        {
            "raw-AND-div-on-scope-B": {"recall@5": [0.0, 0.2], "hit@5": [0.0, 1.0]},
            "raw-AND-div-on-scope-A": {"recall@5": [0.4, 0.6], "hit@5": [1.0, 1.0]},
            "lexical-AND-div-on-scope-A": {"recall@5": [0.4, 0.6], "hit@5": [1.0, 1.0]},
        }
    )

    ablation_md, _ = render_ablation(result)

    lexical_cols = _ablation_row_columns(ablation_md, label_prefix="| lexical-AND")
    # columns: '', Setting, Scope, n, Recall@5, Hit@5, DeltaRecall@5, CI, McNemar p, ''
    assert lexical_cols[6] == "0.000"

    cross_cols = _ablation_row_columns(ablation_md, label_prefix="| scope B -> A")
    assert cross_cols[6] == "0.400"


def test_render_attribution_scope_b_to_a_bar_uses_cross_scope_comparison() -> None:
    result = _synthetic_result(
        {
            "raw-AND-div-on-scope-B": {"recall@5": [0.0, 0.2], "hit@5": [0.0, 1.0]},
            "raw-AND-div-on-scope-A": {"recall@5": [0.4, 0.6], "hit@5": [1.0, 1.0]},
        }
    )

    svg, csv_text = render_attribution(result)

    # The SVG text node properly entity-escapes ">" rather than stripping it,
    # so the arrow in "scope B -> A" is not silently rendered as "scope B - A".
    assert "scope B -&gt; A" in svg
    assert "+0.400" in svg
    assert "scope B -> A,0.4" in csv_text


def _result_with_recall5(values: dict[str, float]) -> dict:
    specs = enumerate_settings()
    aggregates = {
        setting_id: {"recall@5": {"mean": value, "n": 28}} for setting_id, value in values.items()
    }
    baseline_value = values["raw-AND-div-on-scope-B"]
    comparisons = {
        setting_id: {"recall@5": {"bootstrap": {"mean_difference": value - baseline_value}}}
        for setting_id, value in values.items()
        if setting_id != "raw-AND-div-on-scope-B"
    }
    return {
        "settings": [
            {
                "setting_id": spec.setting_id,
                "method": spec.method,
                "lexical_variant": spec.lexical_variant,
                "diversify": spec.diversify,
                "scope": spec.scope,
            }
            for spec in specs
        ],
        "baseline_setting_id": "raw-AND-div-on-scope-B",
        "aggregates": aggregates,
        "comparisons": comparisons,
    }


def test_top_settings_by_recall5_any_scope_can_surface_scope_a() -> None:
    # Scope A settings are typically far ahead of scope B (the known-meeting
    # gain); an unscoped ranking should surface them.
    result = _result_with_recall5(
        {
            "raw-AND-div-on-scope-B": 0.14,
            "minmax-OR-div-on-scope-A": 0.44,
            "rrf-OR-div-on-scope-B": 0.25,
        }
    )
    top = top_settings_by_recall5(result, limit=1)
    assert top[0]["setting_id"] == "minmax-OR-div-on-scope-A"


def test_top_settings_by_recall5_scope_b_excludes_scope_a_entries() -> None:
    # The attribution chart's "best combined setting" bar must not just
    # rediscover the separate scope-B-to-A bar: restricting to scope B must
    # never return a scope-A setting_id, even when scope-A dominates overall.
    result = _result_with_recall5(
        {
            "raw-AND-div-on-scope-B": 0.14,
            "minmax-OR-div-on-scope-A": 0.44,
            "rrf-OR-div-off-scope-B": 0.25,
        }
    )
    top = top_settings_by_recall5(result, limit=1, scope="B")
    assert len(top) == 1
    assert top[0]["setting_id"] == "rrf-OR-div-off-scope-B"
    assert top[0]["scope"] == "B"


def _manifest_with_two_meetings() -> dict:
    return {
        "meetings": {
            "m1": {
                "item_id": 1,
                "segments": [
                    {"id": 10, "text": "m1 segment text", "full_sentence_ids": [], "partial_sentence_ids": []},
                ],
            },
            "m2": {
                "item_id": 2,
                "segments": [
                    {"id": 20, "text": "m2 distractor text", "full_sentence_ids": [], "partial_sentence_ids": []},
                ],
            },
        }
    }


def test_global_segment_index_spans_every_meeting() -> None:
    index = _global_segment_index(_manifest_with_two_meetings())
    assert index[10]["meeting_id"] == "m1"
    assert index[10]["text"] == "m1 segment text"
    assert index[20]["meeting_id"] == "m2"
    assert index[20]["text"] == "m2 distractor text"


def _gold_case() -> Case:
    meeting_id = "m1"
    return Case(
        case_id=f"qmsum:{meeting_id}:specific:0",
        meeting_id=meeting_id,
        split="val",
        domain="Academic(icsi)",
        query_kind="specific",
        query_index=0,
        query="what happened",
        answer="gold answer",
        evidence_groups=[[stable_sentence_id(meeting_id, 0, 0)]],
        evidence_types=[["CES"]],
        answer_sentence_index=[[0]],
        answer_sentence_texts=["gold answer"],
        lifecycle_state="citation_scorable",
        eligibility={"ces": "citation_scorable", "ces_pes": "citation_scorable"},
    )


def test_render_failure_cases_attributes_cross_meeting_chunks_correctly() -> None:
    # Scope B is tenant-wide: a zero-recall case's top chunks can legitimately
    # come from a distractor meeting. The render must show that meeting's own
    # id/text, not blank out the row or mislabel it as the case's meeting.
    case = _gold_case()
    sentence = Sentence(
        id=stable_sentence_id("m1", 0, 0),
        meeting_id="m1",
        turn_index=0,
        sentence_index=0,
        speaker="A",
        text="Gold sentence text.",
    )
    bundle = {"cases": [case], "corpus": {"m1": MeetingCorpus([sentence])}}
    manifest = _manifest_with_two_meetings()
    baseline_id = "raw-AND-div-on-scope-B"
    result = {
        "baseline_setting_id": baseline_id,
        "library_manifest": manifest,
        "per_case": [
            {
                "case_id": case.case_id,
                "domain": case.domain,
                "eligible": True,
                "gold_sentence_ids": [sentence.id],
                "ceiling": {"5": {"recall": 1.0, "hit": 1.0, "lower_bound": False}},
                "settings": {
                    baseline_id: {
                        "segment_ids": [20],  # the distractor meeting's segment, not m1's
                        "scores": [0.9],
                        "metrics": {"recall@5": 0.0, "hit@5": 0.0},
                    }
                },
            }
        ],
    }
    attach_case_metric_lookup(result)
    selected = select_failure_cases(result)
    assert len(selected) == 1
    assert selected[0]["case_id"] == case.case_id

    rendered = render_failure_cases(result, bundle)
    assert "| 1 | m2 | 0.9000 | m2 distractor text |" in rendered
    assert "different meeting than `m1`" in rendered


def test_default_diagnosis_zero_candidates_is_distinct_from_wrong_meeting() -> None:
    zero_candidates = _default_diagnosis(
        baseline_hit5=0.0, wrong_meeting_hits=0, top5_count=0, case_meeting_id="m1"
    )
    assert "zero candidates" in zero_candidates
    assert "ANN diagnostic" in zero_candidates

    wrong_meeting = _default_diagnosis(
        baseline_hit5=0.0, wrong_meeting_hits=5, top5_count=5, case_meeting_id="m1"
    )
    assert "different meeting" in wrong_meeting
    assert wrong_meeting != zero_candidates


def test_dense_candidate_stats_flags_approx_pool_shortfall_per_scope() -> None:
    # Regression for the R7 finding that scope B's approximate HNSW dense
    # query often returns fewer than the requested pool_limit candidates
    # (and sometimes zero), while scope A's item-scoped exact sort and the
    # forced exact scan return the full pool.
    result = {
        "pool_limit": 50,
        "per_case": [
            {"dense_candidate_counts": {"A": 50, "B": 7, "B_exact": 50}},
            {"dense_candidate_counts": {"A": 50, "B": 0, "B_exact": 48}},
            {"dense_candidate_counts": {"A": 50, "B": 9, "B_exact": 50}},
        ],
    }
    stats = dense_candidate_stats(result, pool_limit=50)
    assert stats["A"]["n"] == 3
    assert stats["A"]["n_under_requested"] == 0
    assert stats["B_approx"]["n"] == 3
    assert stats["B_approx"]["n_under_10"] == 3
    assert stats["B_approx"]["n_empty"] == 1
    assert stats["B_exact"]["n_under_10"] == 0


def test_render_setup_md_reports_dense_candidate_pool_caveat() -> None:
    manifest = {
        "meetings": {},
        "git_sha": "abc123",
        "chunker_fingerprint": "fp",
    }
    result = {
        "library_manifest": manifest,
        "eligible_case_ids": ["a"],
        "ineligible_case_ids": [],
        "pool_limit": 50,
        "per_case": [
            {"dense_candidate_counts": {"A": 50, "B": 7, "B_exact": 50}},
        ],
    }
    setup_md = render_setup_md(result, dataset_hash="deadbeef")
    assert "dense candidate pool size" in setup_md.lower()
    assert "scope B dense (tenant-wide, production plan)" in setup_md
    assert "| scope B dense (tenant-wide, production plan) | 1 | 50 | 7 | 7 | 7 | 7 | 1 | 1 | 0 |" in setup_md


def test_r7_summary_flags_empty_approx_nonempty_exact_cases() -> None:
    records = [
        {
            "case_id": "a",
            "differs": True,
            "approx_top10": [],
            "exact_top10": [1, 2, 3],
            "approx_recall@5": 0.0,
            "exact_recall@5": 0.4,
        },
        {
            "case_id": "b",
            "differs": False,
            "approx_top10": [1, 2],
            "exact_top10": [1, 2],
            "approx_recall@5": 0.5,
            "exact_recall@5": 0.5,
        },
    ]
    summary = _r7_summary(records)
    assert summary["cases"] == 2
    assert summary["differs"] == 1
    assert summary["empty_approx_nonempty_exact_cases"] == ["a"]
