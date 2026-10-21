"""Rank fusion and candidate-window selection for retrieval hit lists.

The functions here are pure: they only read ``segment_id``, ``item_id`` and
``score`` from each hit and rebuild hits with ``dataclasses.replace``, so they
work for ``app.retrieval.search.Hit`` and for the evaluation harness's
lightweight ranked hits alike. A third retrieval path (for example media
embeddings) is fused by passing one more list and one more weight; no rule
changes.

Reciprocal rank fusion (RRF) compares ranks, never raw scores. That matters
because the lexical backend (``ts_rank_cd``, roughly 0.01 - 1) and the vector
backend (cosine similarity, roughly 0.3 - 0.8) are not on a common scale: with
raw-score merging a lexical hit practically never reaches the public window.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

RRF_K = 60


class _RankedLike(Protocol):
    @property
    def segment_id(self) -> int: ...

    @property
    def item_id(self) -> int: ...

    @property
    def score(self) -> float: ...


H = TypeVar("H", bound=_RankedLike)


@dataclass(frozen=True)
class FusionConfig:
    """Tunable retrieval fusion settings (defaults match the production plan).

    ``method="raw"`` keeps the pre-RRF behaviour (raw-score merge followed by
    the five-video diversification window) as a rollback switch and as the
    evaluation baseline. ``per_item_cap`` is the soft per-video cap of
    ``select_window``: it spreads results over videos but never shortens the
    window.
    """

    method: Literal["rrf", "raw"] = "rrf"
    rrf_k: int = RRF_K
    lexical_weight: float = 1.0
    vector_weight: float = 1.0
    candidate_pool_limit: int = 50
    per_item_cap: int = 3


def _best_per_segment(hits: Sequence[H]) -> list[H]:
    """Deduplicate by segment ID keeping the highest score, best first.

    Ordering is ``(score desc, segment_id asc)`` so ties never depend on the
    incoming list order.
    """

    best: dict[int, H] = {}
    for hit in hits:
        current = best.get(hit.segment_id)
        if current is None or hit.score > current.score:
            best[hit.segment_id] = hit
    return sorted(best.values(), key=lambda hit: (-hit.score, hit.segment_id))


def fuse_rrf(
    lists: Sequence[Sequence[H]], *, k: int = RRF_K, weights: Sequence[float]
) -> list[H]:
    """Fuse several ranked hit lists with weighted reciprocal rank fusion.

    Each list is first deduplicated by segment ID (best raw score wins) and
    ranked by raw score; the hit at rank ``r`` (starting at 1) contributes
    ``weight / (k + r)``. A segment present in several lists accumulates the
    contributions. Empty lists and lists with weight 0 are skipped, so a
    missing or disabled path never affects the others. The returned hits carry
    the fused score and are ordered ``(score desc, segment_id asc)``.
    """

    if k < 1:
        raise ValueError("rrf_k must be at least 1")
    if len(weights) != len(lists):
        raise ValueError("weights must match the number of hit lists")
    if any(weight < 0 for weight in weights):
        raise ValueError("weights must not be negative")

    scores: dict[int, float] = defaultdict(float)
    representatives: dict[int, H] = {}
    for ranked, weight in zip(lists, weights):
        if weight == 0 or not ranked:
            continue
        for rank, hit in enumerate(_best_per_segment(ranked), start=1):
            scores[hit.segment_id] += weight / (k + rank)
            representatives.setdefault(hit.segment_id, hit)
    fused = [
        dataclasses.replace(representatives[segment_id], score=score)
        for segment_id, score in scores.items()
    ]
    fused.sort(key=lambda hit: (-hit.score, hit.segment_id))
    return fused


def fuse_raw(lists: Sequence[Sequence[H]]) -> list[H]:
    """Merge lists by raw score, keeping the best score per segment.

    This is the legacy behaviour (``method="raw"``): scores from different
    backends are compared directly, so a low-scale path is outranked.
    """

    merged: list[H] = [hit for ranked in lists for hit in ranked]
    return _best_per_segment(merged)


def select_window(
    fused: Sequence[H], *, limit: int, per_item_cap: int
) -> list[H]:
    """Take ``limit`` fused hits, spreading them over videos with a soft cap.

    ``fused`` must already be ranked and deduplicated by segment (the output of
    ``fuse_rrf`` / ``fuse_raw``). The window is built in two passes:

    1. walk the ranking and take a hit unless its video already contributed
       ``per_item_cap`` hits, stopping at ``limit``;
    2. if that left the window short, fill the remaining slots with the
       skipped hits in ranking order, no longer limited by ``per_item_cap``.

    First-pass hits come first (in ranking order), filler hits after them. The
    cap only guarantees diversity: it never makes the window shorter than
    ``min(limit, len(fused))``, so a search that is narrowed to one video
    (every hit shares an item) is simply filled to ``limit`` by ranking.
    ``per_item_cap >= limit`` is equivalent to a plain ranking cut.
    """

    if per_item_cap < 1:
        raise ValueError("per_item_cap must be at least 1")
    if limit < 1:
        return []
    counts: dict[int, int] = defaultdict(int)
    window: list[H] = []
    skipped: list[H] = []
    for hit in fused:
        if counts[hit.item_id] >= per_item_cap:
            skipped.append(hit)
            continue
        counts[hit.item_id] += 1
        window.append(hit)
        if len(window) >= limit:
            return window
    window.extend(skipped[: limit - len(window)])
    return window
