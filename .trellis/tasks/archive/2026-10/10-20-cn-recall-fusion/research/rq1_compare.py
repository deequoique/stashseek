"""Offline: paired comparison of RQ1 settings from a finished run's per_case.jsonl.

Reads ``<run_dir>/per_case.jsonl`` (no database, no provider call) and prints
markdown tables comparing the legacy production setting ``raw/AND/div-on`` with
the new online setting ``rrf/AND/cap3`` and the reference ``rrf/AND/div-off``,
per scope, with paired statistics (McNemar for Hit@5, exact sign test for the
other metrics, percentile bootstrap CI of the mean difference).

Run from the repository root:

    PYTHONPATH=. .venv/bin/python \
      .trellis/tasks/10-20-cn-recall-fusion/research/rq1_compare.py \
      data/meeting_gold/runs/rq1-20261020-rrf
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from evals.meeting_gold.retrieval_study import compare_to_baseline

BASELINE = "raw-AND-div-on"
CANDIDATE = "rrf-AND-cap3"
REFERENCE = "rrf-AND-div-off"
METRICS = (
    ("hit@5", "Hit@5"),
    ("recall@5", "Recall@5"),
    ("rr", "MRR@10"),
    ("ndcg@10", "nDCG@10"),
)
GATE_A_TOLERANCE = 0.02


def sign_test_p(baseline: list[float], other: list[float], eps: float = 1e-9) -> float | None:
    """Two-sided exact sign test over the non-tied pairs (None if all tied)."""

    wins = sum(1 for b, o in zip(baseline, other) if o > b + eps)
    losses = sum(1 for b, o in zip(baseline, other) if o < b - eps)
    n = wins + losses
    if n == 0:
        return None
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def load(run_dir: Path) -> list[dict]:
    rows = [json.loads(line) for line in (run_dir / "per_case.jsonl").read_text().splitlines()]
    return [r for r in rows if r["eligible"] and not r["summary_excluded"]]


def values(cases: list[dict], setting_id: str, metric: str) -> list[float]:
    return [case["settings"][setting_id]["metrics"][metric] for case in cases]


def fmt(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def table(cases: list[dict], scope: str) -> tuple[list[str], dict[str, dict]]:
    base_id = f"{BASELINE}-scope-{scope}"
    lines = [
        f"| Setting | Metric | mean | Δ vs baseline | 95% CI of Δ | p | win/loss/tie |",
        "|---|---|---|---|---|---|---|",
    ]
    stats: dict[str, dict] = {}
    for setting in (BASELINE, CANDIDATE, REFERENCE):
        setting_id = f"{setting}-scope-{scope}"
        for metric, label in METRICS:
            mine = values(cases, setting_id, metric)
            base = values(cases, base_id, metric)
            mean = sum(mine) / len(mine)
            if setting == BASELINE:
                lines.append(f"| {setting} (baseline) | {label} | {mean:.3f} | n/a | n/a | n/a | n/a |")
                continue
            comparison = compare_to_baseline(base, mine, metric_name=metric)
            if metric.startswith("hit@"):
                delta = mean - sum(base) / len(base)
                low = high = None
                p = comparison["mcnemar"]["p_value"]
                wlt = "n/a"
            else:
                boot = comparison["bootstrap"]
                delta, low, high = boot["mean_difference"], boot["ci_low"], boot["ci_high"]
                p = sign_test_p(base, mine)
                w = sum(1 for b, o in zip(base, mine) if o > b + 1e-9)
                l = sum(1 for b, o in zip(base, mine) if o < b - 1e-9)
                wlt = f"{w}/{l}/{len(base) - w - l}"
            ci = "n/a" if low is None else f"[{low:+.3f}, {high:+.3f}]"
            lines.append(
                f"| {setting} | {label} | {mean:.3f} | {delta:+.3f} | {ci} | {fmt(p)} | {wlt} |"
            )
            stats[f"{setting}:{metric}"] = {"delta": delta, "ci": (low, high), "p": p}
    return lines, stats


def main() -> int:
    run_dir = Path(sys.argv[1])
    cases = load(run_dir)
    print(f"Counted cases: {len(cases)} (CES citation-scorable, none held out)\n")
    gate: dict[str, dict] = {}
    for scope in ("A", "B"):
        print(f"### Scope {scope}\n")
        lines, stats = table(cases, scope)
        print("\n".join(lines) + "\n")
        gate[scope] = stats

    # Result counts: a per-video cap trims a single-video (scope A) result list.
    for scope in ("A", "B"):
        for setting in (BASELINE, CANDIDATE, REFERENCE):
            sizes = [len(c["settings"][f"{setting}-scope-{scope}"]["segment_ids"]) for c in cases]
            print(
                f"- results returned, scope {scope}, {setting}: min {min(sizes)}, "
                f"max {max(sizes)}, mean {sum(sizes) / len(sizes):.2f}"
            )
    # Videos per window (needs the library manifest to map segment -> video).
    library_path = run_dir / "library.json"
    if library_path.exists():
        library = json.loads(library_path.read_text())
        seg_item = {
            seg["id"]: meeting["item_id"]
            for meeting in library["meetings"].values()
            for seg in meeting["segments"]
        }
        for scope in ("A", "B"):
            for setting in (BASELINE, CANDIDATE, REFERENCE):
                counts = [
                    len({seg_item.get(i) for i in c["settings"][f"{setting}-scope-{scope}"]["segment_ids"]})
                    for c in cases
                ]
                print(
                    f"- videos per window, scope {scope}, {setting}: "
                    f"min {min(counts)}, max {max(counts)}, mean {sum(counts) / len(counts):.2f}"
                )
    empty_lexical = sum(
        1 for c in cases if not c["settings"]["lexical-AND-div-off-scope-B"]["segment_ids"]
    )
    print(f"- lexical AND returned no hit (scope B): {empty_lexical}/{len(cases)} cases")
    # What the cap would cost in scope A if it were not applied to an
    # item-scoped search: scope A then equals the uncapped RRF window.
    uncapped = values(cases, f"{REFERENCE}-scope-A", "recall@5")
    base = values(cases, f"{BASELINE}-scope-A", "recall@5")
    print(
        f"- scope A uncapped RRF (rrf-AND-div-off) Recall@5 {sum(uncapped) / len(uncapped):.3f} "
        f"vs baseline {sum(base) / len(base):.3f}"
    )

    a = gate["A"][f"{CANDIDATE}:recall@5"]
    b = gate["B"][f"{CANDIDATE}:recall@5"]
    # Gate A: the regression must be within the tolerance AND not significant
    # (the 95% bootstrap CI of the paired difference must not lie below zero).
    a_significant_loss = a["ci"][1] is not None and a["ci"][1] < 0
    a_pass = a["delta"] >= -GATE_A_TOLERANCE and not a_significant_loss
    b_base = sum(values(cases, f"{BASELINE}-scope-B", "recall@5")) / len(cases)
    b_new = sum(values(cases, f"{CANDIDATE}-scope-B", "recall@5")) / len(cases)
    print("\n### Gate")
    print(
        f"- Scope A: Recall@5 delta {a['delta']:+.3f}, CI [{a['ci'][0]:+.3f}, {a['ci'][1]:+.3f}], "
        f"sign-test p {fmt(a['p'])} -> {'PASS' if a_pass else 'FAIL'} "
        f"(needs delta >= -{GATE_A_TOLERANCE} and no significant loss)"
    )
    print(
        f"- Scope B: Recall@5 {b_new:.3f} vs baseline {b_base:.3f} -> "
        f"{'PASS' if b_new >= b_base else 'FAIL'}"
    )
    return 0 if (a_pass and b_new >= b_base) else 1


if __name__ == "__main__":
    raise SystemExit(main())
