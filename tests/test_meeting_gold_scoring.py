from __future__ import annotations

import copy

import pytest

from evals.meeting_gold.core import (
    Case,
    MeetingCorpus,
    Sentence,
    canonical_hash,
    stable_case_id,
    stable_sentence_id,
)
from evals.meeting_gold.scoring import (
    PREDICTIONS_VERSION,
    SELECTION_VERSION,
    ScoringError,
    score_cases,
)


DATASET_HASH = "a" * 64
C1 = stable_case_id("m1", "general", 0)
C2 = stable_case_id("m1", "general", 1)
C3 = stable_case_id("m2", "general", 0)
C4 = stable_case_id("m1", "general", 2)


def _sentence(meeting_id: str, turn_index: int) -> Sentence:
    return Sentence(
        id=stable_sentence_id(meeting_id, turn_index, 0),
        meeting_id=meeting_id,
        turn_index=turn_index,
        sentence_index=0,
        speaker="Alice",
        text=f"Sentence {meeting_id} {turn_index}.",
    )


def _case(
    case_id: str,
    meeting_id: str,
    query_index: int,
    *,
    domain: str = "alpha",
    evidence_groups: list[list[str]] | None = None,
    evidence_types: list[list[str]] | None = None,
    eligibility: dict[str, str] | None = None,
) -> Case:
    evidence_groups = evidence_groups or []
    evidence_types = evidence_types or []
    answer_sentence_texts = [
        f"Answer {case_id} group {index}."
        for index in range(len(evidence_groups))
    ]
    answer = " ".join(answer_sentence_texts) or f"Answer {case_id}."
    eligibility = eligibility or {"ces": "answer_only", "ces_pes": "answer_only"}
    if any(state == "quarantined" for state in eligibility.values()):
        lifecycle_state = "quarantined"
    elif all(state == "answer_only" for state in eligibility.values()):
        lifecycle_state = "answer_only"
    elif all(state == "citation_scorable" for state in eligibility.values()):
        lifecycle_state = "citation_scorable"
    else:
        lifecycle_state = "partial_annotation"
    return Case(
        case_id=case_id,
        meeting_id=meeting_id,
        split="val",
        domain=domain,
        query_kind="general",
        query_index=query_index,
        query=f"Question {case_id}?",
        answer=answer,
        answer_sentence_index=[[index] for index in range(len(evidence_groups))],
        evidence_groups=evidence_groups,
        evidence_types=evidence_types,
        answer_sentence_texts=answer_sentence_texts,
        evidence_records=[],
        lifecycle_state=lifecycle_state,
        eligibility=eligibility,
    )


@pytest.fixture
def fixture_data() -> tuple[list[Case], dict[str, list[Sentence] | MeetingCorpus], dict[str, object]]:
    m1 = [_sentence("m1", index) for index in range(4)]
    m2 = [_sentence("m2", index) for index in range(2)]
    m1_s0, m1_s1, m1_s2, _ = m1
    m2_s0, _ = m2

    cases = [
        _case(
            C1,
            "m1",
            0,
            evidence_groups=[[m1_s0.id], [m1_s1.id, m1_s2.id]],
            evidence_types=[["CES"], ["PES", "CES"]],
            eligibility={"ces": "citation_scorable", "ces_pes": "citation_scorable"},
        ),
        _case(
            C2,
            "m1",
            1,
            evidence_groups=[[m1_s1.id]],
            evidence_types=[["PES"]],
            eligibility={"ces": "partial_annotation", "ces_pes": "citation_scorable"},
        ),
        _case(
            C3,
            "m2",
            0,
            domain="beta",
            evidence_groups=[[m2_s0.id]],
            evidence_types=[["CES"]],
            eligibility={"ces": "citation_scorable", "ces_pes": "citation_scorable"},
        ),
        _case(
            C4,
            "m1",
            2,
            evidence_groups=[[m1_s0.id]],
            evidence_types=[["CES"]],
            eligibility={"ces": "citation_scorable", "ces_pes": "citation_scorable"},
        ),
    ]
    corpus = {"m1": MeetingCorpus(m1), "m2": m2}
    selection = _selection([case.case_id for case in cases])
    return cases, corpus, selection


