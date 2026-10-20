"""Chinese lexical search compiles to bigram full-text SQL; English is unchanged.

No live database is used: ``FakeSession`` captures the single statement
``bm25_search`` executes and the tests inspect its compiled (parameterised)
PostgreSQL form. ``literal_binds`` cannot render the ``regconfig`` argument,
so parameters are asserted from ``compiled.params``.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.models import ContentItem, Segment
from app.retrieval.search import Hit, bm25_search


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.statements: list = []

    def execute(self, stmt):
        self.statements.append(stmt)
        return FakeResult(self.rows)


def compile_stmt(stmt):
    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


def tail(sql: str) -> str:
    """The FROM/WHERE/ORDER/LIMIT part, without the long column list."""

    return sql[sql.index("FROM segment") :]


# The exact pre-change English statement tail (captured before the Chinese branch changed).
ENGLISH_TAIL = (
    "FROM segment JOIN content_item ON content_item.id = segment.item_id \n"
    "WHERE content_item.user_id = %(user_id_1)s AND content_item.deleted_at IS NULL "
    "AND content_item.archived_at IS NULL AND content_item.state = %(state_1)s "
    "AND (segment.fts @@ websearch_to_tsquery(%(websearch_to_tsquery_1)s, "
    "%(websearch_to_tsquery_2)s)) "
    "ORDER BY ts_rank_cd(segment.fts, websearch_to_tsquery(%(websearch_to_tsquery_1)s, "
    "%(websearch_to_tsquery_2)s)) DESC \n"
    " LIMIT %(param_1)s"
)


def test_chinese_query_uses_simple_tsquery_with_or_semantics():
    db = FakeSession()

    assert bm25_search(db, "电脑相关的视频", user_id=7, k=20) == []

    sql, params = compile_stmt(db.statements[0])
    assert "websearch_to_tsquery(" in sql
    assert params["websearch_to_tsquery_1"] == "simple"
    assert params["websearch_to_tsquery_2"] == "电脑 OR 脑相 OR 相关 OR 视频"
    assert "segment.fts @@ websearch_to_tsquery(" in sql
    assert "ts_rank_cd(segment.fts, websearch_to_tsquery(" in sql
    assert "ORDER BY ts_rank_cd(" in sql
    assert params["param_1"] == 20


def test_chinese_query_drops_the_trigram_and_language_gate():
    db = FakeSession()

    bm25_search(db, "什么时候买内存", user_id=7)

    sql, _params = compile_stmt(db.statements[0])
    where = tail(sql)
    lowered = where.lower()
    assert "similarity" not in lowered
    assert "ilike" not in lowered
    assert "lang like" not in lowered
    assert "content_item.lang LIKE" not in sql
    assert "%%" not in where  # no pg_trgm `%` operator


def test_chinese_query_keeps_every_tenant_and_lifecycle_predicate():
    db = FakeSession()

    bm25_search(
        db,
        "电脑",
        user_id=7,
        platform="bilibili",
        platform_ids=["BVabc"],
        item_id=5,
    )

    sql, params = compile_stmt(db.statements[0])
    where = tail(sql)
    assert "content_item.user_id = %(user_id_1)s" in where
    assert "content_item.deleted_at IS NULL" in where
    assert "content_item.archived_at IS NULL" in where
    assert "content_item.state = %(state_1)s" in where
    assert "content_item.platform = %(platform_1)s" in where
    assert "content_item.platform_id IN" in where
    assert "segment.item_id = %(item_id_1)s" in where
    assert params["user_id_1"] == 7
    assert params["state_1"] == "ready"
    assert params["item_id_1"] == 5


def test_chinese_ties_break_by_segment_id_for_a_stable_order():
    db = FakeSession()

    bm25_search(db, "电脑", user_id=7)

    sql, _params = compile_stmt(db.statements[0])
    assert "DESC, segment.id" in sql


def test_chinese_query_without_any_token_issues_no_sql():
    db = FakeSession()

    assert bm25_search(db, "好吗", user_id=7) == []
    assert db.statements == []


def test_mixed_query_tokenizes_ascii_words_too():
    db = FakeSession()

    bm25_search(db, "DDR5 内存价格", user_id=7)

    _sql, params = compile_stmt(db.statements[0])
    assert params["websearch_to_tsquery_1"] == "simple"
    assert params["websearch_to_tsquery_2"] == "ddr5 OR 内存 OR 存价 OR 价格"


def test_english_query_sql_is_unchanged():
    db = FakeSession()

    bm25_search(db, "gradient descent", user_id=7, k=20)

    sql, params = compile_stmt(db.statements[0])
    assert tail(sql) == ENGLISH_TAIL
    assert params["websearch_to_tsquery_1"] == "english"
    assert params["websearch_to_tsquery_2"] == "gradient descent"
    assert "DESC, segment.id" not in sql


def test_english_query_with_scope_keeps_its_filters():
    db = FakeSession()

    bm25_search(
        db,
        "gradient descent",
        user_id=7,
        platform="youtube",
        platform_ids=["abc"],
        item_id=5,
    )

    sql, params = compile_stmt(db.statements[0])
    assert "content_item.platform = %(platform_1)s" in sql
    assert "segment.item_id = %(item_id_1)s" in sql
    assert params["websearch_to_tsquery_1"] == "english"


def test_hits_carry_the_segment_boundary_kind():
    item = ContentItem(
        id=1,
        title="video",
        platform="bilibili",
        platform_id="BVabc",
        kind="video",
        url="https://www.bilibili.com/video/BVabc",
    )
    body = Segment(id=10, item_id=1, seq=1, start_sec=3, text="电脑", boundary_kind="gap")
    title = Segment(id=11, item_id=1, seq=-1, start_sec=0, text="标题", boundary_kind="title")
    db = FakeSession(rows=[(body, item, 0.2), (title, item, 0.1)])

    hits = bm25_search(db, "电脑", user_id=7)

    assert [h.boundary_kind for h in hits] == ["gap", "title"]
    assert Hit(1, "t", "p", 1, "x", 0.0, 0.5).boundary_kind == ""
