"""CLI for the isolated video-recognition benchmark harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .probe import run_formal_probe, write_probe_snapshot
from .redaction import redact_report
from .pilot_schema import load_pilot_manifest, pilot_summary
from .runner import dry_run, pilot_dry_run, planned_pilot_units, run_provider_benchmark
from .schema import CatalogValidationError, load_catalog, load_probe_snapshot, catalog_summary


class _CLIArgumentError(ValueError):
    """Argparse failure without echoing user-controlled argument text."""


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _CLIArgumentError("cli_arguments_invalid")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(prog="python -m evals.video_recognition")
    parser.add_argument("--catalog", type=Path, default=None, help="catalog YAML path")
    parser.add_argument("--pilot-catalog", type=Path, default=None, help="pilot planning manifest YAML path")
    parser.add_argument("--validate-catalog", action="store_true")
    parser.add_argument("--validate-pilot", action="store_true", help="validate the offline pilot planning manifest")
    parser.add_argument("--probe", action="store_true", help="emit the safe metadata/subtitle probe projection")
    parser.add_argument("--live", action="store_true", help="run the public connector probe; never calls recognition providers")
    parser.add_argument("--probe-output", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="run local schemas, fixture adapters, redaction, and cleanup")
    parser.add_argument("--pilot-dry-run", action="store_true", help="run the offline pilot planner and fixture checks")
    parser.add_argument("--temp-base-dir", type=Path, default=None)
    parser.add_argument("--provider-preflight", action="store_true", help="show credential presence only")
    parser.add_argument("--run", action="store_true", help="attempt provider benchmark (always gated in this revision)")
    parser.add_argument(
        "--openai-smoke",
        action="store_true",
        help="run the isolated OpenAI smoke test (requires explicit confirmation)",
    )
    parser.add_argument(
        "--confirm-openai-smoke",
        action="store_true",
        help="confirm the four low-volume OpenAI smoke calls",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except _CLIArgumentError:
        print("video-recognition benchmark unavailable: cli_arguments_invalid", file=sys.stderr)
        return 2
    pilot_flags = (args.validate_pilot, args.pilot_dry_run)
    legacy_flags = (args.validate_catalog, args.probe, args.dry_run, args.provider_preflight, args.run, args.live)
    if args.confirm_openai_smoke and not args.openai_smoke:
        print("video-recognition smoke unavailable: smoke_confirmation_requires_smoke_flag", file=sys.stderr)
        return 2
    if args.pilot_catalog is not None and not any(pilot_flags):
        print("video-recognition pilot unavailable: pilot_catalog_requires_pilot_flag", file=sys.stderr)
        return 2
    if args.probe_output is not None and not args.probe:
        print("video-recognition benchmark unavailable: probe_output_requires_probe_flag", file=sys.stderr)
        return 2
    if args.openai_smoke:
        # Dispatch before loading the catalog.  The smoke path is intentionally
        # independent from the formal benchmark and its execution gate.
        benchmark_flags = (
            args.validate_catalog,
            args.validate_pilot,
            args.probe,
            args.dry_run,
            args.pilot_dry_run,
            args.provider_preflight,
            args.run,
            args.live,
            args.catalog is not None,
            args.pilot_catalog is not None,
            args.probe_output is not None,
        )
        if any(benchmark_flags):
            print("video-recognition smoke unavailable: openai_smoke_flags_conflict", file=sys.stderr)
            return 2
        from .openai_smoke import (
            OpenAISmokeError,
            SMOKE_PROVIDER_FAILURE_EXIT_CODE,
            confirmation_from_environment,
            run_openai_smoke,
            safe_blocked_report,
        )

        try:
            report = run_openai_smoke(
                confirm=args.confirm_openai_smoke or confirmation_from_environment(),
                temp_base_dir=args.temp_base_dir,
            )
        except OpenAISmokeError as exc:
            print(json.dumps(safe_blocked_report(exc.error_code), ensure_ascii=False, sort_keys=True))
            return 2
        except Exception:
            # Keep CLI failures stable; never expose provider messages, bodies,
            # headers, URLs, credentials, or temporary paths.
            print(json.dumps({"status": "blocked", "error_code": "smoke_unavailable"}, sort_keys=True))
            return 2
        payload = report.as_dict()
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        if any(not record.success for record in report.records):
            return SMOKE_PROVIDER_FAILURE_EXIT_CODE
        return 0
    if sum(bool(flag) for flag in pilot_flags) > 1:
        print("video-recognition pilot unavailable: pilot_flags_conflict", file=sys.stderr)
        return 2
    if any(pilot_flags) and any(legacy_flags):
        print("video-recognition pilot unavailable: pilot_flags_conflict", file=sys.stderr)
        return 2
    if args.live and not args.probe:
        print("video-recognition benchmark unavailable: live_probe_requires_probe_flag", file=sys.stderr)
        return 2
    if not any((*legacy_flags, *pilot_flags)):
        args.dry_run = True
    try:
        catalog = load_catalog(args.catalog)
        if args.validate_pilot or args.pilot_dry_run:
            pilot = load_pilot_manifest(args.pilot_catalog, catalog=catalog)
            if args.validate_pilot:
                payload = pilot_summary(pilot, catalog)
                plan = planned_pilot_units(pilot, catalog=catalog)
                payload.update(
                    {
                        "status": "pilot_validated",
                        "plan": plan,
                        "potential_units": plan["potential_units"],
                        "planned_default_units": plan["planned_default_units"],
                        "fallback_planned_units": plan["fallback_planned_units"],
                        "external_calls": 0,
                    }
                )
                print(json.dumps(redact_report(payload), ensure_ascii=False, indent=2, sort_keys=True))
            else:
                report = pilot_dry_run(pilot, catalog=catalog, temp_base_dir=args.temp_base_dir)
                print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.validate_catalog:
            print(json.dumps(catalog_summary(catalog), ensure_ascii=False, indent=2, sort_keys=True))
        if args.provider_preflight:
            from .adapters import credential_status

            print(
                json.dumps(
                    {"profiles": [credential_status(profile).as_dict() for profile in catalog.profiles]},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        if args.probe:
            snapshot = run_formal_probe(catalog, live=args.live)
            payload = redact_report(snapshot.model_dump(mode="json"))
            if args.probe_output:
                write_probe_snapshot(snapshot, args.probe_output)
                payload = {"output_written": True, "record_count": len(snapshot.records), "probe_kind": snapshot.probe_kind}
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        if args.dry_run:
            report = dry_run(catalog, temp_base_dir=args.temp_base_dir)
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        if args.run:
            run_provider_benchmark(catalog=catalog)
    except CatalogValidationError as exc:
        print(f"video-recognition benchmark unavailable: {exc.error_code}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Keep CLI failures stable and free of provider response bodies,
        # signed URLs, or credentials.
        code = getattr(exc, "error_code", None) or type(exc).__name__
        print(f"video-recognition benchmark unavailable: {code}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
