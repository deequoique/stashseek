"""``app.cli reindex``: Chinese fts backfill and title rows.

No live database: ``FakeDB`` is a tiny in-memory stand-in that answers the four
statement shapes ``app.ingest.reindex`` issues (recognised from their compiled
PostgreSQL text) and applies writes only on ``commit``. The statements
themselves are checked separately by compiling them.
"""

from __future__ import annotations

import sys
from collections import namedtuple
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app import cli
from app.ingest import reindex as reindex_module
from app.ingest.reindex import ReindexResult, reindex
from app.ingest.title_row import (
    TITLE_ROW_DESCRIPTION_CHARS,
    build_title_row,
    title_row_fts,
    title_row_text,
)
from app.models import Segment

SECRET_TITLE = "SECRET-TITLE-TEXT"

Row = namedtuple("Row", "id title description platform_id lang")
SegmentRow = namedtuple("SegmentRow", "id text")


class Store:
    """Committed rows: content items and segments."""

    def __init__(self):
        self.items: list[dict] = []
        self.segments: list[dict] = []
        self._next_segment_id = 1000

    def item(self, item_id, *, user_id=7, title="A title", description=None, lang="en"):
        self.items.append(
            {
                "id": item_id,
                "user_id": user_id,
                "title": title,
                "description": description,
                "platform_id": f"pid{item_id}",
                "lang": lang,
            }
        )

    def segment(self, item_id, seq, text, *, fts=None, kind="semantic"):
        self._next_segment_id += 1
        row = {
            "id": self._next_segment_id,
            "item_id": item_id,
            "seq": seq,
            "text": text,
            "embedding": None,
            "fts": fts,
            "boundary_kind": kind,
        }
        self.segments.append(row)
        return row

    def title_row(self, item_id):
        rows = [r for r in self.segments if r["item_id"] == item_id and r["seq"] == -1]
        assert len(rows) <= 1
        return rows[0] if rows else None

    def snapshot(self):
        return [dict(row) for row in self.segments]


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    def __init__(self, store):
        self.store = store
        self.pending: list[tuple] = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        sql, params = " ".join(str(compiled).split()), compiled.params
        if sql.startswith("SELECT content_item.id"):
            return FakeResult(
                [
                    Row(
                        i["id"], i["title"], i["description"], i["platform_id"], i["lang"]
                    )
                    for i in self.store.items
                    if params.get("user_id_1") in (None, i["user_id"])
                ]
            )
        if sql.startswith("SELECT segment.id, segment.text"):
            rows = [r for r in self.store.segments if r["item_id"] == params["item_id_1"]]
            if "segment.seq =" in sql:
                rows = [r for r in rows if r["seq"] == params["seq_1"]]
            else:
                assert "segment.fts IS NULL" in sql
                rows = sorted(
                    (
                        r
                        for r in rows
                        if r["fts"] is None
                        and r["boundary_kind"] != params["boundary_kind_1"]
                        and r["id"] > params["id_1"]
                    ),
                    key=lambda r: r["id"],
                )[: params["param_1"]]
            return FakeResult([SegmentRow(r["id"], r["text"]) for r in rows])
        if sql.startswith("UPDATE segment SET"):
            self.pending.append(("update", params))
            return FakeResult([])
        raise AssertionError(f"unexpected statement: {sql}")

    def add(self, value):
        assert isinstance(value, Segment)
        self.pending.append(("insert", value))

    def commit(self):
        self.commits += 1
        for kind, payload in self.pending:
            if kind == "insert":
                self.store.segment(
                    payload.item_id,
                    payload.seq,
                    payload.text,
                    fts=payload.fts,
                    kind=payload.boundary_kind,
                ).update(embedding=payload.embedding)
            else:
                row = next(r for r in self.store.segments if r["id"] == payload["id_1"])
                row["fts"] = payload["to_tsvector_2"]
                if "text" in payload:
                    row["text"] = payload["text"]
                    row["embedding"] = payload["embedding"]
        self.pending = []

    def rollback(self):
        self.rollbacks += 1
        self.pending = []