def _selection(case_ids: list[str], *, dataset_hash: str = DATASET_HASH) -> dict[str, object]:
    selection: dict[str, object] = {
        "selection_version": SELECTION_VERSION,
        "dataset_hash": dataset_hash,
        "split": "val",
        "selected_case_ids": case_ids,
        "seed": 17,
        "selection_policy": "synthetic-test-v1",
    }
    selection["selection_hash"] = canonical_hash(selection)
    return selection


def _predictions(
    selection: dict[str, object],
    records: list[dict[str, object]],
    *,
    dataset_hash: str = DATASET_HASH,
    selection_hash: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": PREDICTIONS_VERSION,
        "dataset_hash": dataset_hash,
        "selection_hash": selection_hash or selection["selection_hash"],
        "records": records,
    }


def _record(case_id: str, retrieved: list[str], cited: list[str]) -> dict[str, object]:
    return {
        "case_id": case_id,
        "retrieved_sentence_ids": retrieved,
        "cited_sentence_ids": cited,
    }


def test_ces_scores_overlap_retrieval_and_other_meeting_misses(fixture_data):
    cases, corpus, selection = fixture_data
    m1_s0 = cases[0].evidence_groups[0][0]
    m1_s1 = cases[0].evidence_groups[1][0]
    m1_s2 = cases[0].evidence_groups[1][1]
    m2_s0 = cases[2].evidence_groups[0][0]
    predictions = _predictions(
        selection,
        [
            _record(
                C1,
                [m1_s1, m1_s0, m1_s0, m2_s0, m1_s2],
                [m1_s0, m1_s0, m2_s0],
            ),
            _record(C2, [], []),
            _record(C3, [m1_s0, m2_s0, m2_s0], [m2_s0]),
            _record(C4, [], []),
        ],
    )

    report = score_cases(
        cases,
        predictions,
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
        policy="ces",
        ks=(1, 3, 5),
    )

    assert report["status"] == "complete"
    assert report["semantic_citation_completeness"] is None
    assert report["coverage"] == {
        **report["coverage"],
        "S": 4,
        "P": 4,
        "S_minus_P": 0,
        "E": 3,
        "S_minus_E": 1,
        "E_minus_P": 0,
        "P_intersect_E": 3,
    }
    by_id = {row["case_id"]: row for row in report["cases"]}
    c1 = by_id[C1]
    assert c1["retrieved_sentence_ids"] == [m1_s1, m1_s0, m2_s0, m1_s2]
    assert c1["cited_sentence_ids"] == [m1_s0, m2_s0]
    assert c1["gold_evidence_recall"] == pytest.approx(1 / 2)
    assert c1["gold_citation_precision"] == pytest.approx(1 / 2)
    assert c1["reference_group_any_hit"] == pytest.approx(1 / 2)
    assert c1["reference_group_all_hit"] == pytest.approx(1 / 2)
    assert c1["retrieval"]["recall"]["@1"]["value"] == 0.0
    assert c1["retrieval"]["recall"]["@3"]["value"] == pytest.approx(1 / 2)
    assert c1["retrieval"]["recall"]["@5"]["value"] == 1.0
    assert c1["retrieval"]["hit"]["@1"]["value"] == 0.0
    assert c1["retrieval"]["hit"]["@3"]["value"] == 1.0
    assert c1["retrieval"]["mrr"]["value"] == pytest.approx(1 / 2)
    assert c1["gold_evidence_recall_numerator"] == 1
    assert c1["gold_evidence_recall_denominator"] == 2
    assert by_id[C2]["eligible"] is False
    assert by_id[C2]["gold_evidence_recall"] is None

    query = report["aggregates"]["query_macro"]
    meeting = report["aggregates"]["meeting_macro"]
    assert query["gold_evidence_recall"] == pytest.approx((1 / 2 + 1 + 0) / 3)
    assert meeting["gold_evidence_recall"] == pytest.approx(((1 / 2 + 0) / 2 + 1) / 2)
    assert query["gold_evidence_recall_denominator"] == 3
    assert meeting["gold_evidence_recall_denominator"] == 2
    assert set(report["aggregates"]["by_split_domain"]) == {"val/alpha", "val/beta"}


