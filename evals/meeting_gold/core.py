"""Deterministic contracts for the QMSum/ExplainMeetSum golden set."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "meeting-gold-v2"
CONVERTER_VERSION = "qmsum-explainmeetsum-v2"
COMMITS = {
    "qmsum": "83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e",
    "explainmeetsum": "d8459311a6578cd438a785bf19dd2fa757ade1c5",
}
ALLOWED_OUTPUT = (
    "corpus.jsonl",
    "train.jsonl",
    "val.jsonl",
    "test.jsonl",
    "audit.json",
    "NOTICE.md",
    "QMSUM_LICENSE",
    "EXPLAINMEETSUM_LICENSE",
)
SPLITS = ("train", "val", "test")
QUERY_KINDS = ("general", "specific")
EVIDENCE_TYPES = frozenset({"CES", "PES"})
ELIGIBILITY_STATES = frozenset(
    {"answer_only", "citation_scorable", "partial_annotation", "quarantined"}
)


class SourceFormatError(ValueError):
    """Raised when a source cannot be interpreted without guessing."""


def canonical_bytes(value: Any) -> bytes:
    """Serialize one JSON value using the dataset's byte-level contract."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(canonical_bytes(row) for row in rows))


def stable_case_id(meeting_id: str, query_kind: str, query_index: int) -> str:
    return f"qmsum:{meeting_id}:{query_kind}:{query_index}"


def stable_sentence_id(meeting_id: str, turn_index: int, sentence_index: int) -> str:
    return f"qmsum:{meeting_id}:t{turn_index}:s{sentence_index}"


@dataclass(frozen=True)
class Sentence:
    """A published ExplainMeetSum sentence coordinate and its source text."""

    id: str
    meeting_id: str
    turn_index: int
    sentence_index: int
    speaker: str
    text: str
    dialogue_act_id: str | None = None


@dataclass(frozen=True)
class Turn:
    """A raw QMSum/ExplainMeetSum turn used for transcript reconciliation."""

    meeting_id: str
    turn_index: int
    speaker: str
    text: str


class MeetingCorpus(list[Sentence]):
    """Validated EMS sentence view with both raw transcript snapshots.

    QMSum-only turns stay in ``qmsum_turns`` until ExplainMeetSum sentence
    coordinates have been validated; they are never represented as fallback
    sentence records.
    """

    def __init__(
        self,
        sentences: Iterable[Sentence] = (),
        *,
        qmsum_turns: Iterable[Turn] = (),
        explainmeetsum_turns: Iterable[Turn] = (),
    ) -> None:
        super().__init__(sentences)
        self.qmsum_turns = tuple(qmsum_turns)
        self.explainmeetsum_turns = tuple(explainmeetsum_turns)


@dataclass
class Case:
    case_id: str
    meeting_id: str
    split: str
    domain: str
    query_kind: str
    query_index: int
    query: str
    answer: str
    relevant_spans: list[Any] = field(default_factory=list)
    answer_sentence_index: list[list[int]] = field(default_factory=list)
    evidence_groups: list[list[str]] = field(default_factory=list)
    evidence_types: list[list[str]] = field(default_factory=list)
    answer_sentence_texts: list[str] = field(default_factory=list)
    evidence_records: list[list[dict[str, Any]]] = field(default_factory=list)
    lifecycle_state: str = "answer_only"
    eligibility: dict[str, str] = field(
        default_factory=lambda: {"ces": "answer_only", "ces_pes": "answer_only"}
    )
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def answer_sentences(self) -> list[str]:
        """Compatibility alias for consumers that call groups sentences."""

        return self.answer_sentence_texts

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


class Audit:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def add(self, code: str, **fields: Any) -> None:
        self.records.append({"code": code, **fields})

    def summary(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(Counter(x["code"] for x in self.records).items())),
            "records": sorted(
                self.records,
                key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False),
            ),
        }


def source_inventory(root: Path, relative: str = ".") -> dict[str, str]:
    """Hash regular, non-symlink files below a source root deterministically."""

    root_path = Path(root)
    if root_path.is_symlink() or not root_path.is_dir():
        raise SourceFormatError(f"invalid_source_root:{root_path.name}")
    root_resolved = root_path.resolve()
    base_path = root_resolved / relative
    if base_path.is_symlink():
        raise SourceFormatError("symlink_in_source_inventory_path")
    base = base_path.resolve()
    try:
        base.relative_to(root_resolved)
    except ValueError as exc:
        raise SourceFormatError("source_inventory_outside_root") from exc
    if base.is_symlink() or not base.is_dir():
        raise SourceFormatError("invalid_source_inventory_path")

    inventory: dict[str, str] = {}
    for path in sorted(base.rglob("*")):
        if path.is_symlink():
            raise SourceFormatError(f"symlink_in_source:{path.name}")
        if not path.is_file() and not path.is_dir():
            raise SourceFormatError(f"non_regular_source_entry:{path.name}")
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise SourceFormatError("source_file_outside_root") from exc
        relative_path = resolved.relative_to(root_resolved).as_posix()
        inventory[relative_path] = sha256(resolved)
    return inventory