class Embedder:
    def __init__(self, *, fail_on=()):
        self.calls: list[list[str]] = []
        self.fail_on = set(fail_on)

    def embed(self, values):
        self.calls.append(list(values))
        if any(value in self.fail_on for value in values):
            raise RuntimeError(f"provider said: {SECRET_TITLE}")
        return [[0.5, 0.25] for _ in values]


def run(store, **kwargs):
    kwargs.setdefault("embedder", Embedder())
    return reindex(session_factory=lambda: FakeDB(store), **kwargs)


# ---------------------------------------------------------------------------
# Title rows
# ---------------------------------------------------------------------------


def test_only_missing_or_stale_title_rows_are_embedded_and_written():
    store = Store()
    store.item(1, title="Missing row")
    store.item(2, title="Fresh title")
    store.item(3, title="Current row")
    store.segment(2, -1, "Old title", kind="title")
    store.segment(3, -1, "Current row", kind="title")
    embedder = Embedder()

    result = run(store, embedder=embedder, zh_fts=False)

    assert result == ReindexResult(0, 1, 1, 1, (), False)
    # Only the missing and the stale item reach the embedding provider.
    assert embedder.calls == [["Missing row"], ["Fresh title"]]
    inserted = store.title_row(1)
    assert inserted["boundary_kind"] == "title"
    assert inserted["text"] == "Missing row"
    assert inserted["embedding"] == [0.5, 0.25]
    assert store.title_row(2)["text"] == "Fresh title"
    assert store.title_row(2)["embedding"] == [0.5, 0.25]
    assert store.title_row(3)["embedding"] is None  # untouched

    again = run(store, embedder=Embedder(), zh_fts=False)
    assert again == ReindexResult(0, 0, 0, 3, (), False)


def test_title_row_text_includes_a_bounded_description_and_defaults_to_platform_id():
    long_description = "d" * (TITLE_ROW_DESCRIPTION_CHARS + 50)
    item = SimpleNamespace(title=" T ", description=long_description, platform_id="pid")
    assert title_row_text(item) == "T\n" + "d" * TITLE_ROW_DESCRIPTION_CHARS
    assert title_row_text(SimpleNamespace(title=None, description=None, platform_id="pid")) == "pid"
    assert title_row_text(SimpleNamespace(title="  ", description="  ", platform_id="pid")) == "pid"
    assert title_row_text(SimpleNamespace(title="T", description=None, platform_id="pid")) == "T"


def test_build_title_row_shape_and_fts_language():
    english = build_title_row(
        SimpleNamespace(id=9, lang="en"), text="hello", embedding=[1.0]
    )
    chinese = build_title_row(
        SimpleNamespace(id=9, lang="zh-CN"), text="电脑价格", embedding=[1.0]
    )
    for row in (english, chinese):
        assert (row.item_id, row.seq, row.boundary_kind) == (9, -1, "title")
        assert (row.start_sec, row.end_sec) == (0, 0)

    def config(row):
        return row.fts.compile(dialect=postgresql.dialect()).params

    assert config(english)["to_tsvector_1"] == "english"
    assert config(chinese)["to_tsvector_1"] == "simple"
    assert config(chinese)["to_tsvector_2"] == "电脑 脑价 价格"
    assert title_row_fts("x", None).compile(dialect=postgresql.dialect()).params[
        "to_tsvector_1"
    ] == "english"


