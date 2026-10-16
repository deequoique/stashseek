"""Split/selection resolution for the RQ1 study (val-30 default, full test split)."""

from __future__ import annotations

import pytest

from evals.meeting_gold import __main__ as cli
from evals.meeting_gold.artifacts import ArtifactError, select_cases
from evals.meeting_gold.core import Case
from evals.meeting_gold.production_path import (
    ProductionPathError,
    assert_manifest_meetings_within,
    meeting_domains,
    split_meeting_ids,
    val_meeting_ids,
)
from evals.meeting_gold.retrieval_study import (
    HELD_OUT_COLLISION_REASON,
    RQ1_CASE_IDS,
    StudyError,
    resolve_rq1_cases,
    validate_study_cases,
)

_DATASET_HASH = "a" * 64


def _case(meeting_id: str, split: str, *, kind: str = "specific", index: int = 0, domain: str = "Academic(icsi)") -> Case:
    return Case(
        case_id=f"qmsum:{meeting_id}:{kind}:{index}",
        meeting_id=meeting_id,
        split=split,
        domain=domain,
        query_kind=kind,
        query_index=index,
        query=f"query {meeting_id} {kind} {index}",
        answer="answer",
    )


def _bundle(*, affected: list[str] | None = None, collision_cases: list[str] | None = None) -> dict:
    cases = [
        _case("v1", "val"),
        _case("v2", "val", domain="Product(ami)"),
        _case("v2", "val", kind="general"),
        _case("t1", "test"),
        _case("t1", "test", kind="general"),
        _case("t2", "test", domain="Committee"),
        _case("t3", "test", domain="Committee"),
        _case("r1", "train"),
    ]
    records = []
    if collision_cases:
        records.append(
            {
                "cases": collision_cases,
                "hash": "b" * 64,
                "meetings": ["t3", "v1"],
                "splits": ["test", "val"],
                "type": "answer",
            }
        )
    leakage = {
        "answer_hash_collisions": records,
        "held_out_affected_case_ids": affected or [],
        "held_out_affected_meeting_ids": [],
        "held_out_eligible": not affected,
        "limitations": [],
        "policy_version": "test",
        "query_tuple_collisions": [],
        "transcript_collisions": [],
    }
    return {"cases": cases, "dataset_hash": _DATASET_HASH, "audit": {"leakage": leakage}}


# ------------------------------------------------------------ meeting ids
def test_split_meeting_ids_returns_each_splits_meetings_sorted_and_unique() -> None:
    bundle = _bundle()
    assert split_meeting_ids(bundle, "val") == ["v1", "v2"]
    assert split_meeting_ids(bundle, "test") == ["t1", "t2", "t3"]
    assert split_meeting_ids(bundle, "train") == ["r1"]


def test_val_meeting_ids_remains_a_thin_wrapper_over_split_meeting_ids() -> None:
    bundle = _bundle()
    assert val_meeting_ids(bundle) == split_meeting_ids(bundle, "val")


def test_meeting_domains_come_from_case_metadata_for_one_split() -> None:
    domains = meeting_domains(_bundle(), "test")
    assert domains == {"t1": "Academic(icsi)", "t2": "Committee", "t3": "Committee"}


def test_meeting_domains_rejects_a_meeting_with_two_domains() -> None:
    bundle = {
        "cases": [
            _case("m1", "test", domain="Committee"),
            _case("m1", "test", kind="general", domain="Product(ami)"),
        ]
    }
    with pytest.raises(ProductionPathError, match="meeting_domain_conflict:m1"):
        meeting_domains(bundle, "test")


def test_manifest_with_meetings_outside_the_run_is_refused() -> None:
    # A manifest left by another split would put its meetings into the scope-B
    # tenant as extra distractors.
    assert_manifest_meetings_within({"meetings": {"t1": {}, "t2": {}}}, ["t1", "t2", "t3"])
    assert_manifest_meetings_within({"meetings": {}}, ["t1"])
    with pytest.raises(ProductionPathError, match="library_manifest_has_unrequested_meetings:1"):
        assert_manifest_meetings_within({"meetings": {"t1": {}, "v1": {}}}, ["t1", "t2"])


# ------------------------------------------------------- case validation
def test_validate_study_cases_accepts_cases_of_the_split() -> None:
    validate_study_cases(_bundle(), ["qmsum:t1:specific:0", "qmsum:t2:specific:0"], "test")


@pytest.mark.parametrize(
    ("case_ids", "split", "message"),
    [
        (["qmsum:v1:specific:0"], "test", "case_split_mismatch:qmsum:v1:specific:0"),
        (["qmsum:t1:specific:0"], "val", "case_split_mismatch:qmsum:t1:specific:0"),
        (["qmsum:nope:specific:0"], "test", "unknown_case_id:qmsum:nope:specific:0"),
        (["qmsum:t1:specific:0", "qmsum:t1:specific:0"], "test", "duplicate_case_id"),
        ([], "test", "no_cases"),
    ],
)
def test_validate_study_cases_rejects_bad_case_lists(case_ids: list[str], split: str, message: str) -> None:
    with pytest.raises(StudyError, match=message):
        validate_study_cases(_bundle(), case_ids, split)


