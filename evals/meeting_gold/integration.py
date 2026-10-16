"""Offline projection of ranked chunks onto frozen meeting sentence IDs.

This module is deliberately a small boundary between a retrieval system and
the meeting-gold scorer.  It consumes only the published corpus sentence view;
cases, answers, evidence groups, and evidence labels are neither needed nor
returned.

Chunk records must identify their source in one of two exact ways:

* ``source_sentence_ids`` contains frozen sentence IDs; or
* ``char_start``/``char_end`` identify a half-open range in an ExplainMeetSum
  turn (``turn_index``) or in an explicitly supplied ``source_text``.

Character ranges are validated against the source text and the frozen
sentence text.  There is no normalization, fuzzy matching, query matching, or
Gold-label lookup here.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from numbers import Real
from typing import Any

from .core import MeetingCorpus, Sentence, Turn, stable_sentence_id

INTEGRATION_VERSION = "meeting-gold-integration-v1"


class IntegrationError(ValueError):
    """Raised when a chunk cannot be bound to the frozen corpus safely."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# A retrieval result is not allowed to smuggle the answer key through this
# boundary.  The check is applied to input projections as well as corpus rows.
_FORBIDDEN_KEY_PARTS = (
    "answer",
    "eligib",
    "evidence",
    "gold",
    "reference",
)
_FORBIDDEN_KEYS = frozenset(
    {
        "cases",
        "case",
        "labels",
        "label",
        "lifecycle_state",
        "relevant_spans",
    }
)


