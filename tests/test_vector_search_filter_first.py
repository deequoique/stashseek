"""Filter-first exact vector ranking: query-1 predicates, query-2 shape,
hydration re-check, and the reusable ``filter_first_vector_rank`` helper.

No live database is used. ``FakeSession`` records every statement handed to
``execute()`` and returns canned rows, so assertions inspect the compiled SQL
text of the exact statements ``vector_search`` issues.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.models import Base, ContentItem, Segment
from app.retrieval.search import (
    Hit,
    _eligible_item_ids_stmt,
    filter_first_vector_rank,
    vector_search,
)


def compiled(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeSession:
    """Captures every executed statement and returns queued canned rows."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.statements: list = []

    def execute(self, stmt):
        self.statements.append(stmt)
        return FakeResult(self.responses.pop(0))


def _item(item_id: int, platform_id: str) -> ContentItem:
    return ContentItem(
        id=item_id,
        title=f"video-{item_id}",
        platform="youtube",
        platform_id=platform_id,
        kind="video",
        url=f"https://youtu.be/{platform_id}",
    )


def _segment(segment_id: int, item_id: int, text: str, start_sec: float) -> Segment:
    return Segment(
        id=segment_id,
        item_id=item_id,
        seq=1,
        start_sec=start_sec,
        text=text,
        boundary_kind="gap",
    )


# --------------------------------------------------------------- query 1


def test_query1_plain_predicates_have_no_optional_filter():
    stmt = _eligible_item_ids_stmt(
        user_id=7, platform=None, platform_ids=None, platform_id=None, item_id=None
    )
    assert compiled(stmt) == (
        "SELECT content_item.id \n"
        "FROM content_item \n"
        "WHERE content_item.user_id = 7 AND content_item.deleted_at IS NULL "
        "AND content_item.archived_at IS NULL AND content_item.state = 'ready'"
    )


def test_query1_platform_predicate():
    stmt = _eligible_item_ids_stmt(
        user_id=7, platform="youtube", platform_ids=None, platform_id=None, item_id=None
    )
    assert "content_item.platform = 'youtube'" in compiled(stmt)


def test_query1_platform_ids_predicate():
    stmt = _eligible_item_ids_stmt(
        user_id=7,
        platform=None,
        platform_ids=["abc", "def"],
        platform_id=None,
        item_id=None,
    )
    assert "content_item.platform_id IN ('abc', 'def')" in compiled(stmt)


def test_query1_empty_platform_ids_is_unsatisfiable():
    stmt = _eligible_item_ids_stmt(
        user_id=7, platform=None, platform_ids=[], platform_id=None, item_id=None
    )
    assert "false" in compiled(stmt).lower()


def test_query1_item_id_predicate():
    stmt = _eligible_item_ids_stmt(
        user_id=7, platform=None, platform_ids=None, platform_id=None, item_id=42
    )
    assert "content_item.id = 42" in compiled(stmt)


def test_empty_tenant_returns_empty_with_no_second_query():
    db = FakeSession(responses=[[]])

    result = vector_search(db, [0.1, 0.2], user_id=7, k=10)

    assert result == []
    assert len(db.statements) == 1


def test_empty_platform_ids_short_circuits_without_any_query_rows():
    db = FakeSession(responses=[[]])

    result = vector_search(db, [0.1, 0.2], user_id=7, k=10, platform_ids=[])

    assert result == []
    assert "false" in compiled(db.statements[0]).lower()


# --------------------------------------------------------------- query 2


def test_query2_uses_any_materialized_cte_and_not_null_and_limit():
    db = FakeSession(
        responses=[
            [(10,), (20,)],  # query 1: eligible item ids
            [],  # query 2: ranked ids (empty -> short circuit before hydrate)
        ]
    )

    result = vector_search(db, [0.1, 0.2], user_id=7, k=5)

    assert result == []
    assert len(db.statements) == 2
    sql = compiled(db.statements[1])
    assert "WITH tenant_vectors AS MATERIALIZED" in sql
    assert "segment.item_id = ANY (ARRAY[10, 20])" in sql
    assert "segment.embedding IS NOT NULL" in sql
    assert "ORDER BY tenant_vectors.embedding <=>" in sql
    assert "LIMIT 5" in sql


def test_helper_k_below_one_returns_empty_without_a_query():
    class ExplodingSession:
        def execute(self, stmt):
            raise AssertionError("must not query when k < 1")

    from sqlalchemy import select

    candidates = select(Segment.id.label("id"), Segment.embedding.label("embedding"))
    assert (
        filter_first_vector_rank(
            ExplodingSession(),
            candidates,
            id_column="id",
            embedding_column="embedding",
            query_vector=[0.1],
            k=0,
        )
        == []
    )


# ------------------------------------------------------- no global ANN index


def test_no_ann_index_declared_on_any_multi_tenant_embedding_column():
    """Regression guard for the truncation defect this task fixes.

    ``database-guidelines.md``'s "Vector indexes in a multi-tenant table"
    scenario requires that no HNSW/IVFFlat index exists on a shared embedding
    column; ranking must always go through ``filter_first_vector_rank``
    instead. This walks every declared index rather than naming tables, so
    a future embedding table (e.g. media) is covered automatically.
    """

    offending = [
        f"{table.name}.{index.name}"
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if str(index.kwargs.get("postgresql_using", "")).lower() in {"hnsw", "ivfflat"}
    ]
    assert offending == []


# --------------------------------------------------------------- hydration


def test_hydration_reapplies_tenant_predicates_and_preserves_rank_order():
    item_a = _item(10, "pid-10")
    item_b = _item(20, "pid-20")
    segment_101 = _segment(101, 10, "evidence-101", 10.0)
    segment_102 = _segment(102, 20, "evidence-102", 20.0)
    segment_103 = _segment(103, 10, "evidence-103", 30.0)

    db = FakeSession(
        responses=[
            [(10,), (20,)],  # query 1
            [(101, 0.9), (103, 0.5), (102, 0.7)],  # query 2: ranked (id, score)
            # hydrate returns rows out of rank order, and omits 103 (as if it
            # had become deleted/archived between query 2 and the hydrate).
            [(segment_102, item_b), (segment_101, item_a)],
        ]
    )

    result = vector_search(db, [0.1, 0.2], user_id=7, k=5)

    assert len(db.statements) == 3
    hydrate_sql = compiled(db.statements[2])
    assert "content_item.user_id = 7" in hydrate_sql
    assert "content_item.deleted_at IS NULL" in hydrate_sql
    assert "content_item.archived_at IS NULL" in hydrate_sql
    assert "content_item.state = 'ready'" in hydrate_sql

    assert [hit.segment_id for hit in result] == [101, 102]
    assert [hit.score for hit in result] == [0.9, 0.7]
    assert all(isinstance(hit, Hit) for hit in result)
