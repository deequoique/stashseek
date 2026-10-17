"""Render the RQ1 R6 deliverables (markdown, CSV, and a hand-written SVG).

This module only transforms the dict returned by
``retrieval_study.run_rq1`` plus the artifact bundle into files; it performs
no retrieval or database access of its own.
"""

from __future__ import annotations

import csv
import hashlib
import io
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import stats as rq1_stats
from .core import canonical_hash, write_json, write_jsonl
from .retrieval_study import QUERY_KINDS

_METRIC_KS = (1, 3, 5, 10)
_DOMAINS = ("Academic(icsi)", "Product(ami)", "Committee")


# ------------------------------------------------------------------- helpers
def _library_word_stats(manifest: Mapping[str, Any]) -> dict[str, Any]:
    words: list[int] = []
    segment_count = 0
    boundary_kinds: dict[str, int] = {}
    for meeting in manifest["meetings"].values():
        for segment in meeting["segments"]:
            words.append(segment["words"])
            segment_count += 1
        for kind, count in meeting.get("boundary_kinds", {}).items():
            boundary_kinds[kind] = boundary_kinds.get(kind, 0) + count
    words.sort()
    n = len(words)

    def pct(p: float) -> int:
        if not n:
            return 0
        return words[min(n - 1, int(p * n))]

    return {
        "meetings": len(manifest["meetings"]),
        "segments": segment_count,
        "median_words": pct(0.5),
        "p10_words": pct(0.10),
        "p90_words": pct(0.90),
        "boundary_kinds": dict(sorted(boundary_kinds.items())),
    }


def _percentile(values: Sequence[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p * len(ordered)))]


def dense_candidate_stats(
    result: Mapping[str, Any], *, pool_limit: int
) -> dict[str, dict[str, Any]]:
    """Per-scope dense candidate pool size diagnostics (R7).

    ``KnowledgeServices``' dense query requests ``pool_limit`` candidates, but
    an approximate HNSW scan (typically scope B's tenant-wide plan) can return
    fewer than requested after post-filtering. This reports, per scope, how
    many dense candidates the pool query actually returned, plus the scope-B
    exact (sequential scan) comparison, so the deck can state the caveat with
    numbers rather than only the case-by-case R7 diagnostic.
    """

    series: dict[str, list[int]] = {"A": [], "B_approx": [], "B_exact": []}
    for case in result.get("per_case", ()):
        counts = case.get("dense_candidate_counts") or {}
        if "A" in counts:
            series["A"].append(counts["A"])
        if "B" in counts:
            series["B_approx"].append(counts["B"])
        if "B_exact" in counts:
            series["B_exact"].append(counts["B_exact"])

    stats: dict[str, dict[str, Any]] = {}
    for key, values in series.items():
        n = len(values)
        stats[key] = {
            "n": n,
            "requested": pool_limit,
            "min": min(values) if values else None,
            "median": _percentile(values, 0.5),
            "p10": _percentile(values, 0.10),
            "max": max(values) if values else None,
            "n_under_requested": sum(1 for v in values if v < pool_limit),
            "n_under_10": sum(1 for v in values if v < 10),
            "n_empty": sum(1 for v in values if v == 0),
        }
    return stats


def _macro_mean(case_records: Sequence[dict[str, Any]], key: str) -> float | None:
    values = [record[key] for record in case_records if key in record and record[key] is not None]
    if not values:
        return None
    return statistics.fmean(values)


def _setting_label(setting: Mapping[str, Any]) -> str:
    method = setting["method"]
    variant = setting.get("lexical_variant")
    diversify = "div-on" if setting["diversify"] else "div-off"
    label = method if not variant else f"{method}-{variant}"
    return f"{label} ({diversify})"


