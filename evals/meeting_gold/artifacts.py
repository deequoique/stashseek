"""Deterministic build, validation, and selection for meeting-gold artifacts.

The source readers own source-shaped parsing and joins.  This module owns the
boundary between those in-memory objects and the reproducible files consumed
by an evaluator.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .core import (
    ALLOWED_OUTPUT,
    COMMITS,
    CONVERTER_VERSION,
    EVIDENCE_TYPES,
    ELIGIBILITY_STATES,
    QUERY_KINDS,
    SCHEMA_VERSION,
    SPLITS,
    Case,
    MeetingCorpus,
    Sentence,
    SourceFormatError,
    Turn,
    canonical_bytes,
    canonical_hash,
)
from .sources import join_explainable, load_json_files, parse_qmsum


ARTIFACT_VERSION = "meeting-gold-artifacts-v1"
SOURCE_LOCK_VERSION = "meeting-gold-source-lock-v1"
DESCRIPTOR_VERSION = "meeting-gold-dataset-hash-v1"
VALIDATION_POLICY_VERSION = "meeting-gold-validation-v1"
EVIDENCE_POLICY_VERSION = "meeting-gold-evidence-v1"
LEAKAGE_POLICY_VERSION = "meeting-gold-leakage-v1"
FREEZE_BASELINE_VERSION = "meeting-gold-freeze-baseline-v1"
SELECTION_VERSION = "meeting-gold-selection-v1"
SELECTION_POLICY = "meeting-gold-stratified-domain-kind-v1"
MAX_SMOKE_SELECTION = 30
QMSUM_DOMAINS = {
    "Academic": "Academic(icsi)",
    "Product": "Product(ami)",
    "Committee": "Committee",
}

PAYLOAD_FILES = tuple(ALLOWED_OUTPUT)
OUTPUT_FILES = PAYLOAD_FILES + ("manifest.json",)
CASE_FIELDS = tuple(
    Case(
        case_id="case",
        meeting_id="meeting",
        split="val",
        domain="domain",
        query_kind="general",
        query_index=0,
        query="query",
        answer="answer",
    ).asdict()
)
POLICIES = ("ces", "ces_pes")
_HEX64 = frozenset("0123456789abcdef")
_SOURCE_NAMES = ("qmsum", "explainmeetsum")
_APPROVED_LEGACY_EXCEPTION_INVENTORY_HASH = (
    "9e34367d4ec5cbd1f90b0ea5af80ce0ebcbb44987dc93bead0313150f33a22be"
)
# The approved source lock predates the coverage field. Keep its bytes and
# exception inventory unchanged while carrying forward the supervisor-reviewed
# coverage audit until that lock is deliberately versioned.
_APPROVED_LEGACY_COVERAGE = {
    "test/Academic(icsi)/general": {
        "cases": 9,
        "eligibility": {
            "ces": {"answer_only": 1, "citation_scorable": 7, "partial_annotation": 1},
            "ces_pes": {"answer_only": 1, "citation_scorable": 7, "partial_annotation": 1},
        },
    },
    "test/Academic(icsi)/specific": {
        "cases": 49,
        "eligibility": {
            "ces": {"answer_only": 4, "citation_scorable": 43, "partial_annotation": 2},
            "ces_pes": {"answer_only": 4, "citation_scorable": 43, "partial_annotation": 2},
        },
    },
    "test/Committee/general": {
        "cases": 6,
        "eligibility": {
            "ces": {"citation_scorable": 6},
            "ces_pes": {"citation_scorable": 6},
        },
    },
    "test/Committee/specific": {
        "cases": 66,
        "eligibility": {
            "ces": {"citation_scorable": 66},
            "ces_pes": {"citation_scorable": 66},
        },
    },
    "test/Product(ami)/general": {
        "cases": 22,
        "eligibility": {
            "ces": {"citation_scorable": 22},
            "ces_pes": {"citation_scorable": 22},
        },
    },
    "test/Product(ami)/specific": {
        "cases": 129,
        "eligibility": {
            "ces": {"citation_scorable": 126, "partial_annotation": 3},
            "ces_pes": {"citation_scorable": 129},
        },
    },
    "train/Academic(icsi)/general": {
        "cases": 41,
        "eligibility": {
            "ces": {"answer_only": 5, "citation_scorable": 34, "partial_annotation": 2},
            "ces_pes": {"answer_only": 5, "citation_scorable": 36},
        },
    },
    "train/Academic(icsi)/specific": {
        "cases": 218,
        "eligibility": {
            "ces": {"answer_only": 20, "citation_scorable": 196, "partial_annotation": 2},
            "ces_pes": {"answer_only": 20, "citation_scorable": 198},
        },
    },
    "train/Committee/general": {
        "cases": 24,
        "eligibility": {
            "ces": {"answer_only": 1, "citation_scorable": 21, "partial_annotation": 2},
            "ces_pes": {"answer_only": 1, "citation_scorable": 23},
        },
    },
    "train/Committee/specific": {
        "cases": 284,
        "eligibility": {
            "ces": {"answer_only": 11, "citation_scorable": 266, "partial_annotation": 7},
            "ces_pes": {"answer_only": 11, "citation_scorable": 271, "partial_annotation": 2},
        },
    },
    "train/Product(ami)/general": {
        "cases": 97,
        "eligibility": {
            "ces": {"answer_only": 1, "citation_scorable": 95, "partial_annotation": 1},
            "ces_pes": {"answer_only": 1, "citation_scorable": 95, "partial_annotation": 1},
        },
    },
    "train/Product(ami)/specific": {
        "cases": 593,
        "eligibility": {
            "ces": {"answer_only": 2, "citation_scorable": 581, "partial_annotation": 10},
            "ces_pes": {"answer_only": 2, "citation_scorable": 588, "partial_annotation": 3},
        },
    },
    "val/Academic(icsi)/general": {
        "cases": 9,
        "eligibility": {
            "ces": {"answer_only": 2, "citation_scorable": 5, "partial_annotation": 2},
            "ces_pes": {"answer_only": 2, "citation_scorable": 5, "partial_annotation": 2},
        },
    },
    "val/Academic(icsi)/specific": {
        "cases": 45,
        "eligibility": {
            "ces": {"answer_only": 4, "citation_scorable": 41},
            "ces_pes": {"answer_only": 4, "citation_scorable": 41},
        },
    },
    "val/Committee/general": {
        "cases": 6,
        "eligibility": {
            "ces": {"citation_scorable": 6},
            "ces_pes": {"citation_scorable": 6},
        },
    },
    "val/Committee/specific": {
        "cases": 67,
        "eligibility": {
            "ces": {"citation_scorable": 65, "partial_annotation": 2},
            "ces_pes": {"citation_scorable": 66, "partial_annotation": 1},
        },
    },
    "val/Product(ami)/general": {
        "cases": 20,
        "eligibility": {
            "ces": {"citation_scorable": 19, "partial_annotation": 1},
            "ces_pes": {"citation_scorable": 20},
        },
    },
    "val/Product(ami)/specific": {
        "cases": 125,
        "eligibility": {
            "ces": {"citation_scorable": 121, "partial_annotation": 4},
            "ces_pes": {"citation_scorable": 124, "partial_annotation": 1},
        },
    },
}
_BASELINE_EXCEPTION_FIELDS = {
    "answer_only_case_ids",
    "partial_case_ids",
    "quarantined_case_ids",
    "missing_explainmeetsum_meetings",
    "unmatched_explainable_ids",
}
_SELECTION_CORE_FIELDS = {
    "selection_version",
    "dataset_hash",
    "split",
    "selected_case_ids",
    "seed",
    "selection_policy",
    "selection_hash",
}
_SELECTION_DIAGNOSTIC_FIELDS = {
    "shortfalls",
    "held_out_eligible",
    "held_out_reasons",
}

NOTICE_TEXT = """# Meeting Golden Set Notice

This artifact contains material from the following MIT-licensed projects:

* QMSum, Copyright (c) 2021 Yale-LILY. The complete upstream license text is
  included in `QMSUM_LICENSE`.
* ExplainMeetSum, Copyright (c) 2023 Hyun Kim, Euidong Yang. The complete
  upstream license text is included in `EXPLAINMEETSUM_LICENSE`.

Paper attribution:

* Zhong et al., "QMSum: A New Benchmark for Query-based Multi-domain Meeting
  Summarization," NAACL 2021, https://arxiv.org/abs/2104.05938.
* Kim et al., "ExplainMeetSum: A Dataset for Explainable Meeting Summarization
  Aligned with Human Intent," ACL 2023,
  https://aclanthology.org/2023.acl-long.731/.

