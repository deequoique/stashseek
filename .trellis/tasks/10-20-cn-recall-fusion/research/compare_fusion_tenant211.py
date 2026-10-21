"""Read-only: run KnowledgeServices.search_segments for tenant 211 under raw and rrf fusion.

Hits the production database read-only (one embedding call per query).  Title
rows only exist after ``stashseek reindex`` has run, so before deployment this
measures RRF + window alone.
"""

from app.agent.services import KnowledgeServices
from app.bootstrap import build_embedding_provider
from app.channels.types import TenantContext
from app.config import get_settings
from app.db import get_session_factory
from app.retrieval.fusion import FusionConfig

USER = 211
QUERIES = ["电脑相关的视频", "电脑", "什么时候买内存", "电脑 内存 显卡 价格"]
WATCH = {429: "等等党", 430: "内存"}

tenant = TenantContext(app_user_id=USER, channel_identity_id=1, channel="cli", account_id="research", external_user_id="research")
embedder = build_embedding_provider(get_settings())
factory = get_session_factory()
for method in ("raw", "rrf"):
    services = KnowledgeServices(tenant, factory, embedder=embedder, fusion=FusionConfig(method=method))
    print(f"\n===== method={method} =====")
    for query in QUERIES:
        citations = services.search_segments(query, limit=10)
        items = [c.item_id for c in citations]
        distinct = list(dict.fromkeys(items))
        present = {label: (item in items) for item, label in WATCH.items()}
        print(f"{query!r}: {len(citations)} hits, {len(distinct)} videos {distinct} | 429={'in' if present['等等党'] else 'OUT'} 430={'in' if present['内存'] else 'OUT'}")
        for c in citations:
            tag = c.kind if c.kind != "segment" else ""
            print(f"    item {c.item_id} seg {c.segment_id} score={c._retrieval_score:.4f} {tag} {c.title[:24]}")