def test_dry_run_counts_without_writing_or_calling_the_provider(monkeypatch):
    monkeypatch.setattr(
        "app.ingest.tasks.build_worker_embedder",
        lambda *a, **k: pytest.fail("a dry run must not build the embedder"),
    )
    store = Store()
    store.item(1, title="Missing")
    store.item(2, title="New", lang="zh")
    store.item(3, title="Same")
    store.segment(2, -1, "Old", kind="title")
    store.segment(3, -1, "Same", kind="title")
    store.segment(2, 0, "电脑价格", fts=None)
    before = store.snapshot()
    pinned = []

    class RecordingDB(FakeDB):
        def commit(self):
            pinned.append("commit")
            super().commit()

    result = reindex(
        dry_run=True,
        session_factory=lambda: RecordingDB(store),
    )

    assert result == ReindexResult(1, 1, 1, 1, (), True)
    assert store.snapshot() == before
    assert pinned == []


def test_user_id_filter_limits_both_jobs_to_one_tenant():
    store = Store()
    store.item(1, user_id=7, title="Mine", lang="zh")
    store.item(2, user_id=8, title="Theirs", lang="zh")
    store.segment(1, 0, "电脑价格")
    store.segment(2, 0, "电脑价格")
    embedder = Embedder()

    result = run(store, user_id=7, embedder=embedder)

    assert (result.zh_segments_updated, result.title_rows_inserted) == (1, 1)
    assert embedder.calls == [["Mine"]]
    assert store.title_row(1) is not None and store.title_row(2) is None
    assert [r["fts"] for r in store.segments if r["item_id"] == 2] == [None]


def test_missing_embedder_is_built_lazily_once(monkeypatch):
    built = []

    def build(*_args, **_kwargs):
        built.append(1)
        return Embedder()

    monkeypatch.setattr("app.ingest.tasks.build_worker_embedder", build)
    store = Store()
    store.item(1, title="One")
    store.item(2, title="Two")

    result = reindex(session_factory=lambda: FakeDB(store))

    assert result.title_rows_inserted == 2
    assert built == [1]


# ---------------------------------------------------------------------------
# Chinese fts
# ---------------------------------------------------------------------------


def test_zh_fts_backfill_fills_only_missing_body_fts_and_never_embeds():
    store = Store()
    store.item(1, lang="zh", title="电脑")
    store.item(2, lang="en", title="English")
    store.segment(1, -1, "电脑", kind="title")  # title rows are not body segments
    needs = store.segment(1, 0, "电脑价格今年涨了")
    done = store.segment(1, 1, "已经有了", fts="'already':1")
    english = store.segment(2, 0, "plain english text")

    class NoEmbedder:
        def embed(self, _values):
            pytest.fail("fts backfill must not call the embedding provider")

    result = run(store, title_rows=False, embedder=NoEmbedder())

    assert result == ReindexResult(1, 0, 0, 0, (), False)
    assert needs["fts"] == "电脑 脑价 价格 格今 今年 年涨"
    assert done["fts"] == "'already':1"
    assert english["fts"] is None
    assert store.title_row(1)["fts"] is None  # the zh job skips title rows

    again = run(store, title_rows=False, embedder=NoEmbedder())
    assert again == ReindexResult(0, 0, 0, 0, (), False)


def test_zh_fts_backfill_pages_through_large_items():
    store = Store()
    store.item(1, lang="zh")
    for index in range(reindex_module._ZH_BATCH_SIZE * 2 + 5):
        store.segment(1, index, f"电脑{index}")

    result = run(store, title_rows=False)

    assert result.zh_segments_updated == reindex_module._ZH_BATCH_SIZE * 2 + 5
    assert all(r["fts"] is not None for r in store.segments)


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


def test_embedding_failure_isolates_the_item_and_continues():
    store = Store()
    store.item(1, title="First")
    store.item(2, title=SECRET_TITLE)
    store.item(3, title="Third")
    before = store.snapshot()
    embedder = Embedder(fail_on={SECRET_TITLE})

    result = run(store, embedder=embedder, zh_fts=False)

    assert result.failed_item_ids == (2,)
    assert (result.title_rows_inserted, result.title_rows_updated) == (2, 0)
    assert store.title_row(1) is not None and store.title_row(3) is not None
    assert store.title_row(2) is None
    assert len(store.segments) == len(before) + 2


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


