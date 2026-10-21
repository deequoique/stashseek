"""Read-only estimate of where the title rows of items 429/430 would rank (production, raw SQL only)."""

import math

from sqlalchemy import text

from app.bootstrap import build_embedding_provider
from app.config import get_settings
from app.db import get_session_factory

USER = 211
QUERIES = ["电脑相关的视频", "电脑", "什么时候买内存", "电脑 内存 显卡 价格"]
ITEMS = (429, 430)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


factory = get_session_factory()
with factory() as db:
    rows = db.execute(text("SELECT id, title, description FROM content_item WHERE id = ANY(:ids)"), {"ids": list(ITEMS)}).all()
    title_text = {r[0]: (r[1] or "") + ("\n" + r[2][:400] if r[2] else "") for r in rows}
    embedder = build_embedding_provider(get_settings())
    vectors = embedder.embed(QUERIES + [title_text[i] for i in ITEMS])
    qvec = dict(zip(QUERIES, vectors[: len(QUERIES)]))
    tvec = dict(zip(ITEMS, vectors[len(QUERIES):]))
    for query in QUERIES:
        board = db.execute(text(
            "SELECT c.id, max(1 - (s.embedding <=> CAST(:v AS vector))) AS best, left(c.title, 22) "
            "FROM segment s JOIN content_item c ON c.id = s.item_id "
            "WHERE c.user_id = :u AND c.deleted_at IS NULL AND c.archived_at IS NULL AND c.state = 'ready' AND s.embedding IS NOT NULL "
            "GROUP BY c.id ORDER BY best DESC"), {"v": str(list(qvec[query])), "u": USER}).all()
        print(f"\n== {query!r} ==")
        for rank, (item_id, best, title) in enumerate(board, 1):
            extra = ""
            if item_id in tvec:
                extra = f"  title-row cosine={cosine(qvec[query], tvec[item_id]):.4f}"
            print(f"  {rank:>2}. item {item_id} best={best:.4f} {title}{extra}")