def test_ces_pes_uses_policy_specific_eligibility_and_gold_groups(fixture_data):
    cases, corpus, selection = fixture_data
    records = [
        _record(case_id, [], [])
        for case_id in selection["selected_case_ids"]
    ]
    report = score_cases(
        cases,
        _predictions(selection, records),
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
        policy="ces_pes",
    )

    by_id = {row["case_id"]: row for row in report["cases"]}
    assert by_id[C1]["eligible"] is True
    assert by_id[C1]["eligibility_state"] == "citation_scorable"
    assert by_id[C1]["gold_sentence_count"] == 3
    assert by_id[C1]["gold_group_count"] == 2
    assert by_id[C2]["eligible"] is True
    assert by_id[C2]["eligibility_state"] == "citation_scorable"
    assert by_id[C2]["gold_sentence_ids"] == [stable_sentence_id("m1", 1, 0)]
    assert report["coverage"]["E"] == 4
    assert report["aggregates"]["query_macro"]["scope"] == "selected_eligible"

    ces_report = score_cases(
        cases,
        _predictions(selection, records),
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
        policy="ces",
    )
    ces_by_id = {row["case_id"]: row for row in ces_report["cases"]}
    assert ces_by_id[C2]["eligible"] is False
    assert ces_by_id[C2]["eligibility_state"] == "partial_annotation"


def test_explicit_empty_prediction_is_scored_as_zero(fixture_data):
    cases, corpus, selection = fixture_data
    selection = _selection([C3])
    report = score_cases(
        cases,
        _predictions(selection, [_record(C3, [], [])]),
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
    )

    row = report["cases"][0]
    assert row["status"] == "scored"
    assert row["retrieved_sentence_ids"] == []
    assert row["cited_sentence_ids"] == []
    assert row["gold_evidence_recall"] == 0.0
    assert row["gold_citation_precision"] == 0.0
    assert row["reference_group_any_hit"] == 0.0
    assert row["reference_group_all_hit"] == 0.0
    assert row["retrieval"]["mrr"]["value"] == 0.0
    assert report["coverage"]["P_intersect_E"] == 1


def test_complete_missing_record_has_null_aggregates_but_partial_is_subset_only(fixture_data):
    cases, corpus, selection = fixture_data
    records = [_record(C1, [], [])]
    predictions = _predictions(selection, records)

    complete = score_cases(
        cases,
        predictions,
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
        mode="complete",
    )
    assert complete["status"] == "incomplete"
    assert complete["aggregates"] is None
    assert complete["coverage"]["selected"] == 4
    assert complete["coverage"]["present"] == 1
    assert complete["coverage"]["missing"] == 3
    assert complete["coverage"]["eligible_missing"] == 2
    assert {row["status"] for row in complete["cases"]} == {
        "scored",
        "missing",
    }

    partial = score_cases(
        cases,
        predictions,
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
        mode="partial",
    )
    assert partial["status"] == "partial"
    assert partial["aggregates"]["scope"] == "subset_only"
    assert partial["aggregates"]["query_macro"]["scope"] == "subset_only"
    assert partial["aggregates"]["query_macro"]["scored_case_count"] == 1


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda selection: selection.update(selection_hash="b" * 64), "selection_hash_mismatch"),
        (lambda selection: selection.update(dataset_hash="b" * 64), "selection_dataset_hash_mismatch"),
        (lambda selection: selection.update(selected_case_ids=[C1, C1]), "duplicate_selection_case_id"),
    ],
)
def test_invalid_selection_hash_and_shape_are_rejected(fixture_data, mutation, message):
    cases, corpus, original_selection = fixture_data
    selection = copy.deepcopy(original_selection)
    mutation(selection)
    predictions = _predictions(original_selection, [])
    with pytest.raises(ScoringError, match=message):
        score_cases(
            cases,
            predictions,
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda predictions: predictions["records"].append(
                _record("unexpected", [], [])
            ),
            "unexpected_prediction_case_id",
        ),
        (
            lambda predictions: predictions["records"].append(
                predictions["records"][0]
            ),
            "duplicate_prediction_case_id",
        ),
        (
            lambda predictions: predictions["records"][0]["cited_sentence_ids"].append(
                "hallucinated"
            ),
            "unknown_sentence_id",
        ),
        (
            lambda predictions: predictions.update(dataset_hash="b" * 64),
            "prediction_dataset_hash_mismatch",
        ),
        (
            lambda predictions: predictions.update(selection_hash="b" * 64),
            "prediction_selection_hash_mismatch",
        ),
    ],
)
def test_invalid_prediction_contract_is_rejected(fixture_data, mutation, message):
    cases, corpus, selection = fixture_data
    predictions = _predictions(selection, [_record(C1, [], [])])
    mutation(predictions)
    with pytest.raises(ScoringError, match=message):
        score_cases(
            cases,
            predictions,
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )


def test_corrupt_case_shapes_and_non_case_inputs_are_rejected(fixture_data):
    cases, corpus, selection = fixture_data
    predictions = _predictions(selection, [])

    corrupt = copy.deepcopy(cases)
    corrupt[0].evidence_types = [[]]
    with pytest.raises(ScoringError, match="evidence_group_label_length"):
        score_cases(
            corrupt,
            predictions,
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )

    corrupt = copy.deepcopy(cases)
    corrupt[0].evidence_types = [["CES"], ["PES"]]
    with pytest.raises(ScoringError, match="evidence_group_label_length"):
        score_cases(
            corrupt,
            predictions,
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )

    duplicate_cases = cases + [copy.deepcopy(cases[0])]
    with pytest.raises(ScoringError, match="duplicate_case_id"):
        score_cases(
            duplicate_cases,
            predictions,
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )

    with pytest.raises(ScoringError, match="cases_must_contain_case_dataclasses"):
        score_cases(
            [{"case_id": C1}],
            predictions,
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda case: case.answer_sentence_index.__setitem__(0, [True]),
            "invalid_answer_indices",
        ),
        (
            lambda case: case.answer_sentence_index.__setitem__(1, [0]),
            "answer_indices_not_contiguous",
        ),
        (
            lambda case: case.answer_sentence_texts.__setitem__(
                0, case.answer_sentence_texts[0] + " omitted"
            ),
            "answer_group_text_not_consumed",
        ),
    ],
)
def test_scorable_summary_mapping_requires_strict_complete_consumption(
    fixture_data, mutation, message
):
    cases, corpus, selection = fixture_data
    corrupt = copy.deepcopy(cases)
    mutation(corrupt[0])

    with pytest.raises(ScoringError, match=message):
        score_cases(
            corrupt,
            _predictions(selection, []),
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )


def test_repeated_summary_text_is_consumed_by_offset(fixture_data):
    cases, corpus, _ = fixture_data
    case = copy.deepcopy(cases[0])
    case.answer = "Repeated. Repeated."
    case.answer_sentence_texts = ["Repeated.", "Repeated."]
    selection = _selection([C1])

    report = score_cases(
        [case],
        _predictions(selection, [_record(C1, [], [])]),
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
    )

    assert report["cases"][0]["status"] == "scored"


def test_contradictory_labels_for_the_same_sentence_are_rejected(fixture_data):
    cases, corpus, selection = fixture_data
    corrupt = copy.deepcopy(cases)
    sentence_id = corrupt[0].evidence_groups[0][0]
    corrupt[0].evidence_groups[0] = [sentence_id, sentence_id]
    corrupt[0].evidence_types[0] = ["CES", "PES"]

    with pytest.raises(ScoringError, match="contradictory_evidence_label"):
        score_cases(
            corrupt,
            _predictions(selection, []),
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )


def test_quarantine_must_be_consistent_across_policies(fixture_data):
    cases, corpus, selection = fixture_data
    corrupt = copy.deepcopy(cases)
    corrupt[0].eligibility["ces"] = "quarantined"

    with pytest.raises(ScoringError, match="quarantine_state_mismatch"):
        score_cases(
            corrupt,
            _predictions(selection, []),
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
            policy="ces_pes",
        )


