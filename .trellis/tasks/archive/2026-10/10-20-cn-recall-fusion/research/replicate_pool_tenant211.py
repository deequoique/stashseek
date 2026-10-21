"""Read-only: replicate KnowledgeServices.search_segments' final 10-citation pool per query."""

from sqlalchemy import text

from app.agent.services import SEARCH_CANDIDATE_POOL_LIMIT, _diversify_hits
from app.bootstrap import build_embedding_provider
from app.config import get_settings
from app.db import session
from app.retrieval.search import bm25_search, vector_search

USER = 211
QUERIES = ["电脑相关的视频", "电脑", "电脑 内存 显卡 价格", "computer"]

with session() as db:
    cols = [r[0] for r in db.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='content_item' AND data_type LIKE 'timestamp%'"
    ))]
    print("timestamp columns:", cols)
    ts = "saved_at" if "saved_at" in cols else cols[0]
    rows = db.execute(text(f"""
        SELECT c.id, c.{ts}, c.platform, c.lang, c.duration_sec, c.text_source,
               (SELECT count(*) FROM segment s WHERE s.item_id=c.id) AS segs,
               (SELECT coalesce(avg(length(s.text)),0)::int FROM segment s WHERE s.item_id=c.id) AS avg_chars,
               left(c.title, 40)
        FROM content_item c WHERE c.user_id=:u AND c.deleted_at IS NULL ORDER BY c.id
    """), {"u": USER}).all()
    print(f"item | {ts} | platform | lang | dur | source | segs | avg_chars | title")
    for r in rows:
        print(" | ".join(str(v)[:19] if i == 1 else str(v) for i, v in enumerate(r)))

    embedder = build_embedding_provider(get_settings())
    vectors = embedder.embed(QUERIES)
    for query, vector in zip(QUERIES, vectors):
        hits = bm25_search(db, query, user_id=USER, k=SEARCH_CANDIDATE_POOL_LIMIT)
        n_lex = len(hits)
        hits.extend(vector_search(db, vector, user_id=USER, k=SEARCH_CANDIDATE_POOL_LIMIT))
        selected = _diversify_hits(hits, limit=10)
        print(f"\n== {query!r}: lexical={n_lex} vector={len(hits) - n_lex} -> 10 citations ==")
        for hit in selected:
            print(f"  item {hit.item_id} seg {hit.segment_id} score={hit.score:.4f} {(hit.title or '')[:30]} | {hit.text[:50]!r}")
