"""Command-line entry point for the offline meeting-gold artifact contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    build_artifacts,
    load_artifact_bundle,
    load_source_lock,
    select_cases,
    validate_artifacts,
    validate_selection,
    write_selection,
)
from .core import SourceFormatError, write_json
from .production_path import ProductionPathError
from .retrieval_study import RQ1_CASE_IDS, StudyError, resolve_rq1_cases
from .scoring import ScoringError, score_cases

DEFAULT_RQ1_CASES = Path("data/meeting_gold/final")


def _add_source_lock_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source-lock",
        type=Path,
        default=None,
        help="source lock path; defaults to the package lock",
    )


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(
        prog="python -m evals.meeting_gold",
        description="Build and validate the offline QMSum/ExplainMeetSum golden set.",
    )
    subparsers = command_parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="verify sources and publish artifacts")
    build_parser.add_argument("--qmsum", type=Path, required=True)
    build_parser.add_argument("--explainmeetsum", type=Path, required=True)
    build_parser.add_argument("--output", type=Path, required=True)
    _add_source_lock_argument(build_parser)

    validate_parser = subparsers.add_parser(
        "validate", help="validate artifact bytes and optionally sources"
    )
    validate_parser.add_argument("--output", type=Path, required=True)
    validate_parser.add_argument("--qmsum", type=Path)
    validate_parser.add_argument("--explainmeetsum", type=Path)
    _add_source_lock_argument(validate_parser)

    select_parser = subparsers.add_parser(
        "select", help="create a deterministic split selection"
    )
    select_parser.add_argument(
        "--cases",
        type=Path,
        required=True,
        help="validated artifact directory",
    )
    select_parser.add_argument("--output", type=Path, required=True)
    select_parser.add_argument(
        "--split", choices=("train", "val", "test"), default="val"
    )
    selection_size = select_parser.add_mutually_exclusive_group()
    selection_size.add_argument(
        "--limit",
        type=int,
        default=None,
        help="smoke selection size; defaults to 30",
    )
    selection_size.add_argument(
        "--all",
        action="store_true",
        help="select every case in the requested split for full-split scoring",
    )
    select_parser.add_argument("--seed", type=int, default=0)

    score_parser = subparsers.add_parser(
        "score", help="run offline sentence-ID diagnostics"
    )
    score_parser.add_argument(
        "--cases", type=Path, required=True, help="validated artifact directory"
    )
    score_parser.add_argument("--selection", type=Path, required=True)
    score_parser.add_argument("--predictions", type=Path, required=True)
    score_parser.add_argument("--output", type=Path, required=True)
    score_parser.add_argument("--policy", choices=("ces", "ces_pes"), default="ces")
    score_parser.add_argument("--mode", choices=("complete", "partial"), default="complete")
    score_parser.add_argument(
        "--k", type=int, nargs="+", default=[1, 3, 5], dest="ks"
    )

    rq1_parser = subparsers.add_parser(
        "rq1",
        help="run the production-path hybrid retrieval study (RQ1) on the Neon test branch",
    )
    rq1_parser.add_argument(
        "--cases", type=Path, default=DEFAULT_RQ1_CASES, help="validated artifact directory"
    )
    rq1_parser.add_argument("--output", type=Path, required=True, help="run directory")
    rq1_parser.add_argument(
        "--split",
        choices=("val", "test"),
        default="val",
        help="split whose meetings form the library and whose cases are run (default: val)",
    )
    rq1_case_source = rq1_parser.add_mutually_exclusive_group()
    rq1_case_source.add_argument(
        "--case-id",
        dest="case_ids",
        action="append",
        default=None,
        help="restrict the run to one case id; repeatable (without --case-id/--selection, "
        "val runs the fixed 30-case selection and test requires --selection)",
    )
    rq1_case_source.add_argument(
        "--selection",
        type=Path,
        default=None,
        help="selection file from `select` (its split and dataset hash must match); "
        "held-out collision cases are retrieved but excluded from summaries",
    )

    agent_smoke_parser = subparsers.add_parser(
        "agent-smoke",
        help="R8 end-to-end production-path agent smoke (reuses an rq1 library; real runs call the model)",
    )
    agent_smoke_parser.add_argument(
        "--cases", type=Path, default=DEFAULT_RQ1_CASES, help="validated artifact directory"
    )
    agent_smoke_parser.add_argument("--output", type=Path, required=True, help="run directory")
    agent_smoke_parser.add_argument(
        "--case-id",
        dest="case_ids",
        action="append",
        default=None,
        help="restrict the run to one case id; repeatable (defaults to the fixed 30-case selection)",
    )
    agent_smoke_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate ingestion reuse and request wiring; never calls the model",
    )
    return command_parser


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactError("duplicate_json_key")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ArtifactError("non_finite_json")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except ArtifactError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Do not echo malformed input bodies into CLI diagnostics.
        raise ArtifactError("invalid_json") from exc


def _selection_for_scorer(selection: dict[str, Any]) -> dict[str, Any]:
    """Drop non-normative selection diagnostics for the strict scorer contract."""

    return {
        key: selection[key]
        for key in (
            "selection_version",
            "dataset_hash",
            "split",
            "selected_case_ids",
            "seed",
            "selection_policy",
            "selection_hash",
        )
    }


def _selection_diagnostics_for_report(selection: dict[str, Any]) -> dict[str, Any]:
    """Retain validated selection diagnostics outside the strict scorer input."""

    return {
        key: selection[key]
        for key in ("shortfalls", "held_out_eligible", "held_out_reasons")
        if key in selection
    }


def _print_summary(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _prepared_settings() -> Any:
    """Point the process at the Neon test branch, then load production settings.

    Must run before any other ``app.*`` import in this process; callers import
    ``production_path``/``retrieval_study``/``agent_smoke`` lazily for exactly
    this reason.
    """

    from .production_path import prepare_test_environment

    prepare_test_environment()
    from app.config import get_settings

    return get_settings()


def _run_rq1(args: argparse.Namespace) -> int:
    from . import report as rq1_report
    from .retrieval_study import run_rq1

    bundle = load_artifact_bundle(args.cases)
    selection = _read_json(args.selection) if args.selection is not None else None
    if selection is not None and not isinstance(selection, dict):
        raise ArtifactError("invalid_selection")
    case_ids, exclusions = resolve_rq1_cases(
        bundle, split=args.split, case_ids=args.case_ids, selection=selection
    )
    settings = _prepared_settings()

    result = run_rq1(
        bundle, case_ids, args.output, settings=settings, split=args.split, exclusions=exclusions
    )
    rq1_report.write_reports(result, bundle, args.output, dataset_hash=bundle["dataset_hash"])

    r2_failed = bool(result["r2_failures"])
    invariance_failed = bool(result["scope_a_invariance_failures"])
    _print_summary(
        {
            "status": "failed" if (r2_failed or invariance_failed) else "ok",
            "dataset_hash": bundle["dataset_hash"],
            "split": args.split,
            "cases": len(case_ids),
            "eligible_cases": len(result["eligible_case_ids"]),
            "excluded_cases": len(result["excluded_case_ids"]),
            "r2_failures": len(result["r2_failures"]),
            "scope_a_invariance_failures": len(result["scope_a_invariance_failures"]),
            "output": str(args.output),
        }
    )
    return 2 if (r2_failed or invariance_failed) else 0


def _run_agent_smoke(args: argparse.Namespace) -> int:
    from .agent_smoke import run_smoke

    case_ids = tuple(args.case_ids) if args.case_ids else RQ1_CASE_IDS
    bundle = load_artifact_bundle(args.cases)
    settings = _prepared_settings()

    result = run_smoke(bundle, case_ids, args.output, settings=settings, dry_run=args.dry_run)
    _print_summary(
        {
            "status": "dry_run" if result["dry_run"] else "ok",
            "cases": len(result["results"]),
            "failure_categories": result.get("failure_categories"),
            "output": str(args.output),
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "select" and not args.all and args.limit is None:
        args.limit = 30
    try:
        if args.command == "build":
            lock = load_source_lock(args.source_lock)
            result = build_artifacts(
                args.qmsum,
                args.explainmeetsum,
                args.output,
                source_lock=lock,
            )
            _print_summary(
                {
                    "status": result["status"],
                    "dataset_hash": result["dataset_hash"],
                    "counts": result["counts"],
                    "output": str(args.output),
                }
            )
            return 0

        if args.command == "validate":
            if (args.qmsum is None) != (args.explainmeetsum is None):
                raise ArtifactError("validate_sources_requires_both_inputs")
            lock = load_source_lock(args.source_lock)
            validate_kwargs: dict[str, Any] = {"source_lock": lock}
            if args.qmsum is not None and args.explainmeetsum is not None:
                validate_kwargs.update(
                    {"qmsum": args.qmsum, "explainmeetsum": args.explainmeetsum}
                )
            result = validate_artifacts(args.output, **validate_kwargs)
            _print_summary(
                {
                    "status": result["status"],
                    "dataset_hash": result["dataset_hash"],
                    "counts": result["counts"],
                    "source_verified": args.qmsum is not None,
                }
            )
            return 0

        if args.command == "select":
            bundle = load_artifact_bundle(args.cases)
            selection = select_cases(
                bundle["cases"],
                dataset_hash=bundle["dataset_hash"],
                split=args.split,
                limit=None if args.all else args.limit,
                seed=args.seed,
                leakage=bundle["audit"]["leakage"],
            )
            validate_selection(
                selection,
                bundle["cases"],
                dataset_hash=bundle["dataset_hash"],
                leakage=bundle["audit"]["leakage"],
            )
            write_selection(args.output, selection)
            _print_summary(selection)
            return 0

        if args.command == "rq1":
            return _run_rq1(args)

        if args.command == "agent-smoke":
            return _run_agent_smoke(args)

        bundle = load_artifact_bundle(args.cases)
        selection = _read_json(args.selection)
        if not isinstance(selection, dict):
            raise ArtifactError("invalid_selection")
        validate_selection(
            selection,
            bundle["cases"],
            dataset_hash=bundle["dataset_hash"],
            leakage=bundle["audit"]["leakage"],
        )
        predictions = _read_json(args.predictions)
        if not isinstance(predictions, dict):
            raise ArtifactError("invalid_predictions")
        report = score_cases(
            bundle["cases"],
            predictions,
            corpus=bundle["corpus"],
            selection=_selection_for_scorer(selection),
            dataset_hash=bundle["dataset_hash"],
            policy=args.policy,
            mode=args.mode,
            ks=args.ks,
        )
        report["selection_diagnostics"] = _selection_diagnostics_for_report(selection)
        write_json(args.output, report)
        _print_summary(
            {
                "status": report["status"],
                "dataset_hash": report["dataset_hash"],
                "selection_hash": report["selection_hash"],
                "prediction_hash": report["prediction_hash"],
                "held_out_eligible": report["selection_diagnostics"].get(
                    "held_out_eligible"
                ),
            }
        )
        return 2 if report["status"] == "incomplete" else 0
    except (
        ArtifactError,
        SourceFormatError,
        ScoringError,
        ProductionPathError,
        StudyError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