def _sql(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    return " ".join(str(compiled).split()), compiled.params


def test_scope_statement_keeps_state_deletion_and_tenant_predicates():
    sql, params = _sql(reindex_module._scope_statement(7))
    assert "content_item.state = %(state_1)s" in sql and params["state_1"] == "ready"
    assert "content_item.deleted_at IS NULL" in sql
    assert "content_item.purge_claimed_at IS NULL" in sql
    assert "content_item.archived_at IS NULL" in sql
    assert "content_item.user_id = %(user_id_1)s" in sql and params["user_id_1"] == 7

    all_sql, _ = _sql(reindex_module._scope_statement(None))
    assert "content_item.user_id" not in all_sql.split("WHERE", 1)[1]


def test_zh_pending_statement_skips_title_rows_and_is_keyset_paged():
    sql, params = _sql(reindex_module._zh_pending_statement(5, 99))
    assert "segment.fts IS NULL" in sql
    assert "segment.boundary_kind != %(boundary_kind_1)s" in sql
    assert params["boundary_kind_1"] == "title"
    assert "segment.id > %(id_1)s" in sql and params["id_1"] == 99
    assert "LIMIT" in sql


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch, capsys, argv, *, result=None):
    calls = []

    def fake_reindex(**kwargs):
        calls.append(kwargs)
        return result or ReindexResult(0, 0, 0, 0, (), kwargs["dry_run"])

    monkeypatch.setattr(sys, "argv", ["stashseek", *argv])
    monkeypatch.setattr(cli, "reindex", fake_reindex)
    try:
        cli.main()
        code = 0
    except SystemExit as exc:
        code = exc.code
    return calls, code, capsys.readouterr()


@pytest.mark.parametrize(
    "flags, zh_fts, title_rows",
    [
        ([], True, True),
        (["--zh-fts"], True, False),
        (["--title-rows"], False, True),
        (["--zh-fts", "--title-rows"], True, True),
    ],
)
def test_cli_flag_selection(monkeypatch, capsys, flags, zh_fts, title_rows):
    calls, code, _out = _run_cli(monkeypatch, capsys, ["reindex", *flags])

    assert code == 0
    assert calls == [
        {"user_id": None, "dry_run": False, "zh_fts": zh_fts, "title_rows": title_rows}
    ]


def test_cli_passes_user_id_and_dry_run_and_prints_only_counts(monkeypatch, capsys):
    result = ReindexResult(149, 100, 3, 28, (), True)
    calls, code, out = _run_cli(
        monkeypatch, capsys, ["reindex", "--user-id", "211", "--dry-run"], result=result
    )

    assert code == 0
    assert calls[0]["user_id"] == 211 and calls[0]["dry_run"] is True
    assert out.out.splitlines() == [
        "zh_segments_updated=149 title_rows_inserted=100 title_rows_updated=3 "
        "title_rows_skipped=28 failed=0 dry_run=true"
    ]


def test_cli_failure_exits_2_and_prints_ids_but_never_titles(monkeypatch, capsys):
    store = Store()
    store.item(1, title="First")
    store.item(2, title=SECRET_TITLE, description="SECRET-DESCRIPTION")
    store.item(3, title="Third")

    monkeypatch.setattr(sys, "argv", ["stashseek", "reindex", "--title-rows"])
    monkeypatch.setattr(
        cli,
        "reindex",
        lambda **kwargs: reindex(
            **kwargs,
            embedder=Embedder(fail_on={f"{SECRET_TITLE}\nSECRET-DESCRIPTION"}),
            session_factory=lambda: FakeDB(store),
        ),
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.main()

    captured = capsys.readouterr()
    assert exit_info.value.code == 2
    assert captured.out.splitlines() == [
        "zh_segments_updated=0 title_rows_inserted=2 title_rows_updated=0 "
        "title_rows_skipped=0 failed=1 dry_run=false",
        "item=2 failed",
    ]
    assert "SECRET" not in captured.out + captured.err
