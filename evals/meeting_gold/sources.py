"""Strict readers and one-to-one alignment for the published source files."""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .core import (
    EVIDENCE_TYPES,
    QUERY_KINDS,
    SPLITS,
    Audit,
    Case,
    MeetingCorpus,
    Sentence,
    SourceFormatError,
    stable_case_id,
    stable_sentence_id,
    Turn,
)


def _duplicate_key_error(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceFormatError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SourceFormatError(f"non_finite_json:{value}")


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SourceFormatError(f"invalid_source_file:{path.name}")
    try:
        raw = path.read_bytes().decode("utf-8")
        value = json.loads(
            raw,
            object_pairs_hook=_duplicate_key_error,
            parse_constant=_reject_json_constant,
        )
    except SourceFormatError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceFormatError(f"invalid_json:{path.name}") from exc
    if not isinstance(value, dict):
        raise SourceFormatError(f"json_root_not_object:{path.name}")
    return value


def load_json_files(
    root: Path, *, allow_empty: bool = False
) -> list[tuple[Path, dict[str, Any]]]:
    """Load only the canonical, top-level JSON files in a split directory."""

    root_path = Path(root)
    if root_path.is_symlink() or not root_path.is_dir():
        raise SourceFormatError(f"invalid_source_root:{root_path.name}")

    paths: list[Path] = []
    for entry in sorted(root_path.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            raise SourceFormatError(f"symlink_in_source:{entry.name}")
        if entry.suffix == ".json":
            if not entry.is_file():
                raise SourceFormatError(f"invalid_source_file:{entry.name}")
            paths.append(entry)
    if not paths and not allow_empty:
        raise SourceFormatError(f"empty_source_directory:{root_path.name}")
    return [(path, _read_json(path)) for path in paths]


def _require_string(value: Any, error: str) -> str:
    if not isinstance(value, str):
        raise SourceFormatError(error)
    return value


def _require_nonempty_string(value: Any, error: str) -> str:
    value = _require_string(value, error)
    if not value.strip():
        raise SourceFormatError(error)
    return value


def _require_list(value: Any, error: str) -> list[Any]:
    if not isinstance(value, list):
        raise SourceFormatError(error)
    return value


def _validate_split(split: str) -> None:
    if split not in SPLITS:
        raise SourceFormatError(f"invalid_split:{split}")


def _validate_relevant_spans(
    value: Any,
    *,
    file_name: str,
    kind: str,
    index: int,
    turn_count: int,
) -> list[Any]:
    if not isinstance(value, list):
        raise SourceFormatError(f"invalid_relevant_text_span:{file_name}:{kind}:{index}")
    for span in value:
        if not isinstance(span, list) or len(span) != 2:
            raise SourceFormatError(f"invalid_relevant_text_span:{file_name}:{kind}:{index}")
        if not all(
            isinstance(point, str) and point.isascii() and point.isdigit()
            for point in span
        ):
            raise SourceFormatError(f"invalid_relevant_text_span:{file_name}:{kind}:{index}")
        start, end = (int(point) for point in span)
        if start > end or end >= turn_count:
            raise SourceFormatError(f"invalid_relevant_text_span:{file_name}:{kind}:{index}")
    return value


def _topic_list(
    record: dict[str, Any], file_name: str, *, turn_count: int
) -> list[dict[str, Any]]:
    topics = _require_list(record.get("topic_list"), f"invalid_topic_list:{file_name}")
    for index, topic in enumerate(topics):
        if not isinstance(topic, dict):
            raise SourceFormatError(f"invalid_topic:{file_name}:{index}")
        _require_string(topic.get("topic"), f"invalid_topic:{file_name}:{index}")
        if "relevant_text_span" in topic:
            _validate_relevant_spans(
                topic["relevant_text_span"],
                file_name=file_name,
                kind="topic",
                index=index,
                turn_count=turn_count,
            )
    return topics


def _query_list(
    record: dict[str, Any],
    kind: str,
    file_name: str,
    *,
    turn_count: int,
) -> list[dict[str, Any]]:
    values = _require_list(
        record.get(f"{kind}_query_list"),
        f"invalid_query_list:{file_name}:{kind}",
    )
    result: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise SourceFormatError(f"invalid_query:{file_name}:{kind}:{index}")
        _require_nonempty_string(
            item.get("query"), f"invalid_query:{file_name}:{kind}:{index}"
        )
        _require_nonempty_string(
            item.get("answer"), f"invalid_query:{file_name}:{kind}:{index}"
        )
        if "relevant_text_span" in item:
            _validate_relevant_spans(
                item["relevant_text_span"],
                file_name=file_name,
                kind=kind,
                index=index,
                turn_count=turn_count,
            )
        result.append(item)
    return result


def _meeting_transcripts(record: dict[str, Any], file_name: str) -> list[dict[str, Any]]:
    turns = _require_list(record.get("meeting_transcripts"), f"invalid_transcripts:{file_name}")
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise SourceFormatError(f"invalid_turn:{file_name}:{index}")
        _require_string(turn.get("speaker"), f"invalid_turn_speaker:{file_name}:{index}")
        _require_string(turn.get("content"), f"invalid_turn_content:{file_name}:{index}")
    return turns


def _raw_turns(
    record: dict[str, Any], *, file_name: str, meeting_id: str
) -> tuple[Turn, ...]:
    return tuple(
        Turn(
            meeting_id=meeting_id,
            turn_index=turn_index,
            speaker=turn["speaker"],
            text=turn["content"],
        )
        for turn_index, turn in enumerate(_meeting_transcripts(record, file_name))
    )


def _meeting_id(path: Path) -> str:
    if not path.stem:
        raise SourceFormatError(f"invalid_meeting_filename:{path.name}")
    return path.stem


def _case_tuple(case: Case) -> tuple[str, str]:
    return case.query, case.answer


def _record_tuple(record: dict[str, Any]) -> tuple[str, str]:
    return record["query"], record["answer"]


def _add_reason(case: Case, reason: str) -> None:
    reasons = case.provenance.setdefault("alignment_reasons", [])
    if reason not in reasons:
        reasons.append(reason)
        reasons.sort()


def _set_answer_only(case: Case, reason: str) -> None:
    _add_reason(case, reason)
    if case.lifecycle_state == "quarantined":
        return
    case.lifecycle_state = "answer_only"
    case.eligibility = {"ces": "answer_only", "ces_pes": "answer_only"}


def _set_partial(case: Case, reason: str) -> None:
    _add_reason(case, reason)
    if case.lifecycle_state == "quarantined":
        return
    case.lifecycle_state = "partial_annotation"
    case.eligibility = {"ces": "partial_annotation", "ces_pes": "partial_annotation"}


def _quarantine(case: Case, reason: str) -> None:
    _add_reason(case, reason)
    case.lifecycle_state = "quarantined"
    case.eligibility = {"ces": "quarantined", "ces_pes": "quarantined"}


def _classify(case: Case) -> None:
    if not case.answer_sentence_index:
        _set_partial(case, "empty_explainable_answer")
        return

    eligibility: dict[str, str] = {}
    for policy, allowed in (("ces", {"CES"}), ("ces_pes", {"CES", "PES"})):
        filtered_groups = [
            [sentence_id for sentence_id, label in zip(group, labels) if label in allowed]
            for group, labels in zip(case.evidence_groups, case.evidence_types)
        ]
        eligibility[policy] = (
            "citation_scorable"
            if filtered_groups and all(filtered_groups)
            else "partial_annotation"
        )

    case.eligibility = eligibility
    case.lifecycle_state = (
        "citation_scorable"
        if eligibility["ces"] == eligibility["ces_pes"] == "citation_scorable"
        else "partial_annotation"
    )


def _validate_answer_indices(
    value: Any,
    *,
    case: Case,
    group_index: int,
    audit: Audit,
) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or any(type(index) is not int or index < 0 for index in value)
        or value != list(range(value[0], value[0] + len(value)))
    ):
        audit.add("invalid_answer_indices", case_id=case.case_id, group=group_index)
        _add_reason(case, "invalid_answer_indices")
        return []
    return list(value)


def _reconcile_answer(answer: str, group_texts: Iterable[str]) -> bool:
    """Consume groups literally, allowing only whitespace between groups."""

    offset = 0
    for text in group_texts:
        position = answer.find(text, offset)
        if position < 0 or answer[offset:position].strip():
            return False
        offset = position + len(text)
    return not answer[offset:].strip()


def _validate_answer_mapping(case: Case, audit: Audit) -> None:
    flattened = [index for group in case.answer_sentence_index for index in group]
    if flattened != list(range(len(flattened))):
        audit.add("answer_indices_not_contiguous", case_id=case.case_id)
        _add_reason(case, "answer_indices_not_contiguous")
    if not _reconcile_answer(case.answer, case.answer_sentence_texts):
        audit.add("answer_group_text_not_consumed", case_id=case.case_id)
        _add_reason(case, "answer_group_text_not_consumed")


def _sentence_records(
    record: dict[str, Any],
    *,
    file_name: str,
    meeting_id: str,
) -> tuple[list[Sentence], dict[tuple[int, int], Sentence], tuple[Turn, ...]]:
    turns = _meeting_transcripts(record, file_name)
    raw_turns = tuple(
        Turn(
            meeting_id=meeting_id,
            turn_index=turn_index,
            speaker=turn["speaker"],
            text=turn["content"],
        )
        for turn_index, turn in enumerate(turns)
    )
    sentences: list[Sentence] = []
    by_coordinate: dict[tuple[int, int], Sentence] = {}
    for turn_index, turn in enumerate(turns):
        contents = _require_list(
            turn.get("sentence_level_content"),
            f"invalid_sentence_level_content:{file_name}:{turn_index}",
        )
        for sentence_index, item in enumerate(contents):
            if not isinstance(item, dict):
                raise SourceFormatError(
                    f"invalid_sentence:{file_name}:{turn_index}:{sentence_index}"
                )
            if type(item.get("turn_index")) is not int or item["turn_index"] != turn_index:
                raise SourceFormatError(
                    f"invalid_sentence_turn_index:{file_name}:{turn_index}:{sentence_index}"
                )
            if type(item.get("sent_index")) is not int or item["sent_index"] != sentence_index:
                raise SourceFormatError(
                    f"invalid_sentence_index:{file_name}:{turn_index}:{sentence_index}"
                )
            dialogue_sentence = _require_string(
                item.get("dialogue_sentence"),
                f"invalid_dialogue_sentence:{file_name}:{turn_index}:{sentence_index}",
            )
            dialogue_act_id = item.get("dialogue-act_id")
            if dialogue_act_id is not None and not isinstance(dialogue_act_id, str):
                raise SourceFormatError(
                    f"invalid_dialogue_act_id:{file_name}:{turn_index}:{sentence_index}"
                )
            sentence = Sentence(
                id=stable_sentence_id(meeting_id, turn_index, sentence_index),
                meeting_id=meeting_id,
                turn_index=turn_index,
                sentence_index=sentence_index,
                speaker=turn["speaker"],
                text=dialogue_sentence,
                dialogue_act_id=dialogue_act_id,
            )
            coordinate = (turn_index, sentence_index)
            if coordinate in by_coordinate:
                raise SourceFormatError(
                    f"duplicate_sentence_coordinate:{file_name}:{turn_index}:{sentence_index}"
                )
            by_coordinate[coordinate] = sentence
            sentences.append(sentence)
    return sentences, by_coordinate, raw_turns


def _validate_transcript_alignment(
    qmsum_turns: Iterable[Turn],
    explainable_record: dict[str, Any],
    *,
    file_name: str,
    audit: Audit,
    meeting_id: str,
    split: str,
 ) -> tuple[list[Sentence], dict[tuple[int, int], Sentence], tuple[Turn, ...], bool]:
    ems_turns = _meeting_transcripts(explainable_record, file_name)
    ems_raw_turns = tuple(
        Turn(
            meeting_id=meeting_id,
            turn_index=turn_index,
            speaker=turn["speaker"],
            text=turn["content"],
        )
        for turn_index, turn in enumerate(ems_turns)
    )
    qmsum_view = [(turn.speaker, turn.text) for turn in qmsum_turns]
    ems_view = [(turn.speaker, turn.text) for turn in ems_raw_turns]
    if qmsum_view != ems_view:
        audit.add(
            "transcript_mismatch",
            meeting_id=meeting_id,
            split=split,
            explainmeetsum_file=file_name,
        )
        return [], {}, ems_raw_turns, True
    sentences, by_coordinate, _ = _sentence_records(
        explainable_record,
        file_name=file_name,
        meeting_id=meeting_id,
    )
    return sentences, by_coordinate, ems_raw_turns, False


def parse_qmsum(
    root: Path,
    split: str,
    audit: Audit,
) -> tuple[dict[str, list[Sentence]], list[Case]]:
    """Parse QMSum's canonical per-meeting JSON files for one official split."""

    _validate_split(split)
    corpus: dict[str, list[Sentence]] = {}
    cases: list[Case] = []
    for path, record in load_json_files(Path(root)):
        meeting_id = _meeting_id(path)
        if meeting_id in corpus:
            raise SourceFormatError(f"duplicate_meeting_id:{meeting_id}")
        if "domain" in record and not isinstance(record["domain"], str):
            raise SourceFormatError(f"invalid_domain:{path.name}")
        turns = _meeting_transcripts(record, path.name)
        _topic_list(record, path.name, turn_count=len(turns))
        # QMSum's canonical ALL files contain raw turns, not sentence units.
        if any("sentence_level_content" in turn for turn in turns):
            raise SourceFormatError(f"unexpected_sentence_level_content:{path.name}")
        qmsum_turns = _raw_turns(record, file_name=path.name, meeting_id=meeting_id)
        # QMSum has raw turns only; sentence coordinates are usable after EMS validation.
        corpus[meeting_id] = MeetingCorpus(qmsum_turns=qmsum_turns)
        for kind in QUERY_KINDS:
            for query_index, query_record in enumerate(
                _query_list(record, kind, path.name, turn_count=len(turns))
            ):
                query = query_record["query"]
                answer = query_record["answer"]
                cases.append(
                    Case(
                        case_id=stable_case_id(meeting_id, kind, query_index),
                        meeting_id=meeting_id,
                        split=split,
                        domain=(
                            record["domain"]
                            if isinstance(record.get("domain"), str)
                            else "unknown"
                        ),
                        query_kind=kind,
                        query_index=query_index,
                        query=query,
                        answer=answer,
                        relevant_spans=query_record.get("relevant_text_span", []),
                        provenance={
                            "qmsum_file": path.name,
                            "qmsum_query_index": query_index,
                        },
                    )
                )
    return corpus, cases


def _validate_explainable_queries(
    record: dict[str, Any],
    *,
    file_name: str,
    turn_count: int,
) -> dict[str, list[dict[str, Any]]]:
    block = record.get("explainable_qmsum")
    if not isinstance(block, dict):
        raise SourceFormatError(f"invalid_explainable_qmsum:{file_name}")
    return {
        kind: _query_list(
            block,
            kind,
            f"{file_name}:explainable_qmsum",
            turn_count=turn_count,
        )
        for kind in QUERY_KINDS
    }


def _mark_unmatched_case(
    case: Case,
    *,
    reason: str,
    audit: Audit,
    split: str,
) -> None:
    _set_answer_only(case, reason)
    audit.add(
        "unmatched_qmsum",
        case_id=case.case_id,
        meeting_id=case.meeting_id,
        split=split,
        reason=reason,
    )


def _mark_unmatched_annotation(
    *,
    path: Path,
    meeting_id: str,
    split: str,
    kind: str,
    query_index: int,
    reason: str,
    audit: Audit,
) -> None:
    audit.add(
        "unmatched_explainable",
        explainmeetsum_file=path.name,
        meeting_id=meeting_id,
        split=split,
        query_kind=kind,
        query_index=query_index,
        reason=reason,
    )


def _audit_unconsumed_annotations(
    *,
    record: dict[str, Any],
    path: Path,
    meeting_id: str,
    split: str,
    reason: str,
    audit: Audit,
) -> None:
    block = record.get("explainable_qmsum")
    if not isinstance(block, dict):
        return
    for kind in QUERY_KINDS:
        annotations = block.get(f"{kind}_query_list")
        if not isinstance(annotations, list):
            continue
        for query_index in range(len(annotations)):
            _mark_unmatched_annotation(
                path=path,
                meeting_id=meeting_id,
                split=split,
                kind=kind,
                query_index=query_index,
                reason=reason,
                audit=audit,
            )


def _attach_annotation(
    case: Case,
    annotation: dict[str, Any],
    *,
    annotation_index: int,
    sentence_by_coordinate: dict[tuple[int, int], Sentence],
    path: Path,
    audit: Audit,
) -> None:
    already_quarantined = case.lifecycle_state == "quarantined"
    if "explainable_answer" in annotation:
        case.provenance["raw_annotation_groups"] = copy.deepcopy(
            annotation["explainable_answer"]
        )
    if "explainable_answer" not in annotation:
        audit.add("missing_explainable_answer", case_id=case.case_id)
        case.provenance.update(
            {
                "explainmeetsum_file": path.name,
                "explainmeetsum_query_index": annotation_index,
                "explainmeetsum_query_kind": case.query_kind,
                "annotation_group_count": 0,
            }
        )
        if not already_quarantined:
            _set_partial(case, "missing_explainable_answer")
        return

    groups = annotation["explainable_answer"]
    if not isinstance(groups, list):
        audit.add("invalid_evidence_groups", case_id=case.case_id)
        _quarantine(case, "invalid_evidence_groups")
        return
    if not groups:
        case.answer_sentence_index = []
        case.answer_sentence_texts = []
        case.evidence_groups = []
        case.evidence_types = []
        case.evidence_records = []
        case.provenance.update(
            {
                "explainmeetsum_file": path.name,
                "explainmeetsum_query_index": annotation_index,
                "explainmeetsum_query_kind": case.query_kind,
                "annotation_group_count": 0,
            }
        )
        if not already_quarantined:
            _set_partial(case, "empty_explainable_answer")
        return

    answer_indices: list[list[int]] = []
    answer_texts: list[str] = []
    evidence_groups: list[list[str]] = []
    evidence_types: list[list[str]] = []
    evidence_records: list[list[dict[str, Any]]] = []
    hard_failure = False

    for group_index, group in enumerate(groups):
        if not isinstance(group, dict):
            audit.add("invalid_evidence_group", case_id=case.case_id, group=group_index)
            _add_reason(case, "invalid_evidence_group")
            hard_failure = True
            answer_indices.append([])
            answer_texts.append("")
            evidence_groups.append([])
            evidence_types.append([])
            evidence_records.append([])
            continue

        answer_text = group.get("answer_sentence")
        if not isinstance(answer_text, str) or not answer_text.strip():
            audit.add("invalid_answer_sentence", case_id=case.case_id, group=group_index)
            _add_reason(case, "invalid_answer_sentence")
            hard_failure = True
            answer_text = answer_text if isinstance(answer_text, str) else ""
        answer_texts.append(answer_text)

        indices = _validate_answer_indices(
            group.get("answer_sentence_index"),
            case=case,
            group_index=group_index,
            audit=audit,
        )
        if not indices:
            hard_failure = True
        answer_indices.append(indices)

        if "evidence" not in group or not isinstance(group.get("evidence"), list):
            audit.add("invalid_evidence", case_id=case.case_id, group=group_index)
            _add_reason(case, "invalid_evidence")
            hard_failure = True
            raw_evidence: list[Any] = []
        else:
            raw_evidence = group["evidence"]

        valid_evidence: list[tuple[str, str]] = []
        records: list[dict[str, Any]] = []
        seen_coordinates: dict[tuple[int, int], str] = {}
        for evidence_index, evidence in enumerate(raw_evidence):
            if not isinstance(evidence, dict):
                audit.add(
                    "invalid_pointer",
                    case_id=case.case_id,
                    group=group_index,
                    evidence=evidence_index,
                )
                _add_reason(case, "invalid_pointer")
                hard_failure = True
                continue
            records.append(dict(evidence))

            label = evidence.get("type")
            if not isinstance(label, str) or label not in EVIDENCE_TYPES:
                audit.add(
                    "unknown_evidence_type",
                    case_id=case.case_id,
                    group=group_index,
                    evidence=evidence_index,
                )
                _add_reason(case, "unknown_evidence_type")
                hard_failure = True
                continue

            turn_index = evidence.get("turn_index")
            sentence_index = evidence.get("sent_index")
            if (
                type(turn_index) is not int
                or turn_index < 0
                or type(sentence_index) is not int
                or sentence_index < 0
            ):
                audit.add(
                    "invalid_pointer",
                    case_id=case.case_id,
                    group=group_index,
                    evidence=evidence_index,
                )
                _add_reason(case, "invalid_pointer")
                hard_failure = True
                continue

            coordinate = (turn_index, sentence_index)
            source_sentence = sentence_by_coordinate.get(coordinate)
            if source_sentence is None:
                audit.add(
                    "pointer_out_of_bounds",
                    case_id=case.case_id,
                    group=group_index,
                    evidence=evidence_index,
                )
                _add_reason(case, "pointer_out_of_bounds")
                hard_failure = True
                continue
            if (
                evidence.get("speaker") != source_sentence.speaker
                or evidence.get("content") != source_sentence.text
            ):
                audit.add(
                    "pointer_text_mismatch",
                    case_id=case.case_id,
                    group=group_index,
                    evidence=evidence_index,
                )
                _add_reason(case, "pointer_text_mismatch")
                hard_failure = True
                continue
            if label in EVIDENCE_TYPES:
                previous_label = seen_coordinates.get(coordinate)
                if previous_label is not None and previous_label != label:
                    audit.add(
                        "contradictory_evidence_label",
                        case_id=case.case_id,
                        group=group_index,
                        evidence=evidence_index,
                    )
                    _add_reason(case, "contradictory_evidence_label")
                    hard_failure = True
                    continue
                seen_coordinates[coordinate] = label
                valid_evidence.append((source_sentence.id, label))

        evidence_groups.append([sentence_id for sentence_id, _ in valid_evidence])
        evidence_types.append([label for _, label in valid_evidence])
        evidence_records.append(records)

    case.answer_sentence_index = answer_indices
    case.answer_sentence_texts = answer_texts
    case.evidence_groups = evidence_groups
    case.evidence_types = evidence_types
    case.evidence_records = evidence_records
    case.provenance.update(
        {
            "explainmeetsum_file": path.name,
            "explainmeetsum_query_index": annotation_index,
            "explainmeetsum_query_kind": case.query_kind,
            "annotation_group_count": len(groups),
        }
    )
    _validate_answer_mapping(case, audit)
    if "answer_group_text_not_consumed" in case.provenance.get("alignment_reasons", []):
        hard_failure = True
    if "answer_indices_not_contiguous" in case.provenance.get("alignment_reasons", []):
        hard_failure = True
    if hard_failure:
        _quarantine(case, "annotation_validation_failed")
    elif already_quarantined:
        return
    else:
        _classify(case)


def _annotation_indexes(
    cases: list[Case],
    annotations: list[dict[str, Any]],
    *,
    meeting_id: str,
    kind: str,
    path: Path,
    split: str,
    audit: Audit,
    sentence_by_coordinate: dict[tuple[int, int], Sentence],
) -> None:
    qmsum_by_tuple: dict[tuple[str, str], list[Case]] = defaultdict(list)
    for case in cases:
        qmsum_by_tuple[_case_tuple(case)].append(case)
    explainable_by_tuple: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for annotation_index, annotation in enumerate(annotations):
        if not isinstance(annotation, dict):
            raise SourceFormatError(
                f"invalid_query:{path.name}:explainable_qmsum:{kind}:{annotation_index}"
            )
        _require_string(
            annotation.get("query"),
            f"invalid_query:{path.name}:explainable_qmsum:{kind}:{annotation_index}",
        )
        _require_string(
            annotation.get("answer"),
            f"invalid_query:{path.name}:explainable_qmsum:{kind}:{annotation_index}",
        )
        explainable_by_tuple[_record_tuple(annotation)].append((annotation_index, annotation))

    for query_tuple in sorted(
        set(qmsum_by_tuple) | set(explainable_by_tuple),
        key=lambda value: (value[0], value[1]),
    ):
        qmsum_rows = qmsum_by_tuple.get(query_tuple, [])
        explainable_rows = explainable_by_tuple.get(query_tuple, [])
        if len(qmsum_rows) == 1 and len(explainable_rows) == 1:
            case = qmsum_rows[0]
            annotation_index, annotation = explainable_rows[0]
            if case.provenance.get("transcript_mismatch"):
                continue
            _attach_annotation(
                case,
                annotation,
                annotation_index=annotation_index,
                sentence_by_coordinate=sentence_by_coordinate,
                path=path,
                audit=audit,
            )
            case.domain = case.provenance.get("explainmeetsum_domain", case.domain)
            continue

        if qmsum_rows and explainable_rows and (
            len(qmsum_rows) != 1 or len(explainable_rows) != 1
        ):
            for case in qmsum_rows:
                _quarantine(case, "ambiguous_join")
                audit.add(
                    "ambiguous_join",
                    case_id=case.case_id,
                    meeting_id=meeting_id,
                    split=split,
                    query_kind=kind,
                    reason="duplicate_exact_tuple",
                )
            for annotation_index, _ in explainable_rows:
                _mark_unmatched_annotation(
                    path=path,
                    meeting_id=meeting_id,
                    split=split,
                    kind=kind,
                    query_index=annotation_index,
                    reason="ambiguous_exact_tuple",
                    audit=audit,
                )
            continue

        for case in qmsum_rows:
            _mark_unmatched_case(
                case,
                reason="missing_explainmeetsum_query",
                audit=audit,
                split=split,
            )
        for annotation_index, _ in explainable_rows:
            _mark_unmatched_annotation(
                path=path,
                meeting_id=meeting_id,
                split=split,
                kind=kind,
                query_index=annotation_index,
                reason="missing_qmsum_query",
                audit=audit,
            )


def join_explainable(
    cases: list[Case],
    root: Path,
    split: str,
    corpus: dict[str, list[Sentence]],
    audit: Audit,
) -> None:
    """Join one ExplainMeetSum split onto QMSum cases by exact tuple only."""

    _validate_split(split)
    if any(case.split != split for case in cases):
        raise SourceFormatError("case_split_mismatch")

    cases_by_meeting: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        cases_by_meeting[case.meeting_id].append(case)
    seen_meetings: set[str] = set()

    for path, record in load_json_files(Path(root), allow_empty=True):
        meeting_id = _meeting_id(path)
        meeting_cases = cases_by_meeting.get(meeting_id)
        if meeting_cases is None:
            audit.add(
                "unexpected_explainmeetsum_meeting",
                explainmeetsum_file=path.name,
                meeting_id=meeting_id,
                split=split,
            )
            _audit_unconsumed_annotations(
                record=record,
                path=path,
                meeting_id=meeting_id,
                split=split,
                reason="unexpected_meeting",
                audit=audit,
            )
            continue
        seen_meetings.add(meeting_id)

        domain = record.get("domain")
        if not isinstance(domain, str) or not domain:
            raise SourceFormatError(f"invalid_domain:{path.name}")
        ems_turns = _meeting_transcripts(record, path.name)
        _topic_list(record, path.name, turn_count=len(ems_turns))
        for case in meeting_cases:
            if case.domain not in {"unknown", domain}:
                _quarantine(case, "domain_mismatch")
                audit.add("domain_mismatch", case_id=case.case_id, meeting_id=meeting_id)
            case.domain = domain
            case.provenance["explainmeetsum_domain"] = domain

        if meeting_id not in corpus:
            raise SourceFormatError(f"missing_qmsum_meeting:{meeting_id}")
        transcript = corpus[meeting_id]
        if isinstance(transcript, MeetingCorpus):
            qmsum_turns = transcript.qmsum_turns
        else:
            qmsum_turns = tuple(
                Turn(
                    meeting_id=meeting_id,
                    turn_index=turn.turn_index,
                    speaker=turn.speaker,
                    text=turn.text,
                )
                for turn in transcript
            )
        sentence_records, sentence_by_coordinate, explainmeetsum_turns, transcript_mismatch = _validate_transcript_alignment(
            qmsum_turns,
            record,
            file_name=path.name,
            audit=audit,
            meeting_id=meeting_id,
            split=split,
        )
        if transcript_mismatch:
            if isinstance(transcript, MeetingCorpus):
                transcript.explainmeetsum_turns = explainmeetsum_turns
            for case in meeting_cases:
                case.provenance["transcript_mismatch"] = True
                _quarantine(case, "transcript_mismatch")
            _audit_unconsumed_annotations(
                record=record,
                path=path,
                meeting_id=meeting_id,
                split=split,
                reason="transcript_mismatch",
                audit=audit,
            )
            continue
        corpus[meeting_id] = MeetingCorpus(
            sentence_records,
            qmsum_turns=qmsum_turns,
            explainmeetsum_turns=explainmeetsum_turns,
        )

        block = record.get("explainable_qmsum")
        if not isinstance(block, dict):
            audit.add(
                "missing_explainable_qmsum",
                meeting_id=meeting_id,
                split=split,
                explainmeetsum_file=path.name,
            )
            for case in meeting_cases:
                _set_partial(case, "missing_explainable_qmsum")
            continue

        explainable_queries = _validate_explainable_queries(
            record,
            file_name=path.name,
            turn_count=len(qmsum_turns),
        )
        for kind in QUERY_KINDS:
            kind_cases = [case for case in meeting_cases if case.query_kind == kind]
            _annotation_indexes(
                kind_cases,
                explainable_queries[kind],
                meeting_id=meeting_id,
                kind=kind,
                path=path,
                split=split,
                audit=audit,
                sentence_by_coordinate=sentence_by_coordinate,
            )

    for meeting_id, meeting_cases in cases_by_meeting.items():
        if meeting_id in seen_meetings:
            continue
        audit.add("missing_explainmeetsum_meeting", meeting_id=meeting_id, split=split)
        for case in meeting_cases:
            _mark_unmatched_case(
                case,
                reason="missing_explainmeetsum_meeting",
                audit=audit,
                split=split,
            )
