"""End-to-end ``run_rq1`` + report rendering over fakes (no database, no embedding calls).

Every database/embedding boundary is replaced, so this exercises the orchestration
loop (split library, kind/pool/localization fields, exclusions, per-kind
comparisons) and the new report files without touching Neon.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evals.meeting_gold import production_path, report
from evals.meeting_gold import retrieval_study as rs
from evals.meeting_gold.core import Case, MeetingCorpus, Sentence, stable_sentence_id
from evals.meeting_gold.retrieval_study import CandidatePools, FusionConfig, RankedHit

_DATASET_HASH = "d" * 64
_SCORABLE = {"ces": "citation_scorable", "ces_pes": "citation_scorable"}

CASE_A = "qmsum:m1:specific:0"  # eligible, scope-B top-1 is the right meeting
CASE_B = "qmsum:m2:general:0"  # eligible, scope-B top-1 is a different-source meeting
CASE_C = "qmsum:m3:specific:0"  # ineligible (answer only)
CASE_D = "qmsum:m1:specific:1"  # eligible but excluded (held-out collision)
CASE_IDS = (CASE_A, CASE_B, CASE_C, CASE_D)

_GOLD = {
    CASE_A: frozenset({"g1", "g2"}),
    CASE_B: frozenset({"h1"}),
    CASE_D: frozenset({"g1"}),
}
_ITEM = {"m1": 101, "m2": 102, "m3": 103}
_DOMAIN = {"m1": "Academic(icsi)", "m2": "Academic(icsi)", "m3": "Product(ami)"}


def _case(meeting_id: str, kind: str, index: int, *, eligible: bool) -> Case:
    return Case(
        case_id=f"qmsum:{meeting_id}:{kind}:{index}",
        meeting_id=meeting_id,
        split="test",
        domain=_DOMAIN[meeting_id],
        query_kind=kind,
        query_index=index,
        query=f"query {meeting_id} {kind} {index}",
        answer="answer",
        eligibility=dict(_SCORABLE) if eligible else {"ces": "answer_only", "ces_pes": "answer_only"},
    )


def _bundle() -> dict[str, Any]:
    cases = [
        _case("m1", "specific", 0, eligible=True),
        _case("m2", "general", 0, eligible=True),
        _case("m3", "specific", 0, eligible=False),
        _case("m1", "specific", 1, eligible=True),
        # A val case that must never enter a test-split library.
        Case(
            case_id="qmsum:v1:specific:0",
            meeting_id="v1",
            split="val",
            domain="Academic(icsi)",
            query_kind="specific",
            query_index=0,
            query="val query",
            answer="answer",
        ),
    ]
    corpus = {
        meeting_id: MeetingCorpus(
            [
                Sentence(
                    id=stable_sentence_id(meeting_id, 0, 0),
                    meeting_id=meeting_id,
                    turn_index=0,
                    sentence_index=0,
                    speaker="Alice",
                    text=f"A sentence of {meeting_id}.",
                )
            ]
        )
        for meeting_id in ("m1", "m2", "m3", "v1")
    }
    return {"cases": cases, "corpus": corpus, "dataset_hash": _DATASET_HASH}


def _segment(segment_id: int, item_id: int, full: list[str]) -> dict[str, Any]:
    return {
        "id": segment_id,
        "seq": 0,
        "item_id": item_id,
        "text": f"text of segment {segment_id}",
        "words": 100,
        "boundary_kind": "semantic",
        "full_sentence_ids": full,
        "partial_sentence_ids": [],
    }


def _manifest() -> dict[str, Any]:
    def meeting(meeting_id: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "meeting_id": meeting_id,
            "item_id": _ITEM[meeting_id],
            "segments": segments,
            "boundary_kinds": {"semantic": len(segments)},
        }

    return {
        "git_sha": "abc123",
        "chunker_fingerprint": {"lower_units": {"en": 80}},
        "tenant_user_id": 7,
        "meetings": {
            "m1": meeting("m1", [_segment(1, 101, ["g1"]), _segment(2, 101, ["g2", "x"])]),
            "m2": meeting("m2", [_segment(11, 102, ["h1"]), _segment(12, 102, ["h2"])]),
            "m3": meeting("m3", [_segment(21, 103, ["k1"])]),
        },
    }


def _pools(dense: list[RankedHit], lex_and: list[RankedHit], lex_or: list[RankedHit]) -> CandidatePools:
    return CandidatePools(tuple(lex_and), tuple(lex_or), tuple(dense))


# (query, scoped to one item?) -> pools. A scope-A query is restricted to the
# case's meeting; a scope-B query sees the whole tenant.
_POOLS: dict[tuple[str, bool], CandidatePools] = {
    ("query m1 specific 0", True): _pools(
        [RankedHit(1, 101, 0.9), RankedHit(2, 101, 0.5)],
        [RankedHit(2, 101, 3.0)],
        [RankedHit(2, 101, 3.0)],
    ),
    ("query m1 specific 0", False): _pools(
        [RankedHit(1, 101, 0.9), RankedHit(21, 103, 0.85)],
        [],
        [RankedHit(2, 101, 2.0)],
    ),
    ("query m2 general 0", True): _pools(
        [RankedHit(12, 102, 0.9), RankedHit(11, 102, 0.4)], [], []
    ),
    ("query m2 general 0", False): _pools(
        [RankedHit(21, 103, 0.95), RankedHit(1, 101, 0.9), RankedHit(12, 102, 0.1)], [], []
    ),
    ("query m3 specific 0", True): _pools([RankedHit(21, 103, 0.9)], [], []),
    ("query m3 specific 0", False): _pools([RankedHit(11, 102, 0.9)], [], []),
    ("query m1 specific 1", True): _pools([RankedHit(1, 101, 0.9)], [], []),
    ("query m1 specific 1", False): _pools([RankedHit(11, 102, 0.9)], [], []),
}


@pytest.fixture
def fake_study(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"library_meetings": None}

    monkeypatch.setattr(production_path, "assert_test_environment_active", lambda: None)

    def fake_ingest(bundle: Any, meeting_ids: Any, run_dir: Any, *, settings: Any) -> dict[str, Any]:
        seen["library_meetings"] = list(meeting_ids)
        return _manifest()

    monkeypatch.setattr(production_path, "ingest_library", fake_ingest)
    monkeypatch.setattr(production_path, "session_factory_from_manifest", lambda: object)
    monkeypatch.setattr(
        rs,
        "fetch_candidate_pools",
        lambda ro_factory, query, vector, *, user_id, item_id, pool_limit=50: _POOLS[
            (query, item_id is not None)
        ],
    )
    monkeypatch.setattr(rs, "explain_dense_plan", lambda *a, **k: {"node_types": []})
    monkeypatch.setattr(rs, "fetch_dense_exact", lambda *a, **k: ())
    monkeypatch.setattr(rs, "r2_equivalence_check", lambda *a, **k: (True, [], []))
    monkeypatch.setattr(
        rs,
        "build_query_vector_cache",
        lambda queries, *, embedder, cache_path: {query: [0.0] for query in queries},
    )
    monkeypatch.setattr(
        rs,
        "_validate_case",
        lambda case, **kwargs: SimpleNamespace(gold_ids=_GOLD[case.case_id]),
    )
    monkeypatch.setattr(
        "app.bootstrap.build_embedding_provider",
        lambda settings, trusted_ca=None: SimpleNamespace(dimensions=1),
    )
    monkeypatch.setattr("app.tls.configure_trusted_ca", lambda bundle: None)
    return seen


def _run(tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    return rs.run_rq1(
        _bundle(),
        CASE_IDS,
        tmp_path,
        settings=SimpleNamespace(tls_ca_bundle=None),
        split="test",
        exclusions={CASE_D: rs.HELD_OUT_COLLISION_REASON},
        **kwargs,
    )


def test_run_builds_the_library_from_the_requested_split(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    _run(tmp_path)
    # Only the test-split meetings; the val case's meeting never enters scope B.
    assert fake_study["library_meetings"] == ["m1", "m2", "m3"]


def test_run_rejects_a_case_from_another_split_before_any_ingest(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    with pytest.raises(rs.StudyError, match="case_split_mismatch:qmsum:v1:specific:0"):
        rs.run_rq1(
            _bundle(),
            ("qmsum:v1:specific:0",),
            tmp_path,
            settings=SimpleNamespace(tls_ca_bundle=None),
            split="test",
        )
    assert fake_study["library_meetings"] is None


def test_run_partitions_cases_into_counted_excluded_and_ineligible(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    result = _run(tmp_path)

    assert result["split"] == "test"
    assert result["eligible_case_ids"] == [CASE_A, CASE_B]
    assert result["excluded_case_ids"] == [CASE_D]
    assert result["exclusions"] == {CASE_D: rs.HELD_OUT_COLLISION_REASON}
    assert result["ineligible_case_ids"] == [CASE_C]
    assert [record["case_id"] for record in result["per_case"]] == list(CASE_IDS)
    assert result["r2_failures"] == []
    assert result["scope_a_invariance_failures"] == []


def test_excluded_case_is_recorded_in_full_but_never_aggregated(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    result = _run(tmp_path)
    baseline_id = result["baseline_setting_id"]
    by_case = {record["case_id"]: record for record in result["per_case"]}

    excluded = by_case[CASE_D]
    assert excluded["summary_excluded"] is True
    assert excluded["exclusion_reason"] == rs.HELD_OUT_COLLISION_REASON
    assert excluded["ces_status"] == "citation_scorable"
    assert excluded["query_kind"] == "specific"
    # Retrieved and scored like any eligible case ...
    assert excluded["settings"][baseline_id]["metrics"]["hit@5"] == 0.0
    assert excluded["pool"]["A"]["AND"]["pool_recall"] == 1.0
    # ... but absent from every aggregate and comparison denominator.
    assert result["aggregates"][baseline_id]["hit@5"]["n"] == 2
    assert result["by_kind"]["specific"]["n_cases"] == 1
    assert result["by_kind"]["general"]["n_cases"] == 1
    assert result["by_kind"]["specific"]["aggregates"][baseline_id]["hit@5"]["n"] == 1
    assert by_case[CASE_A]["summary_excluded"] is False
    assert by_case[CASE_A]["exclusion_reason"] is None


def test_per_case_records_carry_kind_status_pool_and_localization(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    result = _run(tmp_path)
    baseline_id = result["baseline_setting_id"]
    scope_a_id = baseline_id.replace("scope-B", "scope-A")
    by_case = {record["case_id"]: record for record in result["per_case"]}

    record_a = by_case[CASE_A]
    assert record_a["query_kind"] == "specific"
    assert record_a["ces_status"] == "citation_scorable"
    assert by_case[CASE_B]["query_kind"] == "general"
    assert by_case[CASE_C]["ces_status"] == "answer_only"

    # Scope A AND pool = dense{1,2} U lex_and{2}: {g1} U {g2} = all gold.
    assert record_a["pool"]["A"]["AND"] == {"pool_size": 2, "pool_hit": 1.0, "pool_recall": 1.0}
    # Scope B AND pool = dense{1,21} U {}: only g1 of {g1, g2}.
    assert record_a["pool"]["B"]["AND"] == {"pool_size": 2, "pool_hit": 1.0, "pool_recall": 0.5}
    # The OR pool adds segment 2 (g2) back, so OR recall is higher.
    assert record_a["pool"]["B"]["OR"]["pool_recall"] == 1.0
    # An ineligible case still has a pool size but no coverage.
    assert by_case[CASE_C]["pool"]["B"]["AND"] == {
        "pool_size": 1,
        "pool_hit": None,
        "pool_recall": None,
    }

    assert record_a["settings"][baseline_id]["localization"] == {
        "top1_empty": False,
        "top1_meeting_correct": 1.0,
        "wrong_top1_meeting_id": None,
        "wrong_top1_same_source": None,
    }
    # Case B: top-1 is a Product (m3) meeting for an Academic (m2) case.
    localization_b = by_case[CASE_B]["settings"][baseline_id]["localization"]
    assert localization_b["wrong_top1_meeting_id"] == "m3"
    assert localization_b["wrong_top1_same_source"] is False
    # Case D (excluded) and C (ineligible) are localized too: same-source (m2/Academic) for D.
    assert by_case[CASE_D]["settings"][baseline_id]["localization"]["wrong_top1_same_source"] is True
    # Localization is a scope-B notion only.
    assert "localization" not in record_a["settings"][scope_a_id]


def test_baseline_metrics_are_unchanged_by_the_new_bookkeeping(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    result = _run(tmp_path)
    baseline_id = result["baseline_setting_id"]
    aggregates = result["aggregates"]
    # Scope B: case A ranks [1, 21] and covers g1 of {g1, g2}; case B's top 3 are
    # [21, 1, 12] and cover no h1.
    assert aggregates[baseline_id]["hit@5"] == {"mean": 0.5, "n": 2}
    assert aggregates[baseline_id]["recall@5"] == {"mean": 0.25, "n": 2}
    # Both cases' own meeting is somewhere in their top 5.
    assert aggregates[baseline_id]["meeting_hit@5"]["mean"] == 1.0
    # Scope A (item-scoped): both cases cover all their gold.
    scope_a = aggregates["raw-AND-div-on-scope-A"]
    assert scope_a["recall@5"] == {"mean": 1.0, "n": 2}
    assert scope_a["hit@5"] == {"mean": 1.0, "n": 2}
    # Per-kind comparisons are paired against the same scope's production setting.
    specific = result["by_kind"]["specific"]
    assert "raw-AND-div-on-scope-B" not in specific["comparisons"]
    assert specific["comparisons"]["raw-AND-div-off-scope-B"]["hit@5"]["n"] == 1
    assert specific["cross_scope_comparison"]["recall@5"]["n"] == 1


# ------------------------------------------- R2 equivalence orchestration
def _record_equivalence(
    monkeypatch: pytest.MonkeyPatch, *, fail_for: FusionConfig | None = None
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(
        ro_factory: Any,
        *,
        tenant: Any,
        cached_embedder: Any,
        query: str,
        item_id: int | None,
        expected_ranked: Any,
        fusion: FusionConfig | None = None,
    ) -> tuple[bool, list[int], list[int]]:
        expected = [hit.segment_id for hit in expected_ranked]
        calls.append(
            {"query": query, "item_id": item_id, "fusion": fusion, "expected": expected}
        )
        if fail_for is not None and fusion == fail_for:
            return False, expected, [*expected, -1]
        return True, expected, expected

    monkeypatch.setattr(rs, "r2_equivalence_check", fake)
    return calls


def test_run_checks_the_legacy_baseline_and_the_online_setting_in_both_scopes(
    fake_study: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _record_equivalence(monkeypatch)

    result = _run(tmp_path)

    # 4 cases x 2 scopes x (legacy raw baseline + new online setting).
    assert len(calls) == 4 * 2 * 2
    raw = [call for call in calls if call["fusion"] == FusionConfig(method="raw")]
    online = [call for call in calls if call["fusion"] == FusionConfig()]
    assert len(raw) == len(online) == 4 * 2
    assert result["r2_failures"] == []

    # Each check is handed the ranking of exactly the setting it validates.
    by_case = {record["case_id"]: record for record in result["per_case"]}
    for case_id in CASE_IDS:
        query = f"query {case_id.split(':')[1]} {case_id.split(':')[2]} {case_id.split(':')[3]}"
        for scope, item_scoped in (("A", True), ("B", False)):
            settings = by_case[case_id]["settings"]
            matching = [
                call
                for call in calls
                if call["query"] == query and (call["item_id"] is not None) == item_scoped
            ]
            expected_by_fusion = {
                call["fusion"]: call["expected"] for call in matching
            }
            assert (
                expected_by_fusion[FusionConfig(method="raw")]
                == settings[f"raw-AND-div-on-scope-{scope}"]["segment_ids"]
            )
            assert (
                expected_by_fusion[FusionConfig()]
                == settings[f"rrf-AND-cap3-scope-{scope}"]["segment_ids"]
            )


def test_online_setting_mismatch_is_reported_with_its_setting_id(
    fake_study: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _record_equivalence(monkeypatch, fail_for=FusionConfig())

    result = _run(tmp_path)

    failures = result["r2_failures"]
    assert len(failures) == 4 * 2
    assert {failure["setting_id"] for failure in failures} == {
        "rrf-AND-cap3-scope-A",
        "rrf-AND-cap3-scope-B",
    }
    assert all(failure["actual"][-1] == -1 for failure in failures)
    # The scope-A diversify invariance is a separate, untouched assertion.
    assert result["scope_a_invariance_failures"] == []


def test_legacy_baseline_mismatch_is_reported_with_its_setting_id(
    fake_study: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _record_equivalence(monkeypatch, fail_for=FusionConfig(method="raw"))

    result = _run(tmp_path)

    assert {failure["setting_id"] for failure in result["r2_failures"]} == {
        "raw-AND-div-on-scope-A",
        "raw-AND-div-on-scope-B",
    }


def test_rq1_command_exits_nonzero_when_the_online_equivalence_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from evals.meeting_gold import __main__ as cli

    ok_result: dict[str, Any] = {
        "r2_failures": [],
        "scope_a_invariance_failures": [],
        "eligible_case_ids": [],
        "excluded_case_ids": [],
    }
    failing = ok_result | {
        "r2_failures": [{"case_id": "c", "scope": "B", "setting_id": "rrf-AND-cap3-scope-B"}]
    }
    monkeypatch.setattr(cli, "load_artifact_bundle", lambda path: {"dataset_hash": "h"})
    monkeypatch.setattr(cli, "resolve_rq1_cases", lambda *a, **k: (("c",), {}))
    monkeypatch.setattr(cli, "_prepared_settings", lambda: object())
    monkeypatch.setattr(report, "write_reports", lambda *a, **k: None)

    for result, expected in ((ok_result, 0), (failing, 2)):
        monkeypatch.setattr(rs, "run_rq1", lambda *a, _r=result, **k: _r)
        assert cli.main(["rq1", "--output", str(tmp_path)]) == expected


def test_capped_window_equals_the_uncapped_ranking_in_scope_a(
    fake_study: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Six segments of one meeting: diversify on/off agree (one item), and the
    # soft cap fills the short window by ranking, so it agrees as well.
    many = _pools(
        [RankedHit(index, 101, 0.9 - index / 100) for index in range(1, 7)],
        [RankedHit(index, 101, 3.0 - index / 10) for index in range(1, 7)],
        [RankedHit(index, 101, 3.0 - index / 10) for index in range(1, 7)],
    )
    monkeypatch.setattr(
        rs,
        "fetch_candidate_pools",
        lambda ro_factory, query, vector, *, user_id, item_id, pool_limit=50: many,
    )

    result = _run(tmp_path)

    assert result["scope_a_invariance_failures"] == []
    case_a = next(r for r in result["per_case"] if r["case_id"] == CASE_A)
    capped = case_a["settings"]["rrf-AND-cap3-scope-A"]["segment_ids"]
    uncapped = case_a["settings"]["rrf-AND-div-off-scope-A"]["segment_ids"]
    assert len(capped) == len(uncapped) == 6
    assert capped == uncapped


def test_a_capped_window_that_differs_from_uncapped_in_scope_a_is_reported(
    fake_study: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    many = _pools(
        [RankedHit(index, 101, 0.9 - index / 100) for index in range(1, 7)],
        [RankedHit(index, 101, 3.0 - index / 10) for index in range(1, 7)],
        [RankedHit(index, 101, 3.0 - index / 10) for index in range(1, 7)],
    )
    monkeypatch.setattr(
        rs,
        "fetch_candidate_pools",
        lambda ro_factory, query, vector, *, user_id, item_id, pool_limit=50: many,
    )
    original = rs.rank_for_setting

    def truncating(pools: Any, spec: Any) -> list[RankedHit]:
        ranked = original(pools, spec)
        # Model the earlier hard cap: a capped single-video list stops at 3.
        return ranked[:3] if spec.per_item_cap is not None and spec.scope == "A" else ranked

    monkeypatch.setattr(rs, "rank_for_setting", truncating)

    result = _run(tmp_path)

    failures = result["scope_a_invariance_failures"]
    assert failures
    assert all(failure["per_item_cap"] == 3 for failure in failures)
    assert {failure["lexical_variant"] for failure in failures} == {"AND", "OR"}
    assert all(len(failure["capped"]) == 3 for failure in failures)
    assert all(len(failure["diversify_off"]) == 6 for failure in failures)


def test_capped_settings_are_paired_with_the_scope_baseline(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    result = _run(tmp_path)

    for setting_id in ("rrf-AND-cap3-scope-B", "rrf-AND-cap3-scope-A"):
        assert result["aggregates"][setting_id]["recall@5"]["n"] == 2
        comparison = result["comparisons"][setting_id]
        assert comparison["hit@5"]["mcnemar"]["p_value"] is not None
        assert "bootstrap" in comparison["recall@5"]
        assert comparison["recall@5"]["win_loss_tie"]


# ------------------------------------------------------------------ reports
def _write(result: dict[str, Any], tmp_path: Path) -> Path:
    report.write_reports(result, _bundle(), tmp_path, dataset_hash=_DATASET_HASH)
    return tmp_path


def test_write_reports_emits_group_summaries_and_per_kind_ablations(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    out = _write(_run(tmp_path), tmp_path)

    for name in (
        "summary_ppt.md",
        "summary_by_group.md",
        "summary_by_group.csv",
        "ablation.md",
        "ablation_specific.md",
        "ablation_specific.csv",
        "ablation_general.md",
        "ablation_general.csv",
        "per_case.jsonl",
        "run_manifest.json",
    ):
        assert (out / name).is_file(), name

    # The excluded case stays in per_case.jsonl with its reason.
    rows = [json.loads(line) for line in (out / "per_case.jsonl").read_text().splitlines()]
    assert [row["case_id"] for row in rows] == list(CASE_IDS)
    excluded_row = next(row for row in rows if row["case_id"] == CASE_D)
    assert excluded_row["summary_excluded"] is True
    assert "_metrics" not in excluded_row

    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["split"] == "test"
    assert manifest["excluded_case_ids"] == [CASE_D]
    assert manifest["exclusions"] == {CASE_D: "held_out_collision"}
    assert manifest["kind_case_counts"] == {"general": 1, "specific": 1}

    setup = (out / "setup.md").read_text()
    assert "Split: `test`" in setup
    assert f"`{CASE_D}` (held_out_collision)" in setup
    assert "2 eligible" in setup


def test_summary_ppt_tables_group_by_scope_and_kind_without_excluded_cases(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    ppt = (_write(_run(tmp_path), tmp_path) / "summary_ppt.md").read_text()

    # Production baseline rows: scope x kind with k/n and pool recall.
    assert "| A | specific | 1 | 1/1 (1.000) |" in ppt
    assert "| A | general | 1 | 1/1 (1.000) |" in ppt
    assert "| A | all | 2 | 2/2 (1.000) |" in ppt
    assert "| B | specific | 1 | 1/1 (1.000) |" in ppt  # case D (also specific) is excluded
    assert "| B | general | 1 | 0/1 (0.000) |" in ppt
    assert "| B | all | 2 | 1/2 (0.500) |" in ppt
    # Candidate-pool columns: scope B specific has pool hit 1/1 and pool recall 0.500.
    specific_b = next(line for line in ppt.splitlines() if line.startswith("| B | specific |"))
    assert specific_b.rstrip().endswith("| 1/1 | 0.500 |")
    assert "Best non-production scope-B setting by overall Recall@5" in ppt
    # Exclusions and the comparability caveat are stated.
    assert "excluded: 1" in ppt and f"`{CASE_D}`" in ppt
    assert "not comparable with Agent end-to-end" in ppt
    # Localization: A is right at top-1, B is wrong (different source).
    assert "| specific | 1 | 1/1 | 1/1 | 0 | 0/0 |" in ppt
    assert "| general | 1 | 0/1 | 1/1 | 1 | 0/1 |" in ppt


def test_summary_by_group_csv_has_one_row_per_scope_and_group(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    out = _write(_run(tmp_path), tmp_path)
    with (out / "summary_by_group.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    # 2 scopes x (all, specific, general, Academic); the only Product case is ineligible.
    assert len(rows) == 2 * 4
    by_key = {(row["scope"], row["group"]): row for row in rows}
    assert by_key[("B", "all")]["n"] == "2"
    assert by_key[("B", "all")]["hit5_k"] == "1"
    assert by_key[("B", "Academic(icsi)")]["n"] == "2"
    assert ("B", "Product(ami)") not in by_key  # the only Product case is ineligible
    assert by_key[("B", "general")]["wrong_top1_same_source_k"] == "0"
    assert by_key[("A", "all")]["top1_meeting_correct_k"] == ""  # localization is scope B only
    assert by_key[("B", "all")]["pool_recall"] == str((0.5 + 0.0) / 2)


def test_per_kind_ablation_files_use_that_kinds_denominator(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    out = _write(_run(tmp_path), tmp_path)
    specific = (out / "ablation_specific.md").read_text()
    assert "specific queries only (n=1)" in specific
    baseline_row = next(line for line in specific.splitlines() if "(baseline)" in line and "| B |" in line)
    cells = [cell.strip() for cell in baseline_row.split("|")]
    assert cells[3] == "1"  # n of the scope-B production row
    assert "general queries only (n=1)" in (out / "ablation_general.md").read_text()


def test_ppt_summary_states_the_agent_vs_direct_query_caveat(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    ppt = (_write(_run(tmp_path), tmp_path) / "summary_ppt.md").read_text()
    # Both differences from the Agent end-to-end numbers: own search terms and
    # one account per meeting.
    assert "own search terms" in ppt
    assert "own account" in ppt
    assert "Picked post hoc" in ppt


def test_excluded_case_that_is_also_ineligible_is_flagged_in_setup_and_deck(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    # A held-out collision case can itself be answer-only/partial (the real
    # test split's Bmr023 case is partial_annotation); it must not read as a
    # twelfth case outside eligible + ineligible.
    result = rs.run_rq1(
        _bundle(),
        CASE_IDS,
        tmp_path,
        settings=SimpleNamespace(tls_ca_bundle=None),
        split="test",
        exclusions={CASE_C: rs.HELD_OUT_COLLISION_REASON},
    )
    assert result["excluded_case_ids"] == [CASE_C]
    assert CASE_C in result["ineligible_case_ids"]
    # Not counted either way: C stays out and D (eligible, not excluded) is counted.
    assert result["eligible_case_ids"] == [CASE_A, CASE_B, CASE_D]

    out = _write(result, tmp_path)
    setup = (out / "setup.md").read_text()
    assert "CES `answer_only`, so also counted among the ineligible cases" in setup
    ppt = (out / "summary_ppt.md").read_text()
    assert "held-out excluded: 1" in ppt
    assert "also counted among the ineligible cases" in ppt


def test_ablation_reports_hit5_numerator_and_wilson_interval(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    out = _write(_run(tmp_path), tmp_path)

    baseline_row = next(
        line for line in (out / "ablation.md").read_text().splitlines() if "(baseline)" in line and "| B |" in line
    )
    cells = [cell.strip() for cell in baseline_row.split("|")]
    assert cells[9] == "1/2"  # scope-B production: 1 of 2 counted cases hit
    assert cells[10].startswith("[") and cells[10].endswith("]")

    with (out / "ablation.csv").open(encoding="utf-8", newline="") as handle:
        rows = {row["setting_id"]: row for row in csv.DictReader(handle)}
    row = rows["raw-AND-div-on-scope-B"]
    assert row["hit@5_k"] == "1"
    low, high = float(row["hit@5_wilson_low"]), float(row["hit@5_wilson_high"])
    assert 0.0 < low < 0.5 < high < 1.0
    assert rows["scope-B-to-A-production"]["hit@5_k"] == "2"


def _localization(*, empty: bool, correct: float, wrong_id: str | None, same: bool | None) -> dict[str, Any]:
    return {
        "top1_empty": empty,
        "top1_meeting_correct": correct,
        "wrong_top1_meeting_id": wrong_id,
        "wrong_top1_same_source": same,
    }


def test_summarize_cases_counts_every_wrong_top1_in_the_same_source_denominator() -> None:
    setting_id = "raw-AND-div-on-scope-B"

    def case(localization: dict[str, Any]) -> dict[str, Any]:
        return {
            "settings": {
                setting_id: {
                    "metrics": {
                        "hit@5": 0.0,
                        "recall@5": 0.0,
                        "rr": 0.0,
                        "ndcg@10": 0.0,
                        "meeting_hit@5": 1.0,
                    },
                    "localization": localization,
                }
            },
            "ceiling": None,
            "pool": {"B": {"AND": {"pool_size": 0, "pool_hit": 0.0, "pool_recall": 0.0}}},
        }

    cases = [
        case(_localization(empty=False, correct=1.0, wrong_id=None, same=None)),
        case(_localization(empty=False, correct=0.0, wrong_id="m2", same=True)),
        case(_localization(empty=False, correct=0.0, wrong_id="m3", same=False)),
        # A wrong first result whose meeting could not be resolved still counts as
        # a wrong top-1; only its same-source flag is undetermined.
        case(_localization(empty=False, correct=0.0, wrong_id=None, same=None)),
        case(_localization(empty=True, correct=0.0, wrong_id=None, same=None)),
    ]
    row = report.summarize_cases(cases, setting_id=setting_id, scope="B", lexical_variant="AND")

    assert row["n"] == 5
    assert row["top1_meeting_correct_k"] == 1
    assert row["top1_empty_n"] == 1
    assert row["wrong_top1_n"] == 3
    assert row["wrong_top1_same_source_k"] == 1
    # Every case is accounted for exactly once.
    assert row["top1_meeting_correct_k"] + row["top1_empty_n"] + row["wrong_top1_n"] == row["n"]


class _FlakyEmbedder:
    def __init__(self, fail_on: int) -> None:
        self.calls = 0
        self._fail_on = fail_on

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls == self._fail_on:
            raise OSError("simulated transient failure")
        return [[float(len(text))] for text in texts]


def test_query_vector_cache_keeps_progress_when_embedding_fails_midway(tmp_path: Path) -> None:
    cache_path = tmp_path / "query_vectors.json"
    queries = ["one", "three", "five!", "seven!!"]

    with pytest.raises(OSError, match="simulated transient failure"):
        rs.build_query_vector_cache(queries, embedder=_FlakyEmbedder(fail_on=3), cache_path=cache_path)

    # The two queries embedded before the failure survive on disk; no temp file is left.
    assert json.loads(cache_path.read_text(encoding="utf-8")) == {"one": [3.0], "three": [5.0]}
    assert not (tmp_path / "query_vectors.json.tmp").exists()

    # The rerun embeds only what is missing.
    resumed = _FlakyEmbedder(fail_on=0)
    vectors = rs.build_query_vector_cache(queries, embedder=resumed, cache_path=cache_path)
    assert resumed.calls == 2
    assert set(vectors) == set(queries)
    assert json.loads(cache_path.read_text(encoding="utf-8")) == vectors

    # A fully cached rerun makes no embedding calls and leaves the file alone.
    idle = _FlakyEmbedder(fail_on=0)
    assert rs.build_query_vector_cache(queries, embedder=idle, cache_path=cache_path) == vectors
    assert idle.calls == 0


def test_ablation_lists_the_capped_settings_but_still_hides_redundant_scope_a_rows(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    out = _write(_run(tmp_path), tmp_path)
    with (out / "ablation.csv").open(encoding="utf-8", newline="") as handle:
        rows = {row["setting_id"]: row for row in csv.DictReader(handle)}

    # The capped window changes scope-A results, so its rows are reported ...
    for setting_id in (
        "rrf-AND-cap3-scope-A",
        "rrf-AND-cap3-scope-B",
        "rrf-OR-cap3-scope-A",
        "rrf-OR-cap3-scope-B",
    ):
        assert setting_id in rows, setting_id
        assert rows[setting_id]["delta_recall@5"] != ""
        assert rows[setting_id]["mcnemar_p_hit@5"] != ""
    assert rows["rrf-AND-cap3-scope-B"]["label"] == "rrf-AND (cap3)"
    # ... while an uncapped scope-A diversify-off row stays folded into its twin.
    assert "raw-AND-div-off-scope-A" not in rows
    assert "raw-AND-div-off-scope-B" in rows


def test_attribution_has_a_bar_for_the_capped_online_setting(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    out = _write(_run(tmp_path), tmp_path)

    attribution = (out / "attribution.csv").read_text()
    assert "raw -> RRF + cap3" in attribution


def test_setup_reports_title_rows_apart_from_segments(
    fake_study: dict[str, Any], tmp_path: Path
) -> None:
    out = _write(_run(tmp_path), tmp_path)

    # The fake library predates title rows, so none are counted.
    setup = (out / "setup.md").read_text()
    assert "Title rows indexed (not transcript segments, not counted above): 0" in setup
