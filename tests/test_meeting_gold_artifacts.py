from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from evals.meeting_gold import __main__ as cli
from evals.meeting_gold.artifacts import (
    ArtifactError,
    SourceIntegrityError,
    build_artifacts,
    derive_leakage,
    load_artifact_bundle,
    load_source_lock,
    select_cases,
    validate_artifacts,
    validate_selection,
    write_selection,
    _validate_case_semantics,
)
from evals.meeting_gold.core import (
    COMMITS,
    Case,
    Sentence,
    canonical_hash,
    stable_case_id,
    write_json,
)
from evals.meeting_gold.scoring import score_cases


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _source_record(
    meeting_id: str, *, cross_group_overlap: bool = False
) -> tuple[dict, dict]:
    turns = [
        {"speaker": "Alice", "content": "Hello ."},
        {"speaker": "Bob", "content": "World ."},
    ]
    query = {"query": f"What is {meeting_id}?", "answer": "Hello . World ."}
    qmsum = {
        "topic_list": [],
        "meeting_transcripts": turns,
        "general_query_list": [query],
        "specific_query_list": [],
    }
    explainable_turns = [
        {
            **turn,
            "sentence_level_content": [
                {
                    "turn_index": index,
                    "sent_index": 0,
                    "dialogue_sentence": turn["content"],
                    "dialogue-act_id": f"{meeting_id}.act{index}",
                }
            ],
        }
        for index, turn in enumerate(turns)
    ]
    explainmeetsum = {
        "domain": "Committee",
        "topic_list": [],
        "meeting_transcripts": explainable_turns,
        "explainable_ami": {},
        "explainable_icsi": {},
        "explainable_qmsum": {
            "general_query_list": [
                {
                    **query,
                    "explainable_answer": [
                        {
                            "answer_sentence": "Hello .",
                            "answer_sentence_index": [0],
                            "evidence": [
                                {
                                    "type": "CES",
                                    "turn_index": 0,
                                    "sent_index": 0,
                                    "speaker": "Alice",
                                    "content": "Hello .",
                                }
                            ],
                        },
                        {
                            "answer_sentence": "World .",
                            "answer_sentence_index": [1],
                            "evidence": [
                                {
                                    "type": "PES",
                                    "turn_index": 0 if cross_group_overlap else 1,
                                    "sent_index": 0,
                                    "speaker": "Alice" if cross_group_overlap else "Bob",
                                    "content": "Hello ." if cross_group_overlap else "World .",
                                }
                            ],
                        },
                    ],
                }
            ],
            "specific_query_list": [],
        },
    }
    return qmsum, explainmeetsum


def _fixture_sources(
    tmp_path: Path, *, cross_group_overlap: bool = False
) -> tuple[Path, Path, dict[str, str]]:
    qmsum = tmp_path / "qmsum"
    explainmeetsum = tmp_path / "explainmeetsum"
    for root in (qmsum, explainmeetsum):
        for split in ("train", "val", "test"):
            (root / split).mkdir(parents=True)
        (root / "LICENSE").write_bytes(b"MIT License\n")
        (root / "README.md").write_bytes(b"fixture source\n")

    meeting_by_split = {"train": "fixture_train", "val": "fixture_val", "test": "fixture_test"}
    for split, meeting_id in meeting_by_split.items():
        q_record, e_record = _source_record(
            meeting_id, cross_group_overlap=cross_group_overlap
        )
        (qmsum / split / f"{meeting_id}.json").write_text(
            json.dumps(q_record), encoding="utf-8"
        )
        (explainmeetsum / split / f"{meeting_id}.json").write_text(
            json.dumps(e_record), encoding="utf-8"
        )
    return qmsum, explainmeetsum, meeting_by_split