This package uses the published text datasets only; it does not redistribute
audio or video and makes no claim about licenses for media that may be
referenced upstream.
"""
NOTICE_BYTES = NOTICE_TEXT.encode("utf-8")


class ArtifactError(ValueError):
    """Raised when an artifact or its independent integrity checks fail."""


class SourceIntegrityError(ArtifactError):
    """Raised when a pinned checkout is not the exact locked source."""


def _fail(message: str) -> None:
    raise ArtifactError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _is_safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and "." not in path.parts


def _require_source_hash(value: Any, label: str) -> None:
    if not _is_hash(value):
        raise SourceIntegrityError(f"invalid_source_hash:{label}")


def _require_sorted_unique_strings(value: Any, label: str) -> None:
    if (
        not isinstance(value, list)
        or any(type(item) is not str or not item for item in value)
        or value != sorted(set(value))
    ):
        raise SourceIntegrityError(f"invalid_source_id_list:{label}")


def _assert_no_symlink(path: Path) -> None:
    """Reject a symlink at the path itself.

    Ancestors are checked separately when a caller has an explicit trusted
    root; this keeps ordinary `/tmp` paths valid on macOS where `/tmp` itself
    is commonly a system symlink.
    """

    if Path(path).is_symlink():
        raise ArtifactError(f"symlink_path:{path.name}")


def _assert_no_symlink_below(root: Path, path: Path) -> None:
    root_real = Path(os.path.realpath(root))
    candidate = Path(os.path.abspath(path))
    boundary = candidate
    while boundary != boundary.parent and Path(os.path.realpath(boundary)) != root_real:
        boundary = boundary.parent
    if Path(os.path.realpath(boundary)) != root_real:
        raise ArtifactError("path_outside_trusted_root")
    if boundary.is_symlink():
        raise ArtifactError(f"symlink_path:{boundary.name}")
    try:
        relative = candidate.relative_to(boundary)
    except ValueError as exc:
        raise ArtifactError("path_outside_trusted_root") from exc
    current = boundary
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ArtifactError(f"symlink_path:{current.name}")


def _assert_directory(path: Path, label: str) -> None:
    _assert_no_symlink(path)
    if not path.is_dir():
        raise ArtifactError(f"invalid_directory:{label}")


def _assert_regular_file(path: Path, label: str) -> None:
    _assert_no_symlink(path)
    if not path.is_file():
        raise ArtifactError(f"invalid_file:{label}")


def _canonical_json_load(path: Path, label: str) -> Any:
    _assert_regular_file(path, label)
    raw = path.read_bytes()

    def duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ArtifactError(f"duplicate_json_key:{label}:{key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ArtifactError(f"non_finite_json:{label}:{value}")

    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=duplicate_key,
            parse_constant=reject_constant,
        )
    except ArtifactError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid_json:{label}") from exc
    if canonical_bytes(value) != raw:
        raise ArtifactError(f"noncanonical_json:{label}")
    return value


def _canonical_jsonl_load(path: Path, label: str) -> list[dict[str, Any]]:
    _assert_regular_file(path, label)
    raw = path.read_bytes()
    if not raw:
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
        if not line.endswith(b"\n") or line.endswith(b"\r\n"):
            raise ArtifactError(f"noncanonical_jsonl:{label}:{line_number}")
        try:
            value = json.loads(
                line[:-1].decode("utf-8"),
                object_pairs_hook=lambda pairs: _object_from_pairs(
                    pairs, f"{label}:{line_number}"
                ),
                parse_constant=lambda constant: _reject_constant(
                    constant, f"{label}:{line_number}"
                ),
            )
        except ArtifactError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError(f"invalid_jsonl:{label}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ArtifactError(f"jsonl_row_not_object:{label}:{line_number}")
        if canonical_bytes(value) != line:
            raise ArtifactError(f"noncanonical_jsonl:{label}:{line_number}")
        rows.append(value)
    return rows


def _object_from_pairs(pairs: list[tuple[str, Any]], label: str) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactError(f"duplicate_json_key:{label}:{key}")
        value[key] = item
    return value


def _reject_constant(value: str, label: str) -> None:
    raise ArtifactError(f"non_finite_json:{label}:{value}")


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(path, canonical_bytes(value))


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    _write_bytes(path, b"".join(canonical_bytes(dict(row)) for row in rows))


def _path_relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise SourceIntegrityError("path_outside_source_root") from exc


def _git_output(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SourceIntegrityError("git_source_check_failed") from exc
    return result.stdout.strip()


def _git_root(path: Path) -> Path:
    return Path(os.path.abspath(_git_output(path, "rev-parse", "--show-toplevel")))


def _tracked_files(repo_root: Path) -> dict[str, str]:
    raw = _git_output(repo_root, "ls-files", "-z")
    names = [name for name in raw.split("\x00") if name]
    result: dict[str, str] = {}
    for name in names:
        path = repo_root / name
        _assert_regular_file(path, f"tracked:{name}")
        result[name] = _sha256_bytes(path.read_bytes())
    return dict(sorted(result.items()))


def _expected_source_spec(lock: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    sources = lock.get("sources")
    if not isinstance(sources, dict) or not isinstance(sources.get(name), dict):
        raise SourceIntegrityError(f"missing_source_lock:{name}")
    return sources[name]


def _is_approved_legacy_baseline(baseline: Mapping[str, Any]) -> bool:
    expected = baseline.get("expected")
    exception_ids = baseline.get("known_exception_ids")
    if not isinstance(expected, Mapping) or not isinstance(exception_ids, Mapping):
        return False
    try:
        exception_hash = canonical_hash(dict(exception_ids))
    except (TypeError, ValueError):
        return False
    return (
        exception_hash == _APPROVED_LEGACY_EXCEPTION_INVENTORY_HASH
        and expected.get("qmsum_meetings") == 232
        and expected.get("explainmeetsum_meetings") == 231
        and expected.get("qmsum_cases") == 1810
        and expected.get("exact_joined_cases") == 1759
        and expected.get("answer_only_cases") == 51
        and expected.get("unknown_domain_cases") == 0
    )


def _baseline_expected(baseline: Mapping[str, Any]) -> dict[str, Any]:
    expected = baseline.get("expected")
    if not isinstance(expected, Mapping):
        raise SourceIntegrityError("invalid_freeze_baseline_expected")
    value = dict(expected)
    if "coverage" not in value:
        if not _is_approved_legacy_baseline(baseline):
            raise SourceIntegrityError("invalid_freeze_baseline_coverage")
        value["coverage"] = _APPROVED_LEGACY_COVERAGE
    return value


def _validate_baseline_coverage(
    coverage: Any,
    *,
    qmsum_cases: int,
    eligibility: Mapping[str, Any],
) -> None:
    if not isinstance(coverage, Mapping) or not coverage:
        raise SourceIntegrityError("invalid_freeze_baseline_coverage")
    total_cases = 0
    policy_totals: dict[str, Counter[str]] = {
        policy: Counter() for policy in POLICIES
    }
    for key, group in coverage.items():
        if not isinstance(key, str):
            raise SourceIntegrityError("invalid_freeze_baseline_coverage")
        parts = key.split("/")
        if len(parts) != 3 or parts[0] not in SPLITS or not parts[1] or parts[2] not in QUERY_KINDS:
            raise SourceIntegrityError("invalid_freeze_baseline_coverage")
        if not isinstance(group, Mapping) or set(group) != {"cases", "eligibility"}:
            raise SourceIntegrityError("invalid_freeze_baseline_coverage")
        cases = group["cases"]
        if type(cases) is not int or cases <= 0:
            raise SourceIntegrityError("invalid_freeze_baseline_coverage")
        group_eligibility = group["eligibility"]
        if not isinstance(group_eligibility, Mapping) or set(group_eligibility) != set(POLICIES):
            raise SourceIntegrityError("invalid_freeze_baseline_coverage")
        total_cases += cases
        for policy in POLICIES:
            policy_counts = group_eligibility[policy]
            if not isinstance(policy_counts, Mapping) or not set(policy_counts) <= set(
                ELIGIBILITY_STATES
            ):
                raise SourceIntegrityError("invalid_freeze_baseline_coverage")
            if any(type(count) is not int or count < 0 for count in policy_counts.values()):
                raise SourceIntegrityError("invalid_freeze_baseline_coverage")
            if sum(policy_counts.values()) != cases:
                raise SourceIntegrityError("invalid_freeze_baseline_coverage")
            policy_totals[policy].update(policy_counts)
    if total_cases != qmsum_cases:
        raise SourceIntegrityError("invalid_freeze_baseline_coverage")
    for policy in POLICIES:
        if dict(sorted(policy_totals[policy].items())) != dict(
            sorted(eligibility[policy].items())
        ):
            raise SourceIntegrityError("invalid_freeze_baseline_coverage")


def _validate_source_lock_document(
    lock: Mapping[str, Any], *, require_accepted_baseline: bool
) -> None:
    """Validate the lock shape before using any of its paths or hashes."""

    if not isinstance(lock, Mapping) or lock.get("lock_version") != SOURCE_LOCK_VERSION:
        raise SourceIntegrityError("unsupported_source_lock")
    if set(lock) not in (
        {"lock_version", "sources"},
        {"lock_version", "sources", "baseline"},
    ):
        raise SourceIntegrityError("invalid_source_lock_shape")
    sources = lock.get("sources")
    if not isinstance(sources, Mapping) or set(sources) != set(_SOURCE_NAMES):
        raise SourceIntegrityError("invalid_source_lock_sources")

    for name in _SOURCE_NAMES:
        spec = _expected_source_spec(lock, name)
        expected_keys = {
            "repository",
            "commit",
            "canonical_root",
            "license_path",
            "readme_path",
            "license_sha256",
            "readme_sha256",
            "canonical_files",
            "tracked_files",
        }
        if name == "qmsum":
            expected_keys.add("domain_membership")
        if set(spec) != expected_keys:
            raise SourceIntegrityError(f"invalid_source_lock_shape:{name}")
        if not isinstance(spec.get("repository"), str) or not spec["repository"]:
            raise SourceIntegrityError(f"invalid_source_repository:{name}")
        if spec.get("commit") != COMMITS[name]:
            raise SourceIntegrityError(f"source_lock_commit_mismatch:{name}")
        if not _is_safe_relative_path(spec.get("canonical_root")):
            raise SourceIntegrityError(f"invalid_source_canonical_root:{name}")
        if spec["canonical_root"] != (
            "data/ALL" if name == "qmsum" else "data/ExplainMeetSum"
        ):
            raise SourceIntegrityError(f"invalid_source_canonical_root:{name}")
        for field in ("license_path", "readme_path"):
            if not _is_safe_relative_path(spec.get(field)):
                raise SourceIntegrityError(f"invalid_source_metadata_path:{name}:{field}")
        _require_source_hash(spec.get("license_sha256"), f"{name}:license")
        _require_source_hash(spec.get("readme_sha256"), f"{name}:readme")

        canonical_files = spec.get("canonical_files")
        if not isinstance(canonical_files, Mapping) or set(canonical_files) != set(SPLITS):
            raise SourceIntegrityError(f"invalid_canonical_file_inventory:{name}")
        for split in SPLITS:
            files = canonical_files[split]
            if not isinstance(files, Mapping):
                raise SourceIntegrityError(f"invalid_canonical_file_inventory:{name}:{split}")
            for file_name, digest in files.items():
                if (
                    not isinstance(file_name, str)
                    or Path(file_name).name != file_name
                    or not file_name.endswith(".json")
                ):
                    raise SourceIntegrityError(f"invalid_canonical_file_name:{name}:{split}")
                _require_source_hash(digest, f"{name}:{split}/{file_name}")

        tracked_files = spec.get("tracked_files")
        if not isinstance(tracked_files, Mapping) or not tracked_files:
            raise SourceIntegrityError(f"invalid_tracked_file_inventory:{name}")
        for file_name, digest in tracked_files.items():
            if not _is_safe_relative_path(file_name):
                raise SourceIntegrityError(f"invalid_tracked_file_name:{name}")
            _require_source_hash(digest, f"{name}:{file_name}")

        if name == "qmsum":
            membership = spec.get("domain_membership")
            if not isinstance(membership, Mapping) or set(membership) != {"root", "domains"}:
                raise SourceIntegrityError("invalid_domain_membership_lock")
            if membership.get("root") != "data":
                raise SourceIntegrityError("invalid_domain_membership_root")
            domains = membership.get("domains")
            if not isinstance(domains, Mapping) or set(domains) != set(QMSUM_DOMAINS):
                raise SourceIntegrityError("invalid_domain_membership_lock")
            for source_domain, output_domain in QMSUM_DOMAINS.items():
                domain_spec = domains[source_domain]
                if not isinstance(domain_spec, Mapping) or set(domain_spec) != {"label", "files"}:
                    raise SourceIntegrityError(f"invalid_domain_membership:{source_domain}")
                if domain_spec.get("label") != output_domain:
                    raise SourceIntegrityError(f"invalid_domain_membership_label:{source_domain}")
                files_by_split = domain_spec.get("files")
                if not isinstance(files_by_split, Mapping) or set(files_by_split) != set(SPLITS):
                    raise SourceIntegrityError(f"invalid_domain_membership_files:{source_domain}")
                for split in SPLITS:
                    files = files_by_split[split]
                    canonical = canonical_files[split]
                    if not isinstance(files, Mapping):
                        raise SourceIntegrityError(
                            f"invalid_domain_membership_files:{source_domain}:{split}"
                        )
                    if not set(files) <= set(canonical):
                        raise SourceIntegrityError(
                            f"domain_membership_file_not_canonical:{source_domain}:{split}"
                        )
                    for file_name, digest in files.items():
                        _require_source_hash(
                            digest, f"domain:{source_domain}:{split}/{file_name}"
                        )
                        if digest != canonical[file_name]:
                            raise SourceIntegrityError(
                                f"domain_membership_canonical_mismatch:{source_domain}:{split}:{file_name}"
                            )
            for split in SPLITS:
                covered = set().union(
                    *(
                        set(domains[source_domain]["files"][split])
                        for source_domain in QMSUM_DOMAINS
                    )
                )
                if covered != set(canonical_files[split]):
                    raise SourceIntegrityError(f"incomplete_domain_membership:{split}")

    baseline = lock.get("baseline")
    if not require_accepted_baseline and baseline is None:
        return
    if not isinstance(baseline, Mapping):
        raise SourceIntegrityError("missing_freeze_baseline")
    expected_baseline_keys = {
        "version",
        "status",
        "accepted",
        "expected",
        "known_exception_ids",
        "known_leakage",
    }
    if set(baseline) != expected_baseline_keys:
        raise SourceIntegrityError("invalid_freeze_baseline_shape")
    if baseline.get("version") != FREEZE_BASELINE_VERSION:
        raise SourceIntegrityError("unsupported_freeze_baseline")
    if baseline.get("accepted") is not True or baseline.get("status") != "accepted":
        if require_accepted_baseline:
            raise SourceIntegrityError("freeze_baseline_not_accepted")
    expected = baseline.get("expected")
    if not isinstance(expected, Mapping):
        raise SourceIntegrityError("invalid_freeze_baseline_expected")
    required_expected = {
        "qmsum_meetings",
        "explainmeetsum_meetings",
        "qmsum_cases",
        "exact_joined_cases",
        "answer_only_cases",
        "unknown_domain_cases",
        "eligibility",
        "audit_counts",
    }
    if set(expected) not in (required_expected, required_expected | {"coverage"}):
        raise SourceIntegrityError("invalid_freeze_baseline_expected")
    for field in (
        "qmsum_meetings",
        "explainmeetsum_meetings",
        "qmsum_cases",
        "exact_joined_cases",
        "answer_only_cases",
        "unknown_domain_cases",
    ):
        if type(expected[field]) is not int or expected[field] < 0:
            raise SourceIntegrityError(f"invalid_freeze_baseline_count:{field}")
    eligibility = expected["eligibility"]
    if not isinstance(eligibility, Mapping) or set(eligibility) != set(POLICIES):
        raise SourceIntegrityError("invalid_freeze_baseline_eligibility")
    for policy in POLICIES:
        policy_counts = eligibility[policy]
        if not isinstance(policy_counts, Mapping):
            raise SourceIntegrityError(f"invalid_freeze_baseline_eligibility:{policy}")
        if set(policy_counts) - set(ELIGIBILITY_STATES):
            raise SourceIntegrityError(f"invalid_freeze_baseline_eligibility:{policy}")
        for state, count in policy_counts.items():
            if type(count) is not int or count < 0:
                raise SourceIntegrityError(
                    f"invalid_freeze_baseline_eligibility_count:{policy}:{state}"
                )
        if sum(policy_counts.values()) != expected["qmsum_cases"]:
            raise SourceIntegrityError(f"invalid_freeze_baseline_eligibility_total:{policy}")
    if not isinstance(expected["audit_counts"], Mapping) or any(
        type(value) is not int or value < 0
        for value in expected["audit_counts"].values()
    ):
        raise SourceIntegrityError("invalid_freeze_baseline_audit_counts")
    exception_ids = baseline.get("known_exception_ids")
    if not isinstance(exception_ids, Mapping) or set(exception_ids) != _BASELINE_EXCEPTION_FIELDS:
        raise SourceIntegrityError("invalid_freeze_exception_inventory")
    for field in (
        "answer_only_case_ids",
        "quarantined_case_ids",
        "missing_explainmeetsum_meetings",
        "unmatched_explainable_ids",
    ):
        _require_sorted_unique_strings(exception_ids[field], field)
    partial_ids = exception_ids["partial_case_ids"]
    if not isinstance(partial_ids, Mapping) or set(partial_ids) != set(POLICIES):
        raise SourceIntegrityError("invalid_freeze_partial_exception_inventory")
    for policy in POLICIES:
        _require_sorted_unique_strings(partial_ids[policy], f"partial_case_ids:{policy}")
    if len(exception_ids["answer_only_case_ids"]) != expected["answer_only_cases"]:
        raise SourceIntegrityError("invalid_freeze_exception_inventory")
    if any(
        len(partial_ids[policy])
        != expected["eligibility"][policy].get("partial_annotation", 0)
        for policy in POLICIES
    ):
        raise SourceIntegrityError("invalid_freeze_exception_inventory")
    quarantine_counts = [
        expected["eligibility"][policy].get("quarantined", 0) for policy in POLICIES
    ]
    if len(set(quarantine_counts)) != 1 or len(exception_ids["quarantined_case_ids"]) != quarantine_counts[0]:
        raise SourceIntegrityError("invalid_freeze_exception_inventory")
    if len(exception_ids["missing_explainmeetsum_meetings"]) != expected["audit_counts"].get(
        "missing_explainmeetsum_meeting", 0
    ):
        raise SourceIntegrityError("invalid_freeze_exception_inventory")
    if len(exception_ids["unmatched_explainable_ids"]) != expected["audit_counts"].get(
        "unmatched_explainable", 0
    ):
        raise SourceIntegrityError("invalid_freeze_exception_inventory")

    expected_with_coverage = _baseline_expected(baseline)
    _validate_baseline_coverage(
        expected_with_coverage["coverage"],
        qmsum_cases=expected["qmsum_cases"],
        eligibility=eligibility,
    )

    leakage = baseline.get("known_leakage")
    if not isinstance(leakage, list):
        raise SourceIntegrityError("invalid_freeze_leakage_inventory")
    if leakage != sorted(leakage, key=lambda item: canonical_bytes(item)):
        raise SourceIntegrityError("unsorted_freeze_leakage_inventory")
    for item in leakage:
        if not isinstance(item, Mapping) or set(item) != {
            "type",
            "hash",
            "cases",
            "splits",
            "meetings",
        }:
            raise SourceIntegrityError("invalid_freeze_leakage_record")
        if item["type"] not in {"transcript", "answer", "query_tuple"}:
            raise SourceIntegrityError("invalid_freeze_leakage_record")
        _require_source_hash(item.get("hash"), "known_leakage")
        for field in ("cases", "splits", "meetings"):
            _require_sorted_unique_strings(item[field], f"known_leakage:{field}")
        if any(split not in SPLITS for split in item["splits"]):
            raise SourceIntegrityError("invalid_freeze_leakage_record")


def _canonical_split_inventory(root: Path, expected: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    _assert_directory(root, "canonical_input_root")
    actual: dict[str, dict[str, str]] = {}
    expected_files = expected.get("canonical_files")
    if not isinstance(expected_files, dict):
        raise SourceIntegrityError("missing_canonical_file_inventory")
    for split in SPLITS:
        split_root = root / split
        _assert_directory(split_root, f"canonical_split:{split}")
        expected_split = expected_files.get(split)
        if not isinstance(expected_split, dict):
            raise SourceIntegrityError(f"missing_canonical_split_inventory:{split}")
        names = set(expected_split)
        entries = list(split_root.iterdir())
        for entry in entries:
            if entry.is_symlink():
                raise SourceIntegrityError(f"symlink_in_canonical_split:{split}")
            if not entry.is_file() or entry.name not in names:
                raise SourceIntegrityError(f"extra_canonical_input:{split}:{entry.name}")
        if {entry.name for entry in entries} != names:
            missing = sorted(names - {entry.name for entry in entries})
            raise SourceIntegrityError(
                f"missing_canonical_input:{split}:{missing[0] if missing else 'unknown'}"
            )
        actual[split] = {}
        for name in sorted(names):
            path = split_root / name
            _assert_regular_file(path, f"canonical:{split}/{name}")
            digest = _sha256_bytes(path.read_bytes())
            if digest != expected_split[name]:
                raise SourceIntegrityError(f"source_hash_mismatch:{split}/{name}")
            actual[split][name] = digest
    return actual


def make_source_lock(
    qmsum: Path,
    explainmeetsum: Path,
    *,
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a lock document from clean checkouts for the release process."""

    result: dict[str, Any] = {"lock_version": SOURCE_LOCK_VERSION, "sources": {}}
    for name, input_root, repository, canonical_root in (
        ("qmsum", Path(qmsum), "Yale-LILY/QMSum", "data/ALL"),
        (
            "explainmeetsum",
            Path(explainmeetsum),
            "hkim-etri/ExplainMeetSum",
            "data/ExplainMeetSum",
        ),
        ):
        repo_root = _git_root(input_root)
        _assert_no_symlink_below(repo_root, input_root)
        if Path(os.path.abspath(input_root)).resolve() != (
            repo_root / canonical_root
        ).resolve():
            raise SourceIntegrityError(f"invalid_canonical_root:{name}")
        if _git_output(repo_root, "rev-parse", "HEAD") != COMMITS[name]:
            raise SourceIntegrityError(f"wrong_source_revision:{name}")
        if _git_output(
            repo_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ):
            raise SourceIntegrityError(f"dirty_source_checkout:{name}")
        tracked = _tracked_files(repo_root)
        canonical_files: dict[str, dict[str, str]] = {}
        for split in SPLITS:
            split_root = input_root / split
            _assert_directory(split_root, f"canonical_split:{split}")
            names = sorted(entry.name for entry in split_root.iterdir())
            if any(
                entry.is_symlink() or not entry.is_file() or not entry.name.endswith(".json")
                for entry in split_root.iterdir()
            ):
                raise SourceIntegrityError(f"invalid_canonical_split:{name}:{split}")
            canonical_files[split] = {
                file_name: _sha256_bytes((split_root / file_name).read_bytes())
                for file_name in names
            }
        domain_membership: dict[str, Any] = {
            "root": "data",
            "domains": {},
        }
        if name == "qmsum":
            all_names = {
                split: set(canonical_files[split]) for split in SPLITS
            }
            seen_membership: dict[str, set[str]] = {split: set() for split in SPLITS}
            for source_domain, output_domain in QMSUM_DOMAINS.items():
                domain_spec: dict[str, Any] = {"label": output_domain, "files": {}}
                for split in SPLITS:
                    domain_root = repo_root / "data" / source_domain / split
                    _assert_directory(domain_root, f"domain_membership:{source_domain}:{split}")
                    entries = list(domain_root.iterdir())
                    if any(
                        entry.is_symlink()
                        or not entry.is_file()
                        or entry.name not in all_names[split]
                        for entry in entries
                    ):
                        raise SourceIntegrityError(
                            f"invalid_domain_membership:{source_domain}:{split}"
                        )
                    if {entry.name for entry in entries} - all_names[split]:
                        raise SourceIntegrityError(
                            f"extra_domain_membership:{source_domain}:{split}"
                        )
                    # Membership files must cover every ALL file exactly once.
                    for entry in entries:
                        all_path = repo_root / "data/ALL" / split / entry.name
                        if entry.read_bytes() != all_path.read_bytes():
                            raise SourceIntegrityError(
                                f"domain_membership_bytes_mismatch:{source_domain}:{split}:{entry.name}"
                            )
                    seen_membership[split].update(entry.name for entry in entries)
                    domain_spec["files"][split] = {
                        entry.name: _sha256_bytes(entry.read_bytes())
                        for entry in sorted(entries, key=lambda item: item.name)
                    }
                domain_membership["domains"][source_domain] = domain_spec
            if any(seen_membership[split] != all_names[split] for split in SPLITS):
                raise SourceIntegrityError("incomplete_domain_membership")
        license_path = repo_root / "LICENSE"
        readme_path = repo_root / "README.md"
        _assert_regular_file(license_path, f"{name}:LICENSE")
        _assert_regular_file(readme_path, f"{name}:README.md")
        result["sources"][name] = {
            "repository": repository,
            "commit": COMMITS[name],
            "canonical_root": canonical_root,
            "license_path": "LICENSE",
            "readme_path": "README.md",
            "license_sha256": _sha256_bytes(license_path.read_bytes()),
            "readme_sha256": _sha256_bytes(readme_path.read_bytes()),
            "canonical_files": canonical_files,
            "tracked_files": tracked,
        }
        if name == "qmsum":
            result["sources"][name]["domain_membership"] = domain_membership
    if baseline is not None:
        result["baseline"] = dict(baseline)
    return result