def _reject_gold_fields(value: Any) -> None:
    """Reject answer-key-shaped mappings without inspecting source text."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                continue
            normalized = key.casefold().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS or any(
                part in normalized for part in _FORBIDDEN_KEY_PARTS
            ):
                raise IntegrationError("gold_label_field")
            _reject_gold_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_gold_fields(nested)


def _require_string(value: Any, code: str) -> str:
    if type(value) is not str or not value:
        raise IntegrationError(code)
    return value


def _require_integer(value: Any, code: str, *, positive: bool = False) -> int:
    if type(value) is not int or (value <= 0 if positive else value < 0):
        raise IntegrationError(code)
    return value


def _require_score(value: Any, code: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise IntegrationError(code)
    numeric = float(value)
    if not math.isfinite(numeric):
        raise IntegrationError(code)
    return value


@dataclass(frozen=True)
class _SentenceView:
    sentence: Sentence


@dataclass(frozen=True)
class _CorpusIndex:
    by_meeting: dict[str, tuple[_SentenceView, ...]]
    by_id: dict[str, _SentenceView]
    turns: dict[str, dict[int, str]]


def _coerce_sentence(value: Any, meeting_id: str, index: int) -> _SentenceView:
    if isinstance(value, Sentence):
        sentence = value
    elif isinstance(value, Mapping):
        _reject_gold_fields(value)
        expected = {
            "id",
            "meeting_id",
            "turn_index",
            "sentence_index",
            "speaker",
            "text",
            "dialogue_act_id",
        }
        if set(value) != expected:
            raise IntegrationError("sentence_shape")
        sentence = Sentence(
            id=value["id"],
            meeting_id=value["meeting_id"],
            turn_index=value["turn_index"],
            sentence_index=value["sentence_index"],
            speaker=value["speaker"],
            text=value["text"],
            dialogue_act_id=value["dialogue_act_id"],
        )
    else:
        raise IntegrationError("sentence_shape")

    if (
        type(sentence.id) is not str
        or not sentence.id
        or sentence.meeting_id != meeting_id
        or type(sentence.turn_index) is not int
        or sentence.turn_index < 0
        or type(sentence.sentence_index) is not int
        or sentence.sentence_index < 0
        or type(sentence.speaker) is not str
        or type(sentence.text) is not str
        or (
            sentence.dialogue_act_id is not None
            and type(sentence.dialogue_act_id) is not str
        )
        or sentence.id
        != stable_sentence_id(meeting_id, sentence.turn_index, sentence.sentence_index)
    ):
        raise IntegrationError(f"invalid_sentence:{index}")
    return _SentenceView(sentence)


def _coerce_turns(value: Any, meeting_id: str) -> dict[int, str]:
    if value is None:
        return {}
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise IntegrationError("turns_shape")
    result: dict[int, str] = {}
    for index, turn in enumerate(value):
        if isinstance(turn, Turn):
            turn_meeting_id = turn.meeting_id
            turn_index = turn.turn_index
            speaker = turn.speaker
            text = turn.text
        elif isinstance(turn, Mapping):
            _reject_gold_fields(turn)
            expected = {"meeting_id", "turn_index", "speaker", "text"}
            if set(turn) != expected:
                raise IntegrationError("turn_shape")
            turn_meeting_id = turn["meeting_id"]
            turn_index = turn["turn_index"]
            speaker = turn["speaker"]
            text = turn["text"]
        else:
            raise IntegrationError("turn_shape")
        if (
            turn_meeting_id != meeting_id
            or type(turn_index) is not int
            or turn_index != index
            or type(speaker) is not str
            or type(text) is not str
        ):
            raise IntegrationError("invalid_turn")
        result[index] = text
    return result


def _row_parts(value: Any, meeting_id: str) -> tuple[Any, Any]:
    """Return sentence rows and the best available raw turn snapshot."""

    if isinstance(value, MeetingCorpus):
        return value, value.explainmeetsum_turns or value.qmsum_turns
    if isinstance(value, Mapping) and "sentences" in value:
        _reject_gold_fields(value)
        if value.get("meeting_id") != meeting_id:
            raise IntegrationError("corpus_meeting_mismatch")
        turns = value.get("explainmeetsum_turns")
        if turns is None or turns == []:
            turns = value.get("qmsum_turns")
        return value["sentences"], turns
    return value, None


def _corpus_rows(corpus: Any) -> list[tuple[str, Any]]:
    # A full artifact bundle is accepted only as a convenience.  We project
    # its corpus member and never traverse or return its case collection.
    if isinstance(corpus, Mapping) and isinstance(corpus.get("corpus"), Mapping):
        corpus = corpus["corpus"]

    if isinstance(corpus, Mapping):
        return [(meeting_id, value) for meeting_id, value in corpus.items()]

    if isinstance(corpus, Sequence) and not isinstance(corpus, (str, bytes)):
        if all(isinstance(row, Sentence) for row in corpus):
            grouped: dict[str, list[Sentence]] = {}
            for sentence in corpus:
                grouped.setdefault(sentence.meeting_id, []).append(sentence)
            return list(grouped.items())
        rows: list[tuple[str, Any]] = []
        for row in corpus:
            if not isinstance(row, Mapping) or type(row.get("meeting_id")) is not str:
                raise IntegrationError("corpus_shape")
            rows.append((row["meeting_id"], row))
        return rows

    raise IntegrationError("corpus_shape")


def _build_index(corpus: Any) -> _CorpusIndex:
    by_meeting: dict[str, tuple[_SentenceView, ...]] = {}
    by_id: dict[str, _SentenceView] = {}
    turns: dict[str, dict[int, str]] = {}

    for meeting_id, raw_value in _corpus_rows(corpus):
        _require_string(meeting_id, "corpus_meeting_id")
        if meeting_id in by_meeting:
            raise IntegrationError("duplicate_meeting_id")
        sentence_rows, raw_turns = _row_parts(raw_value, meeting_id)
        if not isinstance(sentence_rows, Sequence) or isinstance(
            sentence_rows, (str, bytes)
        ):
            raise IntegrationError("sentences_shape")
        views = [
            _coerce_sentence(sentence, meeting_id, index)
            for index, sentence in enumerate(sentence_rows)
        ]
        ordered = tuple(
            sorted(
                views,
                key=lambda item: (
                    item.sentence.turn_index,
                    item.sentence.sentence_index,
                ),
            )
        )
        if list(views) != list(ordered):
            raise IntegrationError("sentences_not_sorted")
        coordinates = {
            (item.sentence.turn_index, item.sentence.sentence_index) for item in ordered
        }
        if len(coordinates) != len(ordered):
            raise IntegrationError("duplicate_sentence_coordinate")
        for view in ordered:
            if view.sentence.id in by_id:
                raise IntegrationError("duplicate_sentence_id")
            by_id[view.sentence.id] = view
        by_meeting[meeting_id] = ordered
        turns[meeting_id] = _coerce_turns(raw_turns, meeting_id)

    return _CorpusIndex(by_meeting=by_meeting, by_id=by_id, turns=turns)


def _coalesce(values: list[tuple[str, Any]], code: str) -> Any:
    present = [(name, value) for name, value in values if value is not None]
    if not present:
        return None
    first = present[0][1]
    if any(value != first for _, value in present[1:]):
        raise IntegrationError(code)
    return first


def _offset_fields(record: Mapping[str, Any]) -> tuple[Any, Any, Any, Any]:
    """Read one explicit offset representation and reject disagreements."""

    nested: list[Mapping[str, Any]] = []
    for name in ("character_offsets", "char_offsets", "offsets"):
        value = record.get(name)
        if value is None:
            continue
        if isinstance(value, Mapping):
            nested.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if len(value) != 2:
                raise IntegrationError("invalid_character_offsets")
            nested.append({"start": value[0], "end": value[1]})
        else:
            raise IntegrationError("invalid_character_offsets")

    start_values = [("char_start", record.get("char_start"))]
    start_values += [("start_char", record.get("start_char"))]
    start_values += [("source_char_start", record.get("source_char_start"))]
    start_values += [("start", record.get("start"))]
    end_values = [("char_end", record.get("char_end"))]
    end_values += [("end_char", record.get("end_char"))]
    end_values += [("source_char_end", record.get("source_char_end"))]
    end_values += [("end", record.get("end"))]
    turn_values = [("turn_index", record.get("turn_index"))]
    turn_values += [("source_turn_index", record.get("source_turn_index"))]

    for value in nested:
        start_values.extend(
            (f"nested_{name}", value.get(name))
            for name in ("start", "char_start", "source_char_start")
        )
        end_values.extend(
            (f"nested_{name}", value.get(name))
            for name in ("end", "char_end", "source_char_end")
        )
        turn_values.extend(
            (f"nested_{name}", value.get(name))
            for name in ("turn_index", "source_turn_index")
        )

    start = _coalesce(start_values, "ambiguous_character_offsets")
    end = _coalesce(end_values, "ambiguous_character_offsets")
    turn = _coalesce(turn_values, "ambiguous_turn_index")
    return start, end, turn, nested


def _source_text(record: Mapping[str, Any]) -> str | None:
    values = [
        ("source_text", record.get("source_text")),
        ("transcript_text", record.get("transcript_text")),
        ("source_content", record.get("source_content")),
    ]
    value = _coalesce(values, "ambiguous_source_text")
    if value is None:
        return None
    if type(value) is not str:
        raise IntegrationError("source_text_string")
    return value


def _occurrences(text: str, needle: str, start: int) -> list[int]:
    result: list[int] = []
    cursor = start
    while True:
        position = text.find(needle, cursor)
        if position < 0:
            return result
        result.append(position)
        cursor = position + 1


def _sentence_spans(
    source_text: str, sentences: Sequence[_SentenceView]
) -> list[tuple[_SentenceView, int, int]]:
    """Find one and only one exact ordered placement for each sentence."""

    for view in sentences:
        if not view.sentence.text:
            raise IntegrationError("empty_sentence_text")

    @cache
    def placements(
        sentence_position: int, cursor: int
    ) -> tuple[tuple[tuple[int, int], ...], ...]:
        if sentence_position == len(sentences):
            return ((),)
        sentence_text = sentences[sentence_position].sentence.text
        found: list[tuple[tuple[int, int], ...]] = []
        for start in _occurrences(source_text, sentence_text, cursor):
            end = start + len(sentence_text)
            for suffix in placements(sentence_position + 1, end):
                candidate = ((start, end), *suffix)
                if candidate not in found:
                    found.append(candidate)
                if len(found) == 2:
                    return tuple(found)
        return tuple(found)

    candidates = placements(0, 0)
    if not candidates:
        raise IntegrationError("offset_source_text_mismatch")
    if len(candidates) > 1:
        raise IntegrationError("ambiguous_character_offsets")
    return [
        (view, start, end)
        for view, (start, end) in zip(sentences, candidates[0], strict=True)
    ]


def _meeting_sentences(
    index: _CorpusIndex, meeting_id: str, turn_index: int
) -> tuple[_SentenceView, ...]:
    return tuple(
        view
        for view in index.by_meeting.get(meeting_id, ())
        if view.sentence.turn_index == turn_index
    )


def _resolve_offsets(
    record: Mapping[str, Any],
    *,
    meeting_id: str,
    index: _CorpusIndex,
) -> list[str]:
    start, end, turn_index, _ = _offset_fields(record)
    if start is None and end is None:
        raise IntegrationError("source_identity_required")
    if start is None or end is None:
        raise IntegrationError("invalid_character_offsets")
    start = _require_integer(start, "invalid_character_offsets")
    end = _require_integer(end, "invalid_character_offsets", positive=True)
    if end <= start:
        raise IntegrationError("invalid_character_offsets")
    if turn_index is not None:
        turn_index = _require_integer(turn_index, "invalid_turn_index")

    explicit_source = _source_text(record)
    source_text = explicit_source
    turns = index.turns.get(meeting_id, {})

    if turn_index is not None:
        if turn_index not in _turns_for_meeting(index, meeting_id):
            raise IntegrationError("unknown_turn")
        canonical_turn = turns.get(turn_index)
        if canonical_turn is not None:
            if source_text is not None and source_text != canonical_turn:
                raise IntegrationError("offset_source_text_mismatch")
            source_text = canonical_turn
        elif source_text is None:
            source_text = _record_text_for_local_offsets(record, start, end)
            if source_text is None:
                raise IntegrationError("offset_source_unavailable")
    else:
        if source_text is None:
            raise IntegrationError("turn_index_required_for_offsets")
        candidates = [
            candidate for candidate in turns.values() if candidate == source_text
        ]
        if len(candidates) > 1:
            raise IntegrationError("ambiguous_offset_source")
        if len(candidates) == 1:
            matching_turns = [
                number for number, value in turns.items() if value == source_text
            ]
            if len(matching_turns) != 1:
                raise IntegrationError("ambiguous_offset_source")
            turn_index = matching_turns[0]
        else:
            # A source-less fixture may still prove a one-sentence chunk by
            # making the chunk text itself the complete source interval.
            matching = [
                view
                for view in index.by_meeting.get(meeting_id, ())
                if view.sentence.text == source_text
            ]
            if len(matching) == 1 and start == 0 and end == len(source_text):
                return [matching[0].sentence.id]
            if not matching:
                raise IntegrationError("offset_source_text_mismatch")
            raise IntegrationError("ambiguous_offset_source")

    assert turn_index is not None
    sentence_views = _meeting_sentences(index, meeting_id, turn_index)
    if not sentence_views:
        raise IntegrationError("turn_has_no_sentences")
    if source_text is None:
        raise IntegrationError("offset_source_unavailable")
    if end > len(source_text):
        raise IntegrationError("invalid_character_offsets")

    chunk_text = record.get("text")
    if chunk_text is not None and (
        type(chunk_text) is not str or source_text[start:end] != chunk_text
    ):
        raise IntegrationError("chunk_text_offset_mismatch")

    spans = _sentence_spans(source_text, sentence_views)
    # Retrieval chunks are only credited for source sentences they contain
    # completely. An overlap may be a clipped fragment at either boundary and
    # must not inflate a sentence-level retrieval hit.
    return [
        view.sentence.id
        for view, sentence_start, sentence_end in spans
        if start <= sentence_start and sentence_end <= end
    ]


def _turns_for_meeting(index: _CorpusIndex, meeting_id: str) -> set[int]:
    turns = set(index.turns.get(meeting_id, {}))
    turns.update(
        view.sentence.turn_index for view in index.by_meeting.get(meeting_id, ())
    )
    return turns


def _record_text_for_local_offsets(
    record: Mapping[str, Any], start: int, end: int
) -> str | None:
    value = record.get("text")
    if type(value) is not str or start != 0 or end != len(value):
        return None
    return value


def _explicit_ids(
    record: Mapping[str, Any], *, meeting_id: str, index: _CorpusIndex
) -> list[str] | None:
    if "source_sentence_ids" not in record:
        return None
    values = record["source_sentence_ids"]
    if values is None:
        return None
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise IntegrationError("source_sentence_ids_shape")
    if not values:
        return None
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if type(value) is not str or not value:
            raise IntegrationError("source_sentence_id_string")
        view = index.by_id.get(value)
        if view is None:
            raise IntegrationError("unknown_sentence_id")
        if view.sentence.meeting_id != meeting_id:
            raise IntegrationError("wrong_meeting")
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _chunk_identifier(record: Mapping[str, Any], index: int) -> str | int:
    value = record.get("chunk_id", record.get("id", record.get("segment_id", index)))
    if type(value) not in (str, int) or (isinstance(value, str) and not value):
        raise IntegrationError("chunk_id_invalid")
    return value


def _safe_chunk_projection(
    record: Mapping[str, Any], *, input_index: int, sentence_ids: list[str], method: str
) -> dict[str, Any]:
    rank = record.get("rank", input_index + 1)
    rank = _require_integer(rank, "rank_invalid", positive=True)
    score = record.get("score")
    if score is not None:
        score = _require_score(score, "score_invalid")
    return {
        "input_index": input_index,
        "chunk_id": _chunk_identifier(record, input_index),
        "rank": rank,
        "score": score,
        "mapping": method,
        "source_sentence_ids": list(sentence_ids),
    }


def _validate_chunk_metadata(record: Mapping[str, Any], input_index: int) -> None:
    """Validate metadata even when a caller requests partial coverage."""

    _chunk_identifier(record, input_index)
    rank = record.get("rank", input_index + 1)
    _require_integer(rank, "rank_invalid", positive=True)
    score = record.get("score")
    if score is not None:
        _require_score(score, "score_invalid")


def _record_meeting_id(record: Mapping[str, Any], expected: str | None) -> str:
    value = record.get("meeting_id", expected)
    meeting_id = _require_string(value, "meeting_id_required")
    if expected is not None and meeting_id != expected:
        raise IntegrationError("wrong_meeting")
    return meeting_id


_PARTIAL_CODES = frozenset(
    {
        "source_identity_required",
        "turn_index_required_for_offsets",
        "offset_source_unavailable",
        "offset_matches_no_sentence",
    }
)


def map_chunk_records(
    chunk_records: Iterable[Mapping[str, Any]],
    corpus: Any,
    *,
    meeting_id: str | None = None,
    allow_unmapped: bool = False,
) -> dict[str, Any]:
    """Map ranked chunk records to frozen sentence IDs.

    The input sequence order is the retrieval ranking.  It is preserved while
    sentence IDs are deduplicated by first occurrence.  ``allow_unmapped`` is
    an explicit partial-coverage mode for records with no source identity; it
    never suppresses malformed IDs, ambiguous offsets, or cross-meeting data.
    """

    index = _build_index(corpus)
    if not isinstance(chunk_records, Iterable) or isinstance(
        chunk_records, (str, bytes, Mapping)
    ):
        raise IntegrationError("chunk_records_shape")

    records = list(chunk_records)
    if meeting_id is not None:
        meeting_id = _require_string(meeting_id, "meeting_id_required")
        if meeting_id not in index.by_meeting:
            raise IntegrationError("unknown_meeting")

    mapped: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    retrieved_ids: list[str] = []
    retrieved_seen: set[str] = set()

    for input_index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise IntegrationError("chunk_record_shape")
        _reject_gold_fields(record)
        _validate_chunk_metadata(record, input_index)
        record_meeting = _record_meeting_id(record, meeting_id)
        if record_meeting not in index.by_meeting:
            raise IntegrationError("unknown_meeting")
        if meeting_id is None:
            meeting_id = record_meeting
        explicit = _explicit_ids(record, meeting_id=record_meeting, index=index)
        has_offsets = any(
            record.get(name) is not None
            for name in (
                "char_start",
                "char_end",
                "start_char",
                "end_char",
                "source_char_start",
                "source_char_end",
                "start",
                "end",
                "character_offsets",
                "char_offsets",
                "offsets",
            )
        )
        try:
            offset_ids = (
                _resolve_offsets(record, meeting_id=record_meeting, index=index)
                if has_offsets
                else None
            )
            if explicit is None and offset_ids is None:
                raise IntegrationError("source_identity_required")
            if explicit is not None and offset_ids is not None:
                if set(explicit) != set(offset_ids):
                    raise IntegrationError("mapping_authority_mismatch")
                sentence_ids = explicit
                method = "source_sentence_ids+character_offsets"
            else:
                sentence_ids = explicit if explicit is not None else offset_ids or []
                method = (
                    "source_sentence_ids"
                    if explicit is not None
                    else "character_offsets"
                )
            if not sentence_ids:
                raise IntegrationError("offset_matches_no_sentence")
        except IntegrationError as exc:
            if not allow_unmapped or exc.code not in _PARTIAL_CODES:
                raise
            unmapped.append(
                {
                    "input_index": input_index,
                    "chunk_id": _chunk_identifier(record, input_index),
                    "rank": record.get("rank", input_index + 1),
                    "reason": exc.code,
                }
            )
            continue

        projection = _safe_chunk_projection(
            record,
            input_index=input_index,
            sentence_ids=sentence_ids,
            method=method,
        )
        mapped.append(projection)
        for sentence_id in sentence_ids:
            if sentence_id not in retrieved_seen:
                retrieved_seen.add(sentence_id)
                retrieved_ids.append(sentence_id)

    if meeting_id is None:
        raise IntegrationError("meeting_id_required")

    return {
        "schema_version": INTEGRATION_VERSION,
        "meeting_id": meeting_id,
        "chunks": mapped,
        "retrieved_sentence_ids": retrieved_ids,
        "unmapped_chunks": unmapped,
        "status": "partial" if unmapped else "complete",
        "coverage": {
            "input_chunks": len(records),
            "mapped_chunks": len(mapped),
            "unmapped_chunks": len(unmapped),
            "mapped_sentence_ids": len(retrieved_ids),
        },
    }


def map_chunks_to_gold_sentence_ids(
    corpus: Any,
    chunk_records: Iterable[Mapping[str, Any]],
    *,
    meeting_id: str | None = None,
    allow_unmapped: bool = False,
) -> dict[str, Any]:
    """Corpus-first spelling for callers working from a frozen bundle."""

    return map_chunk_records(
        chunk_records,
        corpus,
        meeting_id=meeting_id,
        allow_unmapped=allow_unmapped,
    )


def to_prediction_record(result: Mapping[str, Any], *, case_id: str) -> dict[str, Any]:
    """Create the scorer's retrieval-only projection without Gold fields."""

    _reject_gold_fields(result)
    case_id = _require_string(case_id, "case_id_required")
    sentence_ids = result.get("retrieved_sentence_ids")
    if not isinstance(sentence_ids, list):
        raise IntegrationError("mapped_result_shape")
    return {
        "case_id": case_id,
        "retrieved_sentence_ids": list(sentence_ids),
        "cited_sentence_ids": [],
    }


# Keep the alternate names discoverable for evaluator callers while keeping a
# single implementation of the strict mapping rules.
map_retrieval_chunks = map_chunk_records
chunk_records_to_sentence_ids = map_chunk_records


__all__ = [
    "INTEGRATION_VERSION",
    "IntegrationError",
    "chunk_records_to_sentence_ids",
    "map_chunk_records",
    "map_chunks_to_gold_sentence_ids",
    "map_retrieval_chunks",
    "to_prediction_record",
]
