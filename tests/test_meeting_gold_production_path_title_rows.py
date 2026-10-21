"""The study library ignores title rows when it maps and validates segments.

A newly ingested item carries one extra ``boundary_kind == "title"`` row (the
item title plus description). It holds no transcript, so it must neither enter
the segment/cue mapping nor break the reuse check of an existing library.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from evals.meeting_gold import production_path
from evals.meeting_gold.core import MeetingCorpus, Sentence, stable_sentence_id


def _sql(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class _Result:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return list(self._values)


class _FakeDb:
    def __init__(self, *, rows: list[Any], item: Any = None, title_count: int = 0) -> None:
        self.rows = rows
        self.item = item
        self.title_count = title_count
        self.executed: list[str] = []
        self.scalar_statements: list[str] = []

    def __enter__(self) -> "_FakeDb":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get(self, model: Any, key: Any) -> Any:
        return self.item

    def execute(self, statement: Any) -> _Result:
        self.executed.append(_sql(statement))
        return _Result(self.rows)

    def scalar(self, statement: Any) -> int:
        self.scalar_statements.append(_sql(statement))
        return self.title_count


def test_reuse_check_selects_transcript_segments_only() -> None:
    texts = ["first chunk", "second chunk"]
    info = {
        "item_id": 7,
        "segments": [{"id": 1}, {"id": 2}],
        "segment_text_hash": hashlib.sha256("\x1f".join(texts).encode("utf-8")).hexdigest(),
    }
    db = _FakeDb(rows=texts, item=SimpleNamespace(state="ready"))

    assert production_path._meeting_still_valid(info, lambda: db) is True

    (statement,) = db.executed
    assert "segment.boundary_kind != 'title'" in statement
    assert "segment.item_id = 7" in statement


def test_reuse_check_still_detects_a_changed_library() -> None:
    info = {"item_id": 7, "segments": [{"id": 1}], "segment_text_hash": "0" * 64}
    db = _FakeDb(rows=["other text"], item=SimpleNamespace(state="ready"))

    assert production_path._meeting_still_valid(info, lambda: db) is False


def test_ingest_meeting_maps_segments_without_the_title_row_and_counts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meeting_id = "m1"
    sentence = Sentence(
        id=stable_sentence_id(meeting_id, 0, 0),
        meeting_id=meeting_id,
        turn_index=0,
        sentence_index=0,
        speaker="A",
        text="hello there everyone",
    )
    meeting = MeetingCorpus([sentence])
    body, _ = production_path.build_caption_track(meeting)
    from app.connectors.youtube import parse_json3

    (cue,) = parse_json3(body)
    body_row = SimpleNamespace(
        id=11,
        seq=0,
        start_sec=cue.start,
        end_sec=cue.end,
        text=cue.text.strip(),
        boundary_kind="semantic",
    )
    db = _FakeDb(rows=[body_row], title_count=1)

    monkeypatch.setattr("app.ingest.tasks.create_item", lambda *a, **k: 5)
    monkeypatch.setattr("app.ingest.tasks.process_item", lambda *a, **k: "ready")
    monkeypatch.setattr(
        "app.ingest.tasks.build_worker_embedder",
        lambda settings: SimpleNamespace(dimensions=1, embed=lambda texts: [[0.0]] * len(texts)),
    )

    info = production_path.ingest_meeting(meeting_id, meeting, 3, lambda: db, object())

    # The body statement excludes the title row; the count statement finds it.
    (body_statement,) = db.executed
    assert "segment.boundary_kind != 'title'" in body_statement
    (count_statement,) = db.scalar_statements
    assert "segment.boundary_kind = 'title'" in count_statement
    assert info["title_rows"] == 1
    assert [segment["id"] for segment in info["segments"]] == [11]
    assert info["boundary_kinds"] == {"semantic": 1}
