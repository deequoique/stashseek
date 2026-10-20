from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.agent.services import (
    EmbeddingUnavailable,
    KnowledgeNotFound,
    KnowledgeServices,
    RetrievalUnavailable,
    _citation,
)
from app.browser_capture import timestamp_url
from app.channels.types import TenantContext
from app.retrieval.fusion import FusionConfig
from app.retrieval.search import Hit

RAW = FusionConfig(method="raw")


class FakeEmbeddingProvider:
    dimensions = 2

    def __init__(self, result=None, error: Exception | None = None):
        self.result = result if result is not None else [[0.5, 0.5]]
        self.error = error
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.error is not None:
            raise self.error
        return self.result


def tenant() -> TenantContext:
    return TenantContext(1, 1, "telegram", "account", "user")


def hit(item_id: int, segment_id: int, score: float, start_sec: float = 0) -> Hit:
    return Hit(
        item_id=item_id,
        title=f"video-{item_id}",
        platform_id=f"platform-{item_id}",
        segment_id=segment_id,
        text=f"evidence-{segment_id}",
        start_sec=start_sec,
        score=score,
    )


def catalog_database(*hit_lists: list[Hit], title_segment_ids: frozenset[int] = frozenset()):
    """A fake session whose hydration query returns every hit it was given.

    The service only keeps rows whose IDs it selected, so returning the whole
    catalog is equivalent to the ``Segment.id IN (...)`` filter of real SQL.
    """

    rows = []
    for ranked in hit_lists:
        for value in ranked:
            is_title = value.segment_id in title_segment_ids
            segment = SimpleNamespace(
                id=value.segment_id,
                text=value.text,
                start_sec=0 if is_title else value.start_sec,
                anchor=None,
                boundary_kind="title" if is_title else "gap",
            )
            item = SimpleNamespace(
                id=value.item_id,
                title=value.title,
                platform_id=value.platform_id,
                platform="youtube",
                url=f"https://youtu.be/{value.platform_id}",
            )
            rows.append((segment, item))

    class Database:
        def execute(self, _statement):
            return SimpleNamespace(all=lambda: rows)

    @contextmanager
    def database():
        yield Database()

    return database


def search_service(
    monkeypatch,
    lexical: list[Hit],
    vector: list[Hit],
    *,
    fusion: FusionConfig | None = None,
    title_segment_ids: frozenset[int] = frozenset(),
) -> KnowledgeServices:
    monkeypatch.setattr("app.agent.services.bm25_search", lambda *_a, **_k: list(lexical))
    monkeypatch.setattr("app.agent.services.vector_search", lambda *_a, **_k: list(vector))
    return KnowledgeServices(
        tenant(),
        catalog_database(lexical, vector, title_segment_ids=title_segment_ids),
        embedder=FakeEmbeddingProvider(),
        fusion=fusion,
    )


def test_search_requires_embedding_before_any_database_work():
    def database_must_not_open():
        raise AssertionError("database search must not run without an embedding")

    service = KnowledgeServices(tenant(), database_must_not_open)

    with pytest.raises(EmbeddingUnavailable):
        service.search_segments("redacted query")


def test_provider_failure_is_embedding_unavailable_without_query_leakage():
    provider = FakeEmbeddingProvider(error=RuntimeError("upstream failure"))
    service = KnowledgeServices(tenant(), lambda: pytest.fail("database must not open"), embedder=provider)

    with pytest.raises(EmbeddingUnavailable) as caught:
        service.search_segments("redacted query")

    assert "redacted query" not in str(caught.value)
    assert provider.calls == [["redacted query"]]


