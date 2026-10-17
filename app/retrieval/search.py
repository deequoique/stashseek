"""Independent vector and lexical retrieval paths for P0 comparison."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import BigInteger, Select, any_, bindparam, desc, false, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY

from app.browser_capture import timestamp_url
from app.models import ContentItem, Segment


@dataclass(frozen=True)
class Hit:
    item_id: int
    title: str | None
    platform_id: str
    segment_id: int
    text: str
    start_sec: float
    score: float
    platform: str = "youtube"
    source_url: str | None = None

    @property
    def url(self) -> str:
        source_url = self.source_url or f"https://youtu.be/{self.platform_id}"
        return timestamp_url(self.platform, source_url, self.start_sec)


def _hits(rows) -> list[Hit]:
    return [
        Hit(
            item.id,
            item.title,
            item.platform_id,
            segment.id,
            segment.text,
            float(segment.start_sec),
            float(score),
            platform=item.platform,
            source_url=item.url,
        )
        for segment, item, score in rows
    ]


def filter_first_vector_rank(
    db,
    candidates: Select,
    *,
    id_column: str,
    embedding_column: str,
    query_vector: Sequence[float],
    k: int,
) -> list[tuple[int, float]]:
    """Rank an already tenant-restricted candidate set by exact cosine distance.

    ``candidates`` MUST already be filtered to a single tenant through an
    indexed predicate (for example ``item_id = ANY(:ids)`` for segments, or
    ``app_user_id = :tenant`` plus the embedding-space columns for media)
    before it reaches this helper. The candidate select is wrapped in a
    ``WITH ... AS MATERIALIZED`` CTE so PostgreSQL evaluates the
    tenant-restricted set first; the ranking step is then an *exact* nearest
    neighbor sort over exactly those rows, so an approximate ANN index on the
    base table can never be used for ranking, even if one exists.

    Returns at most ``k`` ``(id, score)`` pairs, best first, where
    ``score = 1 - cosine_distance``. Any future media vector search must call
    this helper rather than re-implement ranking; see
    ``.trellis/spec/backend/agent-retrieval-convergence.md`` and
    ``.trellis/spec/backend/database-guidelines.md``.

    Ties are broken by ascending ``id`` so the result order is fully
    deterministic and never depends on incidental physical scan order.
    """

    if k < 1:
        return []
    tenant_vectors = candidates.cte("tenant_vectors").prefix_with("MATERIALIZED")
    id_col = tenant_vectors.c[id_column]
    embedding_col = tenant_vectors.c[embedding_column]
    distance = embedding_col.cosine_distance(query_vector)
    stmt = (
        select(id_col, (1 - distance).label("score"))
        .order_by(distance, id_col)
        .limit(k)
    )
    return [(row[0], float(row[1])) for row in db.execute(stmt).all()]


def _eligible_item_ids_stmt(
    *,
    user_id: int,
    platform: str | None,
    platform_ids: Iterable[str] | None,
    platform_id: str | None,
    item_id: int | None,
) -> Select:
    predicates = [
        ContentItem.user_id == user_id,
        ContentItem.deleted_at.is_(None),
        ContentItem.archived_at.is_(None),
        ContentItem.state == "ready",
    ]
    if platform is not None:
        predicates.append(ContentItem.platform == platform)
    if platform_ids is not None or platform_id is not None:
        values = tuple(platform_ids) if platform_ids is not None else (platform_id,)
        predicates.append(ContentItem.platform_id.in_(values) if values else false())
    if item_id is not None:
        predicates.append(ContentItem.id == item_id)
    return select(ContentItem.id).where(*predicates)


def vector_search(
    db,
    query_vector: list[float],
    *,
    user_id: int,
    k: int = 20,
    platform: str | None = None,
    platform_ids: Iterable[str] | None = None,
    platform_id: str | None = None,
    item_id: int | None = None,
) -> list[Hit]:
    # Query 1: resolve the tenant's eligible item IDs first. A global ANN
    # index with a tenant post-filter silently truncates results once the
    # tenant's own rows fall outside the index's approximate candidate
    # window; filtering by item ID before ranking removes that failure mode.
    item_ids_stmt = _eligible_item_ids_stmt(
        user_id=user_id,
        platform=platform,
        platform_ids=platform_ids,
        platform_id=platform_id,
        item_id=item_id,
    )
    item_ids = [row[0] for row in db.execute(item_ids_stmt).all()]
    if not item_ids:
        return []

    # Query 2: rank only the tenant's own segments, using the existing
    # (item_id, seq) index for the filter and an exact cosine sort for the
    # ranking (see filter_first_vector_rank). IDs are bound as one array
    # parameter (`= ANY(:ids)`) so the plan stays stable for large ID lists,
    # instead of `IN (...)`, whose SQL text (and plan) varies with list size.
    item_ids_param = any_(bindparam("item_ids", value=item_ids, type_=ARRAY(BigInteger)))
    candidates = select(
        Segment.id.label("id"), Segment.embedding.label("embedding")
    ).where(Segment.item_id == item_ids_param, Segment.embedding.isnot(None))
    ranked = filter_first_vector_rank(
        db,
        candidates,
        id_column="id",
        embedding_column="embedding",
        query_vector=query_vector,
        k=k,
    )
    if not ranked:
        return []
    ranked_ids = [segment_id for segment_id, _score in ranked]

    # Query 3 (hydrate): re-apply the tenant/lifecycle predicates as defense
    # in depth against an item being deleted, archived, or reassigned between
    # queries 1 and 2.
    hydrate_predicates = [
        Segment.id.in_(ranked_ids),
        ContentItem.user_id == user_id,
        ContentItem.deleted_at.is_(None),
        ContentItem.archived_at.is_(None),
        ContentItem.state == "ready",
    ]
    stmt = select(Segment, ContentItem).join(ContentItem).where(*hydrate_predicates)
    rows = {segment.id: (segment, item) for segment, item in db.execute(stmt).all()}

    # Walk `ranked` in its own (deterministic) order rather than the
    # hydrate query's unordered result, so rank order and tie-breaking are
    # never silently re-derived from an unrelated physical scan order.
    ordered = [
        (*rows[segment_id], score)
        for segment_id, score in ranked
        if segment_id in rows
    ]
    return _hits(ordered)


def bm25_search(
    db,
    query: str,
    *,
    user_id: int,
    k: int = 20,
    platform: str | None = None,
    platform_ids: Iterable[str] | None = None,
    platform_id: str | None = None,
    item_id: int | None = None,
) -> list[Hit]:
    is_zh = bool(re.search(r"[\u3400-\u9fff]", query))
    predicates = [
        ContentItem.user_id == user_id,
        ContentItem.deleted_at.is_(None),
        ContentItem.archived_at.is_(None),
        ContentItem.state == "ready",
    ]
    if platform is not None:
        predicates.append(ContentItem.platform == platform)
    if platform_ids is not None or platform_id is not None:
        values = tuple(platform_ids) if platform_ids is not None else (platform_id,)
        predicates.append(ContentItem.platform_id.in_(values) if values else false())
    if item_id is not None:
        predicates.append(Segment.item_id == item_id)
    base = select(Segment, ContentItem).join(ContentItem).where(*predicates)
    if is_zh:
        score = func.similarity(Segment.text, query)
        stmt = base.add_columns(score.label("score")).where(ContentItem.lang.like("zh%"), or_(Segment.text.op("%")(query), Segment.text.ilike(f"%{query}%"))).order_by(desc(score)).limit(k)
    else:
        tsquery = func.websearch_to_tsquery("english", query)
        score = func.ts_rank_cd(Segment.fts, tsquery)
        stmt = base.add_columns(score.label("score")).where(Segment.fts.op("@@")(tsquery)).order_by(desc(score)).limit(k)
    return _hits(db.execute(stmt).all())
