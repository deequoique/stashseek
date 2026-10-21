"""Read-only overview of tenant 211's library, duplicate chunks and per-item vector leaderboard."""

from sqlalchemy import func, select, text

from app.bootstrap import build_embedding_provider
from app.config import get_settings
from app.db import session
from app.models import ContentItem, Segment

USER = 211
QUERIES = ["电脑相关的视频", "电脑", "什么时候买内存", "等等党 电脑价格"]

ready = [
    ContentItem.user_id == USER,
    ContentItem.deleted_at.is_(None),
    ContentItem.archived_at.is_(None),
    ContentItem.state == "ready",
]

with session() as db:
    rows = db.execute(
        select(
            ContentItem.id, ContentItem.title, ContentItem.platform, ContentItem.lang,
            ContentItem.state, ContentItem.text_source, ContentItem.duration_sec,
            func.count(Segment.id), func.count(Segment.embedding),
            func.coalesce(func.sum(func.length(Segment.text)), 0),
        )
        .outerjoin(Segment, Segment.item_id == ContentItem.id)
        .where(ContentItem.user_id == USER, ContentItem.deleted_at.is_(None))
        .group_by(ContentItem.id)
        .order_by(ContentItem.id)
    ).all()
    print("item_id | platform | lang | state | source | dur | segs | embedded | chars | title")
    titles = {}
    for r in rows:
        titles[r[0]] = (r[1] or "")[:34]
        print(f"{r[0]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | {r[8]} | {r[9]} | {titles[r[0]]}")

    print("\n== duplicate chunk texts across the tenant (count>1) ==")
    dups = db.execute(
        select(func.count(), func.count(func.distinct(Segment.item_id)), func.left(Segment.text, 70))
        .join(ContentItem, Segment.item_id == ContentItem.id)
        .where(*ready)
        .group_by(Segment.text)
        .having(func.count() > 1)
        .order_by(func.count().desc())
        .limit(12)
    ).all()
    for n, items, preview in dups:
        print(f"  x{n} in {items} items: {preview!r}")
    dup_total = db.execute(
        select(func.coalesce(func.sum(text("c")), 0)).select_from(
            select(func.count().label("c"))
            .select_from(Segment).join(ContentItem, Segment.item_id == ContentItem.id)
            .where(*ready).group_by(Segment.text).having(func.count() > 1).subquery()
        )
    ).scalar()
    print("  segments that belong to a duplicate group:", dup_total)

    print("\n== first chunks of the two Bilibili videos ==")
    bili = db.execute(
        select(Segment.item_id, Segment.seq, Segment.start_sec, Segment.text)
        .join(ContentItem, Segment.item_id == ContentItem.id)
        .where(ContentItem.user_id == USER, ContentItem.platform == "bilibili")
        .order_by(Segment.item_id, Segment.seq)
    ).all()
    seen = {}
    for item_id, seq, start, body in bili:
        seen[item_id] = seen.get(item_id, 0) + 1
        if seen[item_id] <= 3:
            print(f"  item {item_id} seq {seq} @{float(start):.0f}s len={len(body)}: {body[:110]!r}")
    print("  '电脑' substring hits per item:", db.execute(
        select(Segment.item_id, func.count()).join(ContentItem, Segment.item_id == ContentItem.id)
        .where(ContentItem.user_id == USER, Segment.text.ilike("%电脑%")).group_by(Segment.item_id)
    ).all())

    embedder = build_embedding_provider(get_settings())
    vectors = embedder.embed(QUERIES)
    for query, vector in zip(QUERIES, vectors):
        print(f"\n== per-item best cosine for {query!r} (all ready segments) ==")
        best = (1 - Segment.embedding.cosine_distance(vector))
        board = db.execute(
            select(ContentItem.id, func.max(best), func.count())
            .join(Segment, Segment.item_id == ContentItem.id)
            .where(*ready, Segment.embedding.isnot(None))
            .group_by(ContentItem.id)
            .order_by(func.max(best).desc())
        ).all()
        for rank, (item_id, score, n) in enumerate(board, 1):
            print(f"  {rank:>2}. item {item_id} best={score:.4f} segs={n} {titles.get(item_id, '')}")