# --------------------------------------------------------------------- setup
def render_setup_md(result: Mapping[str, Any], *, dataset_hash: str) -> str:
    manifest = result["library_manifest"]
    index_stats = _library_word_stats(manifest)
    n_eligible = len(result["eligible_case_ids"])
    n_ineligible = len(result["ineligible_case_ids"])
    exclusions = result.get("exclusions") or {}
    mde = rq1_stats.minimum_detectable_discordance()

    lines = [
        "# RQ1 Setup",
        "",
        f"- Split: `{result.get('split', 'val')}`",
        f"- Dataset hash: `{dataset_hash}`",
        f"- Git SHA: `{manifest.get('git_sha')}`",
        f"- Chunker fingerprint: `{manifest.get('chunker_fingerprint')}`",
        f"- Library: {index_stats['meetings']} meetings, {index_stats['segments']} segments "
        f"(median {index_stats['median_words']} words/segment, "
        f"p10 {index_stats['p10_words']}, p90 {index_stats['p90_words']})",
        f"- Boundary kinds: {index_stats['boundary_kinds']}",
        f"- Cases: {n_eligible} eligible (`ces` policy, `citation_scorable`) + "
        f"{n_ineligible} ineligible (excluded from macro averages)",
    ]
    if exclusions:
        lines.append(
            f"- Excluded from every summary and comparison (still retrieved, kept in "
            f"`per_case.jsonl`): {len(exclusions)} case(s): "
            + ", ".join(_exclusion_label(result, case_id, reason) for case_id, reason in sorted(exclusions.items()))
        )
    lines += [
        "",
        "## Metric definitions",
        "",
        "- **Hit@k**: at least one gold evidence sentence is fully contained by the top-k "
        "returned chunks (every word of the sentence lies inside one chunk's cue range).",
        "- **Recall@k**: the fraction of gold evidence sentences fully contained by the "
        "top-k chunks.",
        "- **MRR@10**: mean reciprocal rank of the first chunk that fully contains a gold "
        "sentence (0 if none in the top 10).",
        "- **nDCG@10**: binary-gain nDCG; the ideal ranking places `min(gold-bearing "
        "chunks, 10)` relevant chunks first.",
        "- **Ceiling@k**: the exact maximum Recall@k/Hit@k achievable by any k of the "
        "case's own post-fix chunks (an oracle over chunking, not retrieval).",
        "- **Candidate pool**: the deduplicated union of the dense top-50 and the lexical "
        "top-50 candidates of one scope, before fusion, ranking and diversification. Pool "
        "hit/recall use the same full-containment rule as Hit/Recall@k, so pool recall is "
        "the highest Recall any ranking of that pool could reach (AND is the production "
        "pool).",
        "- **Meeting localization (scope B)**: whether the case's own meeting is the first "
        "result (`top-1 correct`) or appears in the top 5 (`meeting hit@5`); when the first "
        "result is another meeting, whether that meeting is from the same source domain "
        "(Academic/Product/Committee) as the case's meeting.",
        "",
        "## Statistical power",
        "",
        f"- n = {n_eligible} paired eligible cases.",
        f"- Minimum one-directional discordant count for exact McNemar p < 0.05: "
        f"{mde} (independent of n; McNemar depends only on discordant pairs).",
        "",
        "## R7: dense candidate pool size (approx vs exact)",
        "",
        "Each dense query requests `pool_limit` candidates; an approximate "
        "HNSW scan (typically scope B's tenant-wide plan) can return fewer "
        "than requested after post-filtering. Counts are over all "
        f"{len(result.get('per_case', ()))} cases (not only eligible ones).",
        "",
    ]
    pool_limit = result.get("pool_limit")
    candidate_stats = dense_candidate_stats(result, pool_limit=pool_limit) if pool_limit else {}
    if candidate_stats:
        lines.append(
            "| Series | n | requested | min | median | p10 | max | "
            "< requested | < 10 | empty |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        labels = {
            "A": "scope A dense (item-scoped, exact sort)",
            "B_approx": "scope B dense (tenant-wide, production plan)",
            "B_exact": "scope B dense (forced sequential/exact scan)",
        }
        for key, label in labels.items():
            row = candidate_stats.get(key)
            if row is None or row["n"] == 0:
                continue
            lines.append(
                f"| {label} | {row['n']} | {row['requested']} | {_fmt(row['min'])} | "
                f"{_fmt(row['median'])} | {_fmt(row['p10'])} | {_fmt(row['max'])} | "
                f"{row['n_under_requested']} | {row['n_under_10']} | {row['n_empty']} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def _exclusion_label(result: Mapping[str, Any], case_id: str, reason: str) -> str:
    """``case (reason)``; flags an excluded case that is also CES-ineligible.

    A held-out collision case can itself be answer-only/partial, in which case
    it is already one of the ineligible cases; saying so keeps
    ``eligible + ineligible`` equal to the number of cases run.
    """

    status = next(
        (case.get("ces_status") for case in result.get("per_case", ()) if case["case_id"] == case_id),
        None,
    )
    if status is not None and status != "citation_scorable":
        return f"`{case_id}` ({reason}; CES `{status}`, so also counted among the ineligible cases)"
    return f"`{case_id}` ({reason})"


# ------------------------------------------------------------------ baseline
def render_baseline(result: Mapping[str, Any]) -> tuple[str, str]:
    baseline_id = result["baseline_setting_id"]
    rows: list[dict[str, Any]] = []
    for scope in ("B", "A"):
        setting_id = baseline_id if scope == "B" else baseline_id.replace("scope-B", "scope-A")
        metrics = result["aggregates"].get(setting_id, {})
        ceiling_means = _ceiling_macro(result, scope=scope)
        row: dict[str, Any] = {"scope": scope, "setting": setting_id}
        for k in _METRIC_KS:
            row[f"hit@{k}"] = metrics.get(f"hit@{k}", {}).get("mean")
            row[f"recall@{k}"] = metrics.get(f"recall@{k}", {}).get("mean")
            row[f"ceiling_recall@{k}"] = ceiling_means.get(k, {}).get("recall")
            row[f"ceiling_hit@{k}"] = ceiling_means.get(k, {}).get("hit")
        row["mrr"] = metrics.get("rr", {}).get("mean")
        row["ndcg@10"] = metrics.get("ndcg@10", {}).get("mean")
        row["n"] = metrics.get("hit@5", {}).get("n", 0)
        rows.append(row)

    md_lines = ["# Baseline (production setting: raw-score merge, AND, diversify on)", ""]
    for row in rows:
        md_lines.append(f"## Scope {row['scope']} (n={row['n']})")
        md_lines.append("")
        md_lines.append("| k | Hit@k | Recall@k | Ceiling Hit@k | Ceiling Recall@k |")
        md_lines.append("|---|---|---|---|---|")
        for k in _METRIC_KS:
            md_lines.append(
                f"| {k} | {_fmt(row[f'hit@{k}'])} | {_fmt(row[f'recall@{k}'])} | "
                f"{_fmt(row[f'ceiling_hit@{k}'])} | {_fmt(row[f'ceiling_recall@{k}'])} |"
            )
        md_lines.append("")
        md_lines.append(f"MRR@10: {_fmt(row['mrr'])}  nDCG@10: {_fmt(row['ndcg@10'])}")
        md_lines.append("")

    csv_buffer = io.StringIO()
    fieldnames = list(rows[0].keys()) if rows else []
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return "\n".join(md_lines) + "\n", csv_buffer.getvalue()


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _ceiling_macro(result: Mapping[str, Any], *, scope: str) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for k in _METRIC_KS:
        recalls = []
        hits = []
        for case in result["per_case"]:
            if not case["eligible"] or case.get("summary_excluded") or case.get("ceiling") is None:
                continue
            entry = case["ceiling"].get(str(k))
            if entry is None:
                continue
            recalls.append(entry["recall"])
            hits.append(entry["hit"])
        out[k] = {
            "recall": statistics.fmean(recalls) if recalls else None,
            "hit": statistics.fmean(hits) if hits else None,
        }
    return out


def _hit5_proportion(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Hit@5 as a numerator with its Wilson 95% interval (PRD R5), from the aggregate."""

    hit = metrics.get("hit@5", {})
    n = hit.get("n", 0)
    mean = hit.get("mean")
    if not n or mean is None:
        return {"hit@5_k": None, "hit@5_wilson_low": None, "hit@5_wilson_high": None}
    k = round(mean * n)
    low, high = rq1_stats.wilson_ci(k, n)
    return {"hit@5_k": k, "hit@5_wilson_low": low, "hit@5_wilson_high": high}


# ------------------------------------------------------------------ ablation
def render_ablation(result: Mapping[str, Any], *, heading_suffix: str = "") -> tuple[str, str]:
    scope_production_ids = result.get("scope_production_setting_ids", {})
    scope_baseline_ids = set(scope_production_ids.values())
    rows: list[dict[str, Any]] = []
    for setting in result["settings"]:
        setting_id = setting["setting_id"]
        if setting["scope"] == "A" and not setting["diversify"]:
            # Asserted identical to diversify-on in scope A; one column only.
            continue
        metrics = result["aggregates"].get(setting_id, {})
        comparison = result["comparisons"].get(setting_id, {})
        recall5_cmp = comparison.get("recall@5", {})
        hit5_cmp = comparison.get("hit@5", {})
        row = {
            "setting_id": setting_id,
            "label": _setting_label(setting),
            "scope": setting["scope"],
            "is_baseline": setting_id in scope_baseline_ids,
            "n": metrics.get("hit@5", {}).get("n", 0),
            "recall@5": metrics.get("recall@5", {}).get("mean"),
            "hit@5": metrics.get("hit@5", {}).get("mean"),
            "delta_recall@5": recall5_cmp.get("bootstrap", {}).get("mean_difference"),
            "recall@5_ci_low": recall5_cmp.get("bootstrap", {}).get("ci_low"),
            "recall@5_ci_high": recall5_cmp.get("bootstrap", {}).get("ci_high"),
            "mcnemar_p_hit@5": hit5_cmp.get("mcnemar", {}).get("p_value"),
            **_hit5_proportion(metrics),
        }
        rows.append(row)

    # One explicit cross-scope row: the production setting in scope A vs the
    # production setting in scope B. Every other row above is compared only
    # against the production setting in its *own* scope, so this is the only
    # row that measures the scope-A "known meeting" effect itself.
    cross = result.get("cross_scope_comparison", {})
    scope_a_production_id = scope_production_ids.get("A")
    if scope_a_production_id is not None:
        scope_a_metrics = result["aggregates"].get(scope_a_production_id, {})
        recall5_cmp = cross.get("recall@5", {})
        hit5_cmp = cross.get("hit@5", {})
        rows.append(
            {
                "setting_id": "scope-B-to-A-production",
                "label": "scope B -> A (production setting, cross-scope)",
                "scope": "A",
                "is_baseline": False,
                "n": scope_a_metrics.get("hit@5", {}).get("n", 0),
                "recall@5": scope_a_metrics.get("recall@5", {}).get("mean"),
                "hit@5": scope_a_metrics.get("hit@5", {}).get("mean"),
                "delta_recall@5": recall5_cmp.get("bootstrap", {}).get("mean_difference"),
                "recall@5_ci_low": recall5_cmp.get("bootstrap", {}).get("ci_low"),
                "recall@5_ci_high": recall5_cmp.get("bootstrap", {}).get("ci_high"),
                "mcnemar_p_hit@5": hit5_cmp.get("mcnemar", {}).get("p_value"),
                **_hit5_proportion(scope_a_metrics),
            }
        )

    md_lines = [
        f"# Ablation (Δ vs the production setting in the same scope){heading_suffix}",
        "",
        "Each row's ΔRecall@5/McNemar p compares it against the production "
        "setting (raw merge, AND, diversify on) in its *own* scope, so the "
        "delta isolates the method/diversification change. The final "
        "`scope B -> A` row is the one explicit cross-scope comparison "
        "(production vs production), isolating the known-meeting scope "
        "effect instead.",
        "",
    ]
    md_lines.append(
        "| Setting | Scope | n | Recall@5 | Hit@5 | ΔRecall@5 | 95% CI | McNemar p (Hit@5) | "
        "Hit@5 (k/n) | Hit@5 Wilson 95% |"
    )
    md_lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        marker = " (baseline)" if row["is_baseline"] else ""
        ci = (
            f"[{_fmt(row['recall@5_ci_low'])}, {_fmt(row['recall@5_ci_high'])}]"
            if row["delta_recall@5"] is not None
            else "n/a"
        )
        wilson = (
            f"[{_fmt(row['hit@5_wilson_low'])}, {_fmt(row['hit@5_wilson_high'])}]"
            if row["hit@5_k"] is not None
            else "n/a"
        )
        md_lines.append(
            f"| {row['label']}{marker} | {row['scope']} | {row['n']} | "
            f"{_fmt(row['recall@5'])} | {_fmt(row['hit@5'])} | "
            f"{_fmt(row['delta_recall@5'])} | {ci} | {_fmt(row['mcnemar_p_hit@5'])} | "
            f"{_frac(row['hit@5_k'], row['n'] or None)} | {wilson} |"
        )

    csv_buffer = io.StringIO()
    fieldnames = list(rows[0].keys()) if rows else []
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return "\n".join(md_lines) + "\n", csv_buffer.getvalue()


def top_settings_by_recall5(
    result: Mapping[str, Any], *, limit: int = 3, scope: str | None = None
) -> list[dict[str, Any]]:
    """The top settings by mean Recall@5, excluding the baseline itself.

    ``scope=None`` ranks across both scopes (used for the overall "top 3
    settings" deliverable). Pass ``scope="B"`` to rank one-factor changes
    within the production deployment scenario only -- used for the
    attribution chart's "best combined setting" bar, so it does not merely
    rediscover the separate scope-B-to-A bar.
    """

    baseline_id = result["baseline_setting_id"]
    scope_baseline_ids = set(result.get("scope_production_setting_ids", {}).values())
    scope_baseline_ids.add(baseline_id)
    scored = []
    for setting in result["settings"]:
        setting_id = setting["setting_id"]
        if setting_id in scope_baseline_ids or (setting["scope"] == "A" and not setting["diversify"]):
            continue
        if scope is not None and setting["scope"] != scope:
            continue
        metrics = result["aggregates"].get(setting_id, {})
        recall5 = metrics.get("recall@5", {}).get("mean")
        if recall5 is None:
            continue
        comparison = result["comparisons"].get(setting_id, {})
        scored.append(
            {
                "setting_id": setting_id,
                "label": _setting_label(setting),
                "scope": setting["scope"],
                "recall@5": recall5,
                "delta_recall@5": comparison.get("recall@5", {})
                .get("bootstrap", {})
                .get("mean_difference"),
                "ci": [
                    comparison.get("recall@5", {}).get("bootstrap", {}).get("ci_low"),
                    comparison.get("recall@5", {}).get("bootstrap", {}).get("ci_high"),
                ],
                "mcnemar_p_hit@5": comparison.get("hit@5", {}).get("mcnemar", {}).get("p_value"),
            }
        )
    scored.sort(key=lambda row: row["recall@5"], reverse=True)
    return scored[:limit]


# --------------------------------------------------------------- attribution
def render_attribution(result: Mapping[str, Any]) -> tuple[str, str]:
    baseline_id = result["baseline_setting_id"]
    baseline_recall5 = result["aggregates"].get(baseline_id, {}).get("recall@5", {}).get("mean", 0.0)
    ceiling = _ceiling_macro(result, scope="B").get(5, {})
    ceiling_recall5 = ceiling.get("recall")

    def delta_for(setting_id: str) -> float | None:
        comp = result["comparisons"].get(setting_id, {}).get("recall@5", {})
        return comp.get("bootstrap", {}).get("mean_difference")

    settings_by_id = {s["setting_id"]: s for s in result["settings"]}
    one_factor = [
        ("AND -> OR", "raw-OR-div-on-scope-B"),
        ("raw -> RRF", "rrf-AND-div-on-scope-B"),
        ("raw -> min-max", "minmax-AND-div-on-scope-B"),
        ("diversify on -> off", "raw-AND-div-off-scope-B"),
    ]
    bars = [(label, delta_for(setting_id)) for label, setting_id in one_factor if setting_id in settings_by_id]

    best = top_settings_by_recall5(result, limit=1, scope="B")
    if best:
        bars.append((f"best ({best[0]['label']})", best[0]["delta_recall@5"]))
    # The scope-B -> A bar is the one explicit cross-scope comparison
    # (production setting A vs production setting B); it must not be read off
    # ``comparisons[scope_a_id]``, which (after the within-scope fix) compares
    # scope-A settings against the scope-A production baseline instead.
    cross_scope_delta = (
        result.get("cross_scope_comparison", {})
        .get("recall@5", {})
        .get("bootstrap", {})
        .get("mean_difference")
    )
    bars.append(("scope B -> A", cross_scope_delta))

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["factor", "delta_recall@5"])
    for label, value in bars:
        writer.writerow([label, value if value is not None else ""])
    writer.writerow(["ceiling_recall@5_scope_B", ceiling_recall5 if ceiling_recall5 is not None else ""])
    writer.writerow(["baseline_recall@5_scope_B", baseline_recall5])

    svg = _render_bar_svg(bars, baseline=baseline_recall5, ceiling=ceiling_recall5)
    return svg, csv_buffer.getvalue()


def _xml_escape_text(text: str) -> str:
    """Escape SVG text-node content without destroying its meaning.

    Stripping ``>`` outright (as opposed to entity-escaping it) silently
    turns a label like ``"scope B -> A"`` into ``"scope B - A"`` in the
    rendered chart, which is a correctness problem for a deck artifact, not
    just cosmetic.
    """

    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_bar_svg(
    bars: list[tuple[str, float | None]], *, baseline: float, ceiling: float | None
) -> str:
    width, height = 720, 80 + 40 * max(1, len(bars))
    margin_left = 220
    plot_width = width - margin_left - 40
    max_abs = max([abs(v) for _, v in bars if v is not None] + [0.01])
    scale = (plot_width / 2) / max_abs
    zero_x = margin_left + plot_width / 2

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'font-family="sans-serif" font-size="12">',
        f'<text x="10" y="20" font-size="14" font-weight="bold">RQ1 attribution: '
        f'delta Recall@5 vs scope-B baseline ({baseline:.3f})</text>',
        f'<line x1="{zero_x:.1f}" y1="40" x2="{zero_x:.1f}" y2="{height - 20}" '
        'stroke="#888" stroke-width="1" />',
    ]
    for index, (label, value) in enumerate(bars):
        y = 50 + index * 40
        safe_label = _xml_escape_text(label)
        parts.append(f'<text x="10" y="{y + 15}" font-size="12">{safe_label}</text>')
        if value is None:
            parts.append(f'<text x="{margin_left}" y="{y + 15}" font-size="12">n/a</text>')
            continue
        bar_width = value * scale
        x = zero_x if bar_width >= 0 else zero_x + bar_width
        color = "#2a7" if value >= 0 else "#c33"
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{abs(bar_width):.1f}" height="20" fill="{color}" />'
        )
        parts.append(
            f'<text x="{zero_x + bar_width + (6 if bar_width >= 0 else -6):.1f}" '
            f'y="{y + 15}" font-size="12" text-anchor="{"start" if bar_width >= 0 else "end"}">'
            f"{value:+.3f}</text>"
        )
    if ceiling is not None:
        parts.append(
            f'<text x="10" y="{height - 5}" font-size="11" fill="#555">'
            f"Remaining gap to ceiling Recall@5 ({ceiling:.3f}): "
            f"{ceiling - baseline:.3f}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ------------------------------------------------------------- failure cases
def select_failure_cases(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    baseline_id = result["baseline_setting_id"]
    best_by_domain: dict[str, dict[str, Any]] = {}
    for case in result["per_case"]:
        if not case["eligible"] or case.get("summary_excluded") or case.get("ceiling") is None:
            continue
        ceiling5 = case["ceiling"].get("5", {}).get("recall", 0.0)
        baseline_metrics = case.get("_metrics", {}).get(baseline_id, {})
        baseline_recall5 = baseline_metrics.get("recall@5", 0.0)
        baseline_hit5 = baseline_metrics.get("hit@5", 0.0)
        gap = ceiling5 - baseline_recall5
        candidate = {
            "case_id": case["case_id"],
            "domain": case["domain"],
            "gap": gap,
            "baseline_hit@5": baseline_hit5,
            "baseline_recall@5": baseline_recall5,
            "ceiling_recall@5": ceiling5,
        }
        domain_key = next((d for d in _DOMAINS if case["domain"] == d), case["domain"])
        current = best_by_domain.get(domain_key)
        if current is None:
            best_by_domain[domain_key] = candidate
            continue
        key_new = (gap, -baseline_hit5, case["case_id"])
        key_cur = (current["gap"], -current["baseline_hit@5"], current["case_id"])
        if key_new > key_cur:
            best_by_domain[domain_key] = candidate
    return [best_by_domain[d] for d in _DOMAINS if d in best_by_domain]


def _global_segment_index(manifest: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """Segment id -> {meeting_id, text, ...}, spanning every ingested meeting.

    Scope B is a tenant-wide search; a zero-recall case's top-ranked chunks
    may come from a different (distractor) meeting entirely. A lookup scoped
    to only the case's own meeting would silently render blank text for
    those rows instead of surfacing the actual wrong-meeting confusion.
    """

    index: dict[int, dict[str, Any]] = {}
    for meeting_id, info in manifest["meetings"].items():
        for segment in info["segments"]:
            index[segment["id"]] = {**segment, "meeting_id": meeting_id}
    return index


def render_failure_cases(
    result: Mapping[str, Any], bundle: Mapping[str, Any], *, diagnoses: Mapping[str, str] | None = None
) -> str:
    diagnoses = diagnoses or {}
    baseline_id = result["baseline_setting_id"]
    manifest = result["library_manifest"]
    cases_by_id = {case.case_id: case for case in bundle["cases"]}
    per_case_by_id = {case["case_id"]: case for case in result["per_case"]}
    global_segments_by_id = _global_segment_index(manifest)

    lines = ["# RQ1 Failure Cases", "", "Selection rule: per domain, the eligible case with the "
             "largest (Ceiling Recall@5 - baseline Recall@5); ties go to the lower baseline "
             "Hit@5, then case_id.", ""]
    for selected in select_failure_cases(result):
        case_id = selected["case_id"]
        case = cases_by_id[case_id]
        record = per_case_by_id[case_id]
        sentences_by_id = {s.id: s for s in bundle["corpus"][case.meeting_id]}

        lines.append(f"## {case_id} ({selected['domain']})")
        lines.append("")
        lines.append(f"Gap (ceiling - baseline) Recall@5: {selected['gap']:.3f}  "
                      f"baseline Hit@5: {selected['baseline_hit@5']:.0f}  "
                      f"baseline Recall@5: {selected['baseline_recall@5']:.3f}  "
                      f"ceiling Recall@5: {selected['ceiling_recall@5']:.3f}")
        lines.append("")
        lines.append(f"**Query:** {case.query}")
        lines.append("")
        lines.append("**Gold evidence sentences:**")
        lines.append("")
        for sentence_id in record["gold_sentence_ids"]:
            sentence = sentences_by_id.get(sentence_id)
            if sentence is not None:
                lines.append(f"- `{sentence_id}` ({sentence.speaker}): {sentence.text}")
        lines.append("")
        lines.append("**Top-5 returned chunks (baseline setting):**")
        lines.append("")
        lines.append("| rank | meeting | score | text |")
        lines.append("|---|---|---|---|")
        setting_data = record["settings"].get(baseline_id, {})
        top5_ids = setting_data.get("segment_ids", [])[:5]
        top5_scores = setting_data.get("scores", [])[:5]
        wrong_meeting_hits = 0
        for rank, (segment_id, score) in enumerate(zip(top5_ids, top5_scores), start=1):
            segment = global_segments_by_id.get(segment_id, {})
            text = (segment.get("text") or "")[:300]
            chunk_meeting_id = segment.get("meeting_id", "unknown")
            if chunk_meeting_id != case.meeting_id:
                wrong_meeting_hits += 1
            lines.append(f"| {rank} | {chunk_meeting_id} | {score:.4f} | {text} |")
        lines.append("")
        diagnosis = diagnoses.get(case_id) or _default_diagnosis(
            baseline_hit5=selected["baseline_hit@5"],
            wrong_meeting_hits=wrong_meeting_hits,
            top5_count=len(top5_ids),
            case_meeting_id=case.meeting_id,
        )
        lines.append(f"**Diagnosis:** {diagnosis}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _default_diagnosis(
    *, baseline_hit5: float, wrong_meeting_hits: int, top5_count: int, case_meeting_id: str
) -> str:
    if baseline_hit5 > 0:
        return (
            "Analysis: the baseline finds some gold evidence in the top 5, but not enough "
            "to match the chunking ceiling; the remaining gap is a ranking/budget issue, not "
            "a chunking issue."
        )
    if top5_count == 0:
        return (
            "Analysis: the baseline returned zero candidates at all (no lexical AND hits and "
            "no dense hits), not merely a low-ranked miss. See the R7 ANN diagnostic: this is "
            "consistent with the approximate HNSW dense query returning no candidates for this "
            "query vector while an exact dense scan over the same filters finds normal, close "
            "neighbors -- an index reliability issue, not a chunking or ranking issue."
        )
    if top5_count and wrong_meeting_hits == top5_count:
        return (
            f"Analysis: all {top5_count} top-ranked chunks come from a different meeting than "
            f"`{case_meeting_id}`. Scope B's tenant-wide dense ranking is confusing a "
            "topically similar distractor meeting for the true source, not failing to find "
            "evidence within the right meeting; the fixed chunker's own chunks could have "
            "surfaced this evidence (ceiling Recall@5 = 1.0)."
        )
    if wrong_meeting_hits:
        return (
            f"Analysis: {wrong_meeting_hits}/{top5_count} top-ranked chunks come from a "
            f"different meeting than `{case_meeting_id}`, crowding out the correct meeting's "
            "evidence even though the fixed chunker's own chunks could have surfaced it."
        )
    return (
        "Analysis: the top-ranked chunks are from the correct meeting but none fully contain "
        "a gold sentence, indicating a within-meeting ranking gap rather than a chunking or "
        "cross-meeting confusion gap for this case."
    )


# ----------------------------------------------------------- group summaries
_PPT_KINDS = (*QUERY_KINDS, "all")
_PRODUCTION_VARIANT = "AND"


def counted_cases(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per-case records that feed summaries: eligible and not excluded."""

    return [
        case
        for case in result.get("per_case", ())
        if case.get("eligible") and not case.get("summary_excluded")
    ]


def case_groups(result: Mapping[str, Any]) -> list[tuple[str, str, list[dict[str, Any]]]]:
    """``(group_type, group, cases)`` for all, each query kind, and each domain."""

    counted = counted_cases(result)
    groups: list[tuple[str, str, list[dict[str, Any]]]] = [("kind", "all", counted)]
    for kind in QUERY_KINDS:
        groups.append(("kind", kind, [c for c in counted if c.get("query_kind") == kind]))
    for domain in sorted({c["domain"] for c in counted}):
        groups.append(("domain", domain, [c for c in counted if c["domain"] == domain]))
    return groups


def _mean(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


def summarize_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    setting_id: str,
    scope: str,
    lexical_variant: str | None,
) -> dict[str, Any]:
    """Flat per-group summary of one setting over ``cases`` (already filtered).

    Proportions carry their numerator and denominator (``*_k`` / ``n``) with a
    Wilson 95% interval for Hit@5. ``pool_*`` read the candidate pool of
    ``lexical_variant`` in ``scope`` (``None`` for a setting with no lexical
    leg). Localization fields exist only for scope B.
    """

    n = len(cases)
    metrics = [case["settings"][setting_id]["metrics"] for case in cases]
    hit5_k = int(sum(metric["hit@5"] for metric in metrics))
    hit5_lo, hit5_hi = rq1_stats.wilson_ci(hit5_k, n) if n else (None, None)
    row: dict[str, Any] = {
        "n": n,
        "hit5_k": hit5_k,
        "hit5_rate": hit5_k / n if n else None,
        "hit5_ci_low": hit5_lo,
        "hit5_ci_high": hit5_hi,
        "recall5": _mean([metric["recall@5"] for metric in metrics]),
        "mrr": _mean([metric["rr"] for metric in metrics]),
        "ndcg10": _mean([metric["ndcg@10"] for metric in metrics]),
        "ceiling_recall5": _mean(
            [(case.get("ceiling") or {}).get("5", {}).get("recall") for case in cases]
        ),
        "ceiling_hit5": _mean(
            [(case.get("ceiling") or {}).get("5", {}).get("hit") for case in cases]
        ),
        "pool_hit_k": None,
        "pool_hit_rate": None,
        "pool_recall": None,
        "pool_size_mean": None,
    }
    if lexical_variant is not None and n:
        pools = [case["pool"][scope][lexical_variant] for case in cases]
        pool_hits = [pool["pool_hit"] for pool in pools]
        row["pool_hit_k"] = int(sum(hit or 0.0 for hit in pool_hits))
        row["pool_hit_rate"] = row["pool_hit_k"] / n
        row["pool_recall"] = _mean([pool["pool_recall"] for pool in pools])
        row["pool_size_mean"] = _mean([pool["pool_size"] for pool in pools])
    if scope == "B":
        localization = [case["settings"][setting_id]["localization"] for case in cases]
        # Every non-empty first result that is not the case's meeting, even if its
        # meeting could not be resolved (then its same-source flag is undetermined).
        wrong = [
            loc for loc in localization if not loc["top1_empty"] and not loc["top1_meeting_correct"]
        ]
        row.update(
            {
                "meeting_hit5_k": int(sum(metric["meeting_hit@5"] for metric in metrics)),
                "top1_meeting_correct_k": int(
                    sum(loc["top1_meeting_correct"] for loc in localization)
                ),
                "top1_empty_n": sum(1 for loc in localization if loc["top1_empty"]),
                "wrong_top1_n": len(wrong),
                "wrong_top1_same_source_k": sum(
                    1 for loc in wrong if loc["wrong_top1_same_source"] is True
                ),
            }
        )
    return row


def _frac(numerator: Any, denominator: Any) -> str:
    if numerator is None or denominator is None:
        return "n/a"
    return f"{numerator}/{denominator}"


def _scope_setting_ids(result: Mapping[str, Any]) -> dict[str, str]:
    ids = dict(result.get("scope_production_setting_ids") or {})
    if not ids:
        baseline_id = result["baseline_setting_id"]
        ids = {"B": baseline_id, "A": baseline_id.replace("scope-B", "scope-A")}
    return ids


def group_summary_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Production-setting summaries for every scope and group (CSV/markdown rows)."""

    rows: list[dict[str, Any]] = []
    setting_ids = _scope_setting_ids(result)
    for scope in ("A", "B"):
        for group_type, group, cases in case_groups(result):
            rows.append(
                {
                    "scope": scope,
                    "group_type": group_type,
                    "group": group,
                    "setting_id": setting_ids[scope],
                    **summarize_cases(
                        cases,
                        setting_id=setting_ids[scope],
                        scope=scope,
                        lexical_variant=_PRODUCTION_VARIANT,
                    ),
                }
            )
    return rows


def _pool_hit_text(row: Mapping[str, Any]) -> str:
    if row["pool_hit_k"] is None:
        return "n/a"
    return _frac(row["pool_hit_k"], row["n"])


def _summary_table_header() -> list[str]:
    return [
        "| Scope | Group | n | Hit@5 | Hit@5 95% CI | Recall@5 | MRR@10 | Pool hit | "
        "Pool recall | Ceiling Recall@5 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]


def _summary_table_row(label: str, row: Mapping[str, Any], scope: str) -> str:
    ci = (
        f"[{_fmt(row['hit5_ci_low'])}, {_fmt(row['hit5_ci_high'])}]"
        if row["hit5_ci_low"] is not None
        else "n/a"
    )
    return (
        f"| {scope} | {label} | {row['n']} | {_frac(row['hit5_k'], row['n'])} "
        f"({_fmt(row['hit5_rate'])}) | {ci} | {_fmt(row['recall5'])} | {_fmt(row['mrr'])} | "
        f"{_pool_hit_text(row)} | {_fmt(row['pool_recall'])} | {_fmt(row['ceiling_recall5'])} |"
    )


def _localization_table(rows: Sequence[Mapping[str, Any]], *, label_key: str) -> list[str]:
    lines = [
        f"| {label_key} | n | Top-1 is the right meeting | Right meeting in top 5 | "
        "Wrong top-1 (non-empty) | Wrong top-1 from the same source |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['group']} | {row['n']} | "
            f"{_frac(row['top1_meeting_correct_k'], row['n'])} | "
            f"{_frac(row['meeting_hit5_k'], row['n'])} | {row['wrong_top1_n']} | "
            f"{_frac(row['wrong_top1_same_source_k'], row['wrong_top1_n'])} |"
        )
    return lines


def render_group_summary(result: Mapping[str, Any]) -> tuple[str, str]:
    rows = group_summary_rows(result)
    md_lines = [
        "# RQ1 summary by query kind and domain (production setting: raw-score merge, AND, "
        "diversify on)",
        "",
        "Cases counted are CES `citation_scorable` and not excluded. Proportions are "
        "numerator/denominator; the interval is Wilson 95%. `Pool hit/recall` is the "
        "AND candidate pool (dense top-50 U lexical top-50).",
        "",
    ]
    for group_type, title in (("kind", "By query kind"), ("domain", "By domain")):
        md_lines += [f"## {title}", ""]
        md_lines += _summary_table_header()
        for row in rows:
            if row["group_type"] == group_type:
                md_lines.append(_summary_table_row(row["group"], row, row["scope"]))
        md_lines.append("")
        md_lines += [f"### Scope B meeting localization ({title.lower()})", ""]
        md_lines += _localization_table(
            [row for row in rows if row["group_type"] == group_type and row["scope"] == "B"],
            label_key="Group",
        )
        md_lines.append("")

    csv_buffer = io.StringIO()
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return "\n".join(md_lines) + "\n", csv_buffer.getvalue()


def render_ppt_summary(result: Mapping[str, Any]) -> str:
    """Deck-ready tables: scope x query kind for the baseline and the best scope-B setting."""

    setting_ids = _scope_setting_ids(result)
    groups = {name: cases for group_type, name, cases in case_groups(result) if group_type == "kind"}
    settings_by_id = {s["setting_id"]: s for s in result.get("settings", ())}

    lines = [
        "# RQ1 deck summary",
        "",
        "Original query text is used as the retrieval query (no Agent rewriting), so these "
        "numbers are not comparable with Agent end-to-end results, where the Agent writes its "
        "own search terms and each meeting is queried in its own account. Scope A restricts "
        "retrieval to the case's meeting; scope B searches the whole library of the split in "
        "one tenant.",
        "",
        "Counted cases: "
        + ", ".join(f"{kind} {len(groups[kind])}" for kind in _PPT_KINDS if kind in groups)
        + f"; ineligible (not CES citation-scorable): {len(result.get('ineligible_case_ids', ()))}"
        + f"; held-out excluded: {len(result.get('exclusions') or {})}"
        + (
            " ("
            + ", ".join(
                _exclusion_label(result, cid, reason)
                for cid, reason in sorted(result["exclusions"].items())
            )
            + ")"
            if result.get("exclusions")
            else ""
        )
        + ".",
        "",
        "## Production setting (raw-score merge, AND, diversify on)",
        "",
        "| Scope | Query kind | n | Hit@5 (k/n) | Hit@5 95% CI | Recall@5 | "
        "Candidate-pool hit (k/n) | Candidate-pool recall |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for scope in ("A", "B"):
        for kind in _PPT_KINDS:
            if kind not in groups:
                continue
            row = summarize_cases(
                groups[kind],
                setting_id=setting_ids[scope],
                scope=scope,
                lexical_variant=_PRODUCTION_VARIANT,
            )
            lines.append(_ppt_row(scope, kind, row))
    lines.append("")

    best = top_settings_by_recall5(result, limit=1, scope="B") if result.get("aggregates") else []
    if best:
        best_id = best[0]["setting_id"]
        variant = settings_by_id.get(best_id, {}).get("lexical_variant")
        lines += [
            f"## Best non-production scope-B setting by overall Recall@5: {best[0]['label']}",
            "",
            f"`{best_id}`. Picked post hoc from the other scope-B settings on these same "
            "cases, so read it with the paired comparisons in `ablation.md`. Its lexical "
            "variant defines the candidate pool shown "
            + ("(dense-only settings show no pool)." if variant is None else f"({variant})."),
            "",
            "| Scope | Query kind | n | Hit@5 (k/n) | Hit@5 95% CI | Recall@5 | "
            "Candidate-pool hit (k/n) | Candidate-pool recall |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for kind in _PPT_KINDS:
            if kind not in groups:
                continue
            row = summarize_cases(
                groups[kind], setting_id=best_id, scope="B", lexical_variant=variant
            )
            lines.append(_ppt_row("B", kind, row))
        lines.append("")

    lines += ["## Scope B meeting localization (production setting)", ""]
    localization_rows = [
        {
            "group": kind,
            **summarize_cases(
                groups[kind],
                setting_id=setting_ids["B"],
                scope="B",
                lexical_variant=_PRODUCTION_VARIANT,
            ),
        }
        for kind in _PPT_KINDS
        if kind in groups
    ]
    lines += _localization_table(localization_rows, label_key="Query kind")
    lines.append("")
    return "\n".join(lines) + "\n"


def _ppt_row(scope: str, kind: str, row: Mapping[str, Any]) -> str:
    ci = (
        f"[{_fmt(row['hit5_ci_low'])}, {_fmt(row['hit5_ci_high'])}]"
        if row["hit5_ci_low"] is not None
        else "n/a"
    )
    return (
        f"| {scope} | {kind} | {row['n']} | {_frac(row['hit5_k'], row['n'])} "
        f"({_fmt(row['hit5_rate'])}) | {ci} | {_fmt(row['recall5'])} | {_pool_hit_text(row)} | "
        f"{_fmt(row['pool_recall'])} |"
    )


# ------------------------------------------------------------------- driver
def attach_case_metric_lookup(result: dict[str, Any]) -> None:
    """Populate ``case["_metrics"][setting_id]`` directly from per-case settings.

    ``run_rq1`` already stores each eligible case's computed metrics at
    ``case["settings"][setting_id]["metrics"]``; this just projects that into
    the flatter ``case["_metrics"][setting_id]`` shape report rendering uses.
    """

    for case in result["per_case"]:
        case["_metrics"] = {
            setting_id: entry["metrics"]
            for setting_id, entry in case["settings"].items()
            if "metrics" in entry
        }


def write_reports(
    result: dict[str, Any], bundle: Mapping[str, Any], run_dir: Path, *, dataset_hash: str
) -> None:
    run_dir = Path(run_dir)
    attach_case_metric_lookup(result)

    (run_dir / "setup.md").write_text(render_setup_md(result, dataset_hash=dataset_hash), encoding="utf-8")

    baseline_md, baseline_csv = render_baseline(result)
    (run_dir / "baseline.md").write_text(baseline_md, encoding="utf-8")
    (run_dir / "baseline.csv").write_text(baseline_csv, encoding="utf-8")

    ablation_md, ablation_csv = render_ablation(result)
    (run_dir / "ablation.md").write_text(ablation_md, encoding="utf-8")
    (run_dir / "ablation.csv").write_text(ablation_csv, encoding="utf-8")

    attribution_svg, attribution_csv = render_attribution(result)
    (run_dir / "attribution.svg").write_text(attribution_svg, encoding="utf-8")
    (run_dir / "attribution.csv").write_text(attribution_csv, encoding="utf-8")

    failure_md = render_failure_cases(result, bundle)
    (run_dir / "failure_cases.md").write_text(failure_md, encoding="utf-8")

    group_md, group_csv = render_group_summary(result)
    (run_dir / "summary_by_group.md").write_text(group_md, encoding="utf-8")
    (run_dir / "summary_by_group.csv").write_text(group_csv, encoding="utf-8")
    (run_dir / "summary_ppt.md").write_text(render_ppt_summary(result), encoding="utf-8")

    for kind, kind_result in sorted(result.get("by_kind", {}).items()):
        kind_ablation_md, kind_ablation_csv = render_ablation(
            {
                **result,
                "aggregates": kind_result["aggregates"],
                "comparisons": kind_result["comparisons"],
                "cross_scope_comparison": kind_result["cross_scope_comparison"],
            },
            heading_suffix=f" - {kind} queries only (n={kind_result['n_cases']})",
        )
        (run_dir / f"ablation_{kind}.md").write_text(kind_ablation_md, encoding="utf-8")
        (run_dir / f"ablation_{kind}.csv").write_text(kind_ablation_csv, encoding="utf-8")

    per_case_rows = [
        {key: value for key, value in case.items() if key != "_metrics"}
        for case in result["per_case"]
    ]
    write_jsonl(run_dir / "per_case.jsonl", per_case_rows)

    case_ids = [case["case_id"] for case in result["per_case"]]
    query_vector_cache_path = run_dir / "query_vectors.json"
    manifest_payload = {
        "dataset_hash": dataset_hash,
        "split": result.get("split", "val"),
        "git_sha": result["library_manifest"].get("git_sha"),
        "chunker_fingerprint": result["library_manifest"].get("chunker_fingerprint"),
        "baseline_setting_id": result["baseline_setting_id"],
        "case_ids": case_ids,
        "case_ids_hash": canonical_hash(case_ids),
        "eligible_case_ids": result["eligible_case_ids"],
        "ineligible_case_ids": result["ineligible_case_ids"],
        "excluded_case_ids": result.get("excluded_case_ids", []),
        "exclusions": result.get("exclusions", {}),
        "kind_case_counts": {
            kind: kind_result["n_cases"] for kind, kind_result in result.get("by_kind", {}).items()
        },
        "query_vector_cache": {
            "path": query_vector_cache_path.name,
            "sha256": (
                _sha256_file(query_vector_cache_path)
                if query_vector_cache_path.exists()
                else None
            ),
        },
        "r2_failures": result["r2_failures"],
        "scope_a_invariance_failures": result["scope_a_invariance_failures"],
        "explain": result["explain"],
        "r7_summary": _r7_summary(result["r7"]),
        "r7": result["r7"],
        "pool_limit": result.get("pool_limit"),
        "dense_candidate_stats": (
            dense_candidate_stats(result, pool_limit=result["pool_limit"])
            if result.get("pool_limit")
            else {}
        ),
    }
    write_json(run_dir / "run_manifest.json", manifest_payload)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _r7_summary(r7_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not r7_records:
        return {"cases": 0, "differs": 0}
    differs = sum(1 for record in r7_records if record["differs"])
    deltas = [
        (record["exact_recall@5"] - record["approx_recall@5"])
        for record in r7_records
        if record["exact_recall@5"] is not None and record["approx_recall@5"] is not None
    ]
    # A severe ANN failure mode: the approximate (HNSW) dense query returns no
    # candidates at all for a query where the exact dense query finds normal,
    # reasonably close neighbors. Distinct from ordinary top-10 re-ordering.
    empty_approx_nonempty_exact = [
        record["case_id"]
        for record in r7_records
        if not record["approx_top10"] and record["exact_top10"]
    ]
    return {
        "cases": len(r7_records),
        "differs": differs,
        "mean_recall@5_delta_exact_minus_approx": statistics.fmean(deltas) if deltas else None,
        "empty_approx_nonempty_exact_cases": empty_approx_nonempty_exact,
    }


__all__ = [
    "case_groups",
    "counted_cases",
    "dense_candidate_stats",
    "group_summary_rows",
    "render_ablation",
    "render_attribution",
    "render_baseline",
    "render_failure_cases",
    "render_group_summary",
    "render_ppt_summary",
    "render_setup_md",
    "select_failure_cases",
    "summarize_cases",
    "top_settings_by_recall5",
    "write_reports",
]