# --------------------------------------------------------- case resolution
def test_val_without_selection_defaults_to_the_fixed_30_cases() -> None:
    case_ids, exclusions = resolve_rq1_cases(_bundle(), split="val")
    assert case_ids == RQ1_CASE_IDS
    assert exclusions == {}


def test_test_split_requires_a_selection_or_explicit_case_ids() -> None:
    with pytest.raises(StudyError, match="case_selection_required_for_split:test"):
        resolve_rq1_cases(_bundle(), split="test")


def test_explicit_case_ids_are_used_as_given() -> None:
    case_ids, exclusions = resolve_rq1_cases(
        _bundle(), split="test", case_ids=["qmsum:t2:specific:0", "qmsum:t1:specific:0"]
    )
    assert case_ids == ("qmsum:t2:specific:0", "qmsum:t1:specific:0")
    assert exclusions == {}


def _selection(bundle: dict, split: str) -> dict:
    return select_cases(
        bundle["cases"],
        dataset_hash=bundle["dataset_hash"],
        split=split,
        limit=None,
        leakage=bundle["audit"]["leakage"],
    )


def test_selection_supplies_the_ordered_case_ids() -> None:
    bundle = _bundle()
    selection = _selection(bundle, "test")
    case_ids, exclusions = resolve_rq1_cases(bundle, split="test", selection=selection)
    assert case_ids == tuple(selection["selected_case_ids"])
    assert set(case_ids) == {case.case_id for case in bundle["cases"] if case.split == "test"}
    assert exclusions == {}


def test_selection_held_out_collision_cases_of_the_split_are_excluded() -> None:
    # The collision record names one test case and one val case; only the test
    # case belongs to this study's split and selection.
    bundle = _bundle(
        affected=["qmsum:t3:specific:0"],
        collision_cases=["qmsum:t3:specific:0", "qmsum:v1:specific:0"],
    )
    selection = _selection(bundle, "test")
    assert selection["held_out_eligible"] is False
    case_ids, exclusions = resolve_rq1_cases(bundle, split="test", selection=selection)
    assert exclusions == {"qmsum:t3:specific:0": HELD_OUT_COLLISION_REASON}
    # An excluded case is still part of the run.
    assert "qmsum:t3:specific:0" in case_ids


def test_val_selection_has_no_exclusions() -> None:
    bundle = _bundle(
        affected=["qmsum:t3:specific:0"],
        collision_cases=["qmsum:t3:specific:0", "qmsum:v1:specific:0"],
    )
    _, exclusions = resolve_rq1_cases(bundle, split="val", selection=_selection(bundle, "val"))
    assert exclusions == {}


def test_selection_for_another_split_is_rejected() -> None:
    bundle = _bundle()
    with pytest.raises(StudyError, match="selection_split_mismatch:test!=val"):
        resolve_rq1_cases(bundle, split="val", selection=_selection(bundle, "test"))


def test_selection_with_a_different_dataset_hash_is_rejected() -> None:
    bundle = _bundle()
    selection = _selection(bundle, "test")
    other = {**bundle, "dataset_hash": "c" * 64}
    with pytest.raises(ArtifactError, match="selection_dataset_hash_mismatch"):
        resolve_rq1_cases(other, split="test", selection=selection)


def test_selection_and_case_ids_are_mutually_exclusive_in_the_resolver() -> None:
    bundle = _bundle()
    with pytest.raises(StudyError, match="mutually_exclusive"):
        resolve_rq1_cases(
            bundle,
            split="test",
            case_ids=["qmsum:t1:specific:0"],
            selection=_selection(bundle, "test"),
        )


# ----------------------------------------------------------------- CLI
def test_rq1_cli_exposes_split_and_selection_flags() -> None:
    args = cli.parser().parse_args(["rq1", "--output", "out"])
    assert args.split == "val"
    assert args.selection is None
    assert args.case_ids is None

    args = cli.parser().parse_args(
        ["rq1", "--split", "test", "--selection", "sel.json", "--output", "out"]
    )
    assert args.split == "test"
    assert str(args.selection) == "sel.json"


def test_rq1_cli_rejects_case_id_together_with_selection(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.parser().parse_args(
            ["rq1", "--case-id", "qmsum:t1:specific:0", "--selection", "sel.json", "--output", "out"]
        )
    assert excinfo.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_rq1_cli_rejects_an_unknown_split() -> None:
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["rq1", "--split", "train", "--output", "out"])