def _fixture_lock(
    qmsum: Path, explainmeetsum: Path, meeting_by_split: dict[str, str]
) -> dict:
    def split_inventory(root: Path) -> dict[str, dict[str, str]]:
        return {
            split: {
                path.name: _sha256(path.read_bytes())
                for path in sorted((root / split).glob("*.json"))
            }
            for split in ("train", "val", "test")
        }

    q_files = split_inventory(qmsum)
    e_files = split_inventory(explainmeetsum)
    q_license = _sha256((qmsum / "LICENSE").read_bytes())
    q_readme = _sha256((qmsum / "README.md").read_bytes())
    e_license = _sha256((explainmeetsum / "LICENSE").read_bytes())
    e_readme = _sha256((explainmeetsum / "README.md").read_bytes())

    def spec(
        repository: str,
        commit_name: str,
        canonical_root: str,
        files: dict[str, dict[str, str]],
        license_hash: str,
        readme_hash: str,
        root: Path,
    ) -> dict:
        return {
            "repository": repository,
            "commit": COMMITS[commit_name],
            "canonical_root": canonical_root,
            "license_path": "LICENSE",
            "readme_path": "README.md",
            "license_sha256": license_hash,
            "readme_sha256": readme_hash,
            "canonical_files": files,
            "tracked_files": {
                "LICENSE": _sha256((root / "LICENSE").read_bytes()),
                "README.md": _sha256((root / "README.md").read_bytes()),
            },
        }

    q_spec = spec(
        "Yale-LILY/QMSum",
        "qmsum",
        "data/ALL",
        q_files,
        q_license,
        q_readme,
        qmsum,
    )
    q_spec["domain_membership"] = {
        "root": "data",
        "domains": {
            "Academic": {
                "label": "Academic(icsi)",
                "files": {split: {} for split in ("train", "val", "test")},
            },
            "Product": {
                "label": "Product(ami)",
                "files": {split: {} for split in ("train", "val", "test")},
            },
            "Committee": {
                "label": "Committee",
                "files": q_files,
            },
        },
    }
    e_spec = spec(
        "hkim-etri/ExplainMeetSum",
        "explainmeetsum",
        "data/ExplainMeetSum",
        e_files,
        e_license,
        e_readme,
        explainmeetsum,
    )
    case_ids = [
        stable_case_id(meeting_id, "general", 0)
        for meeting_id in sorted(meeting_by_split.values())
    ]
    meeting_ids = sorted(meeting_by_split.values())
    known_leakage = sorted(
        [
            {
                "type": "answer",
                "hash": canonical_hash("Hello . World ."),
                "cases": case_ids,
                "splits": ["test", "train", "val"],
                "meetings": meeting_ids,
            },
            {
                "type": "transcript",
                "hash": canonical_hash([["Alice", "Hello ."], ["Bob", "World ."]]),
                "cases": meeting_ids,
                "splits": ["test", "train", "val"],
                "meetings": meeting_ids,
            },
        ],
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    return {
        "lock_version": "meeting-gold-source-lock-v1",
        "sources": {"qmsum": q_spec, "explainmeetsum": e_spec},
        "baseline": {
            "version": "meeting-gold-freeze-baseline-v1",
            "status": "accepted",
            "accepted": True,
            "expected": {
                "qmsum_meetings": 3,
                "explainmeetsum_meetings": 3,
                "qmsum_cases": 3,
                "exact_joined_cases": 3,
                "answer_only_cases": 0,
                "unknown_domain_cases": 0,
                "eligibility": {
                    "ces": {"partial_annotation": 3},
                    "ces_pes": {"citation_scorable": 3},
                },
                "audit_counts": {},
                "coverage": {
                    f"{split}/Committee/general": {
                        "cases": 1,
                        "eligibility": {
                            "ces": {"partial_annotation": 1},
                            "ces_pes": {"citation_scorable": 1},
                        },
                    }
                    for split in ("train", "val", "test")
                },
            },
            "known_exception_ids": {
                "answer_only_case_ids": [],
                "partial_case_ids": {"ces": case_ids, "ces_pes": []},
                "quarantined_case_ids": [],
                "missing_explainmeetsum_meetings": [],
                "unmatched_explainable_ids": [],
            },
            "known_leakage": known_leakage,
        },
    }


def _simple_case(
    case_id: str,
    *,
    split: str = "val",
    domain: str = "Academic(icsi)",
    query_kind: str = "general",
    meeting_id: str | None = None,
    answer: str = "answer",
) -> Case:
    return Case(
        case_id=case_id,
        meeting_id=meeting_id or case_id,
        split=split,
        domain=domain,
        query_kind=query_kind,
        query=f"query {case_id}",
        query_index=0,
        answer=answer,
    )


def test_default_source_lock_is_accepted_and_has_exact_exception_inventory():
    lock = load_source_lock()

    assert lock["baseline"]["accepted"] is True
    assert lock["baseline"]["status"] == "accepted"
    exception_ids = lock["baseline"]["known_exception_ids"]
    assert set(exception_ids) == {
        "answer_only_case_ids",
        "partial_case_ids",
        "quarantined_case_ids",
        "missing_explainmeetsum_meetings",
        "unmatched_explainable_ids",
    }
    assert len(exception_ids["answer_only_case_ids"]) == 51
    assert len(exception_ids["partial_case_ids"]["ces"]) == 39
    assert len(exception_ids["partial_case_ids"]["ces_pes"]) == 13


@pytest.mark.parametrize(
    ("path_mutation", "expected_error"),
    [
        (lambda lock: lock["sources"]["qmsum"].update(commit="0" * 40), "source_lock_commit_mismatch:qmsum"),
        (lambda lock: lock["sources"]["qmsum"].update(readme_sha256="0"), "invalid_source_hash:qmsum:readme"),
        (lambda lock: lock["baseline"].update(accepted=False), "freeze_baseline_not_accepted"),
        (
            lambda lock: lock["baseline"]["known_exception_ids"]["partial_case_ids"]["ces"].pop(),
            "invalid_freeze_exception_inventory",
        ),
    ],
)
def test_source_lock_mutations_are_rejected(tmp_path, path_mutation, expected_error):
    lock = copy.deepcopy(load_source_lock())
    path_mutation(lock)
    path = tmp_path / "source_lock.json"
    write_json(path, lock)

    with pytest.raises(SourceIntegrityError, match=expected_error):
        load_source_lock(path)


def test_fixture_build_requires_exact_accepted_baseline_and_validates_integrity(tmp_path):
    qmsum, explainmeetsum, meeting_by_split = _fixture_sources(tmp_path)
    lock = _fixture_lock(qmsum, explainmeetsum, meeting_by_split)
    output = tmp_path / "artifacts"

    result = build_artifacts(
        qmsum,
        explainmeetsum,
        output,
        source_lock=lock,
        verify_sources=False,
    )

    assert result["status"] == "accepted"
    assert validate_artifacts(output, source_lock=lock)["dataset_hash"] == result["dataset_hash"]

    fixture_bundle = load_artifact_bundle(output, source_lock=lock)
    selection = select_cases(
        fixture_bundle["cases"],
        dataset_hash=result["dataset_hash"],
        leakage=fixture_bundle["audit"]["leakage"],
        limit=1,
        seed=0,
    )
    selection_path = tmp_path / "selection.json"
    write_selection(selection_path, selection)
    assert validate_artifacts(output, source_lock=lock)["dataset_hash"] == result["dataset_hash"]

    drifted = copy.deepcopy(lock)
    drifted["baseline"]["known_exception_ids"]["partial_case_ids"]["ces"] = []
    with pytest.raises(SourceIntegrityError, match="invalid_freeze_exception_inventory"):
        build_artifacts(
            qmsum,
            explainmeetsum,
            tmp_path / "drifted",
            source_lock=drifted,
            verify_sources=False,
        )


def test_evidence_label_conflicts_are_scoped_to_annotation_group(tmp_path):
    qmsum, explainmeetsum, meeting_by_split = _fixture_sources(
        tmp_path, cross_group_overlap=True
    )
    lock = _fixture_lock(qmsum, explainmeetsum, meeting_by_split)
    output = tmp_path / "artifacts"

    build_artifacts(
        qmsum,
        explainmeetsum,
        output,
        source_lock=lock,
        verify_sources=False,
    )
    bundle = load_artifact_bundle(output, source_lock=lock)
    case = next(item for item in bundle["cases"] if item.meeting_id == "fixture_train")
    assert case.evidence_groups == [
        ["qmsum:fixture_train:t0:s0"],
        ["qmsum:fixture_train:t0:s0"],
    ]
    assert case.evidence_types == [["CES"], ["PES"]]

    conflicting = copy.deepcopy(case)
    conflicting.evidence_groups[0].append(conflicting.evidence_groups[0][0])
    conflicting.evidence_types[0].append("PES")
    conflicting.evidence_records[0].append(
        {**conflicting.evidence_records[0][0], "type": "PES"}
    )
    corpus = {row["meeting_id"]: row for row in bundle["corpus_rows"]}
    with pytest.raises(ArtifactError, match="contradictory_evidence_label"):
        _validate_case_semantics(conflicting, corpus)


def test_selection_preserves_shortfalls_and_validates_them():
    cases = [
        _simple_case("a1", domain="Academic(icsi)"),
        _simple_case("p1", domain="Product(ami)"),
        _simple_case("p2", domain="Product(ami)"),
        _simple_case("p3", domain="Product(ami)"),
    ]
    selection = select_cases(cases, dataset_hash="a" * 64, limit=3, seed=4)

    assert selection["shortfalls"] == [
        {
            "domain": "Academic(icsi)",
            "query_kind": "general",
            "requested": 2,
            "available": 1,
            "selected": 1,
        }
    ]
    assert validate_selection(selection, cases, dataset_hash="a" * 64) == selection

    changed = copy.deepcopy(selection)
    changed["shortfalls"] = []
    with pytest.raises(ArtifactError, match="selection_shortfalls_mismatch"):
        validate_selection(changed, cases, dataset_hash="a" * 64)


def test_test_selection_preserves_leakage_and_rejects_false_claims():
    cases = [
        _simple_case("train-case", split="train", meeting_id="train-meeting", answer="same"),
        _simple_case("test-case", split="test", meeting_id="test-meeting", answer="same"),
    ]
    corpus = [
        {
            "meeting_id": "train-meeting",
            "split": "train",
            "qmsum_turns": [{"speaker": "A", "text": "train"}],
        },
        {
            "meeting_id": "test-meeting",
            "split": "test",
            "qmsum_turns": [{"speaker": "A", "text": "test"}],
        },
    ]
    leakage = derive_leakage(corpus, cases)
    selection = select_cases(
        cases,
        dataset_hash="b" * 64,
        split="test",
        limit=1,
        leakage=leakage,
    )

    assert selection["held_out_eligible"] is False
    assert selection["held_out_reasons"] == leakage["answer_hash_collisions"]
    validate_selection(selection, cases, dataset_hash="b" * 64, leakage=leakage)

    changed = copy.deepcopy(selection)
    changed["held_out_eligible"] = True
    with pytest.raises(ArtifactError, match="selection_leakage_mismatch"):
        validate_selection(changed, cases, dataset_hash="b" * 64, leakage=leakage)


@pytest.mark.parametrize("filename", ["selection.json", "predictions.json"])
def test_cli_json_inputs_reject_duplicate_keys_without_echoing_body(tmp_path, filename):
    marker = "secret-input-body-that-must-not-be-printed"
    path = tmp_path / filename
    path.write_text(
        '{"payload":"%s","payload":"repeated"}' % marker,
        encoding="utf-8",
    )

    with pytest.raises(ArtifactError, match="duplicate_json_key") as exc_info:
        cli._read_json(path)

    assert marker not in str(exc_info.value)


def test_score_cli_returns_nonzero_for_incomplete_report_and_keeps_diagnostics(
    monkeypatch, tmp_path
):
    dataset_hash = "d" * 64
    case = _simple_case(
        "qmsum:score-meeting:general:0",
        meeting_id="score-meeting",
    )
    sentence = Sentence(
        id="qmsum:score-meeting:t0:s0",
        meeting_id="score-meeting",
        turn_index=0,
        sentence_index=0,
        speaker="Speaker",
        text="Reference sentence",
    )
    selection = select_cases([case], dataset_hash=dataset_hash, limit=None)
    selection_path = tmp_path / "selection.json"
    predictions_path = tmp_path / "predictions.json"
    report_path = tmp_path / "report.json"
    write_json(selection_path, selection)
    write_json(
        predictions_path,
        {
            "schema_version": "meeting-gold-predictions-v1",
            "dataset_hash": dataset_hash,
            "selection_hash": selection["selection_hash"],
            "records": [],
        },
    )
    monkeypatch.setattr(
        cli,
        "load_artifact_bundle",
        lambda path: {
            "cases": [case],
            "corpus": {"score-meeting": [sentence]},
            "audit": {"leakage": {}},
            "dataset_hash": dataset_hash,
        },
    )

    assert cli.main(
        [
            "score",
            "--cases",
            str(tmp_path / "frozen"),
            "--selection",
            str(selection_path),
            "--predictions",
            str(predictions_path),
            "--output",
            str(report_path),
        ]
    ) == 2

    strict_selection = cli._selection_for_scorer(selection)
    assert set(strict_selection) == {
        "selection_version",
        "dataset_hash",
        "split",
        "selected_case_ids",
        "seed",
        "selection_policy",
        "selection_hash",
    }
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "incomplete"
    assert report["selection_hash"] == strict_selection["selection_hash"]
    assert report["selection_diagnostics"] == {
        "shortfalls": [],
        "held_out_eligible": "not_applicable",
        "held_out_reasons": [],
    }


def test_cli_all_selects_full_split_without_smoke_cap(monkeypatch, tmp_path):
    dataset_hash = "e" * 64
    cases = [
        _simple_case(
            f"qmsum:full-meeting-{index}:general:0",
            meeting_id=f"full-meeting-{index}",
        )
        for index in range(31)
    ]
    monkeypatch.setattr(
        cli,
        "load_artifact_bundle",
        lambda path: {
            "cases": cases,
            "audit": {"leakage": {}},
            "dataset_hash": dataset_hash,
        },
    )
    output = tmp_path / "runs" / "full-selection.json"
    output.parent.mkdir()

    assert cli.main(
        [
            "select",
            "--cases",
            str(tmp_path / "frozen"),
            "--split",
            "val",
            "--all",
            "--seed",
            "0",
            "--output",
            str(output),
        ]
    ) == 0

    selection = json.loads(output.read_text(encoding="utf-8"))
    assert len(selection["selected_case_ids"]) == 31
    assert selection["shortfalls"] == []
    assert selection["held_out_eligible"] == "not_applicable"


def test_cli_all_and_limit_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(
            [
                "select",
                "--cases",
                "frozen",
                "--output",
                "selection.json",
                "--all",
                "--limit",
                "30",
            ]
        )


def test_cli_build_and_validate_delegate_to_artifact_contract(monkeypatch, tmp_path, capsys):
    calls: list[tuple[str, Path]] = []
    lock = {"fixture": True}

    monkeypatch.setattr(cli, "load_source_lock", lambda path: lock)

    def fake_build(qmsum, explainmeetsum, output, *, source_lock):
        calls.append(("build", output))
        return {"status": "accepted", "dataset_hash": "c" * 64, "counts": {}}

    def fake_validate(output, *, source_lock):
        calls.append(("validate", output))
        return {"status": "accepted", "dataset_hash": "c" * 64, "counts": {}}

    monkeypatch.setattr(cli, "build_artifacts", fake_build)
    monkeypatch.setattr(cli, "validate_artifacts", fake_validate)

    assert cli.main(
        [
            "build",
            "--qmsum",
            str(tmp_path / "qmsum"),
            "--explainmeetsum",
            str(tmp_path / "ems"),
            "--output",
            str(tmp_path / "out"),
        ]
    ) == 0
    assert cli.main(["validate", "--output", str(tmp_path / "out")]) == 0
    assert calls == [("build", tmp_path / "out"), ("validate", tmp_path / "out")]
    assert "accepted" in capsys.readouterr().out