def test_partial_and_quarantined_cases_remain_reportable_but_unscored(fixture_data):
    cases, corpus, selection = fixture_data
    corrupt = copy.deepcopy(cases)
    corrupt[3].eligibility = {"ces": "quarantined", "ces_pes": "quarantined"}
    corrupt[3].lifecycle_state = "quarantined"

    report = score_cases(
        corrupt,
        _predictions(
            selection,
            [_record(case_id, [], []) for case_id in selection["selected_case_ids"]],
        ),
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
        policy="ces_pes",
    )

    by_id = {row["case_id"]: row for row in report["cases"]}
    assert by_id[C2]["eligibility_state"] == "citation_scorable"
    assert by_id[C2]["status"] == "scored"
    assert by_id[C4]["eligibility_state"] == "quarantined"
    assert by_id[C4]["eligible"] is False
    assert by_id[C4]["status"] == "omitted"


def test_stable_ids_are_reconstructed_from_case_and_sentence_coordinates(fixture_data):
    cases, corpus, selection = fixture_data
    corrupt_cases = copy.deepcopy(cases)
    corrupt_cases[0].query_index = 4
    with pytest.raises(ScoringError, match="case_id_mismatch"):
        score_cases(
            corrupt_cases,
            _predictions(selection, []),
            corpus=corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )

    corrupt_corpus = {meeting_id: list(sentences) for meeting_id, sentences in corpus.items()}
    original = corrupt_corpus["m1"][0]
    corrupt_corpus["m1"][0] = Sentence(
        id="not-a-coordinate-id",
        meeting_id=original.meeting_id,
        turn_index=original.turn_index,
        sentence_index=original.sentence_index,
        speaker=original.speaker,
        text=original.text,
    )
    with pytest.raises(ScoringError, match="sentence_id_mismatch"):
        score_cases(
            cases,
            _predictions(selection, []),
            corpus=corrupt_corpus,
            selection=selection,
            dataset_hash=DATASET_HASH,
        )


def test_prediction_hash_and_configuration_are_canonical(fixture_data):
    cases, corpus, selection = fixture_data
    predictions = _predictions(
        selection,
        [_record(C1, [stable_sentence_id("m1", 0, 0)], [])]
        + [_record(case_id, [], []) for case_id in [C2, C3, C4]],
    )
    report = score_cases(
        cases,
        predictions,
        corpus=corpus,
        selection=selection,
        dataset_hash=DATASET_HASH,
    )
    expected_hash = canonical_hash(predictions)
    assert report["prediction_hash"] == expected_hash
    assert report["canonical_prediction_hash"] == expected_hash
    assert report["configuration"]["canonical_prediction_hash"] == expected_hash
    assert report["configuration"]["dataset_hash"] == DATASET_HASH
    assert report["configuration"]["selection_hash"] == selection["selection_hash"]


def test_source_text_may_be_empty_but_scorable_summary_groups_must_align(fixture_data):
    cases, corpus, selection = fixture_data
    empty_source = Sentence(
        id=stable_sentence_id("m1", 0, 0),
        meeting_id="m1",
        turn_index=0,
        sentence_index=0,
        speaker="",
        text="",
    )
    corpus_with_empty_text = {
        "m1": MeetingCorpus([empty_source, *list(corpus["m1"])[1:]]),
        "m2": corpus["m2"],
    }
    selection_one = _selection([C1])
    report = score_cases(
        cases,
        _predictions(
            selection_one,
            [_record(C1, [empty_source.id], [empty_source.id])],
        ),
        corpus=corpus_with_empty_text,
        selection=selection_one,
        dataset_hash=DATASET_HASH,
    )
    assert report["status"] == "complete"

    corrupt = copy.deepcopy(cases)
    corrupt[0].answer_sentence_texts = ["only one summary"]
    with pytest.raises(ScoringError, match="summary_group_count_mismatch"):
        score_cases(
            corrupt,
            _predictions(selection_one, []),
            corpus=corpus,
            selection=selection_one,
            dataset_hash=DATASET_HASH,
        )
