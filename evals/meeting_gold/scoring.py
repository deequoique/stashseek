"""Strict, offline diagnostics for the meeting golden-set contract.

This module deliberately scores sentence-ID overlap only.  It does not make a
claim about whether a generated answer is semantically supported by a
citation.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import re
from typing import Any

from .core import (
    Case,
    ELIGIBILITY_STATES,
    EVIDENCE_TYPES,
    MeetingCorpus,
    QUERY_KINDS,
    SPLITS,
    Sentence,
    canonical_hash,
    stable_case_id,
    stable_sentence_id,
)


SCORER_VERSION = "meeting-gold-scorer-v1"
SELECTION_VERSION = "meeting-gold-selection-v1"
PREDICTIONS_VERSION = "meeting-gold-predictions-v1"

_HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
_POLICY_LABELS = {
    "ces": frozenset({"CES"}),
    "ces_pes": frozenset({"CES", "PES"}),
}
_SELECTION_FIELDS = frozenset(
    {
        "selection_version",
        "dataset_hash",
        "split",
        "selected_case_ids",
        "seed",
        "selection_policy",
        "selection_hash",
    }
)
_PREDICTION_FIELDS = frozenset(
    {"schema_version", "dataset_hash", "selection_hash", "records"}
)
_PREDICTION_RECORD_FIELDS = frozenset(
    {"case_id", "retrieved_sentence_ids", "cited_sentence_ids"}
)


class ScoringError(ValueError):
    """Raised when a scoring input violates the frozen evaluator contract."""


class _ValidatedCase:
    __slots__ = ("case", "groups", "gold_ids")

    def __init__(
        self,
        case: Case,
        groups: tuple[frozenset[str], ...],
        gold_ids: frozenset[str],
    ) -> None:
        self.case = case
        self.groups = groups
        self.gold_ids = gold_ids


def _require_exact_keys(value: Any, expected: frozenset[str], name: str) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != expected:
        raise ScoringError(f"{name}_shape")
    return value


def _require_string(value: Any, name: str) -> str:
    if type(value) is not str or not value:
        raise ScoringError(f"{name}_string")
    return value


def _require_text(value: Any, name: str) -> str:
    """Validate source text fields without rejecting an empty source string."""

    if type(value) is not str:
        raise ScoringError(f"{name}_string")
    return value


def _require_hash(value: Any, name: str) -> str:
    if type(value) is not str or _HASH_PATTERN.fullmatch(value) is None:
        raise ScoringError(f"{name}_hash")
    return value


def _require_nonnegative_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ScoringError(f"{name}_integer")
    return value


def _deduplicate_ids(ids: list[str], known_ids: set[str], name: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for index, sentence_id in enumerate(ids):
        _require_string(sentence_id, f"{name}_{index}")
        if sentence_id not in known_ids:
            raise ScoringError(f"unknown_sentence_id:{name}")
        if sentence_id not in seen:
            seen.add(sentence_id)
            result.append(sentence_id)
    return result


def _validate_corpus(
    corpus: Mapping[str, list[Sentence] | MeetingCorpus],
) -> tuple[dict[str, Sentence], set[str]]:
    if not isinstance(corpus, Mapping):
        raise ScoringError("corpus_mapping")

    known: dict[str, Sentence] = {}
    meetings: set[str] = set()
    for meeting_id, sentences in corpus.items():
        _require_string(meeting_id, "corpus_meeting_id")
        if not isinstance(sentences, (list, MeetingCorpus)):
            raise ScoringError("corpus_sentence_list")
        meetings.add(meeting_id)
        for index, sentence in enumerate(sentences):
            if not isinstance(sentence, Sentence):
                raise ScoringError(f"corpus_sentence:{index}")
            sentence_id = _require_string(sentence.id, "corpus_sentence_id")
            if sentence.meeting_id != meeting_id:
                raise ScoringError("corpus_sentence_meeting_mismatch")
            _require_string(sentence.meeting_id, "corpus_sentence_meeting_id")
            _require_nonnegative_int(sentence.turn_index, "corpus_turn_index")
            _require_nonnegative_int(sentence.sentence_index, "corpus_sentence_index")
            if sentence.id != stable_sentence_id(
                meeting_id, sentence.turn_index, sentence.sentence_index
            ):
                raise ScoringError("sentence_id_mismatch")
            _require_text(sentence.speaker, "corpus_sentence_speaker")
            _require_text(sentence.text, "corpus_sentence_text")
            if sentence.dialogue_act_id is not None:
                _require_text(sentence.dialogue_act_id, "corpus_dialogue_act_id")
            if sentence_id in known:
                raise ScoringError("duplicate_corpus_sentence_id")
            known[sentence_id] = sentence
    return known, meetings


def _filtered_groups(
    case: Case,
    *,
    labels: frozenset[str],
    known_sentences: Mapping[str, Sentence],
) -> tuple[tuple[frozenset[str], ...], frozenset[str]]:
    if type(case.evidence_groups) is not list:
        raise ScoringError(f"evidence_groups_shape:{case.case_id}")
    if type(case.evidence_types) is not list:
        raise ScoringError(f"evidence_types_shape:{case.case_id}")
    if len(case.evidence_groups) != len(case.evidence_types):
        raise ScoringError(f"evidence_group_label_length:{case.case_id}")

    filtered: list[frozenset[str]] = []
    all_gold_ids: set[str] = set()
    for group_index, (group, group_labels) in enumerate(
        zip(case.evidence_groups, case.evidence_types)
    ):
        if type(group) is not list or type(group_labels) is not list:
            raise ScoringError(f"evidence_group_shape:{case.case_id}:{group_index}")
        if len(group) != len(group_labels):
            raise ScoringError(
                f"evidence_group_label_length:{case.case_id}:{group_index}"
            )

        kept: list[str] = []
        kept_seen: set[str] = set()
        seen_labels: dict[str, str] = {}
        for evidence_index, (sentence_id, evidence_type) in enumerate(
            zip(group, group_labels)
        ):
            _require_string(
                sentence_id,
                f"evidence_sentence_id:{case.case_id}:{group_index}:{evidence_index}",
            )
            if sentence_id not in known_sentences:
                raise ScoringError(f"unknown_gold_sentence_id:{case.case_id}")
            if known_sentences[sentence_id].meeting_id != case.meeting_id:
                raise ScoringError(f"gold_sentence_meeting_mismatch:{case.case_id}")
            _require_string(
                evidence_type,
                f"evidence_type:{case.case_id}:{group_index}:{evidence_index}",
            )
            if evidence_type not in EVIDENCE_TYPES:
                raise ScoringError(f"unknown_evidence_type:{case.case_id}")
            previous_type = seen_labels.get(sentence_id)
            if previous_type is not None and previous_type != evidence_type:
                raise ScoringError(f"contradictory_evidence_label:{case.case_id}")
            seen_labels[sentence_id] = evidence_type
            if evidence_type in labels and sentence_id not in kept_seen:
                kept_seen.add(sentence_id)
                kept.append(sentence_id)

        if kept:
            group_ids = frozenset(kept)
            filtered.append(group_ids)
            all_gold_ids.update(group_ids)

    return tuple(filtered), frozenset(all_gold_ids)


def _reconcile_answer(answer: str, group_texts: list[str]) -> bool:
    """Consume summary groups literally, allowing only whitespace gaps."""

    offset = 0
    for group_text in group_texts:
        position = answer.find(group_text, offset)
        if position < 0 or answer[offset:position].strip():
            return False
        offset = position + len(group_text)
    return not answer[offset:].strip()


def _validate_answer_summary(case: Case, case_id: str, scorable: bool) -> None:
    if type(case.answer_sentence_index) is not list:
        raise ScoringError(f"answer_sentence_index_shape:{case_id}")
    if type(case.answer_sentence_texts) is not list:
        raise ScoringError(f"answer_sentence_texts_shape:{case_id}")
    if not scorable:
        return

    if (
        len(case.answer_sentence_index) != len(case.evidence_groups)
        or len(case.answer_sentence_texts) != len(case.evidence_groups)
    ):
        raise ScoringError(f"summary_group_count_mismatch:{case_id}")

    flattened: list[int] = []
    for group_index, indices in enumerate(case.answer_sentence_index):
        if type(indices) is not list or not indices:
            raise ScoringError(f"invalid_answer_indices:{case_id}:{group_index}")
        if any(type(index) is not int or index < 0 for index in indices):
            raise ScoringError(f"invalid_answer_indices:{case_id}:{group_index}")
        if indices != list(range(indices[0], indices[0] + len(indices))):
            raise ScoringError(f"invalid_answer_indices:{case_id}:{group_index}")
        flattened.extend(indices)
    if flattened != list(range(len(flattened))):
        raise ScoringError(f"answer_indices_not_contiguous:{case_id}")

    for group_index, group_text in enumerate(case.answer_sentence_texts):
        if type(group_text) is not str or not group_text.strip():
            raise ScoringError(f"invalid_answer_sentence:{case_id}:{group_index}")
    if not _reconcile_answer(case.answer, case.answer_sentence_texts):
        raise ScoringError(f"answer_group_text_not_consumed:{case_id}")


def _validate_case(
    case: Case,
    *,
    known_sentences: Mapping[str, Sentence],
    corpus_meetings: set[str],
    policy: str,
) -> _ValidatedCase:
    if not isinstance(case, Case):
        raise ScoringError("cases_must_contain_case_dataclasses")
    case_id = _require_string(case.case_id, "case_id")
    _require_string(case.meeting_id, f"case_meeting_id:{case_id}")
    if case.meeting_id not in corpus_meetings:
        raise ScoringError(f"unknown_case_meeting:{case_id}")
    split = _require_string(case.split, f"case_split:{case_id}")
    if split not in SPLITS:
        raise ScoringError(f"unknown_case_split:{case_id}")
    _require_string(case.domain, f"case_domain:{case_id}")
    _require_string(case.query_kind, f"case_query_kind:{case_id}")
    if case.query_kind not in QUERY_KINDS:
        raise ScoringError(f"unknown_query_kind:{case_id}")
    _require_nonnegative_int(case.query_index, f"case_query_index:{case_id}")
    if case_id != stable_case_id(case.meeting_id, case.query_kind, case.query_index):
        raise ScoringError(f"case_id_mismatch:{case_id}")
    _require_string(case.query, f"case_query:{case_id}")
    _require_string(case.answer, f"case_answer:{case_id}")

    eligibility = _require_exact_keys(
        case.eligibility,
        frozenset({"ces", "ces_pes"}),
        f"eligibility:{case_id}",
    )
    for eligibility_policy, state in eligibility.items():
        _require_string(state, f"eligibility_state:{case_id}:{eligibility_policy}")
        if state not in ELIGIBILITY_STATES:
            raise ScoringError(f"unknown_eligibility_state:{case_id}")

    lifecycle_state = _require_string(case.lifecycle_state, f"lifecycle_state:{case_id}")
    if lifecycle_state not in ELIGIBILITY_STATES:
        raise ScoringError(f"unknown_lifecycle_state:{case_id}")
    has_quarantined_policy = any(
        state == "quarantined" for state in eligibility.values()
    )
    if lifecycle_state == "quarantined" or has_quarantined_policy:
        if lifecycle_state != "quarantined" or any(
            state != "quarantined" for state in eligibility.values()
        ):
            raise ScoringError(f"quarantine_state_mismatch:{case_id}")

    expected_lifecycle = (
        "quarantined"
        if has_quarantined_policy
        else "answer_only"
        if all(state == "answer_only" for state in eligibility.values())
        else "citation_scorable"
        if all(state == "citation_scorable" for state in eligibility.values())
        else "partial_annotation"
    )
    if lifecycle_state != expected_lifecycle:
        raise ScoringError(f"lifecycle_state_mismatch:{case_id}")

    policy_state = eligibility[policy]
    _validate_answer_summary(case, case_id, policy_state == "citation_scorable")

    groups, gold_ids = _filtered_groups(
        case,
        labels=_POLICY_LABELS[policy],
        known_sentences=known_sentences,
    )
    if eligibility[policy] == "citation_scorable":
        if not case.evidence_groups or len(groups) != len(case.evidence_groups):
            raise ScoringError(f"scorable_case_has_empty_group:{case_id}")
        if not groups or any(not group for group in groups):
            raise ScoringError(f"scorable_case_has_empty_group:{case_id}")

    return _ValidatedCase(case, groups, gold_ids)


def _validate_cases(
    cases: list[Case],
    *,
    known_sentences: Mapping[str, Sentence],
    corpus_meetings: set[str],
    policy: str,
    selected_case_ids: list[str],
    selection_split: str,
) -> dict[str, _ValidatedCase]:
    if not isinstance(cases, list):
        raise ScoringError("cases_list")

    by_id: dict[str, _ValidatedCase] = {}
    for case in cases:
        if not isinstance(case, Case):
            raise ScoringError("cases_must_contain_case_dataclasses")
        case_id = _require_string(getattr(case, "case_id", None), "case_id")
        if case_id in by_id:
            raise ScoringError("duplicate_case_id")
        validated = _validate_case(
            case,
            known_sentences=known_sentences,
            corpus_meetings=corpus_meetings,
            policy=policy,
        )
        by_id[case_id] = validated

    for case_id in selected_case_ids:
        if case_id not in by_id:
            raise ScoringError("selection_case_id_unknown")
        if by_id[case_id].case.split != selection_split:
            raise ScoringError("selection_case_split_mismatch")
    return by_id


def _validate_selection(
    selection: dict[str, Any],
    dataset_hash: str,
) -> tuple[str, list[str], str]:
    value = _require_exact_keys(selection, _SELECTION_FIELDS, "selection")
    if value["selection_version"] != SELECTION_VERSION:
        raise ScoringError("unknown_selection_version")
    if value["dataset_hash"] != dataset_hash:
        raise ScoringError("selection_dataset_hash_mismatch")
    _require_hash(value["dataset_hash"], "selection_dataset")

    split = _require_string(value["split"], "selection_split")
    if split not in SPLITS:
        raise ScoringError("unknown_selection_split")
    selected_case_ids = value["selected_case_ids"]
    if type(selected_case_ids) is not list or not selected_case_ids:
        raise ScoringError("selection_case_ids_list")
    for index, case_id in enumerate(selected_case_ids):
        _require_string(case_id, f"selection_case_id:{index}")
    if len(set(selected_case_ids)) != len(selected_case_ids):
        raise ScoringError("duplicate_selection_case_id")
    _require_nonnegative_int(value["seed"], "selection_seed")
    _require_string(value["selection_policy"], "selection_policy")

    selection_hash = _require_hash(value["selection_hash"], "selection")
    without_hash = {
        key: item for key, item in value.items() if key != "selection_hash"
    }
    if canonical_hash(without_hash) != selection_hash:
        raise ScoringError("selection_hash_mismatch")
    return split, selected_case_ids, selection_hash


def _validate_predictions(
    predictions: dict[str, Any],
    *,
    dataset_hash: str,
    selection_hash: str,
    selected_case_ids: list[str],
    known_sentence_ids: set[str],
) -> tuple[dict[str, dict[str, list[str]]], str]:
    value = _require_exact_keys(predictions, _PREDICTION_FIELDS, "predictions")
    if value["schema_version"] != PREDICTIONS_VERSION:
        raise ScoringError("unknown_predictions_version")
    if value["dataset_hash"] != dataset_hash:
        raise ScoringError("prediction_dataset_hash_mismatch")
    if value["selection_hash"] != selection_hash:
        raise ScoringError("prediction_selection_hash_mismatch")
    _require_hash(value["dataset_hash"], "prediction_dataset")
    _require_hash(value["selection_hash"], "prediction_selection")
    records = value["records"]
    if type(records) is not list:
        raise ScoringError("prediction_records_list")

    selected = set(selected_case_ids)
    by_case: dict[str, dict[str, list[str]]] = {}
    for index, record in enumerate(records):
        record_value = _require_exact_keys(
            record, _PREDICTION_RECORD_FIELDS, f"prediction_record:{index}"
        )
        case_id = _require_string(
            record_value["case_id"], f"prediction_case_id:{index}"
        )
        if case_id not in selected:
            raise ScoringError("unexpected_prediction_case_id")
        if case_id in by_case:
            raise ScoringError("duplicate_prediction_case_id")
        retrieved = record_value["retrieved_sentence_ids"]
        cited = record_value["cited_sentence_ids"]
        if type(retrieved) is not list or type(cited) is not list:
            raise ScoringError(f"prediction_sentence_lists:{case_id}")
        by_case[case_id] = {
            "retrieved_sentence_ids": _deduplicate_ids(
                retrieved, known_sentence_ids, "retrieved_sentence_ids"
            ),
            "cited_sentence_ids": _deduplicate_ids(
                cited, known_sentence_ids, "cited_sentence_ids"
            ),
        }
    return by_case, canonical_hash(value)


def _metric(
    value: float | None,
    numerator: int | float | None,
    denominator: int | float | None,
) -> dict[str, Any]:
    return {"value": value, "numerator": numerator, "denominator": denominator}


def _metric_names(ks: tuple[int, ...]) -> tuple[str, ...]:
    names = [
        "gold_evidence_recall",
        "gold_citation_precision",
        "reference_group_any_hit",
        "reference_group_all_hit",
    ]
    for k in ks:
        names.extend((f"retrieval_recall@{k}", f"retrieval_hit@{k}"))
    names.append("retrieval_mrr")
    return tuple(names)


def _unavailable_metrics(ks: tuple[int, ...]) -> dict[str, dict[str, Any]]:
    return {name: _metric(None, None, None) for name in _metric_names(ks)}


def _add_flat_metrics(
    row: dict[str, Any],
    metrics: Mapping[str, dict[str, Any]],
) -> None:
    for name, descriptor in metrics.items():
        row[name] = descriptor["value"]
        row[f"{name}_numerator"] = descriptor["numerator"]
        row[f"{name}_denominator"] = descriptor["denominator"]


def _score_case(
    validated: _ValidatedCase,
    prediction: Mapping[str, list[str]],
    *,
    policy: str,
    ks: tuple[int, ...],
) -> dict[str, Any]:
    case = validated.case
    policy_state = case.eligibility[policy]
    groups = validated.groups
    gold_ids = set(validated.gold_ids)
    retrieved = prediction["retrieved_sentence_ids"]
    cited = prediction["cited_sentence_ids"]
    cited_set = set(cited)
    overlap = gold_ids & cited_set

    metrics: dict[str, dict[str, Any]] = {
        "gold_evidence_recall": _metric(
            len(overlap) / len(gold_ids), len(overlap), len(gold_ids)
        ),
        "gold_citation_precision": _metric(
            len(overlap) / len(cited_set) if cited_set else 0.0,
            len(overlap),
            len(cited_set),
        ),
        "reference_group_any_hit": _metric(
            sum(bool(cited_set & group) for group in groups) / len(groups),
            sum(bool(cited_set & group) for group in groups),
            len(groups),
        ),
        "reference_group_all_hit": _metric(
            sum(group <= cited_set for group in groups) / len(groups),
            sum(group <= cited_set for group in groups),
            len(groups),
        ),
    }

    retrieval = {"recall": {}, "hit": {}}
    for k in ks:
        top_k = retrieved[:k]
        relevant_at_k = len(set(top_k) & gold_ids)
        recall_descriptor = _metric(
            relevant_at_k / len(gold_ids), relevant_at_k, len(gold_ids)
        )
        hit_descriptor = _metric(
            1.0 if relevant_at_k else 0.0,
            1 if relevant_at_k else 0,
            1,
        )
        metrics[f"retrieval_recall@{k}"] = recall_descriptor
        metrics[f"retrieval_hit@{k}"] = hit_descriptor
        retrieval["recall"][f"@{k}"] = recall_descriptor
        retrieval["hit"][f"@{k}"] = hit_descriptor

    first_relevant_rank = next(
        (
            rank
            for rank, sentence_id in enumerate(retrieved, start=1)
            if sentence_id in gold_ids
        ),
        None,
    )
    mrr_descriptor = _metric(
        1.0 / first_relevant_rank if first_relevant_rank is not None else 0.0,
        1 if first_relevant_rank is not None else 0,
        first_relevant_rank if first_relevant_rank is not None else 1,
    )
    metrics["retrieval_mrr"] = mrr_descriptor
    retrieval["mrr"] = mrr_descriptor

    row: dict[str, Any] = {
        "case_id": case.case_id,
        "meeting_id": case.meeting_id,
        "split": case.split,
        "domain": case.domain,
        "query_kind": case.query_kind,
        "eligibility": dict(case.eligibility),
        "eligibility_state": policy_state,
        "eligible": policy_state == "citation_scorable",
        "present": True,
        "status": "scored",
        "retrieved_sentence_ids": list(retrieved),
        "cited_sentence_ids": list(cited),
        "retrieved_count": len(retrieved),
        "cited_count": len(cited),
        "gold_groups": [sorted(group) for group in groups],
        "original_group_count": len(case.evidence_groups),
        "filtered_group_count": len(groups),
        "filtered_empty_group_count": len(case.evidence_groups) - len(groups),
        "gold_group_count": len(groups),
        "gold_sentence_ids": sorted(gold_ids),
        "gold_sentence_count": len(gold_ids),
        "gold_numerator": len(overlap),
        "gold_denominator": len(gold_ids),
        "citation_numerator": len(overlap),
        "citation_denominator": len(cited_set),
        "reference_group_any_numerator": metrics["reference_group_any_hit"]["numerator"],
        "reference_group_any_denominator": metrics["reference_group_any_hit"]["denominator"],
        "reference_group_all_numerator": metrics["reference_group_all_hit"]["numerator"],
        "reference_group_all_denominator": metrics["reference_group_all_hit"]["denominator"],
        "retrieval": retrieval,
        "metrics": metrics,
    }
    _add_flat_metrics(row, metrics)
    return row


def _row_without_score(
    validated: _ValidatedCase,
    *,
    present: bool,
    prediction: Mapping[str, list[str]] | None,
    policy: str,
    ks: tuple[int, ...],
) -> dict[str, Any]:
    case = validated.case
    policy_state = case.eligibility[policy]
    status = "omitted" if present else "missing"
    metrics = _unavailable_metrics(ks)
    retrieved = (
        list(prediction["retrieved_sentence_ids"])
        if prediction is not None
        else None
    )
    cited = (
        list(prediction["cited_sentence_ids"])
        if prediction is not None
        else None
    )
    row: dict[str, Any] = {
        "case_id": case.case_id,
        "meeting_id": case.meeting_id,
        "split": case.split,
        "domain": case.domain,
        "query_kind": case.query_kind,
        "eligibility": dict(case.eligibility),
        "eligibility_state": policy_state,
        "eligible": policy_state == "citation_scorable",
        "present": present,
        "status": status,
        "retrieved_sentence_ids": retrieved,
        "cited_sentence_ids": cited,
        "retrieved_count": len(retrieved) if retrieved is not None else None,
        "cited_count": len(cited) if cited is not None else None,
        "gold_groups": [sorted(group) for group in validated.groups],
        "original_group_count": len(case.evidence_groups),
        "filtered_group_count": len(validated.groups),
        "filtered_empty_group_count": len(case.evidence_groups)
        - len(validated.groups),
        "gold_group_count": len(validated.groups),
        "gold_sentence_ids": sorted(validated.gold_ids),
        "gold_sentence_count": len(validated.gold_ids),
        "metrics": metrics,
        "retrieval": {
            "recall": {f"@{k}": metrics[f"retrieval_recall@{k}"] for k in ks},
            "hit": {f"@{k}": metrics[f"retrieval_hit@{k}"] for k in ks},
            "mrr": metrics["retrieval_mrr"],
        },
    }
    for name in (
        "gold_numerator",
        "gold_denominator",
        "citation_numerator",
        "citation_denominator",
        "reference_group_any_numerator",
        "reference_group_any_denominator",
        "reference_group_all_numerator",
        "reference_group_all_denominator",
    ):
        row[name] = None
    _add_flat_metrics(row, metrics)
    return row


def _coverage(rows: list[dict[str, Any]]) -> dict[str, int]:
    selected = {row["case_id"] for row in rows}
    present = {row["case_id"] for row in rows if row["present"]}
    eligible = {row["case_id"] for row in rows if row["eligible"]}
    values = {
        "selected": len(selected),
        "present": len(present),
        "missing": len(selected - present),
        "eligible": len(eligible),
        "ineligible": len(selected - eligible),
        "eligible_missing": len(eligible - present),
        "scored": len(present & eligible),
    }
    values.update(
        {
            "S": values["selected"],
            "P": values["present"],
            "S_minus_P": values["missing"],
            "E": values["eligible"],
            "S_minus_E": values["ineligible"],
            "E_minus_P": values["eligible_missing"],
            "P_intersect_E": values["scored"],
            "selected_count": values["selected"],
            "present_count": values["present"],
            "missing_count": values["missing"],
            "eligible_count": values["eligible"],
            "omitted_count": values["ineligible"],
            "scored_count": values["scored"],
        }
    )
    return values


def _macro_summary(
    rows: list[dict[str, Any]],
    *,
    macro: str,
    scope: str,
    ks: tuple[int, ...],
) -> dict[str, Any]:
    scored = [row for row in rows if row["status"] == "scored"]
    metric_names = _metric_names(ks)
    if macro == "query":
        units: list[dict[str, float]] = [
            {name: row["metrics"][name]["value"] for name in metric_names}
            for row in scored
        ]
    else:
        by_meeting: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in scored:
            by_meeting[row["meeting_id"]].append(row)
        units = [
            {
                name: sum(row["metrics"][name]["value"] for row in meeting_rows)
                / len(meeting_rows)
                for name in metric_names
            }
            for meeting_rows in by_meeting.values()
        ]

    metrics: dict[str, dict[str, Any]] = {}
    for name in metric_names:
        if units:
            numerator = sum(unit[name] for unit in units)
            descriptor = _metric(numerator / len(units), numerator, len(units))
        else:
            descriptor = _metric(None, 0, 0)
        metrics[name] = descriptor

    summary: dict[str, Any] = {
        "scope": scope,
        "macro": f"{macro}_macro",
        "case_count": len(rows),
        "scored_case_count": len(scored),
        "eligible_count": sum(row["eligible"] for row in rows),
        "omitted_count": sum(not row["eligible"] for row in rows),
        "unit_count": len(units),
        "metrics": metrics,
    }
    _add_flat_metrics(summary, metrics)
    if macro == "meeting":
        summary["meeting_count"] = len(units)
        summary["meetings_without_scored_case"] = sum(
            not any(
                row["status"] == "scored" and row["meeting_id"] == meeting_id
                for row in rows
            )
            for meeting_id in {row["meeting_id"] for row in rows}
        )
    return summary


def _aggregate_group(
    rows: list[dict[str, Any]],
    *,
    scope: str,
    ks: tuple[int, ...],
) -> dict[str, Any]:
    return {
        "scope": scope,
        "coverage": _coverage(rows),
        "query_macro": _macro_summary(rows, macro="query", scope=scope, ks=ks),
        "meeting_macro": _macro_summary(rows, macro="meeting", scope=scope, ks=ks),
    }


def _validate_ks(ks: Sequence[int]) -> tuple[int, ...]:
    if isinstance(ks, (str, bytes)) or not isinstance(ks, Sequence):
        raise ScoringError("ks_sequence")
    result = tuple(ks)
    if not result or any(type(k) is not int or k <= 0 for k in result):
        raise ScoringError("ks_positive_integers")
    if len(set(result)) != len(result):
        raise ScoringError("duplicate_k")
    return result


def score_cases(
    cases: list[Case],
    predictions: dict[str, Any],
    *,
    corpus: Mapping[str, list[Sentence] | MeetingCorpus],
    selection: dict[str, Any],
    dataset_hash: str,
    policy: str = "ces",
    mode: str = "complete",
    ks: Sequence[int] = (1, 3, 5),
) -> dict[str, Any]:
    """Score strict, hash-bound sentence-ID predictions offline.

    ``complete`` mode requires one prediction record for every selected case;
    any omission leaves aggregate metrics unavailable.  ``partial`` mode is an
    explicit subset diagnostic and aggregates only present, eligible records.
    """

    dataset_hash = _require_hash(dataset_hash, "dataset")
    if policy not in _POLICY_LABELS:
        raise ScoringError("unknown_policy")
    if mode not in {"complete", "partial"}:
        raise ScoringError("unknown_mode")
    normalized_ks = _validate_ks(ks)

    known_sentences, corpus_meetings = _validate_corpus(corpus)
    selection_split, selected_case_ids, selection_hash = _validate_selection(
        selection, dataset_hash
    )
    validated_cases = _validate_cases(
        cases,
        known_sentences=known_sentences,
        corpus_meetings=corpus_meetings,
        policy=policy,
        selected_case_ids=selected_case_ids,
        selection_split=selection_split,
    )
    prediction_by_case, prediction_hash = _validate_predictions(
        predictions,
        dataset_hash=dataset_hash,
        selection_hash=selection_hash,
        selected_case_ids=selected_case_ids,
        known_sentence_ids=set(known_sentences),
    )

    rows: list[dict[str, Any]] = []
    for case_id in selected_case_ids:
        validated = validated_cases[case_id]
        prediction = prediction_by_case.get(case_id)
        if prediction is None:
            row = _row_without_score(
                validated,
                present=False,
                prediction=None,
                policy=policy,
                ks=normalized_ks,
            )
        elif validated.case.eligibility[policy] != "citation_scorable":
            row = _row_without_score(
                validated,
                present=True,
                prediction=prediction,
                policy=policy,
                ks=normalized_ks,
            )
        else:
            row = _score_case(
                validated,
                prediction,
                policy=policy,
                ks=normalized_ks,
            )
        rows.append(row)

    coverage = _coverage(rows)
    missing = coverage["missing"]
    if mode == "complete":
        status = "incomplete" if missing else "complete"
        aggregate_scope = "selected_eligible"
    else:
        status = "partial"
        aggregate_scope = "subset_only"

    aggregates: dict[str, Any] | None
    if mode == "complete" and missing:
        aggregates = None
    else:
        aggregates = _aggregate_group(
            rows,
            scope=aggregate_scope,
            ks=normalized_ks,
        )
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(row["split"], row["domain"])].append(row)
        aggregates["by_split_domain"] = {
            f"{split}/{domain}": _aggregate_group(
                grouped[(split, domain)],
                scope=aggregate_scope,
                ks=normalized_ks,
            )
            for split, domain in sorted(grouped)
        }

    prediction_configuration = {
        "scorer_version": SCORER_VERSION,
        "policy": policy,
        "mode": mode,
        "ks": list(normalized_ks),
        "dataset_hash": dataset_hash,
        "selection_version": SELECTION_VERSION,
        "selection_hash": selection_hash,
        "prediction_schema_version": PREDICTIONS_VERSION,
        "prediction_hash": prediction_hash,
        "canonical_prediction_hash": prediction_hash,
    }
    return {
        "scorer_version": SCORER_VERSION,
        "status": status,
        "configuration": prediction_configuration,
        "prediction_hash": prediction_hash,
        "canonical_prediction_hash": prediction_hash,
        "policy": policy,
        "mode": mode,
        "ks": list(normalized_ks),
        "selection_hash": selection_hash,
        "dataset_hash": dataset_hash,
        "coverage": coverage,
        "cases": rows,
        "aggregates": aggregates,
        "semantic_citation_completeness": None,
    }