def test_zero_hits_is_distinct_from_database_failure(monkeypatch):
    @contextmanager
    def database():
        yield object()

    provider = FakeEmbeddingProvider()
    service = KnowledgeServices(tenant(), database, embedder=provider)
    monkeypatch.setattr("app.agent.services.bm25_search", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.agent.services.vector_search", lambda *_args, **_kwargs: [])

    assert service.search_segments("redacted query") == []
    assert provider.calls == [["redacted query"]]

    @contextmanager
    def broken_database():
        raise RuntimeError("database down")
        yield object()

    broken = KnowledgeServices(tenant(), broken_database, embedder=provider)
    with pytest.raises(RetrievalUnavailable) as caught:
        broken.search_segments("redacted query")
    assert "redacted query" not in str(caught.value)


def test_raw_mode_reserves_top_five_videos_before_extra_segments(monkeypatch):
    crowded = [hit(1, segment_id, 100 - segment_id, start_sec=segment_id * 120) for segment_id in range(1, 9)]
    weaker_videos = [hit(item_id, item_id * 100, 10 - item_id) for item_id in range(2, 7)]
    service = search_service(monkeypatch, [], crowded + weaker_videos, fusion=RAW)

    selected = service.search_segments("redacted query")

    assert [row.item_id for row in selected[:5]] == [1, 2, 3, 4, 5]
    assert 6 not in {row.item_id for row in selected}
    # After video representatives, the public window can retain the distant
    # first video's chapters in relevance order rather than flattening it.
    assert [row.segment_id for row in selected[5:]] == [2, 3, 4, 5, 6]


def test_raw_mode_keeps_best_duplicate_and_distant_same_item_evidence(monkeypatch):
    lexical = [hit(1, 11, 0.4, 10), hit(1, 19, 0.8, 900)]
    vector = [hit(1, 11, 0.9, 10), hit(2, 21, 0.7, 20)]
    service = search_service(monkeypatch, lexical, vector, fusion=RAW)

    selected = service.search_segments("redacted query")

    assert [row.segment_id for row in selected] == [11, 21, 19]
    assert selected[0]._retrieval_score == 0.9
    assert [row.start_sec for row in selected if row.item_id == 1] == [10, 900]


def test_raw_mode_breaks_score_ties_by_segment_id_like_fuse_raw(monkeypatch):
    # The raw branch goes through ``fuse_raw``: equal scores order by segment
    # ID, not by which backend listed the hit first.
    lexical = [hit(2, 20, 0.5)]
    vector = [hit(1, 10, 0.5)]

    selected = search_service(monkeypatch, lexical, vector, fusion=RAW).search_segments(
        "redacted query"
    )

    assert [row.segment_id for row in selected] == [10, 20]


def test_rrf_lets_a_low_scale_lexical_hit_into_the_window(monkeypatch):
    # A lexical hit scores around 0.009 against cosine scores around 0.5. Raw
    # merging puts it last and drops it; RRF compares ranks only.
    lexical = [hit(99, 900, 0.009)]
    vector = [hit(item_id, item_id, 0.6 - item_id / 100) for item_id in range(1, 11)]

    fused = search_service(monkeypatch, lexical, vector).search_segments("redacted query")
    legacy = search_service(monkeypatch, lexical, vector, fusion=RAW).search_segments(
        "redacted query"
    )

    assert len(fused) == 10
    assert 900 in {row.segment_id for row in fused}
    assert 900 not in {row.segment_id for row in legacy}


def test_rrf_window_is_not_limited_to_five_videos(monkeypatch):
    vector = [hit(item_id, item_id, 0.9 - item_id / 100) for item_id in range(1, 7)]

    selected = search_service(monkeypatch, [], vector).search_segments("redacted query")

    assert [row.item_id for row in selected] == [1, 2, 3, 4, 5, 6]


def test_rrf_window_caps_hits_per_video_and_fills_with_other_videos(monkeypatch):
    crowded = [hit(1, segment_id, 0.95 - segment_id / 100) for segment_id in range(1, 9)]
    others = [hit(item_id, item_id * 100, 0.5 - item_id / 100) for item_id in range(2, 6)]
    vector = crowded + others

    default = search_service(monkeypatch, [], vector).search_segments("redacted query")
    capped = search_service(
        monkeypatch, [], vector, fusion=FusionConfig(per_item_cap=2)
    ).search_segments("redacted query")

    # The cap only decides what comes first: the first pass holds at most
    # ``per_item_cap`` hits of the dominant video plus the weaker videos ...
    assert [row.item_id for row in default[:7]].count(1) == 3
    assert [row.item_id for row in capped[:6]].count(1) == 2
    assert {row.item_id for row in default[:7]} == {1, 2, 3, 4, 5}
    # ... and the window is then filled by ranking, so it is never short.
    assert len(default) == 10
    assert len(capped) == 10
    assert [row.segment_id for row in default[:7]] == [1, 2, 3, 200, 300, 400, 500]
    assert [row.segment_id for row in default[7:]] == [4, 5, 6]
    assert [row.segment_id for row in capped[:6]] == [1, 2, 200, 300, 400, 500]
    assert [row.segment_id for row in capped[6:]] == [3, 4, 5, 6]


def test_rrf_retrieval_score_is_the_fused_score(monkeypatch):
    shared = hit(1, 11, 0.4)
    lexical = [shared, hit(2, 21, 0.2)]
    vector = [hit(1, 11, 0.8), hit(3, 31, 0.7)]

    selected = search_service(monkeypatch, lexical, vector).search_segments("redacted query")
    scores = {row.segment_id: row._retrieval_score for row in selected}

    # Segment 11 is rank 1 in both lists; the others are rank 2 in one list.
    assert scores[11] == pytest.approx(2 / 61)
    assert scores[21] == pytest.approx(1 / 62)
    assert scores[31] == pytest.approx(1 / 62)
    assert selected[0].segment_id == 11


def test_rrf_weights_and_k_come_from_the_fusion_config(monkeypatch):
    lexical = [hit(1, 11, 0.4)]
    vector = [hit(2, 21, 0.8)]
    fusion = FusionConfig(rrf_k=10, lexical_weight=3.0, vector_weight=1.0)

    selected = search_service(monkeypatch, lexical, vector, fusion=fusion).search_segments(
        "redacted query"
    )

    assert [row.segment_id for row in selected] == [11, 21]
    assert selected[0]._retrieval_score == pytest.approx(3 / 11)
    assert selected[1]._retrieval_score == pytest.approx(1 / 11)


def test_title_row_is_projected_as_a_time_less_citation(monkeypatch):
    title_hit = replace(hit(7, 700, 0.8, start_sec=0), text="视频标题\n简介内容")
    service = search_service(
        monkeypatch, [], [title_hit, hit(7, 701, 0.7, 30)], title_segment_ids=frozenset({700})
    )

    selected = service.search_segments("redacted query")
    by_id = {row.segment_id: row for row in selected}

    title = by_id[700]
    assert title.kind == "title"
    assert title.start_sec is None
    assert title.url == "https://youtu.be/platform-7"
    assert title.excerpt == "视频标题\n简介内容"
    assert by_id[701].kind == "segment"
    assert by_id[701].start_sec == 30
    assert by_id[701].url == timestamp_url("youtube", "https://youtu.be/platform-7", 30.0)
    assert by_id[701].url != title.url


def test_citation_projection_ignores_the_stored_zero_start_of_a_title_row():
    item = SimpleNamespace(
        id=3, title="T", platform_id="abc", platform="bilibili", url="https://www.bilibili.com/video/BV1x"
    )
    segment = SimpleNamespace(
        id=30, text="T\nintro", start_sec=0, anchor=None, boundary_kind="title"
    )

    citation = _citation(item, segment, score=0.25)

    assert citation.kind == "title"
    assert citation.start_sec is None
    assert citation.url == "https://www.bilibili.com/video/BV1x"
    assert citation._retrieval_score == 0.25
    assert citation.model_dump()["kind"] == "title"


@pytest.mark.parametrize("fusion", [RAW, FusionConfig()], ids=["raw", "rrf"])
def test_search_overfetch_is_bounded_and_public_limit_is_clamped(monkeypatch, fusion):
    calls = []

    @contextmanager
    def database():
        yield object()

    service = KnowledgeServices(
        tenant(), database, embedder=FakeEmbeddingProvider(), fusion=fusion
    )
    monkeypatch.setattr(
        "app.agent.services.bm25_search",
        lambda _db, _query, *, user_id, k: calls.append(("bm25", user_id, k)) or [],
    )
    monkeypatch.setattr(
        "app.agent.services.vector_search",
        lambda _db, _vector, *, user_id, k: calls.append(("vector", user_id, k)) or [],
    )

    assert service.search_segments("redacted query", limit=999) == []
    assert calls == [("bm25", 1, 50), ("vector", 1, 50)]


def test_candidate_pool_size_comes_from_the_fusion_config(monkeypatch):
    calls = []

    @contextmanager
    def database():
        yield object()

    service = KnowledgeServices(
        tenant(),
        database,
        embedder=FakeEmbeddingProvider(),
        fusion=FusionConfig(candidate_pool_limit=30),
    )
    monkeypatch.setattr(
        "app.agent.services.bm25_search",
        lambda _db, _query, *, user_id, k: calls.append(("bm25", k)) or [],
    )
    monkeypatch.setattr(
        "app.agent.services.vector_search",
        lambda _db, _vector, *, user_id, k: calls.append(("vector", k)) or [],
    )

    service.search_segments("redacted query", limit=10)  # max(20, 50) -> capped at 30
    service.search_segments("redacted query", limit=2)  # max(20, 10) = 20
    assert calls == [("bm25", 30), ("vector", 30), ("bm25", 20), ("vector", 20)]


def test_item_scoped_search_fills_the_window_by_fused_rank_under_the_default_cap(
    monkeypatch,
):
    # Every hit belongs to the one scoped video, so the per-video cap cannot
    # apply: the default (rrf, cap 3) window still holds min(limit, candidates)
    # hits, in fused rank order.
    vector = [hit(42, segment_id, 0.9 - segment_id / 100) for segment_id in range(1, 16)]
    lexical = [hit(42, 12, 0.01)]
    service = search_service(monkeypatch, lexical, vector)

    selected = service.search_segments("redacted query", item_id=42)

    # Segment 12 is lexical rank 1 plus vector rank 12; it leads the fusion.
    assert [row.segment_id for row in selected] == [12, 1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert {row.item_id for row in selected} == {42}
    assert [row.segment_id for row in service.search_segments(
        "redacted query", item_id=42, limit=5
    )] == [12, 1, 2, 3, 4]


def test_item_scoped_search_with_few_candidates_returns_all_of_them(monkeypatch):
    vector = [hit(42, segment_id, 0.9 - segment_id / 100) for segment_id in range(1, 5)]

    selected = search_service(monkeypatch, [], vector).search_segments(
        "redacted query", item_id=42
    )

    assert [row.segment_id for row in selected] == [1, 2, 3, 4]


def test_item_scoped_search_passes_the_item_filter_to_both_paths(monkeypatch):
    seen = []

    @contextmanager
    def database():
        yield object()

    service = KnowledgeServices(tenant(), database, embedder=FakeEmbeddingProvider())
    monkeypatch.setattr(
        "app.agent.services.bm25_search",
        lambda _db, _query, **kwargs: seen.append(("bm25", kwargs["item_id"])) or [],
    )
    monkeypatch.setattr(
        "app.agent.services.vector_search",
        lambda _db, _vector, **kwargs: seen.append(("vector", kwargs["item_id"])) or [],
    )

    service.search_segments("redacted query", item_id=42)

    assert seen == [("bm25", 42), ("vector", 42)]


def test_neighbor_hydration_keeps_deleted_and_archived_items_out():
    statements: list[str] = []

    class Database:
        def execute(self, statement):
            statements.append(str(statement))
            return SimpleNamespace(
                one_or_none=lambda: (
                    SimpleNamespace(id=11, item_id=7, seq=2),
                    SimpleNamespace(id=7),
                )
            )

        def scalars(self, statement):
            statements.append(str(statement))
            return SimpleNamespace(all=lambda: [])

    @contextmanager
    def database():
        yield Database()

    assert KnowledgeServices(tenant(), database).get_neighbors(11) == []
    neighbor_query = statements[-1]
    assert "content_item.deleted_at IS NULL" in neighbor_query
    assert "content_item.archived_at IS NULL" in neighbor_query
    assert "segment.boundary_kind !=" in neighbor_query
    # The anchor query excludes title rows too, so a title row can never be the
    # starting point of a neighbor lookup.
    assert "segment.boundary_kind !=" in statements[0]


def test_open_at_excludes_title_rows():
    statements: list[str] = []

    class Database:
        def execute(self, statement):
            statements.append(str(statement))
            return SimpleNamespace(one_or_none=lambda: None)

    @contextmanager
    def database():
        yield Database()

    with pytest.raises(KnowledgeNotFound):
        KnowledgeServices(tenant(), database).open_at(11)

    assert "segment.boundary_kind !=" in statements[0]
