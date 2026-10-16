"""RQ1 production-faithful retrieval study.

Pure computation (settings enumeration, fusion, metrics, ceiling) never
imports ``app.*`` at module scope, so this module can be imported and its
fusion/metrics/ceiling logic unit tested without a database or the Neon test
branch. Only the handful of functions that actually touch the database or a
retrieval backend import ``app.*`` lazily, inside the function body, and only
after the caller has run ``production_path.prepare_test_environment()``.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import production_path
from .artifacts import validate_selection
from .scoring import _validate_case

SEARCH_RESULT_LIMIT = 10
POOL_LIMIT = 50
RRF_K = 60
MINMAX_ALPHA = 0.5
METRIC_KS = (1, 3, 5, 10)
SCOPES = ("A", "B")
_EXACT_CEILING_LIMIT = 25

# The fixed 30 specific-query cases (10 per domain) selected for the parent
# task's smoke runs; see .trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/prd.md and
# the frozen scratch results in
# .trellis/tasks/archive/2026-10/10-06-meeting-qa-quality/research/prodpath-rerun-20261006.cases.json.
# Stored as a sorted tuple so every rq1 run is paired against the same cases
# without depending on an external selection file.
RQ1_CASE_IDS: tuple[str, ...] = (
    "qmsum:Bed002:specific:2",
    "qmsum:Bed002:specific:4",
    "qmsum:Bed002:specific:5",
    "qmsum:Bed010:specific:1",
    "qmsum:Bed015:specific:2",
    "qmsum:Bmr026:specific:0",
    "qmsum:Bro005:specific:3",
    "qmsum:Bro011:specific:3",
    "qmsum:Bro022:specific:0",
    "qmsum:Bro022:specific:3",
    "qmsum:ES2006b:specific:3",
    "qmsum:ES2006b:specific:5",
    "qmsum:ES2006c:specific:2",
    "qmsum:ES2006d:specific:2",
    "qmsum:ES2009b:specific:3",
    "qmsum:ES2009c:specific:0",
    "qmsum:IS1006a:specific:4",
    "qmsum:IS1006c:specific:8",
    "qmsum:TS3009c:specific:0",
    "qmsum:TS3010a:specific:1",
    "qmsum:education_0:specific:4",
    "qmsum:education_0:specific:8",
    "qmsum:education_10:specific:1",
    "qmsum:education_10:specific:11",
    "qmsum:education_18:specific:2",
    "qmsum:education_18:specific:3",
    "qmsum:education_18:specific:5",
    "qmsum:education_22:specific:4",
    "qmsum:education_22:specific:6",
    "qmsum:education_22:specific:8",
)

QUERY_KINDS = ("specific", "general")
HELD_OUT_COLLISION_REASON = "held_out_collision"

METHOD_VARIANTS: tuple[tuple[str, str | None], ...] = (
    ("lexical", "AND"),
    ("lexical", "OR"),
    ("dense", None),
    ("raw", "AND"),
    ("raw", "OR"),
    ("rrf", "AND"),
    ("rrf", "OR"),
    ("minmax", "AND"),
    ("minmax", "OR"),
)


class StudyError(RuntimeError):
    """Raised for harness-level failures that must stop the rq1 run."""


# ------------------------------------------------------------- ranked hits
@dataclass(frozen=True)
class RankedHit:
    """A minimal, backend-independent scored candidate."""

    segment_id: int
    item_id: int
    score: float


def _to_ranked(hits: Sequence[Any]) -> tuple[RankedHit, ...]:
    return tuple(
        RankedHit(segment_id=hit.segment_id, item_id=hit.item_id, score=float(hit.score))
        for hit in hits
    )


# --------------------------------------------------------- settings matrix
@dataclass(frozen=True)
class SettingSpec:
    method: str  # "lexical" | "dense" | "raw" | "rrf" | "minmax"
    lexical_variant: str | None  # "AND" | "OR" | None
    diversify: bool
    scope: str  # "A" | "B"

    @property
    def setting_id(self) -> str:
        parts = [self.method]
        if self.lexical_variant:
            parts.append(self.lexical_variant)
        parts.append("div-on" if self.diversify else "div-off")
        parts.append(f"scope-{self.scope}")
        return "-".join(parts)

    @property
    def is_baseline(self) -> bool:
        return (
            self.method == "raw"
            and self.lexical_variant == "AND"
            and self.diversify
            and self.scope == "B"
        )


def enumerate_settings() -> tuple[SettingSpec, ...]:
    specs: list[SettingSpec] = []
    for method, variant in METHOD_VARIANTS:
        for diversify in (True, False):
            for scope in SCOPES:
                specs.append(SettingSpec(method, variant, diversify, scope))
    return tuple(specs)


# ------------------------------------------------------------------ fusion
def fuse_single(hits: Sequence[RankedHit]) -> list[RankedHit]:
    """Deduplicate one ranked list, keeping the best score per segment."""

    best: dict[int, RankedHit] = {}
    for hit in hits:
        current = best.get(hit.segment_id)
        if current is None or hit.score > current.score:
            best[hit.segment_id] = hit
    return list(best.values())


def fuse_raw(primary: Sequence[RankedHit], dense: Sequence[RankedHit]) -> list[RankedHit]:
    """Concatenate lexical + dense hits, keeping the max score (production)."""

    return fuse_single([*primary, *dense])


def fuse_rrf(
    primary: Sequence[RankedHit], dense: Sequence[RankedHit], *, k: int = RRF_K
) -> list[RankedHit]:
    scores: dict[int, float] = defaultdict(float)
    reps: dict[int, RankedHit] = {}
    for ranked in (primary, dense):
        ordered = sorted(fuse_single(ranked), key=lambda hit: hit.score, reverse=True)
        for rank, hit in enumerate(ordered, start=1):
            scores[hit.segment_id] += 1.0 / (k + rank)
            reps.setdefault(hit.segment_id, hit)
    return [dataclasses.replace(reps[sid], score=score) for sid, score in scores.items()]


def _min_max_normalize(hits: Sequence[RankedHit]) -> dict[int, float]:
    deduped = fuse_single(hits)
    if not deduped:
        return {}
    values = [hit.score for hit in deduped]
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 0:
        return {hit.segment_id: 1.0 for hit in deduped}
    return {hit.segment_id: (hit.score - lo) / span for hit in deduped}


def fuse_minmax(
    primary: Sequence[RankedHit], dense: Sequence[RankedHit], *, alpha: float = MINMAX_ALPHA
) -> list[RankedHit]:
    primary_norm = _min_max_normalize(primary)
    dense_norm = _min_max_normalize(dense)
    reps: dict[int, RankedHit] = {}
    for hit in (*primary, *dense):
        reps.setdefault(hit.segment_id, hit)
    fused: dict[int, float] = {}
    for sid in set(primary_norm) | set(dense_norm):
        fused[sid] = alpha * primary_norm.get(sid, 0.0) + (1 - alpha) * dense_norm.get(sid, 0.0)
    return [dataclasses.replace(reps[sid], score=score) for sid, score in fused.items()]


def fuse_for_method(
    method: str,
    *,
    lex_and: Sequence[RankedHit],
    lex_or: Sequence[RankedHit],
    dense: Sequence[RankedHit],
    lexical_variant: str | None,
) -> list[RankedHit]:
    lexical = lex_and if lexical_variant == "AND" else lex_or
    if method == "lexical":
        return fuse_single(lexical)
    if method == "dense":
        return fuse_single(dense)
    if method == "raw":
        return fuse_raw(lexical, dense)
    if method == "rrf":
        return fuse_rrf(lexical, dense)
    if method == "minmax":
        return fuse_minmax(lexical, dense)
    raise StudyError(f"unknown_method:{method}")


def rank_setting(
    fused: Sequence[RankedHit], *, diversify: bool, limit: int = SEARCH_RESULT_LIMIT
) -> list[RankedHit]:
    """Apply production diversification, or a plain score sort, to a fused list."""

    if diversify:
        from app.agent.services import _diversify_hits

        return _diversify_hits(fused, limit=limit)
    return sorted(fused, key=lambda hit: hit.score, reverse=True)[:limit]


# ----------------------------------------------------------------- metrics
def covered_sentences(
    ranked_segment_ids: Sequence[int],
    segment_full_ids: Mapping[int, Sequence[str]],
    k: int,
) -> frozenset[str]:
    covered: set[str] = set()
    for segment_id in ranked_segment_ids[:k]:
        covered.update(segment_full_ids.get(segment_id, ()))
    return frozenset(covered)


def compute_metrics(
    ranked: Sequence[RankedHit],
    *,
    segment_full_ids: Mapping[int, Sequence[str]],
    gold: frozenset[str],
    gold_chunk_count: int,
    meeting_item_id: int | None = None,
    ks: Sequence[int] = METRIC_KS,
) -> dict[str, float]:
    """Full-containment Hit/Recall/MRR/nDCG for one ranked top-10 list."""

    out: dict[str, float] = {}
    segment_ids = [hit.segment_id for hit in ranked]
    for k in ks:
        covered = covered_sentences(segment_ids, segment_full_ids, k)
        out[f"hit@{k}"] = float(bool(covered & gold))
        out[f"recall@{k}"] = (len(covered & gold) / len(gold)) if gold else 0.0
        if meeting_item_id is not None:
            items_top_k = {hit.item_id for hit in ranked[:k]}
            out[f"meeting_hit@{k}"] = float(meeting_item_id in items_top_k)
    rr = 0.0
    for rank, segment_id in enumerate(segment_ids[:10], start=1):
        if set(segment_full_ids.get(segment_id, ())) & gold:
            rr = 1.0 / rank
            break
    out["rr"] = rr
    gains = [
        1.0 if set(segment_full_ids.get(segment_id, ())) & gold else 0.0
        for segment_id in segment_ids[:10]
    ]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_hits = min(gold_chunk_count, 10)
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    out["ndcg@10"] = (dcg / idcg) if idcg > 0 else 0.0
    return out


def gold_bearing_chunks(
    segments: Sequence[Mapping[str, Any]], gold: frozenset[str]
) -> list[frozenset[str]]:
    chunks = []
    for segment in segments:
        full = frozenset(segment["full_sentence_ids"])
        if full & gold:
            chunks.append(full & gold)
    return chunks


def pool_coverage(
    lexical: Sequence[RankedHit],
    dense: Sequence[RankedHit],
    *,
    segment_full_ids: Mapping[int, Sequence[str]],
    gold: frozenset[str],
) -> dict[str, float | int | None]:
    """Gold coverage of the deduplicated ``dense U lexical`` candidate pool.

    This is the retrieval ceiling of a fusion/ranking step: the share of gold
    sentences fully contained by *any* chunk the two backends proposed (up to
    ``POOL_LIMIT`` each), before any ranking or diversification drops chunks.
    ``segment_full_ids`` maps only the case's own meeting, so a scope-B
    candidate from another meeting contributes nothing (it cannot contain this
    case's gold). ``pool_hit``/``pool_recall`` are ``None`` for a case without
    gold (ineligible).
    """

    segment_ids = list(
        dict.fromkeys([hit.segment_id for hit in dense] + [hit.segment_id for hit in lexical])
    )
    out: dict[str, float | int | None] = {
        "pool_size": len(segment_ids),
        "pool_hit": None,
        "pool_recall": None,
    }
    if gold:
        covered = covered_sentences(segment_ids, segment_full_ids, len(segment_ids)) & gold
        out["pool_hit"] = float(bool(covered))
        out["pool_recall"] = len(covered) / len(gold)
    return out


def meeting_localization(
    ranked: Sequence[RankedHit],
    *,
    case_item_id: int,
    case_meeting_id: str,
    item_meeting_ids: Mapping[int, str],
    meeting_domains: Mapping[str, str],
) -> dict[str, Any]:
    """Did a scope-B ranking put the case's own meeting first, and if not, where?

    ``wrong_top1_same_source`` is ``True`` when the mistaken first-place
    meeting belongs to the same source domain (e.g. another AMI meeting) as the
    case's meeting; the domain comes from the case metadata, never from an id
    prefix. ``meeting_hit@k`` is computed by :func:`compute_metrics`; this adds
    the first-place breakdown. An empty ranking has no first place:
    ``top1_empty`` is set and ``top1_meeting_correct`` is ``0.0``.
    """

    top1 = ranked[0] if ranked else None
    correct = top1 is not None and top1.item_id == case_item_id
    wrong_meeting_id: str | None = None
    same_source: bool | None = None
    if top1 is not None and not correct:
        wrong_meeting_id = item_meeting_ids.get(top1.item_id)
        wrong_domain = meeting_domains.get(wrong_meeting_id) if wrong_meeting_id else None
        case_domain = meeting_domains.get(case_meeting_id)
        if wrong_domain is not None and case_domain is not None:
            same_source = wrong_domain == case_domain
    return {
        "top1_empty": top1 is None,
        "top1_meeting_correct": float(correct),
        "wrong_top1_meeting_id": wrong_meeting_id,
        "wrong_top1_same_source": same_source,
    }


# ----------------------------------------------------------------- ceiling
@dataclass(frozen=True)
class CeilingResult:
    recall: float
    hit: float
    lower_bound: bool


def ceiling_coverage(
    chunks: Sequence[frozenset[str]],
    gold: frozenset[str],
    *,
    ks: Sequence[int] = METRIC_KS,
) -> dict[int, CeilingResult]:
    """Exact maximum coverage of ``gold`` by at most k of ``chunks``.

    ``chunks`` must already be restricted to this case's meeting and to
    gold-bearing chunks (see :func:`gold_bearing_chunks`); each element is the
    intersection of one chunk's fully-contained sentence ids with ``gold``.
    """

    non_empty = [chunk for chunk in chunks if chunk]
    total_gold = len(gold)
    if not non_empty or total_gold == 0:
        return {k: CeilingResult(0.0, 0.0, False) for k in ks}
    if len(non_empty) <= _EXACT_CEILING_LIMIT:
        return _exact_ceiling(non_empty, total_gold, ks)
    return _greedy_ceiling(non_empty, total_gold, ks)


def _exact_ceiling(
    chunks: Sequence[frozenset[str]], total_gold: int, ks: Sequence[int]
) -> dict[int, CeilingResult]:
    n = len(chunks)
    result: dict[int, CeilingResult] = {}
    for k in ks:
        size = min(k, n)
        best = 0
        for combo in itertools.combinations(range(n), size):
            covered: set[str] = set()
            for index in combo:
                covered |= chunks[index]
            if len(covered) > best:
                best = len(covered)
                if best == total_gold:
                    break
        result[k] = CeilingResult(best / total_gold, float(best > 0), False)
    return result


def _greedy_ceiling(
    chunks: Sequence[frozenset[str]], total_gold: int, ks: Sequence[int]
) -> dict[int, CeilingResult]:
    remaining = list(range(len(chunks)))
    covered: set[str] = set()
    coverage_after_pick: list[int] = []
    max_k = max(ks)
    while remaining and len(coverage_after_pick) < max_k:
        best_index, best_gain = None, -1
        for index in remaining:
            gain = len(chunks[index] - covered)
            if gain > best_gain:
                best_gain, best_index = gain, index
        if best_index is None or best_gain <= 0:
            break
        covered |= chunks[best_index]
        remaining.remove(best_index)
        coverage_after_pick.append(len(covered))
    result: dict[int, CeilingResult] = {}
    for k in ks:
        count = min(k, len(coverage_after_pick))
        best = coverage_after_pick[count - 1] if count else 0
        result[k] = CeilingResult(best / total_gold, float(best > 0), True)
    return result


# ------------------------------------------------------------- DB plumbing
def read_only_session_factory(factory: Callable[[], Any]) -> Callable[[], Any]:
    """Wrap a session factory so every session starts a read-only transaction."""

    from sqlalchemy import text as sa_text

    def _factory() -> Any:
        session = factory()
        session.execute(sa_text("SET TRANSACTION READ ONLY"))
        return session

    return _factory


class CachedEmbedder:
    """A query embedder backed by a precomputed ``{query: vector}`` cache.

    Using the exact same cached vector for the production service call and
    every settings-matrix query makes R2 equivalence immune to embedding
    drift between calls.
    """

    def __init__(self, dimensions: int, vectors: Mapping[str, Sequence[float]]) -> None:
        self.dimensions = dimensions
        self._vectors = {key: list(value) for key, value in vectors.items()}

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            return [list(self._vectors[text]) for text in texts]
        except KeyError as exc:
            raise KeyError(f"query_vector_not_cached:{exc}") from exc


def build_query_vector_cache(
    queries: Sequence[str], *, embedder: Any, cache_path: Path
) -> dict[str, list[float]]:
    """Embed each distinct query once; reuse a cache file across reruns.

    Progress is flushed even if an embedding call fails part-way (the full test
    split embeds ~280 queries one by one), so a rerun resumes from the cache
    instead of paying for every query again.
    """

    cached: dict[str, list[float]] = {}
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = {}
    missing = [query for query in dict.fromkeys(queries) if query not in cached]
    embedded = 0
    try:
        for query in missing:
            (vector,) = embedder.embed([query])
            cached[query] = list(vector)
            embedded += 1
    finally:
        if embedded:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = cache_path.with_name(f"{cache_path.name}.tmp")
            tmp_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            tmp_path.replace(cache_path)
    return cached


def lexical_or_search(
    db: Any, query: str, *, user_id: int, k: int = POOL_LIMIT, item_id: int | None = None
) -> list[Any]:
    """Mirror ``bm25_search`` but join ``plainto_tsquery`` lexemes with OR."""

    from sqlalchemy import Text, cast, desc, func, select

    from app.models import ContentItem, Segment
    from app.retrieval.search import _hits

    predicates = [
        ContentItem.user_id == user_id,
        ContentItem.deleted_at.is_(None),
        ContentItem.archived_at.is_(None),
        ContentItem.state == "ready",
    ]
    if item_id is not None:
        predicates.append(Segment.item_id == item_id)
    plain = func.plainto_tsquery("english", query)
    or_text = func.replace(cast(plain, Text), " & ", " | ")
    tsquery = func.to_tsquery("english", or_text)
    score = func.ts_rank_cd(Segment.fts, tsquery)
    stmt = (
        select(Segment, ContentItem)
        .join(ContentItem)
        .where(*predicates, Segment.fts.op("@@")(tsquery))
        .add_columns(score.label("score"))
        .order_by(desc(score))
        .limit(k)
    )
    return _hits(db.execute(stmt).all())


def dense_exact_search(
    db: Any,
    query_vector: Sequence[float],
    *,
    user_id: int,
    k: int = POOL_LIMIT,
    item_id: int | None = None,
) -> list[Any]:
    """The same dense filters, forced onto a sequential/exact scan (R7)."""

    from sqlalchemy import text as sa_text

    from app.retrieval.search import vector_search

    db.execute(sa_text("SET LOCAL enable_indexscan = off"))
    db.execute(sa_text("SET LOCAL enable_bitmapscan = off"))
    return vector_search(db, query_vector, user_id=user_id, k=k, item_id=item_id)


@dataclass(frozen=True)
class CandidatePools:
    lex_and: tuple[RankedHit, ...]
    lex_or: tuple[RankedHit, ...]
    dense: tuple[RankedHit, ...]


def fetch_candidate_pools(
    ro_factory: Callable[[], Any],
    query: str,
    query_vector: Sequence[float],
    *,
    user_id: int,
    item_id: int | None,
    pool_limit: int = POOL_LIMIT,
) -> CandidatePools:
    from app.retrieval.search import bm25_search, vector_search

    with ro_factory() as db:
        lex_and = bm25_search(db, query, user_id=user_id, k=pool_limit, item_id=item_id)
        lex_or = lexical_or_search(db, query, user_id=user_id, k=pool_limit, item_id=item_id)
        dense = vector_search(db, query_vector, user_id=user_id, k=pool_limit, item_id=item_id)
    return CandidatePools(_to_ranked(lex_and), _to_ranked(lex_or), _to_ranked(dense))


def fetch_dense_exact(
    ro_factory: Callable[[], Any],
    query_vector: Sequence[float],
    user_id: int,
    item_id: int | None,
    *,
    pool_limit: int = POOL_LIMIT,
) -> tuple[RankedHit, ...]:
    with ro_factory() as db:
        hits = dense_exact_search(db, query_vector, user_id=user_id, k=pool_limit, item_id=item_id)
    return _to_ranked(hits)


def rank_for_setting(pools: CandidatePools, spec: SettingSpec) -> list[RankedHit]:
    fused = fuse_for_method(
        spec.method,
        lex_and=pools.lex_and,
        lex_or=pools.lex_or,
        dense=pools.dense,
        lexical_variant=spec.lexical_variant,
    )
    return rank_setting(fused, diversify=spec.diversify)


def r2_equivalence_check(
    ro_factory: Callable[[], Any],
    *,
    tenant: Any,
    cached_embedder: CachedEmbedder,
    query: str,
    item_id: int | None,
    expected_ranked: Sequence[RankedHit],
) -> tuple[bool, list[int], list[int]]:
    from app.agent.services import KnowledgeServices

    services = KnowledgeServices(tenant, ro_factory, embedder=cached_embedder)
    citations = services.search_segments(query, limit=SEARCH_RESULT_LIMIT, item_id=item_id)
    actual_ids = [citation.segment_id for citation in citations]
    expected_ids = [hit.segment_id for hit in expected_ranked]
    return actual_ids == expected_ids, expected_ids, actual_ids


def _walk_plan_node_types(node: Mapping[str, Any]) -> list[str]:
    types = [node["Node Type"]] if node.get("Node Type") else []
    for child in node.get("Plans") or []:
        if isinstance(child, Mapping):
            types.extend(_walk_plan_node_types(child))
    return types


_EXPLAIN_STATEMENT_CLASS: Any = None


def _explain_json_statement() -> Any:
    """Build (once, cached) a composable ``EXPLAIN (FORMAT JSON) <stmt>`` construct.

    A plain ``text("EXPLAIN ... " + str(compiled_stmt))`` double-escapes the
    DBAPI's own ``%(name)s`` placeholders and loses the pgvector bind
    processor that casts a Python list to ``::vector``. Composing EXPLAIN at
    the SQLAlchemy compiler level (the documented recipe) keeps the original
    statement's bind-parameter and type handling intact.
    """

    global _EXPLAIN_STATEMENT_CLASS
    if _EXPLAIN_STATEMENT_CLASS is not None:
        return _EXPLAIN_STATEMENT_CLASS

    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.sql.expression import ClauseElement, Executable

    class _Explain(Executable, ClauseElement):
        inherit_cache = False

        def __init__(self, statement: Any) -> None:
            self.statement = statement

    @compiles(_Explain, "postgresql")
    def _compile_explain(element: "_Explain", compiler: Any, **kw: Any) -> str:
        return "EXPLAIN (FORMAT JSON) " + compiler.process(element.statement, **kw)

    _EXPLAIN_STATEMENT_CLASS = _Explain
    return _Explain


def explain_dense_plan(
    ro_factory: Callable[[], Any],
    query_vector: Sequence[float],
    user_id: int,
    item_id: int | None,
    *,
    scope: str,
    case_id: str,
) -> dict[str, Any]:
    """Best-effort EXPLAIN capture for the dense query (diagnostic, R7)."""

    from sqlalchemy import select

    from app.models import ContentItem, Segment

    try:
        explain_cls = _explain_json_statement()
        with ro_factory() as db:
            distance = Segment.embedding.cosine_distance(query_vector)
            predicates = [
                ContentItem.user_id == user_id,
                ContentItem.deleted_at.is_(None),
                ContentItem.archived_at.is_(None),
                ContentItem.state == "ready",
                Segment.embedding.isnot(None),
            ]
            if item_id is not None:
                predicates.append(Segment.item_id == item_id)
            stmt = (
                select(Segment.id)
                .join(ContentItem, Segment.item_id == ContentItem.id)
                .where(*predicates)
                .order_by(distance)
                .limit(SEARCH_RESULT_LIMIT)
            )
            plan = db.execute(explain_cls(stmt)).scalar()
            if isinstance(plan, str):
                plan = json.loads(plan)
            node_types: list[str] = []
            if isinstance(plan, list) and plan and isinstance(plan[0], Mapping):
                node_types = _walk_plan_node_types(plan[0].get("Plan", {}))
            return {"case_id": case_id, "scope": scope, "node_types": node_types, "error": None}
    except Exception as exc:  # best-effort diagnostic; never fails the run
        return {
            "case_id": case_id,
            "scope": scope,
            "node_types": None,
            "error": type(exc).__name__,
        }


def _r7_record(
    case_id: str,
    approx: Sequence[RankedHit],
    exact: Sequence[RankedHit],
    segment_full_ids: Mapping[int, Sequence[str]],
    gold: frozenset[str],
) -> dict[str, Any]:
    approx_top10 = [hit.segment_id for hit in sorted(approx, key=lambda h: h.score, reverse=True)[:10]]
    exact_top10 = [hit.segment_id for hit in sorted(exact, key=lambda h: h.score, reverse=True)[:10]]

    def recall5(ids: list[int]) -> float | None:
        if not gold:
            return None
        covered = covered_sentences(ids, segment_full_ids, 5)
        return len(covered & gold) / len(gold)

    return {
        "case_id": case_id,
        "differs": approx_top10 != exact_top10,
        "approx_top10": approx_top10,
        "exact_top10": exact_top10,
        "approx_recall@5": recall5(approx_top10),
        "exact_recall@5": recall5(exact_top10),
    }


# --------------------------------------------------------------- aggregation
def summarize_metric(values: Sequence[float]) -> dict[str, Any]:
    n = len(values)
    mean = sum(values) / n if n else 0.0
    return {"mean": mean, "n": n}


def compare_to_baseline(
    baseline_values: Sequence[float], setting_values: Sequence[float], *, metric_name: str
) -> dict[str, Any]:
    from . import stats as rq1_stats

    n = len(baseline_values)
    result: dict[str, Any] = {"n": n}
    if n == 0:
        return result
    if metric_name.startswith(("hit@", "meeting_hit@")):
        baseline_bool = [bool(value) for value in baseline_values]
        setting_bool = [bool(value) for value in setting_values]
        mcnemar = rq1_stats.mcnemar_exact(baseline_bool, setting_bool)
        result["mcnemar"] = dataclasses.asdict(mcnemar)
        successes = sum(setting_bool)
        lo, hi = rq1_stats.wilson_ci(successes, n)
        result["proportion"] = successes / n
        result["wilson_ci"] = [lo, hi]
    else:
        diffs = [setting - base for base, setting in zip(baseline_values, setting_values)]
        boot = rq1_stats.paired_bootstrap_ci(diffs)
        result["bootstrap"] = dataclasses.asdict(boot)
        if metric_name in {"recall@5", "rr"}:
            wlt = rq1_stats.win_loss_tie(baseline_values, setting_values)
            result["win_loss_tie"] = dataclasses.asdict(wlt)
    return result


def production_spec_by_scope(
    specs: Sequence[SettingSpec],
) -> dict[str, SettingSpec]:
    """The production setting (raw merge, AND, diversify on) for each scope."""

    return {
        spec.scope: spec
        for spec in specs
        if spec.method == "raw" and spec.lexical_variant == "AND" and spec.diversify
    }


def build_comparisons(
    metrics_by_setting: Mapping[str, Mapping[str, Sequence[float]]],
    specs: Sequence[SettingSpec],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Per-setting deltas against the production setting in the *same* scope.

    A scope-A setting is paired with ``raw-AND-div-on-scope-A`` and a scope-B
    setting with ``raw-AND-div-on-scope-B``. Mixing scopes here would make a
    scope-A delta mostly reflect the scope-A "known meeting" effect rather than
    the method/diversification change being measured.

    Returns ``(comparisons, cross_scope_comparison)``. ``comparisons`` omits
    each scope's own production setting (comparing it to itself is
    meaningless). ``cross_scope_comparison`` is the one explicit
    production-vs-production comparison across scopes (scope-B values are the
    baseline, scope-A values are the "setting"), used only for the dedicated
    scope-B -> A row/bar.
    """

    spec_by_id = {spec.setting_id: spec for spec in specs}
    production_by_scope = production_spec_by_scope(specs)

    comparisons: dict[str, dict[str, Any]] = {}
    for setting_id, metrics in metrics_by_setting.items():
        spec = spec_by_id.get(setting_id)
        if spec is None:
            continue
        scope_baseline_spec = production_by_scope.get(spec.scope)
        if scope_baseline_spec is None or setting_id == scope_baseline_spec.setting_id:
            continue
        scope_baseline_metrics = metrics_by_setting.get(scope_baseline_spec.setting_id, {})
        comparisons[setting_id] = {
            metric_name: compare_to_baseline(
                scope_baseline_metrics[metric_name], values, metric_name=metric_name
            )
            for metric_name, values in metrics.items()
            if metric_name in scope_baseline_metrics
        }

    cross_scope_comparison: dict[str, Any] = {}
    scope_b_spec = production_by_scope.get("B")
    scope_a_spec = production_by_scope.get("A")
    if scope_b_spec is not None and scope_a_spec is not None:
        scope_b_metrics = metrics_by_setting.get(scope_b_spec.setting_id, {})
        scope_a_metrics = metrics_by_setting.get(scope_a_spec.setting_id, {})
        cross_scope_comparison = {
            metric_name: compare_to_baseline(
                scope_b_metrics[metric_name], scope_a_metrics[metric_name], metric_name=metric_name
            )
            for metric_name in scope_b_metrics
            if metric_name in scope_a_metrics
        }

    return comparisons, cross_scope_comparison


# ------------------------------------------------------------ case resolution
def resolve_rq1_cases(
    bundle: Mapping[str, Any],
    *,
    split: str = "val",
    case_ids: Sequence[str] | None = None,
    selection: Mapping[str, Any] | None = None,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Pick the ordered case ids for a run and the cases to leave out of summaries.

    Precedence is ``selection`` (validated against the artifact bundle; its
    split must equal ``split``), else explicit ``case_ids``, else the fixed
    val-30 ``RQ1_CASE_IDS`` (val only). ``selection`` and ``case_ids`` are
    mutually exclusive.

    The second return value maps case id -> exclusion reason. A selection's
    ``held_out_reasons`` (test cases whose answer/transcript duplicates a
    train/val record, so ``held_out_eligible`` is false) are still retrieved
    and written to ``per_case.jsonl`` but never enter a summary or a paired
    comparison.
    """

    if case_ids and selection is not None:
        raise StudyError("case_ids_and_selection_are_mutually_exclusive")
    exclusions: dict[str, str] = {}
    if selection is not None:
        validate_selection(
            selection,
            bundle["cases"],
            dataset_hash=bundle["dataset_hash"],
            leakage=bundle["audit"]["leakage"],
        )
        if selection["split"] != split:
            raise StudyError(f"selection_split_mismatch:{selection['split']}!={split}")
        selected = tuple(selection["selected_case_ids"])
        cases_by_id = {case.case_id: case for case in bundle["cases"]}
        for record in selection.get("held_out_reasons") or ():
            for case_id in record.get("cases", ()):
                case = cases_by_id.get(case_id)
                if case is not None and case.split == split and case_id in selected:
                    exclusions[case_id] = HELD_OUT_COLLISION_REASON
        return selected, exclusions
    if case_ids:
        return tuple(case_ids), exclusions
    if split != "val":
        raise StudyError(f"case_selection_required_for_split:{split}")
    return RQ1_CASE_IDS, exclusions


def validate_study_cases(
    bundle: Mapping[str, Any], case_ids: Sequence[str], split: str
) -> None:
    """Fail before any ingest/DB work if a case does not belong to the library."""

    if not case_ids:
        raise StudyError("no_cases")
    if len(set(case_ids)) != len(case_ids):
        raise StudyError("duplicate_case_id")
    cases_by_id = {case.case_id: case for case in bundle["cases"]}
    library = set(production_path.split_meeting_ids(bundle, split))
    for case_id in case_ids:
        case = cases_by_id.get(case_id)
        if case is None:
            raise StudyError(f"unknown_case_id:{case_id}")
        if case.split != split:
            raise StudyError(f"case_split_mismatch:{case_id}")
        if case.meeting_id not in library:
            raise StudyError(f"case_meeting_not_in_library:{case_id}")


# ------------------------------------------------------------ orchestration
def _aggregate(
    metrics_by_setting: Mapping[str, Mapping[str, Sequence[float]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        setting_id: {
            metric_name: summarize_metric(values) for metric_name, values in metrics.items()
        }
        for setting_id, metrics in metrics_by_setting.items()
    }


def run_rq1(
    bundle: Mapping[str, Any],
    case_ids: Sequence[str],
    run_dir: Path,
    *,
    settings: Any,
    split: str = "val",
    exclusions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run the full R1-R7 settings matrix for ``case_ids`` and return results.

    ``split`` picks the library (every meeting of that split goes into one
    tenant) and every case must belong to it. ``exclusions`` (case id ->
    reason) leaves eligible cases out of aggregates, comparisons and summaries;
    they are still retrieved and keep full records in ``per_case``.
    """

    production_path.assert_test_environment_active()
    validate_study_cases(bundle, case_ids, split)
    from app.bootstrap import build_embedding_provider
    from app.channels.types import TenantContext
    from app.tls import configure_trusted_ca

    run_dir = Path(run_dir)
    cases_by_id = {case.case_id: case for case in bundle["cases"]}
    exclusions = {cid: reason for cid, reason in (exclusions or {}).items() if cid in case_ids}
    # D2: scope B searches every meeting of the split in one tenant (for val,
    # 15 distractors of 35), not just the meetings the selected cases touch.
    meeting_ids = production_path.split_meeting_ids(bundle, split)
    domains_by_meeting = production_path.meeting_domains(bundle, split)

    manifest = production_path.ingest_library(bundle, meeting_ids, run_dir, settings=settings)
    tenant_user_id = manifest["tenant_user_id"]
    tenant = TenantContext(tenant_user_id, 1, "web", "eval", "eval-rq1")
    item_meeting_ids = {info["item_id"]: mid for mid, info in manifest["meetings"].items()}

    factory = production_path.session_factory_from_manifest()
    ro_factory = read_only_session_factory(factory)

    trusted_ca = configure_trusted_ca(settings.tls_ca_bundle)
    embedder = build_embedding_provider(settings, trusted_ca=trusted_ca)
    if embedder is None:
        raise StudyError("embedding_provider_unavailable")
    queries = [cases_by_id[cid].query for cid in case_ids]
    vector_cache = build_query_vector_cache(
        queries, embedder=embedder, cache_path=run_dir / "query_vectors.json"
    )
    cached_embedder = CachedEmbedder(embedder.dimensions, vector_cache)

    specs = enumerate_settings()
    baseline_spec = next(spec for spec in specs if spec.is_baseline)

    per_case_records: list[dict[str, Any]] = []
    r2_failures: list[dict[str, Any]] = []
    invariance_failures: list[dict[str, Any]] = []
    explain_records: list[dict[str, Any]] = []
    r7_records: list[dict[str, Any]] = []
    metrics_by_setting: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    # The same per-case metric lists, split by query kind, so every kind gets
    # its own paired comparison (a pooled "all" number can hide a general-vs-
    # specific difference).
    metrics_by_kind: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    kind_case_counts: dict[str, int] = defaultdict(int)
    eligible_case_ids: list[str] = []

    for case_id in case_ids:
        case = cases_by_id[case_id]
        meeting_info = manifest["meetings"][case.meeting_id]
        item_id = meeting_info["item_id"]
        segment_full_ids = {
            segment["id"]: segment["full_sentence_ids"] for segment in meeting_info["segments"]
        }
        query = case.query
        query_vector = vector_cache[query]

        eligible = case.eligibility.get("ces") == "citation_scorable"
        gold: frozenset[str] = frozenset()
        gold_chunk_count = 0
        ceiling: dict[int, CeilingResult] | None = None
        if eligible:
            known_sentences = {sentence.id: sentence for sentence in bundle["corpus"][case.meeting_id]}
            validated = _validate_case(
                case,
                known_sentences=known_sentences,
                corpus_meetings={case.meeting_id},
                policy="ces",
            )
            gold = validated.gold_ids
            chunks = gold_bearing_chunks(meeting_info["segments"], gold)
            gold_chunk_count = len(chunks)
            ceiling = ceiling_coverage(chunks, gold)
        # ``counted`` cases feed aggregates, comparisons and summaries; an
        # eligible-but-excluded case (held-out collision) is still retrieved
        # and recorded in full below.
        counted = eligible and case_id not in exclusions
        if counted:
            eligible_case_ids.append(case_id)
            kind_case_counts[case.query_kind] += 1

        case_record: dict[str, Any] = {
            "case_id": case_id,
            "meeting_id": case.meeting_id,
            "domain": case.domain,
            "query_kind": case.query_kind,
            "ces_status": case.eligibility.get("ces"),
            "summary_excluded": case_id in exclusions,
            "exclusion_reason": exclusions.get(case_id),
            "query": query,
            "eligible": eligible,
            "gold_count": len(gold),
            "gold_sentence_ids": sorted(gold),
            "gold_chunk_count": gold_chunk_count,
            "ceiling": (
                {str(k): dataclasses.asdict(v) for k, v in ceiling.items()}
                if ceiling is not None
                else None
            ),
            "settings": {},
            # R7 diagnostic: how many dense candidates the production pool
            # query actually returned, per scope (requested pool_limit=50).
            # Scope B's dense plan may be an approximate HNSW scan that
            # returns fewer than the requested pool size; "exact" is only
            # computed for scope B (see dense_exact_search), since scope A's
            # item-scoped query already plans an exact sort.
            "dense_candidate_counts": {},
            # Candidate-pool coverage per scope and lexical variant: gold
            # reachable from dense U lexical before any ranking (AND is the
            # production pool).
            "pool": {},
        }

        for scope in SCOPES:
            scope_item_id = item_id if scope == "A" else None
            pools = fetch_candidate_pools(
                ro_factory, query, query_vector, user_id=tenant_user_id, item_id=scope_item_id
            )
            case_record["dense_candidate_counts"][scope] = len(pools.dense)
            case_record["pool"][scope] = {
                "AND": pool_coverage(
                    pools.lex_and, pools.dense, segment_full_ids=segment_full_ids, gold=gold
                ),
                "OR": pool_coverage(
                    pools.lex_or, pools.dense, segment_full_ids=segment_full_ids, gold=gold
                ),
            }
            explain_records.append(
                explain_dense_plan(
                    ro_factory, query_vector, tenant_user_id, scope_item_id, scope=scope, case_id=case_id
                )
            )
            if scope == "B":
                exact = fetch_dense_exact(ro_factory, query_vector, tenant_user_id, None)
                case_record["dense_candidate_counts"]["B_exact"] = len(exact)
                r7_records.append(_r7_record(case_id, pools.dense, exact, segment_full_ids, gold))

            div_on: dict[tuple[str, str | None], list[int]] = {}
            div_off: dict[tuple[str, str | None], list[int]] = {}
            for spec in (s for s in specs if s.scope == scope):
                ranked = rank_for_setting(pools, spec)
                ranked_ids = [hit.segment_id for hit in ranked]
                ranked_scores = [hit.score for hit in ranked]
                setting_entry: dict[str, Any] = {
                    "segment_ids": ranked_ids,
                    "scores": ranked_scores,
                }
                key = (spec.method, spec.lexical_variant)
                (div_on if spec.diversify else div_off)[key] = ranked_ids

                if eligible:
                    computed = compute_metrics(
                        ranked,
                        segment_full_ids=segment_full_ids,
                        gold=gold,
                        gold_chunk_count=gold_chunk_count,
                        meeting_item_id=item_id if scope == "B" else None,
                    )
                    setting_entry["metrics"] = computed
                    if counted:
                        for metric_name, value in computed.items():
                            metrics_by_setting[spec.setting_id][metric_name].append(value)
                            metrics_by_kind[case.query_kind][spec.setting_id][metric_name].append(
                                value
                            )
                if scope == "B":
                    setting_entry["localization"] = meeting_localization(
                        ranked,
                        case_item_id=item_id,
                        case_meeting_id=case.meeting_id,
                        item_meeting_ids=item_meeting_ids,
                        meeting_domains=domains_by_meeting,
                    )
                case_record["settings"][spec.setting_id] = setting_entry

                if spec.method == "raw" and spec.lexical_variant == "AND" and spec.diversify:
                    equal, expected_ids, actual_ids = r2_equivalence_check(
                        ro_factory,
                        tenant=tenant,
                        cached_embedder=cached_embedder,
                        query=query,
                        item_id=scope_item_id,
                        expected_ranked=ranked,
                    )
                    if not equal:
                        r2_failures.append(
                            {
                                "case_id": case_id,
                                "scope": scope,
                                "expected": expected_ids,
                                "actual": actual_ids,
                            }
                        )

            if scope == "A":
                for key, on_ids in div_on.items():
                    off_ids = div_off.get(key)
                    if on_ids != off_ids:
                        invariance_failures.append(
                            {
                                "case_id": case_id,
                                "method": key[0],
                                "lexical_variant": key[1],
                                "diversify_on": on_ids,
                                "diversify_off": off_ids,
                            }
                        )

        per_case_records.append(case_record)

    aggregates = _aggregate(metrics_by_setting)
    comparisons, cross_scope_comparison = build_comparisons(metrics_by_setting, specs)
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in sorted(metrics_by_kind):
        kind_comparisons, kind_cross_scope = build_comparisons(metrics_by_kind[kind], specs)
        by_kind[kind] = {
            "n_cases": kind_case_counts[kind],
            "aggregates": _aggregate(metrics_by_kind[kind]),
            "comparisons": kind_comparisons,
            "cross_scope_comparison": kind_cross_scope,
        }
    scope_production_setting_ids = {
        scope: spec.setting_id for scope, spec in production_spec_by_scope(specs).items()
    }

    return {
        "run_dir": str(run_dir),
        "library_manifest": manifest,
        "settings": [dataclasses.asdict(spec) | {"setting_id": spec.setting_id} for spec in specs],
        "baseline_setting_id": baseline_spec.setting_id,
        "scope_production_setting_ids": scope_production_setting_ids,
        "split": split,
        # ``eligible_case_ids`` are the cases that feed every aggregate: CES
        # citation-scorable and not excluded. ``excluded_case_ids`` are
        # eligible cases deliberately left out (reasons in ``exclusions``);
        # ``ineligible_case_ids`` are not CES citation-scorable.
        "eligible_case_ids": eligible_case_ids,
        "excluded_case_ids": [cid for cid in case_ids if cid in exclusions],
        "exclusions": dict(exclusions),
        "ineligible_case_ids": [
            cid for cid in case_ids if cases_by_id[cid].eligibility.get("ces") != "citation_scorable"
        ],
        "per_case": per_case_records,
        "aggregates": aggregates,
        "comparisons": comparisons,
        "cross_scope_comparison": cross_scope_comparison,
        "by_kind": by_kind,
        "r2_failures": r2_failures,
        "scope_a_invariance_failures": invariance_failures,
        "explain": explain_records,
        "r7": r7_records,
        "pool_limit": POOL_LIMIT,
    }


__all__ = [
    "CachedEmbedder",
    "CandidatePools",
    "CeilingResult",
    "HELD_OUT_COLLISION_REASON",
    "QUERY_KINDS",
    "RQ1_CASE_IDS",
    "RankedHit",
    "SettingSpec",
    "StudyError",
    "build_comparisons",
    "build_query_vector_cache",
    "ceiling_coverage",
    "compare_to_baseline",
    "compute_metrics",
    "covered_sentences",
    "dense_exact_search",
    "enumerate_settings",
    "explain_dense_plan",
    "fetch_candidate_pools",
    "fetch_dense_exact",
    "fuse_for_method",
    "fuse_minmax",
    "fuse_raw",
    "fuse_rrf",
    "fuse_single",
    "gold_bearing_chunks",
    "lexical_or_search",
    "meeting_localization",
    "pool_coverage",
    "production_spec_by_scope",
    "r2_equivalence_check",
    "rank_for_setting",
    "rank_setting",
    "read_only_session_factory",
    "resolve_rq1_cases",
    "run_rq1",
    "summarize_metric",
    "validate_study_cases",
]