def load_source_lock(path: Path | None = None) -> dict[str, Any]:
    lock_path = Path(path) if path is not None else Path(__file__).with_name("source_lock.json")
    value = _canonical_json_load(lock_path, "source_lock.json")
    if not isinstance(value, dict):
        raise SourceIntegrityError("unsupported_source_lock")
    _validate_source_lock_document(value, require_accepted_baseline=True)
    return value


def verify_pinned_sources(
    qmsum: Path,
    explainmeetsum: Path,
    *,
    source_lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify repository HEAD, tracked bytes, canonical inputs, and notices."""

    lock = source_lock if source_lock is not None else load_source_lock()
    _validate_source_lock_document(lock, require_accepted_baseline=False)
    snapshots: dict[str, Any] = {"lock": lock}
    for name, input_root in (("qmsum", Path(qmsum)), ("explainmeetsum", Path(explainmeetsum))):
        spec = _expected_source_spec(lock, name)
        root = Path(os.path.abspath(input_root))
        _assert_directory(root, f"{name}_input_root")
        repo_root = _git_root(root)
        _assert_no_symlink_below(repo_root, root)
        canonical_root = spec.get("canonical_root")
        if not isinstance(canonical_root, str):
            raise SourceIntegrityError(f"invalid_source_lock_root:{name}")
        if root.resolve() != (repo_root / canonical_root).resolve():
            raise SourceIntegrityError(f"invalid_canonical_root:{name}")
        head = _git_output(repo_root, "rev-parse", "HEAD")
        if head != spec.get("commit"):
            raise SourceIntegrityError(f"wrong_source_revision:{name}")
        status = _git_output(
            repo_root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
        )
        if status:
            raise SourceIntegrityError(f"dirty_source_checkout:{name}")
        expected_tracked = spec.get("tracked_files")
        if not isinstance(expected_tracked, dict):
            raise SourceIntegrityError(f"missing_tracked_inventory:{name}")
        actual_tracked = _tracked_files(repo_root)
        if actual_tracked != dict(sorted(expected_tracked.items())):
            raise SourceIntegrityError(f"tracked_file_bytes_mismatch:{name}")
        inventories = _canonical_split_inventory(root, spec)
        domain_membership = None
        if name == "qmsum":
            domain_membership = _verify_domain_membership(repo_root, inventories, spec)
        for field, default_name in (("license_path", "LICENSE"), ("readme_path", "README.md")):
            relative = spec.get(field, default_name)
            if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise SourceIntegrityError(f"invalid_source_metadata_path:{name}:{field}")
            metadata_path = repo_root / relative
            _assert_regular_file(metadata_path, f"{name}:{relative}")
            expected_hash = spec.get(field.replace("_path", "_sha256"))
            if not _is_hash(expected_hash) or _sha256_bytes(metadata_path.read_bytes()) != expected_hash:
                raise SourceIntegrityError(f"source_metadata_hash_mismatch:{name}:{relative}")
        snapshots[name] = {
            "repo_root": repo_root,
            "input_root": root,
            "head": head,
            "canonical_files": inventories,
            "domain_membership": domain_membership,
        }
    return snapshots


def _verify_domain_membership(
    repo_root: Path,
    canonical_files: Mapping[str, Mapping[str, str]],
    source_spec: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    membership = source_spec.get("domain_membership")
    if not isinstance(membership, dict) or membership.get("root") != "data":
        raise SourceIntegrityError("missing_domain_membership_lock")
    domains = membership.get("domains")
    if not isinstance(domains, dict) or set(domains) != set(QMSUM_DOMAINS):
        raise SourceIntegrityError("invalid_domain_membership_lock")
    mapping: dict[str, dict[str, str]] = {split: {} for split in SPLITS}
    for source_domain in sorted(domains):
        domain_spec = domains[source_domain]
        if not isinstance(domain_spec, dict) or domain_spec.get("label") != QMSUM_DOMAINS[source_domain]:
            raise SourceIntegrityError(f"invalid_domain_membership_label:{source_domain}")
        files_by_split = domain_spec.get("files")
        if not isinstance(files_by_split, dict):
            raise SourceIntegrityError(f"invalid_domain_membership_files:{source_domain}")
        for split in SPLITS:
            expected = files_by_split.get(split)
            if not isinstance(expected, dict):
                raise SourceIntegrityError(f"missing_domain_membership_split:{source_domain}:{split}")
            domain_root = repo_root / "data" / source_domain / split
            _assert_directory(domain_root, f"domain_membership:{source_domain}:{split}")
            entries = list(domain_root.iterdir())
            if {entry.name for entry in entries} != set(expected):
                raise SourceIntegrityError(f"domain_membership_inventory_mismatch:{source_domain}:{split}")
            for entry in entries:
                if entry.is_symlink() or not entry.is_file():
                    raise SourceIntegrityError(f"invalid_domain_membership_entry:{source_domain}:{split}")
                digest = _sha256_bytes(entry.read_bytes())
                if digest != expected[entry.name]:
                    raise SourceIntegrityError(
                        f"domain_membership_hash_mismatch:{source_domain}:{split}:{entry.name}"
                    )
                canonical_hash_value = canonical_files[split].get(entry.name)
                if canonical_hash_value != digest:
                    raise SourceIntegrityError(
                        f"domain_membership_canonical_mismatch:{source_domain}:{split}:{entry.name}"
                    )
                all_path = repo_root / "data/ALL" / split / entry.name
                _assert_regular_file(all_path, f"data/ALL/{split}/{entry.name}")
                if all_path.read_bytes() != entry.read_bytes():
                    raise SourceIntegrityError(
                        f"domain_membership_all_bytes_mismatch:{source_domain}:{split}:{entry.name}"
                    )
                if entry.name in mapping[split]:
                    raise SourceIntegrityError(
                        f"duplicate_domain_membership:{split}:{entry.name}"
                    )
                mapping[split][entry.name] = domain_spec["label"]
    for split in SPLITS:
        if set(mapping[split]) != set(canonical_files[split]):
            raise SourceIntegrityError(f"incomplete_domain_membership:{split}")
    return mapping


def _source_file_counts(qmsum: Path, explainmeetsum: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for split in SPLITS:
        q_files = load_json_files(Path(qmsum) / split)
        e_files = load_json_files(Path(explainmeetsum) / split, allow_empty=True)
        q_cases = sum(
            len(record.get(f"{kind}_query_list", []))
            for _, record in q_files
            for kind in QUERY_KINDS
        )
        e_cases = 0
        for _, record in e_files:
            block = record.get("explainable_qmsum")
            if isinstance(block, dict):
                e_cases += sum(
                    len(block.get(f"{kind}_query_list", []))
                    for kind in QUERY_KINDS
                    if isinstance(block.get(f"{kind}_query_list", []), list)
                )
        q_names = {path.stem for path, _ in q_files}
        e_names = {path.stem for path, _ in e_files}
        result[split] = {
            "qmsum_meetings": len(q_files),
            "explainmeetsum_meetings": len(e_files),
            "missing_explainmeetsum_meetings": sorted(q_names - e_names),
            "unexpected_explainmeetsum_meetings": sorted(e_names - q_names),
            "qmsum_cases": q_cases,
            "explainmeetsum_cases": e_cases,
        }
    return result


def _turn_view(turns: Iterable[Turn]) -> list[list[str]]:
    return [[turn.speaker, turn.text] for turn in turns]


def _turn_dict(turn: Turn) -> dict[str, Any]:
    return {
        "meeting_id": turn.meeting_id,
        "turn_index": turn.turn_index,
        "speaker": turn.speaker,
        "text": turn.text,
    }


def _sentence_dict(sentence: Sentence) -> dict[str, Any]:
    return {
        "id": sentence.id,
        "meeting_id": sentence.meeting_id,
        "turn_index": sentence.turn_index,
        "sentence_index": sentence.sentence_index,
        "speaker": sentence.speaker,
        "text": sentence.text,
        "dialogue_act_id": sentence.dialogue_act_id,
    }


def _fallback_turns(value: Iterable[Sentence], meeting_id: str) -> tuple[Turn, ...]:
    by_turn: dict[int, Sentence] = {}
    for sentence in value:
        by_turn.setdefault(sentence.turn_index, sentence)
    return tuple(
        Turn(meeting_id, index, sentence.speaker, sentence.text)
        for index, sentence in sorted(by_turn.items())
    )


def _meeting_row(
    meeting_id: str,
    split: str,
    value: Iterable[Sentence],
    cases: Sequence[Case],
) -> dict[str, Any]:
    sentences = list(value)
    qmsum_turns = tuple(getattr(value, "qmsum_turns", ()))
    explainmeetsum_turns = tuple(getattr(value, "explainmeetsum_turns", ()))
    if not qmsum_turns:
        qmsum_turns = _fallback_turns(sentences, meeting_id)

    # A QMSum-only or transcript-mismatched view may contain the loader's
    # compatibility turn sentences. They are not published EMS coordinates.
    if not explainmeetsum_turns:
        sentences = []

    qmsum_view = _turn_view(qmsum_turns)
    ems_view = _turn_view(explainmeetsum_turns)
    if sentences and not ems_view:
        raise ArtifactError(f"sentence_view_without_explainmeetsum:{meeting_id}")
    domain_values = sorted({case.domain for case in cases})
    domain = domain_values[0] if domain_values else "unknown"
    if len(domain_values) > 1:
        raise ArtifactError(f"multiple_case_domains:{meeting_id}")
    sentence_rows = [_sentence_dict(sentence) for sentence in sentences]
    return {
        "meeting_id": meeting_id,
        "split": split,
        "domain": domain,
        "qmsum_turns": [_turn_dict(turn) for turn in qmsum_turns],
        "explainmeetsum_turns": [_turn_dict(turn) for turn in explainmeetsum_turns],
        "sentences": sentence_rows,
        "metadata": {
            "qmsum_turn_count": len(qmsum_turns),
            "explainmeetsum_turn_count": len(explainmeetsum_turns),
            "sentence_count": len(sentence_rows),
            "has_explainmeetsum": bool(explainmeetsum_turns),
            "transcript_match": bool(explainmeetsum_turns) and qmsum_view == ems_view,
            "qmsum_transcript_hash": canonical_hash(qmsum_view),
            "explainmeetsum_transcript_hash": canonical_hash(ems_view)
            if explainmeetsum_turns
            else None,
        },
    }


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _eligibility_counts(cases: Sequence[Case]) -> dict[str, dict[str, int]]:
    return {
        policy: _sorted_counter(Counter(case.eligibility.get(policy) for case in cases))
        for policy in POLICIES
    }


def derive_counts(corpus_rows: Sequence[Mapping[str, Any]], cases: Sequence[Case]) -> dict[str, Any]:
    by_split: dict[str, Any] = {}
    by_split_domain: dict[str, Any] = {}
    by_split_domain_query_kind: dict[str, Any] = {}
    for split in SPLITS:
        split_cases = [case for case in cases if case.split == split]
        split_meetings = [row for row in corpus_rows if row["split"] == split]
        by_split[split] = {
            "meetings": len(split_meetings),
            "cases": len(split_cases),
            "query_kinds": _sorted_counter(Counter(case.query_kind for case in split_cases)),
            "lifecycle_state": _sorted_counter(
                Counter(case.lifecycle_state for case in split_cases)
            ),
            "eligibility": _eligibility_counts(split_cases),
        }
        domains = sorted({case.domain for case in split_cases})
        for domain in domains:
            group = [case for case in split_cases if case.domain == domain]
            by_split_domain[f"{split}/{domain}"] = {
                "meetings": len(
                    {
                        row["meeting_id"]
                        for row in split_meetings
                        if row["domain"] == domain
                    }
                ),
                "cases": len(group),
                "query_kinds": _sorted_counter(Counter(case.query_kind for case in group)),
                "lifecycle_state": _sorted_counter(Counter(case.lifecycle_state for case in group)),
                "eligibility": _eligibility_counts(group),
            }
            for query_kind in QUERY_KINDS:
                query_group = [case for case in group if case.query_kind == query_kind]
                if not query_group:
                    continue
                by_split_domain_query_kind[f"{split}/{domain}/{query_kind}"] = {
                    "cases": len(query_group),
                    "eligibility": _eligibility_counts(query_group),
                }
    exact_joined = sum(
        "explainmeetsum_query_index" in case.provenance for case in cases
    )
    return {
        "meetings": len(corpus_rows),
        "cases": len(cases),
        "raw_turns": {
            "qmsum": sum(len(row["qmsum_turns"]) for row in corpus_rows),
            "explainmeetsum": sum(len(row["explainmeetsum_turns"]) for row in corpus_rows),
        },
        "sentences": sum(len(row["sentences"]) for row in corpus_rows),
        "exact_joined_cases": exact_joined,
        "answer_only_cases": sum(
            case.lifecycle_state == "answer_only" for case in cases
        ),
        "by_split": by_split,
        "by_split_domain": by_split_domain,
        "by_split_domain_query_kind": by_split_domain_query_kind,
        "eligibility": _eligibility_counts(cases),
    }


def _collision_records(
    values: Mapping[str, list[tuple[str, str, str]]], collision_type: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for digest, entries in sorted(values.items()):
        if len({entry[1] for entry in entries}) < 2 or len({entry[2] for entry in entries}) < 2:
            continue
        records.append(
            {
                "type": collision_type,
                "hash": digest,
                "cases": [entry[0] for entry in sorted(entries)],
                "splits": sorted({entry[1] for entry in entries}),
                "meetings": sorted({entry[2] for entry in entries}),
            }
        )
    return records


def derive_leakage(corpus_rows: Sequence[Mapping[str, Any]], cases: Sequence[Case]) -> dict[str, Any]:
    transcript_values: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in corpus_rows:
        digest = canonical_hash(
            [[turn["speaker"], turn["text"]] for turn in row["qmsum_turns"]]
        )
        transcript_values[digest].append(
            (row["meeting_id"], row["split"], row["meeting_id"])
        )
    answer_values: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    tuple_values: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for case in cases:
        if case.answer.strip():
            answer_values[canonical_hash(case.answer)].append(
                (case.case_id, case.split, case.meeting_id)
            )
        tuple_values[canonical_hash([case.query_kind, case.query, case.answer])].append(
            (case.case_id, case.split, case.meeting_id)
        )
    transcript_collisions = _collision_records(transcript_values, "transcript")
    answer_collisions = _collision_records(answer_values, "answer")
    tuple_collisions = _collision_records(tuple_values, "query_tuple")
    all_collisions = transcript_collisions + answer_collisions + tuple_collisions
    case_by_id = {case.case_id: case for case in cases}
    affected_case_ids = sorted(
        {
            case_id
            for record in all_collisions
            if "test" in record["splits"]
            and set(record["splits"]) & {"train", "val"}
            for case_id in record["cases"]
            if case_by_id.get(case_id) is not None
            and case_by_id[case_id].split == "test"
        }
    )
    affected_meetings = sorted(
        {
            meeting
            for record in all_collisions
            if "test" in record["splits"]
            for meeting in record["meetings"]
        }
    )
    return {
        "policy_version": LEAKAGE_POLICY_VERSION,
        "transcript_collisions": transcript_collisions,
        "answer_hash_collisions": answer_collisions,
        "query_tuple_collisions": tuple_collisions,
        "held_out_eligible": not bool(affected_meetings),
        "held_out_affected_case_ids": affected_case_ids,
        "held_out_affected_meeting_ids": affected_meetings,
        "limitations": [
            "Exact hashes do not certify absence of near-duplicates or pretraining contamination.",
            "Repeated query text without an identical query-kind/query/answer tuple is not a collision.",
        ],
    }


def _source_observed(
    source_counts: Mapping[str, Mapping[str, Any]],
    counts: Mapping[str, Any],
    audit_summary: Mapping[str, Any],
) -> dict[str, Any]:
    observed: dict[str, Any] = {
        "qmsum_meetings": sum(int(source_counts[split]["qmsum_meetings"]) for split in SPLITS),
        "explainmeetsum_meetings": sum(
            int(source_counts[split]["explainmeetsum_meetings"]) for split in SPLITS
        ),
        "qmsum_cases": int(counts["cases"]),
        "exact_joined_cases": int(counts["exact_joined_cases"]),
        "answer_only_cases": int(counts["answer_only_cases"]),
        "unknown_domain_cases": sum(
            int(value.get("cases", 0))
            for key, value in counts.get("by_split_domain", {}).items()
            if key.endswith("/unknown")
        ),
        "eligibility": counts["eligibility"],
        "coverage": counts["by_split_domain_query_kind"],
        "audit_counts": audit_summary["counts"],
    }
    return observed


def _exception_ids(
    source_counts: Mapping[str, Mapping[str, Any]],
    cases: Sequence[Case],
    audit_summary: Mapping[str, Any],
) -> dict[str, Any]:
    unmatched_explainable = {
        f"ems:{record['meeting_id']}:{record['query_kind']}:{record['query_index']}"
        for record in audit_summary.get("records", [])
        if record.get("code") == "unmatched_explainable"
        and isinstance(record.get("meeting_id"), str)
        and isinstance(record.get("query_kind"), str)
        and type(record.get("query_index")) is int
    }
    return {
        "answer_only_case_ids": sorted(
            case.case_id for case in cases if case.lifecycle_state == "answer_only"
        ),
        "partial_case_ids": {
            policy: sorted(
                case.case_id
                for case in cases
                if case.eligibility.get(policy) == "partial_annotation"
            )
            for policy in POLICIES
        },
        "quarantined_case_ids": sorted(
            case.case_id for case in cases if case.lifecycle_state == "quarantined"
        ),
        "missing_explainmeetsum_meetings": sorted(
            {
                meeting_id
                for split in SPLITS
                for meeting_id in source_counts[split].get(
                    "missing_explainmeetsum_meetings", []
                )
            }
        ),
        "unmatched_explainable_ids": sorted(unmatched_explainable),
    }


def _all_leakage_records(leakage: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for key in ("transcript_collisions", "answer_hash_collisions", "query_tuple_collisions"):
        value = leakage.get(key, [])
        if not isinstance(value, list):
            raise SourceIntegrityError(f"invalid_leakage_records:{key}")
        records.extend(dict(item) for item in value)
    return sorted(records, key=lambda item: canonical_bytes(item))


def _freeze_report(
    lock: Mapping[str, Any],
    source_counts: Mapping[str, Mapping[str, Any]],
    counts: Mapping[str, Any],
    audit_summary: Mapping[str, Any],
    cases: Sequence[Case],
    leakage: Mapping[str, Any],
) -> dict[str, Any]:
    baseline = lock.get("baseline")
    _validate_source_lock_document(lock, require_accepted_baseline=True)
    if not isinstance(baseline, Mapping):
        raise SourceIntegrityError("missing_freeze_baseline")
    observed = _source_observed(source_counts, counts, audit_summary)
    expected = _baseline_expected(baseline)
    drift = {
        key: {"expected": expected[key], "observed": observed.get(key)}
        for key in sorted(set(expected) | set(observed))
        if expected.get(key) != observed.get(key)
    }
    exception_ids = _exception_ids(source_counts, cases, audit_summary)
    expected_exception_ids = baseline["known_exception_ids"]
    if exception_ids != expected_exception_ids:
        drift["known_exception_ids"] = {
            "expected": expected_exception_ids,
            "observed": exception_ids,
        }
    observed_leakage = _all_leakage_records(leakage)
    expected_leakage = baseline["known_leakage"]
    if observed_leakage != expected_leakage:
        drift["known_leakage"] = {
            "expected": expected_leakage,
            "observed": observed_leakage,
        }
    if drift:
        raise SourceIntegrityError("freeze_baseline_drift")
    for key, value in counts.get("by_split_domain", {}).items():
        if value.get("cases", 0) and value.get("eligibility", {}).get("ces_pes", {}).get(
            "citation_scorable", 0
        ) == 0:
            raise SourceIntegrityError(f"no_ces_pes_cases:{key}")
    return {
        "version": FREEZE_BASELINE_VERSION,
        "status": baseline["status"],
        "accepted": baseline["accepted"],
        "observed": observed,
        "expected": expected,
        "observed_exception_ids": exception_ids,
        "expected_exception_ids": expected_exception_ids,
        "observed_leakage": observed_leakage,
        "expected_leakage": expected_leakage,
        "drift": drift,
    }


def _compact_exceptions(cases: Sequence[Case]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in sorted(cases, key=lambda item: item.case_id):
        reasons = case.provenance.get("alignment_reasons", [])
        if case.lifecycle_state == "citation_scorable" and not reasons:
            continue
        rows.append(
            {
                "case_id": case.case_id,
                "meeting_id": case.meeting_id,
                "split": case.split,
                "domain": case.domain,
                "query_kind": case.query_kind,
                "lifecycle_state": case.lifecycle_state,
                "eligibility": dict(sorted(case.eligibility.items())),
                "reasons": sorted(reasons) if isinstance(reasons, list) else [],
            }
        )
    return rows


def _apply_domain_membership(
    cases: Sequence[Case],
    split: str,
    lock: Mapping[str, Any],
    audit: Any,
) -> None:
    """Attach verified QMSum domain metadata without importing duplicate files."""

    source_spec = _expected_source_spec(lock, "qmsum")
    membership = source_spec.get("domain_membership")
    if not isinstance(membership, dict):
        return
    domains = membership.get("domains")
    if not isinstance(domains, dict):
        raise SourceIntegrityError("invalid_domain_membership_lock")
    by_file: dict[str, str] = {}
    for source_domain, domain_spec in domains.items():
        if not isinstance(domain_spec, dict) or not isinstance(domain_spec.get("files"), dict):
            raise SourceIntegrityError("invalid_domain_membership_lock")
        files = domain_spec["files"].get(split)
        if not isinstance(files, dict) or not isinstance(domain_spec.get("label"), str):
            raise SourceIntegrityError(f"missing_domain_membership_split:{split}")
        for file_name in files:
            if file_name in by_file:
                raise SourceIntegrityError(f"duplicate_domain_membership:{split}:{file_name}")
            by_file[file_name] = domain_spec["label"]
    for case in cases:
        file_name = case.provenance.get("qmsum_file")
        label = by_file.get(file_name)
        if label is None:
            raise SourceIntegrityError(f"unmapped_qmsum_domain:{split}:{file_name}")
        explainable_domain = case.provenance.get("explainmeetsum_domain")
        if explainable_domain is not None:
            normalized = {
                "Academic": "Academic(icsi)",
                "Academic(icsi)": "Academic(icsi)",
                "Product": "Product(ami)",
                "Product(ami)": "Product(ami)",
                "Committee": "Committee",
            }.get(explainable_domain, explainable_domain)
            if normalized != label:
                reasons = case.provenance.setdefault("alignment_reasons", [])
                if "domain_membership_mismatch" not in reasons:
                    reasons.append("domain_membership_mismatch")
                case.lifecycle_state = "quarantined"
                case.eligibility = {policy: "quarantined" for policy in POLICIES}
                audit.add(
                    "domain_membership_mismatch",
                    case_id=case.case_id,
                    split=split,
                    expected_domain=label,
                    explainmeetsum_domain=explainable_domain,
                )
        case.domain = label
        case.provenance["qmsum_domain"] = label


def _source_lock_hash(lock: Mapping[str, Any]) -> str:
    return canonical_hash(dict(lock))


def _descriptor(
    lock: Mapping[str, Any],
    counts: Mapping[str, Any],
    leakage: Mapping[str, Any],
    freeze: Mapping[str, Any],
    payload_hashes: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "descriptor_version": DESCRIPTOR_VERSION,
        "schema_version": SCHEMA_VERSION,
        "converter_version": CONVERTER_VERSION,
        "source_lock_hash": _source_lock_hash(lock),
        "source_commits": {
            name: _expected_source_spec(lock, name).get("commit")
            for name in ("qmsum", "explainmeetsum")
        },
        "validation_policy_version": VALIDATION_POLICY_VERSION,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "leakage_policy_version": LEAKAGE_POLICY_VERSION,
        "selection_version": SELECTION_VERSION,
        "counts": dict(counts),
        "leakage": dict(leakage),
        "freeze_baseline_hash": canonical_hash(dict(freeze)),
        "freeze_status": freeze["status"],
        "limitations": [
            "Sentence IDs are published ExplainMeetSum coordinates, not timestamps.",
            "Reference evidence overlap is a coverage proxy and not semantic support.",
            "Generated-answer citation completeness is unavailable in v1.",
            "The accepted freeze baseline is exact for counts, exception IDs, and leakage records.",
        ],
        "payload_files": list(PAYLOAD_FILES),
        "payload_hashes": dict(sorted(payload_hashes.items())),
    }


def _manifest(descriptor: Mapping[str, Any], dataset_hash: str) -> dict[str, Any]:
    value = {
        "manifest_version": ARTIFACT_VERSION,
        "dataset_hash": dataset_hash,
        "descriptor": dict(descriptor),
    }
    # These are convenience projections only. Validation requires exact
    # equality with descriptor values, so they cannot override hashed fields.
    for key in (
        "schema_version",
        "converter_version",
        "source_lock_hash",
        "source_commits",
        "validation_policy_version",
        "evidence_policy_version",
        "leakage_policy_version",
        "selection_version",
        "counts",
        "leakage",
        "freeze_baseline_hash",
        "freeze_status",
        "limitations",
        "payload_files",
        "payload_hashes",
    ):
        value[key] = descriptor[key]
    return value


def _source_repo_for_license(input_root: Path, name: str) -> Path | None:
    try:
        return _git_root(input_root)
    except SourceIntegrityError:
        candidates = [input_root, input_root.parent, input_root.parent.parent]
        for candidate in candidates:
            if (candidate / "LICENSE").is_file():
                return candidate
    return None


def _license_bytes(input_root: Path, name: str, lock: Mapping[str, Any]) -> bytes:
    spec = _expected_source_spec(lock, name)
    relative = spec.get("license_path", "LICENSE")
    repo_root = _source_repo_for_license(input_root, name)
    if repo_root is not None and isinstance(relative, str):
        path = repo_root / relative
        if path.is_file() and not path.is_symlink():
            return path.read_bytes()
    raise SourceIntegrityError(f"source_license_unavailable:{name}")


def _build_in_memory(
    qmsum: Path,
    explainmeetsum: Path,
    lock: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[Case], dict[str, Any], dict[str, Any]]:
    source_counts = _source_file_counts(qmsum, explainmeetsum)
    audit = __import__("evals.meeting_gold.core", fromlist=["Audit"]).Audit()
    all_cases: list[Case] = []
    corpora: dict[str, Any] = {}
    meeting_splits: dict[str, str] = {}
    for split in SPLITS:
        split_corpus, split_cases = parse_qmsum(qmsum / split, split, audit)
        if set(split_corpus) & set(corpora):
            raise SourceFormatError("duplicate_meeting_across_splits")
        join_explainable(split_cases, explainmeetsum / split, split, split_corpus, audit)
        _apply_domain_membership(split_cases, split, lock, audit)
        for meeting_id, value in split_corpus.items():
            if meeting_id in meeting_splits:
                raise SourceFormatError("duplicate_meeting_across_splits")
            meeting_splits[meeting_id] = split
            corpora[meeting_id] = value
        all_cases.extend(split_cases)

    cases_by_meeting: dict[str, list[Case]] = defaultdict(list)
    for case in all_cases:
        cases_by_meeting[case.meeting_id].append(case)
    corpus_rows = [
        _meeting_row(
            meeting_id,
            meeting_splits[meeting_id],
            corpora[meeting_id],
            cases_by_meeting[meeting_id],
        )
        for meeting_id in sorted(corpora)
    ]
    case_rows = sorted(all_cases, key=lambda case: case.case_id)
    counts = derive_counts(corpus_rows, case_rows)
    leakage = derive_leakage(corpus_rows, case_rows)
    summary = audit.summary()
    freeze = _freeze_report(
        lock,
        source_counts,
        counts,
        summary,
        case_rows,
        leakage,
    )
    audit_document = {
        "audit_version": ARTIFACT_VERSION,
        "counts": summary["counts"],
        "records": summary["records"],
        "source_accounting": {
            split: {
                **dict(source_counts[split]),
                "retained_cases": sum(case.split == split for case in case_rows),
                "excluded_cases": 0,
                "source_equals_retained_plus_excluded": source_counts[split]["qmsum_cases"]
                == sum(case.split == split for case in case_rows),
            }
            for split in SPLITS
        },
        "derived_counts": counts,
        "leakage": leakage,
        "exceptions": _compact_exceptions(case_rows),
        "freeze": freeze,
    }
    return corpus_rows, case_rows, audit_document, {
        "counts": counts,
        "leakage": leakage,
        "freeze": freeze,
        "source_counts": source_counts,
    }


def _check_existing_output(output: Path, dataset_hash: str, lock: Mapping[str, Any]) -> dict[str, Any] | None:
    if not output.exists() and not output.is_symlink():
        return None
    if not output.is_dir() or output.is_symlink():
        raise ArtifactError("output_exists_not_directory")
    try:
        existing = validate_artifacts(output, source_lock=lock)
    except ArtifactError as exc:
        raise ArtifactError("output_exists_not_valid") from exc
    if existing.get("dataset_hash") != dataset_hash:
        raise ArtifactError("output_exists_different_revision")
    return existing


def build_artifacts(
    qmsum: Path,
    explainmeetsum: Path,
    output: Path,
    *,
    source_lock: Mapping[str, Any] | None = None,
    verify_sources: bool = True,
) -> dict[str, Any]:
    """Build and atomically publish a candidate artifact directory.

    ``verify_sources=False`` is intentionally a direct-helper-only escape hatch
    for hermetic source-shaped tests. The CLI does not expose this switch.
    """

    qmsum = Path(qmsum)
    explainmeetsum = Path(explainmeetsum)
    output = Path(output)
    lock = dict(source_lock) if source_lock is not None else load_source_lock()
    _validate_source_lock_document(lock, require_accepted_baseline=True)
    snapshots = verify_pinned_sources(qmsum, explainmeetsum, source_lock=lock) if verify_sources else None
    qmsum_input = snapshots["qmsum"]["input_root"] if snapshots else qmsum
    ems_input = snapshots["explainmeetsum"]["input_root"] if snapshots else explainmeetsum

    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=parent))
    try:
        corpus_rows, case_rows, audit_document, derived = _build_in_memory(
            qmsum_input, ems_input, lock
        )
        _write_jsonl(temporary / "corpus.jsonl", corpus_rows)
        for split in SPLITS:
            _write_jsonl(
                temporary / f"{split}.jsonl",
                (case.asdict() for case in case_rows if case.split == split),
            )
        _write_json(temporary / "audit.json", audit_document)
        _write_bytes(temporary / "NOTICE.md", NOTICE_BYTES)
        _write_bytes(
            temporary / "QMSUM_LICENSE",
            _license_bytes(qmsum_input, "qmsum", lock),
        )
        _write_bytes(
            temporary / "EXPLAINMEETSUM_LICENSE",
            _license_bytes(ems_input, "explainmeetsum", lock),
        )
        payload_hashes = {
            name: _sha256_bytes((temporary / name).read_bytes()) for name in PAYLOAD_FILES
        }
        descriptor = _descriptor(
            lock,
            derived["counts"],
            derived["leakage"],
            derived["freeze"],
            payload_hashes,
        )
        dataset_hash = canonical_hash(descriptor)
        _write_json(temporary / "manifest.json", _manifest(descriptor, dataset_hash))
        checked = validate_artifacts(temporary, source_lock=lock)
        existing = _check_existing_output(output, dataset_hash, lock)
        if existing is not None:
            shutil.rmtree(temporary)
            return existing
        os.replace(temporary, output)
        checked["output"] = str(output)
        return checked
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _validate_corpus_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    if list(rows) != sorted(rows, key=lambda row: row.get("meeting_id", "")):
        raise ArtifactError("corpus_not_sorted")
    corpus: dict[str, dict[str, Any]] = {}
    sentence_ids: set[str] = set()
    for row in rows:
        expected_keys = {
            "meeting_id",
            "split",
            "domain",
            "qmsum_turns",
            "explainmeetsum_turns",
            "sentences",
            "metadata",
        }
        if set(row) != expected_keys:
            raise ArtifactError("invalid_corpus_row_shape")
        meeting_id = row["meeting_id"]
        split = row["split"]
        if not isinstance(meeting_id, str) or not meeting_id or split not in SPLITS:
            raise ArtifactError("invalid_corpus_identity")
        if meeting_id in corpus:
            raise ArtifactError("duplicate_corpus_meeting_id")
        if not isinstance(row["domain"], str) or not row["domain"]:
            raise ArtifactError("invalid_corpus_domain")
        q_turns = row["qmsum_turns"]
        e_turns = row["explainmeetsum_turns"]
        sentences = row["sentences"]
        if not isinstance(q_turns, list) or not isinstance(e_turns, list) or not isinstance(sentences, list):
            raise ArtifactError("invalid_corpus_lists")
        _validate_turn_rows(q_turns, meeting_id)
        _validate_turn_rows(e_turns, meeting_id)
        expected_sentence_keys = {
            "id",
            "meeting_id",
            "turn_index",
            "sentence_index",
            "speaker",
            "text",
            "dialogue_act_id",
        }
        by_turn: dict[int, list[int]] = defaultdict(list)
        for sentence in sentences:
            if not isinstance(sentence, dict):
                raise ArtifactError("invalid_sentence_shape")
            if set(sentence) != expected_sentence_keys:
                raise ArtifactError("invalid_sentence_shape")
            if (
                not isinstance(sentence["id"], str)
                or sentence["meeting_id"] != meeting_id
                or type(sentence["turn_index"]) is not int
                or sentence["turn_index"] < 0
                or type(sentence["sentence_index"]) is not int
                or sentence["sentence_index"] < 0
                or not isinstance(sentence["speaker"], str)
                or not isinstance(sentence["text"], str)
                or sentence["dialogue_act_id"] is not None
                and not isinstance(sentence["dialogue_act_id"], str)
            ):
                raise ArtifactError("invalid_sentence_fields")
            expected_id = f"qmsum:{meeting_id}:t{sentence['turn_index']}:s{sentence['sentence_index']}"
            if sentence["id"] != expected_id or sentence["id"] in sentence_ids:
                raise ArtifactError("invalid_or_duplicate_sentence_id")
            sentence_ids.add(sentence["id"])
            by_turn[sentence["turn_index"]].append(sentence["sentence_index"])
            if sentence["turn_index"] >= len(e_turns):
                raise ArtifactError("sentence_turn_out_of_bounds")
            if sentence["speaker"] != e_turns[sentence["turn_index"]]["speaker"]:
                raise ArtifactError("sentence_speaker_mismatch")
        if sentences != sorted(
            sentences,
            key=lambda sentence: (sentence["turn_index"], sentence["sentence_index"]),
        ):
            raise ArtifactError("sentences_not_globally_sorted")
        for turn_index, indices in by_turn.items():
            if sorted(indices) != list(range(len(indices))):
                raise ArtifactError("sentence_indices_not_contiguous")
        if sentences and not e_turns:
            raise ArtifactError("sentences_without_explainmeetsum_turns")
        metadata = row["metadata"]
        if not isinstance(metadata, dict):
            raise ArtifactError("invalid_corpus_metadata")
        if metadata.get("qmsum_turn_count") != len(q_turns) or metadata.get("explainmeetsum_turn_count") != len(e_turns) or metadata.get("sentence_count") != len(sentences):
            raise ArtifactError("corpus_metadata_count_mismatch")
        if metadata.get("has_explainmeetsum") != bool(e_turns):
            raise ArtifactError("corpus_metadata_source_mismatch")
        q_hash = canonical_hash([[t["speaker"], t["text"]] for t in q_turns])
        e_hash = canonical_hash([[t["speaker"], t["text"]] for t in e_turns]) if e_turns else None
        if metadata.get("qmsum_transcript_hash") != q_hash or metadata.get("explainmeetsum_transcript_hash") != e_hash:
            raise ArtifactError("corpus_metadata_hash_mismatch")
        if metadata.get("transcript_match") != (bool(e_turns) and q_turns == e_turns):
            raise ArtifactError("corpus_metadata_transcript_mismatch")
        _assert_no_answer_fields(row)
        corpus[meeting_id] = dict(row)
    return corpus


def _validate_turn_rows(rows: Sequence[Mapping[str, Any]], meeting_id: str) -> None:
    expected = {"meeting_id", "turn_index", "speaker", "text"}
    for index, row in enumerate(rows):
        if set(row) != expected:
            raise ArtifactError("invalid_turn_shape")
        if row["meeting_id"] != meeting_id or row["turn_index"] != index or not isinstance(row["speaker"], str) or not isinstance(row["text"], str):
            raise ArtifactError("invalid_turn_fields")


def _assert_no_answer_fields(value: Any) -> None:
    forbidden = {
        "query",
        "answer",
        "evidence",
        "evidence_groups",
        "evidence_types",
        "evidence_records",
        "answer_sentence_index",
        "answer_sentence_texts",
        "relevant_spans",
        "lifecycle_state",
        "eligibility",
    }
    if isinstance(value, dict):
        if set(value) & forbidden:
            raise ArtifactError("answer_key_field_in_corpus")
        for item in value.values():
            _assert_no_answer_fields(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_answer_fields(item)


def _case_from_row(row: Mapping[str, Any]) -> Case:
    if set(row) != set(CASE_FIELDS):
        raise ArtifactError("invalid_case_row_shape")
    try:
        case = Case(**dict(row))
    except (TypeError, ValueError) as exc:
        raise ArtifactError("invalid_case_row") from exc
    return case


def _mapping_failure(case: Case) -> str | None:
    groups = case.answer_sentence_index
    if not isinstance(groups, list):
        return "invalid_answer_indices"
    if not groups and not case.answer_sentence_texts:
        # Answer-only and absent/empty ExplainMeetSum annotations intentionally
        # carry no answer-group mapping to validate.
        return None
    flattened: list[int] = []
    for group in groups:
        if not isinstance(group, list) or not group or any(type(index) is not int or index < 0 for index in group) or group != list(range(group[0], group[0] + len(group))):
            return "invalid_answer_indices"
        flattened.extend(group)
    if flattened != list(range(len(flattened))):
        return "answer_indices_not_contiguous"
    offset = 0
    for text in case.answer_sentence_texts:
        if not isinstance(text, str):
            return "answer_group_text_not_consumed"
        position = case.answer.find(text, offset)
        if position < 0 or case.answer[offset:position].strip():
            return "answer_group_text_not_consumed"
        offset = position + len(text)
    if case.answer[offset:].strip():
        return "answer_group_text_not_consumed"
    return None


def _expected_evidence_record(
    sentence: Mapping[str, Any], label: str
) -> dict[str, Any]:
    return {
        "type": label,
        "turn_index": sentence["turn_index"],
        "sent_index": sentence["sentence_index"],
        "speaker": sentence["speaker"],
        "content": sentence["text"],
    }


def _validate_evidence_records(
    case: Case,
    meeting: Mapping[str, Any],
    sentence_by_id: Mapping[str, Mapping[str, Any]],
) -> None:
    """Bind normalized evidence projections back to their raw source records."""

    for ids, labels, records in zip(
        case.evidence_groups, case.evidence_types, case.evidence_records
    ):
        records_by_coordinate: dict[tuple[int, int], list[Mapping[str, Any]]] = defaultdict(list)
        labels_by_coordinate: dict[tuple[int, int], set[str]] = defaultdict(set)
        for record in records:
            if not isinstance(record, dict):
                raise ArtifactError("invalid_evidence_records")
            turn_index = record.get("turn_index")
            sentence_index = record.get("sent_index")
            label = record.get("type")
            if (
                type(turn_index) is int
                and turn_index >= 0
                and type(sentence_index) is int
                and sentence_index >= 0
            ):
                coordinate = (turn_index, sentence_index)
                records_by_coordinate[coordinate].append(record)
                if label in EVIDENCE_TYPES:
                    labels_by_coordinate[coordinate].add(label)
        if any(len(labels) > 1 for labels in labels_by_coordinate.values()):
            raise ArtifactError("contradictory_evidence_label")
        if case.lifecycle_state != "quarantined" and len(records) != len(ids):
            raise ArtifactError("evidence_records_length_mismatch")
        for sentence_id, label, record in zip(ids, labels, records):
            sentence = sentence_by_id[sentence_id]
            expected = _expected_evidence_record(sentence, label)
            coordinate = (sentence["turn_index"], sentence["sentence_index"])
            raw_records = records_by_coordinate.get(coordinate, [])
            if case.lifecycle_state != "quarantined":
                if record != expected:
                    raise ArtifactError("evidence_record_mismatch")
            elif expected not in raw_records:
                raise ArtifactError("evidence_record_mismatch")


def _validate_case_semantics(case: Case, corpus: Mapping[str, Mapping[str, Any]]) -> None:
    if case.case_id != f"qmsum:{case.meeting_id}:{case.query_kind}:{case.query_index}":
        raise ArtifactError("case_id_mismatch")
    if case.split not in SPLITS or case.query_kind not in QUERY_KINDS or type(case.query_index) is not int or case.query_index < 0:
        raise ArtifactError("invalid_case_identity")
    if not isinstance(case.query, str) or not case.query.strip() or not isinstance(case.answer, str) or not case.answer.strip():
        raise ArtifactError("empty_case_query_or_answer")
    if case.meeting_id not in corpus:
        raise ArtifactError("case_meeting_not_in_corpus")
    meeting = corpus[case.meeting_id]
    if meeting["split"] != case.split or meeting["domain"] != case.domain:
        raise ArtifactError("case_corpus_metadata_mismatch")
    if set(case.eligibility) != set(POLICIES) or any(value not in ELIGIBILITY_STATES for value in case.eligibility.values()):
        raise ArtifactError("invalid_case_eligibility")
    if case.lifecycle_state not in ELIGIBILITY_STATES:
        raise ArtifactError("invalid_case_lifecycle_state")
    for value in (case.relevant_spans, case.answer_sentence_index, case.evidence_groups, case.evidence_types, case.answer_sentence_texts, case.evidence_records):
        if not isinstance(value, list):
            raise ArtifactError("invalid_case_list_field")
    group_lengths = [len(case.answer_sentence_index), len(case.answer_sentence_texts), len(case.evidence_groups), len(case.evidence_types), len(case.evidence_records)]
    if len(set(group_lengths)) != 1:
        raise ArtifactError("case_group_length_mismatch")
    sentence_by_id = {sentence["id"]: sentence for sentence in meeting["sentences"]}
    for group, labels in zip(case.evidence_groups, case.evidence_types):
        if not isinstance(group, list) or not isinstance(labels, list):
            raise ArtifactError("invalid_evidence_group_shape")
        if len(group) != len(labels):
            raise ArtifactError("evidence_id_label_length_mismatch")
        seen_labels: dict[str, str] = {}
        for sentence_id, label in zip(group, labels):
            if not isinstance(sentence_id, str) or sentence_id not in sentence_by_id:
                raise ArtifactError("evidence_sentence_not_in_corpus")
            if not isinstance(label, str) or label not in EVIDENCE_TYPES:
                raise ArtifactError("unknown_evidence_type")
            previous_label = seen_labels.get(sentence_id)
            if previous_label is not None:
                if previous_label != label:
                    raise ArtifactError("contradictory_evidence_label")
                raise ArtifactError("duplicate_evidence_sentence")
            seen_labels[sentence_id] = label
    for group in case.evidence_records:
        if not isinstance(group, list) or any(not isinstance(item, dict) for item in group):
            raise ArtifactError("invalid_evidence_records")
    _validate_evidence_records(case, meeting, sentence_by_id)

    has_annotation = "explainmeetsum_query_index" in case.provenance
    recomputed: dict[str, str] = {}
    if case.lifecycle_state == "quarantined":
        recomputed = {policy: "quarantined" for policy in POLICIES}
    elif not has_annotation:
        recomputed = {policy: "answer_only" for policy in POLICIES}
    else:
        for policy, allowed in (("ces", {"CES"}), ("ces_pes", {"CES", "PES"})):
            filtered = [
                [sid for sid, label in zip(group, labels) if label in allowed]
                for group, labels in zip(case.evidence_groups, case.evidence_types)
            ]
            recomputed[policy] = "citation_scorable" if filtered and all(filtered) else "partial_annotation"
    if case.eligibility != recomputed:
        raise ArtifactError("case_eligibility_not_derived")
    expected_lifecycle = (
        "quarantined"
        if case.lifecycle_state == "quarantined"
        else "answer_only"
        if all(value == "answer_only" for value in recomputed.values())
        else "citation_scorable"
        if all(value == "citation_scorable" for value in recomputed.values())
        else "partial_annotation"
    )
    if case.lifecycle_state != expected_lifecycle:
        raise ArtifactError("case_lifecycle_not_derived")
    mapping_failure = _mapping_failure(case)
    if mapping_failure is not None:
        reasons = case.provenance.get("alignment_reasons", [])
        if case.lifecycle_state != "quarantined" or mapping_failure not in reasons and "annotation_validation_failed" not in reasons:
            raise ArtifactError(f"case_mapping_invalid:{mapping_failure}")


def _load_payload(output: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], list[Case], dict[str, dict[str, Any]]]:
    manifest = _canonical_json_load(output / "manifest.json", "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("manifest_version") != ARTIFACT_VERSION:
        raise ArtifactError("unsupported_manifest")
    projected_fields = {
        "schema_version",
        "converter_version",
        "source_lock_hash",
        "source_commits",
        "validation_policy_version",
        "evidence_policy_version",
        "leakage_policy_version",
        "selection_version",
        "counts",
        "leakage",
        "freeze_baseline_hash",
        "freeze_status",
        "limitations",
        "payload_files",
        "payload_hashes",
    }
    if set(manifest) != {"manifest_version", "dataset_hash", "descriptor"} | projected_fields:
        raise ArtifactError("invalid_manifest_shape")
    descriptor = manifest.get("descriptor")
    dataset_hash = manifest.get("dataset_hash")
    if not isinstance(descriptor, dict) or not _is_hash(dataset_hash):
        raise ArtifactError("invalid_manifest_descriptor")
    if canonical_hash(descriptor) != dataset_hash:
        raise ArtifactError("dataset_hash_mismatch")
    if set(descriptor) != {"descriptor_version"} | projected_fields:
        raise ArtifactError("invalid_descriptor_shape")
    if descriptor.get("descriptor_version") != DESCRIPTOR_VERSION:
        raise ArtifactError("unsupported_descriptor")
    for key in projected_fields:
        if manifest.get(key) != descriptor.get(key):
            raise ArtifactError(f"manifest_projection_mismatch:{key}")
    if descriptor.get("payload_files") != list(PAYLOAD_FILES):
        raise ArtifactError("invalid_payload_file_allowlist")
    payload_hashes = descriptor.get("payload_hashes")
    if not isinstance(payload_hashes, dict) or set(payload_hashes) != set(PAYLOAD_FILES):
        raise ArtifactError("invalid_payload_hash_inventory")
    for name in PAYLOAD_FILES:
        digest = payload_hashes.get(name)
        if not _is_hash(digest) or _sha256_bytes((output / name).read_bytes()) != digest:
            raise ArtifactError(f"payload_hash_mismatch:{name}")
    corpus_rows = _canonical_jsonl_load(output / "corpus.jsonl", "corpus.jsonl")
    audit = _canonical_json_load(output / "audit.json", "audit.json")
    if not isinstance(audit, dict):
        raise ArtifactError("invalid_audit")
    _validate_audit_records(audit)
    case_rows: list[dict[str, Any]] = []
    cases: list[Case] = []
    for split in SPLITS:
        rows = _canonical_jsonl_load(output / f"{split}.jsonl", f"{split}.jsonl")
        if rows != sorted(rows, key=lambda row: row.get("case_id", "")):
            raise ArtifactError(f"cases_not_sorted:{split}")
        for row in rows:
            case = _case_from_row(row)
            if case.split != split:
                raise ArtifactError("case_file_split_mismatch")
            cases.append(case)
            case_rows.append(row)
    return manifest, corpus_rows, audit, cases, _validate_corpus_rows(corpus_rows)


def _validate_audit_records(audit: Mapping[str, Any]) -> None:
    records = audit.get("records")
    counts = audit.get("counts")
    if not isinstance(records, list) or not isinstance(counts, Mapping):
        raise ArtifactError("invalid_audit_records")
    observed: Counter[str] = Counter()
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("code"), str):
            raise ArtifactError("invalid_audit_record")
        observed[record["code"]] += 1
    if any(
        not isinstance(code, str) or type(count) is not int or count < 0
        for code, count in counts.items()
    ):
        raise ArtifactError("invalid_audit_counts")
    if dict(sorted(observed.items())) != dict(sorted(counts.items())):
        raise ArtifactError("audit_counts_mismatch")


def _serialized_payloads(
    corpus_rows: Sequence[Mapping[str, Any]],
    cases: Sequence[Case],
    audit: Mapping[str, Any],
) -> dict[str, bytes]:
    return {
        "corpus.jsonl": b"".join(canonical_bytes(dict(row)) for row in corpus_rows),
        **{
            f"{split}.jsonl": b"".join(
                canonical_bytes(case.asdict()) for case in cases if case.split == split
            )
            for split in SPLITS
        },
        "audit.json": canonical_bytes(dict(audit)),
    }


def _validate_payload_against_sources(
    output: Path,
    snapshots: Mapping[str, Any],
    lock: Mapping[str, Any],
) -> None:
    qmsum = snapshots["qmsum"]["input_root"]
    explainmeetsum = snapshots["explainmeetsum"]["input_root"]
    corpus_rows, cases, audit, _ = _build_in_memory(qmsum, explainmeetsum, lock)
    expected_payloads = _serialized_payloads(corpus_rows, cases, audit)
    for name, expected in expected_payloads.items():
        if (output / name).read_bytes() != expected:
            raise ArtifactError(f"artifact_source_payload_mismatch:{name}")


def _validate_freeze_and_exceptions(
    lock: Mapping[str, Any],
    audit: Mapping[str, Any],
    counts: Mapping[str, Any],
    leakage: Mapping[str, Any],
    cases: Sequence[Case],
) -> None:
    _validate_source_lock_document(lock, require_accepted_baseline=True)
    baseline = lock["baseline"]
    freeze = audit.get("freeze")
    if not isinstance(freeze, Mapping):
        raise ArtifactError("missing_freeze_report")
    if (
        freeze.get("version") != FREEZE_BASELINE_VERSION
        or freeze.get("accepted") is not True
        or freeze.get("status") != "accepted"
        or freeze.get("drift") != {}
    ):
        raise ArtifactError("freeze_baseline_not_accepted")
    source_accounting = audit.get("source_accounting")
    if not isinstance(source_accounting, Mapping) or set(source_accounting) != set(SPLITS):
        raise ArtifactError("missing_source_accounting")
    observed = _source_observed(source_accounting, counts, audit)
    expected = _baseline_expected(baseline)
    if freeze.get("observed") != observed or freeze.get("expected") != expected:
        raise ArtifactError("freeze_observation_mismatch")
    exception_ids = _exception_ids(source_accounting, cases, audit)
    if (
        freeze.get("observed_exception_ids") != exception_ids
        or freeze.get("expected_exception_ids") != baseline["known_exception_ids"]
        or exception_ids != baseline["known_exception_ids"]
    ):
        raise ArtifactError("freeze_exception_mismatch")
    observed_leakage = _all_leakage_records(leakage)
    if (
        freeze.get("observed_leakage") != observed_leakage
        or freeze.get("expected_leakage") != baseline["known_leakage"]
        or observed_leakage != baseline["known_leakage"]
    ):
        raise ArtifactError("freeze_leakage_mismatch")
    if audit.get("exceptions") != _compact_exceptions(cases):
        raise ArtifactError("exception_inventory_mismatch")
    for split in SPLITS:
        account = source_accounting[split]
        if not isinstance(account, Mapping):
            raise ArtifactError(f"source_accounting_mismatch:{split}")
        if (
            type(account.get("qmsum_cases")) is not int
            or type(account.get("retained_cases")) is not int
            or type(account.get("excluded_cases")) is not int
            or account["retained_cases"] + account["excluded_cases"]
            != account["qmsum_cases"]
            or account.get("source_equals_retained_plus_excluded") is not True
        ):
            raise ArtifactError(f"source_accounting_mismatch:{split}")
    for key, value in counts.get("by_split_domain", {}).items():
        if value.get("cases", 0) and value.get("eligibility", {}).get("ces_pes", {}).get(
            "citation_scorable", 0
        ) == 0:
            raise ArtifactError(f"no_ces_pes_cases:{key}")


def validate_artifacts(
    output: Path,
    *,
    source_lock: Mapping[str, Any] | None = None,
    qmsum: Path | None = None,
    explainmeetsum: Path | None = None,
) -> dict[str, Any]:
    """Validate an artifact directory, optionally against pinned source bytes."""

    output = Path(output)
    if (qmsum is None) != (explainmeetsum is None):
        raise ArtifactError("validate_sources_requires_both_inputs")
    _assert_directory(output, "artifact_output")
    entries = list(output.iterdir())
    if {entry.name for entry in entries} != set(OUTPUT_FILES):
        raise ArtifactError("invalid_artifact_file_inventory")
    for entry in entries:
        _assert_regular_file(entry, f"artifact:{entry.name}")
    lock = dict(source_lock) if source_lock is not None else load_source_lock()
    _validate_source_lock_document(lock, require_accepted_baseline=True)
    manifest, corpus_rows, audit, cases, corpus = _load_payload(output)
    descriptor = manifest["descriptor"]
    if (
        descriptor.get("schema_version") != SCHEMA_VERSION
        or descriptor.get("converter_version") != CONVERTER_VERSION
        or descriptor.get("validation_policy_version") != VALIDATION_POLICY_VERSION
        or descriptor.get("evidence_policy_version") != EVIDENCE_POLICY_VERSION
        or descriptor.get("leakage_policy_version") != LEAKAGE_POLICY_VERSION
        or descriptor.get("selection_version") != SELECTION_VERSION
        or descriptor.get("source_commits")
        != {
            name: _expected_source_spec(lock, name).get("commit")
            for name in _SOURCE_NAMES
        }
        or descriptor.get("freeze_status") != "accepted"
    ):
        raise ArtifactError("descriptor_policy_mismatch")
    if descriptor.get("source_lock_hash") != _source_lock_hash(lock):
        raise ArtifactError("artifact_source_lock_mismatch")
    for name, output_name in (("qmsum", "QMSUM_LICENSE"), ("explainmeetsum", "EXPLAINMEETSUM_LICENSE")):
        spec = _expected_source_spec(lock, name)
        expected_hash = spec.get("license_sha256")
        if expected_hash is not None and _sha256_bytes((output / output_name).read_bytes()) != expected_hash:
            raise ArtifactError(f"artifact_license_hash_mismatch:{name}")
    if (output / "NOTICE.md").read_bytes() != NOTICE_BYTES:
        raise ArtifactError("notice_mismatch")
    for case in cases:
        _validate_case_semantics(case, corpus)
    if len({case.case_id for case in cases}) != len(cases):
        raise ArtifactError("duplicate_case_id")
    derived_counts = derive_counts(corpus_rows, cases)
    leakage = derive_leakage(corpus_rows, cases)
    if descriptor.get("counts") != derived_counts or audit.get("derived_counts") != derived_counts:
        raise ArtifactError("derived_count_mismatch")
    if descriptor.get("leakage") != leakage or audit.get("leakage") != leakage:
        raise ArtifactError("derived_leakage_mismatch")
    _validate_freeze_and_exceptions(lock, audit, derived_counts, leakage, cases)
    if canonical_hash(audit["freeze"]) != descriptor.get("freeze_baseline_hash"):
        raise ArtifactError("freeze_baseline_hash_mismatch")
    if qmsum is not None and explainmeetsum is not None:
        snapshots = verify_pinned_sources(
            qmsum,
            explainmeetsum,
            source_lock=lock,
        )
        _validate_payload_against_sources(output, snapshots, lock)
    return {
        "status": descriptor.get("freeze_status", "candidate"),
        "dataset_hash": manifest["dataset_hash"],
        "counts": derived_counts,
        "leakage": leakage,
        "manifest": manifest,
        "audit": audit,
    }


def load_artifact_bundle(
    output: Path,
    *,
    source_lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    checked = validate_artifacts(output, source_lock=source_lock)
    _, corpus_rows, audit, cases, corpus_rows_by_id = _load_payload(Path(output))
    corpus: dict[str, MeetingCorpus] = {}
    for meeting_id, row in corpus_rows_by_id.items():
        sentences = tuple(Sentence(**sentence) for sentence in row["sentences"])
        q_turns = tuple(Turn(**turn) for turn in row["qmsum_turns"])
        e_turns = tuple(Turn(**turn) for turn in row["explainmeetsum_turns"])
        corpus[meeting_id] = MeetingCorpus(
            sentences,
            qmsum_turns=q_turns,
            explainmeetsum_turns=e_turns,
        )
    return {
        "cases": cases,
        "corpus": corpus,
        "corpus_rows": corpus_rows,
        "audit": audit,
        "manifest": checked["manifest"],
        "dataset_hash": checked["dataset_hash"],
        "validation": checked,
    }


def _stable_order(case: Case, seed: int) -> str:
    material = f"{seed}\x1f{case.domain}\x1f{case.query_kind}\x1f{case.case_id}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _selection_hash_payload(selection: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: selection[key]
        for key in sorted(_SELECTION_CORE_FIELDS - {"selection_hash"})
        if key in selection
    }


def _selection_shortfalls(
    candidates: Sequence[Case], selected: Sequence[Case]
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Case]] = defaultdict(list)
    for case in candidates:
        groups[(case.domain, case.query_kind)].append(case)
    ordered_groups = sorted(groups)
    target = len(selected)
    base_target = target // len(ordered_groups) if ordered_groups else 0
    remainder = target % len(ordered_groups) if ordered_groups else 0
    selected_counts = Counter((case.domain, case.query_kind) for case in selected)
    return [
        {
            "domain": domain,
            "query_kind": query_kind,
            "requested": base_target + (index < remainder),
            "available": len(groups[(domain, query_kind)]),
            "selected": selected_counts[(domain, query_kind)],
        }
        for index, (domain, query_kind) in enumerate(ordered_groups)
        if len(groups[(domain, query_kind)]) < base_target + (index < remainder)
    ]


def _held_out_fields(
    selected: Sequence[Case], split: str, leakage: Mapping[str, Any] | None
) -> dict[str, Any]:
    if split != "test":
        return {"held_out_eligible": "not_applicable", "held_out_reasons": []}
    leakage = leakage or {}
    affected = {
        case_id
        for case_id in leakage.get("held_out_affected_case_ids", [])
        if isinstance(case_id, str)
    }
    if not affected:
        for key in ("transcript_collisions", "answer_hash_collisions", "query_tuple_collisions"):
            for record in leakage.get(key, []):
                if not isinstance(record, Mapping):
                    continue
                splits = set(record.get("splits", []))
                if "test" not in splits or not splits & {"train", "val"}:
                    continue
                affected.update(
                    case.case_id
                    for case in selected
                    if case.split == "test" and case.case_id in set(record.get("cases", []))
                )
    selected_affected = sorted(
        case.case_id for case in selected if case.case_id in affected
    )
    selected_affected_ids = set(selected_affected)
    reasons = [
        record
        for key in ("transcript_collisions", "answer_hash_collisions", "query_tuple_collisions")
        for record in leakage.get(key, [])
        if set(record.get("cases", [])) & selected_affected_ids
    ]
    return {
        "held_out_eligible": not bool(selected_affected),
        "held_out_reasons": reasons,
    }


def select_cases(
    cases: Sequence[Case] | Path,
    *,
    dataset_hash: str | None = None,
    split: str = "val",
    limit: int | None = MAX_SMOKE_SELECTION,
    seed: int = 0,
    leakage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select a deterministic domain/query-kind stratified subset."""

    if isinstance(cases, (str, Path)):
        bundle = load_artifact_bundle(Path(cases))
        cases = bundle["cases"]
        dataset_hash = dataset_hash or bundle["dataset_hash"]
        leakage = leakage or bundle["audit"].get("leakage")
    if split not in SPLITS:
        raise ArtifactError("invalid_selection_split")
    if not _is_hash(dataset_hash):
        raise ArtifactError("invalid_selection_dataset_hash")
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ArtifactError("selection_limit_must_be_positive")
    if limit is not None and limit > MAX_SMOKE_SELECTION:
        raise ArtifactError("selection_limit_exceeds_smoke_cap")
    candidates = [case for case in cases if case.split == split]
    if not candidates:
        raise ArtifactError("selection_split_empty")
    if len({case.case_id for case in cases}) != len(cases):
        raise ArtifactError("duplicate_case_id")
    if type(seed) is not int or seed < 0:
        raise ArtifactError("selection_seed_must_be_nonnegative")
    groups: dict[tuple[str, str], list[Case]] = defaultdict(list)
    for case in candidates:
        groups[(case.domain, case.query_kind)].append(case)
    for group in groups.values():
        group.sort(key=lambda case: (_stable_order(case, seed), case.case_id))
    ordered_groups = sorted(groups)
    target = len(candidates) if limit is None else min(limit, len(candidates))
    selected: list[Case] = []
    positions = {key: 0 for key in ordered_groups}
    while len(selected) < target:
        made_progress = False
        for key in ordered_groups:
            position = positions[key]
            group = groups[key]
            if position >= len(group):
                continue
            selected.append(group[position])
            positions[key] += 1
            made_progress = True
            if len(selected) == target:
                break
        if not made_progress:
            break
    base_target = target // len(ordered_groups)
    remainder = target % len(ordered_groups)
    shortfalls: list[dict[str, Any]] = []
    selected_counts = Counter((case.domain, case.query_kind) for case in selected)
    for index, key in enumerate(ordered_groups):
        requested = base_target + (index < remainder)
        available = len(groups[key])
        if available < requested:
            shortfalls.append(
                {
                    "domain": key[0],
                    "query_kind": key[1],
                    "requested": requested,
                    "available": available,
                    "selected": selected_counts[key],
                }
            )
    selection: dict[str, Any] = {
        "selection_version": SELECTION_VERSION,
        "dataset_hash": dataset_hash,
        "split": split,
        "selected_case_ids": [case.case_id for case in selected],
        "seed": seed,
        "selection_policy": SELECTION_POLICY,
        "shortfalls": shortfalls,
    }
    selection.update(_held_out_fields(selected, split, leakage))
    selection["selection_hash"] = canonical_hash(_selection_hash_payload(selection))
    validate_selection(
        selection,
        cases,
        dataset_hash=dataset_hash,
        leakage=leakage,
    )
    return selection


def validate_selection(
    selection: Mapping[str, Any],
    cases: Sequence[Case],
    *,
    dataset_hash: str,
    leakage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(selection, Mapping):
        raise ArtifactError("invalid_selection")
    required = _SELECTION_CORE_FIELDS
    if not required <= set(selection) or set(selection) - (
        _SELECTION_CORE_FIELDS | _SELECTION_DIAGNOSTIC_FIELDS
    ):
        raise ArtifactError("selection_missing_field")
    if selection["selection_version"] != SELECTION_VERSION or selection["selection_policy"] != SELECTION_POLICY:
        raise ArtifactError("unsupported_selection_version")
    if selection["dataset_hash"] != dataset_hash or not _is_hash(dataset_hash):
        raise ArtifactError("selection_dataset_hash_mismatch")
    if selection["split"] not in SPLITS or type(selection["seed"]) is not int or selection["seed"] < 0:
        raise ArtifactError("invalid_selection_identity")
    selected_ids = selection["selected_case_ids"]
    if not isinstance(selected_ids, list) or not selected_ids or any(not isinstance(item, str) for item in selected_ids) or len(set(selected_ids)) != len(selected_ids):
        raise ArtifactError("invalid_selected_case_ids")
    by_id = {case.case_id: case for case in cases}
    if len(by_id) != len(cases):
        raise ArtifactError("duplicate_case_id")
    selected_cases: list[Case] = []
    for case_id in selected_ids:
        if case_id not in by_id or by_id[case_id].split != selection["split"]:
            raise ArtifactError("selection_case_mismatch")
        selected_cases.append(by_id[case_id])
    if selection["selection_hash"] != canonical_hash(_selection_hash_payload(selection)):
        raise ArtifactError("selection_hash_mismatch")
    candidates = [case for case in cases if case.split == selection["split"]]
    expected_shortfalls = _selection_shortfalls(candidates, selected_cases)
    if "shortfalls" in selection and selection["shortfalls"] != expected_shortfalls:
        raise ArtifactError("selection_shortfalls_mismatch")
    if "shortfalls" in selection:
        shortfalls = selection["shortfalls"]
        if not isinstance(shortfalls, list) or any(
            not isinstance(item, Mapping)
            or set(item) != {"domain", "query_kind", "requested", "available", "selected"}
            or item.get("query_kind") not in QUERY_KINDS
            or not isinstance(item.get("domain"), str)
            or any(type(item.get(field)) is not int or item[field] < 0 for field in ("requested", "available", "selected"))
            for item in shortfalls
        ):
            raise ArtifactError("invalid_selection_shortfalls")
    if any(field in selection for field in ("held_out_eligible", "held_out_reasons")):
        if "held_out_eligible" not in selection or "held_out_reasons" not in selection:
            raise ArtifactError("invalid_selection_leakage")
        if selection["held_out_eligible"] not in {True, False, "not_applicable"}:
            raise ArtifactError("invalid_selection_leakage")
        if not isinstance(selection["held_out_reasons"], list):
            raise ArtifactError("invalid_selection_leakage")
        expected_held_out = _held_out_fields(selected_cases, selection["split"], leakage)
        if (
            selection["held_out_eligible"] != expected_held_out["held_out_eligible"]
            or selection["held_out_reasons"] != expected_held_out["held_out_reasons"]
        ):
            raise ArtifactError("selection_leakage_mismatch")
    return dict(selection)


def validate_predictions_minimal(predictions: Mapping[str, Any]) -> None:
    """Check the non-scorer parts of the predictions contract for the CLI."""

    if not isinstance(predictions, Mapping) or predictions.get("schema_version") != "meeting-gold-predictions-v1":
        raise ArtifactError("unsupported_predictions_schema")
    for key in ("dataset_hash", "selection_hash"):
        if not _is_hash(predictions.get(key)):
            raise ArtifactError(f"invalid_predictions_{key}")
    if not isinstance(predictions.get("records"), list):
        raise ArtifactError("invalid_predictions_records")


def write_selection(path: Path, selection: Mapping[str, Any]) -> None:
    """Atomically write a canonical selection JSON file."""

    if not isinstance(selection, Mapping):
        raise ArtifactError("invalid_selection")
    if set(selection) - (_SELECTION_CORE_FIELDS | _SELECTION_DIAGNOSTIC_FIELDS):
        raise ArtifactError("selection_missing_field")
    if not _SELECTION_CORE_FIELDS <= set(selection):
        raise ArtifactError("selection_missing_field")
    selected_ids = selection["selected_case_ids"]
    if (
        selection["selection_version"] != SELECTION_VERSION
        or selection["selection_policy"] != SELECTION_POLICY
        or not _is_hash(selection["dataset_hash"])
        or selection["split"] not in SPLITS
        or type(selection["seed"]) is not int
        or selection["seed"] < 0
        or not isinstance(selected_ids, list)
        or not selected_ids
        or any(type(case_id) is not str or not case_id for case_id in selected_ids)
        or len(set(selected_ids)) != len(selected_ids)
        or selection["selection_hash"] != canonical_hash(_selection_hash_payload(selection))
    ):
        raise ArtifactError("invalid_selection")
    path = Path(path)
    temporary = Path(tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent or Path("."))[1])
    try:
        _write_json(temporary, dict(selection))
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


__all__ = [
    "ARTIFACT_VERSION",
    "ArtifactError",
    "DESCRIPTOR_VERSION",
    "LEAKAGE_POLICY_VERSION",
    "NOTICE_BYTES",
    "PAYLOAD_FILES",
    "SELECTION_POLICY",
    "SELECTION_VERSION",
    "SOURCE_LOCK_VERSION",
    "SourceIntegrityError",
    "build_artifacts",
    "derive_counts",
    "derive_leakage",
    "load_artifact_bundle",
    "load_source_lock",
    "make_source_lock",
    "select_cases",
    "validate_artifacts",
    "validate_predictions_minimal",
    "validate_selection",
    "verify_pinned_sources",
    "write_selection",
]
