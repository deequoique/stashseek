"""Semantic-first chunking with lower/upper bounds and whole-cue overlap.

The chunker greedily grows each chunk until it reaches a language-specific
lower bound, then looks for the best cut inside the window bounded by the
lower bound, the upper bound, and the 120s hard ceiling. When per-cue
embeddings are available, the cut is chosen at the deepest TextTiling-style
similarity dip in that window (falling back to a nearby sentence end or gap
when it is almost as deep, to avoid mid-sentence cuts). Without embeddings,
gap and punctuation boundaries drive the choice. Adjacent chunks overlap by a
small number of whole cues so that neighbor expansion never loses context at
a cut.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from app.connectors.base import Cue

LOWER_UNITS = {"en": 80, "zh": 130}
UPPER_UNITS = {"en": 200, "zh": 330}
MAX_CHUNK_SECONDS = 120
OVERLAP_RATIO = 0.15

_SENTENCE_END_RE = re.compile(r"[.?!。？！]\s*$")


@dataclass(frozen=True)
class Chunk:
    start_sec: float
    end_sec: float
    text: str
    boundary_kind: str


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
    return dot / norm if norm else 0.0


def semantic_boundary_indices(embeddings: Sequence[Sequence[float]]) -> list[int]:
    """Return cue indices after strict local minima in adjacent similarity.

    Kept for backward compatibility; ``chunk()`` now uses the TextTiling-style
    depth scoring in ``_depth_scores`` instead.
    """
    if len(embeddings) < 4:
        return []
    similarities = [_cosine(a, b) for a, b in zip(embeddings, embeddings[1:])]
    return [i + 1 for i in range(1, len(similarities) - 1) if similarities[i] < similarities[i - 1] and similarities[i] < similarities[i + 1]]


def _make(cues: Sequence[Cue], kind: str) -> Chunk:
    return Chunk(cues[0].start, cues[-1].end, " ".join(c.text.strip() for c in cues if c.text.strip()), kind)


def _lang_key(lang: str) -> str:
    return "zh" if lang.lower().startswith("zh") else "en"


def _units(cues: Sequence[Cue], lang: str) -> int:
    text = " ".join(cue.text for cue in cues)
    if lang.lower().startswith("zh"):
        return len(re.findall(r"[㐀-鿿]", text))
    return len(re.findall(r"\b[\w']+\b", text))


def _depth_scores(sims: Sequence[float]) -> list[float]:
    """TextTiling-style depth score for each adjacent-similarity gap.

    For each gap, walk outward in both directions while the similarity keeps
    rising, stopping at the nearest local maximum on each side. The depth is
    how far the gap sits below those two peaks; the deepest gap is the
    strongest candidate semantic boundary.
    """
    n = len(sims)
    depths = [0.0] * n
    for i in range(n):
        left_peak = sims[i]
        k = i - 1
        while k >= 0 and sims[k] >= left_peak:
            left_peak = sims[k]
            k -= 1
        right_peak = sims[i]
        k = i + 1
        while k < n and sims[k] >= right_peak:
            right_peak = sims[k]
            k += 1
        depths[i] = (left_peak - sims[i]) + (right_peak - sims[i])
    return depths


def _window_candidates(cues: Sequence[Cue], lang: str, lang_key: str, start: int) -> list[int]:
    """Cut positions ``j`` (chunk = ``cues[start:j]``) within all bounds."""
    n = len(cues)
    lower = LOWER_UNITS[lang_key]
    upper = UPPER_UNITS[lang_key]
    candidates: list[int] = []
    end = start + 1
    while end <= n:
        duration = cues[end - 1].end - cues[start].start
        if duration > MAX_CHUNK_SECONDS:
            break
        units = _units(cues[start:end], lang)
        if units > upper:
            break
        if units >= lower:
            candidates.append(end)
        end += 1
    return candidates


def _choose_cut(
    cues: Sequence[Cue],
    lang: str,
    lang_key: str,
    start: int,
    candidates: list[int],
    embeddings: Sequence[Sequence[float]] | None,
) -> tuple[int, str]:
    n = len(cues)

    def is_gap(j: int) -> bool:
        return j < n and cues[j].start - cues[j - 1].end >= 2.0

    def is_sent(j: int) -> bool:
        return bool(_SENTENCE_END_RE.search(cues[j - 1].text))

    depth_eligible = [j for j in candidates if j < n]

    if embeddings is not None and depth_eligible:
        hi = max(depth_eligible)
        local = embeddings[start : hi + 1]
        if len(local) >= 2:
            sims = [_cosine(a, b) for a, b in zip(local, local[1:])]
            depth_values = _depth_scores(sims)
            depths = {
                j: depth_values[j - start - 1]
                for j in depth_eligible
                if 0 <= j - start - 1 < len(depth_values)
            }
            if depths:
                max_depth = max(depths.values())
                threshold = 0.5 * max_depth
                preferred = [
                    j
                    for j in depths
                    if (is_gap(j) or is_sent(j)) and depths[j] >= threshold
                ]
                pool = preferred if preferred else list(depths)
                winner = max(pool, key=lambda j: (depths[j], -j))
                if is_gap(winner):
                    return winner, "gap"
                if is_sent(winner):
                    return winner, "punct"
                return winner, "semantic"

    gaps = [j for j in candidates if is_gap(j)]
    if gaps:
        winner = max(gaps, key=lambda j: (cues[j].start - cues[j - 1].end, -j))
        return winner, "gap"

    sents = [j for j in candidates if is_sent(j)]
    if sents:
        lower = LOWER_UNITS[lang_key]
        upper = UPPER_UNITS[lang_key]
        mid = (lower + upper) / 2
        winner = min(sents, key=lambda j: abs(_units(cues[start:j], lang) - mid))
        return winner, "punct"

    return max(candidates), "hard_cut"


def _hard_cut_end(cues: Sequence[Cue], start: int) -> int:
    """Largest exclusive end keeping ``cues[start:end]`` within 120s.

    Used when no candidate in the window reaches the lower bound without
    breaking the upper bound or the duration ceiling first (R2's "no
    candidate at all" fallback), and always takes at least one cue.
    """
    n = len(cues)
    end = start + 1
    while end < n and cues[end].end - cues[start].start <= MAX_CHUNK_SECONDS:
        end += 1
    return end


def _hard_cut(cues: Sequence[Cue], *, lang: str) -> list[Chunk]:
    """Pure duration-ceiling cutting; the fallback building block for spans
    with no reachable lower-bound boundary. ``lang`` is accepted for
    signature compatibility; the cut itself is duration-only.
    """
    del lang
    result: list[Chunk] = []
    start = 0
    while start < len(cues):
        end = _hard_cut_end(cues, start)
        result.append(_make(cues[start:end], "hard_cut"))
        if end >= len(cues):
            break
        k = max(1, round(OVERLAP_RATIO * (end - start)))
        start = max(start + 1, end - k)
    return result


def _window_chunks(
    cues: Sequence[Cue],
    *,
    lang: str,
    embeddings: Sequence[Sequence[float]] | None = None,
) -> list[Chunk]:
    lang_key = _lang_key(lang)
    lower = LOWER_UNITS[lang_key]
    upper = UPPER_UNITS[lang_key]
    n = len(cues)
    pieces: list[tuple[int, int, str]] = []
    start = 0
    while start < n:
        candidates = _window_candidates(cues, lang, lang_key, start)
        if candidates:
            winner, label = _choose_cut(cues, lang, lang_key, start, candidates, embeddings)
        else:
            winner = _hard_cut_end(cues, start)
            label = "hard_cut"

        if pieces and winner <= pieces[-1][1]:
            # Retreating into the previous chunk's overlap can land right
            # before a very long mid-transcript gap: the window from this
            # retreated ``start`` can't reach the lower bound before the gap
            # pushes duration past the ceiling, so the hard-cut fallback ends
            # at (or before) the same cue as the previous chunk. That would
            # yield a zero-progress, fully-redundant chunk. Drop the overlap
            # for this one boundary and resume exactly where the previous
            # chunk ended instead, which always strictly advances coverage.
            start = pieces[-1][1]
            continue

        pieces.append((start, winner, label))
        if winner >= n:
            break
        k = max(1, round(OVERLAP_RATIO * (winner - start)))
        start = max(start + 1, winner - k)

    if len(pieces) > 1:
        last_start, last_end, _last_label = pieces[-1]
        if _units(cues[last_start:last_end], lang) < lower:
            prev_start, _prev_end, prev_label = pieces[-2]
            merged_units = _units(cues[prev_start:last_end], lang)
            merged_duration = cues[last_end - 1].end - cues[prev_start].start
            if merged_units <= upper and merged_duration <= MAX_CHUNK_SECONDS:
                pieces[-2:] = [(prev_start, last_end, prev_label)]

    return [_make(cues[s:e], label) for s, e, label in pieces]


def chunk(
    cues: list[Cue], *, lang: str, chapters: list[dict] | None = None,
    semantic_embedder: Callable[[list[str]], list[list[float]]] | None = None,
) -> list[Chunk]:
    if not cues:
        return []
    if chapters:
        result: list[Chunk] = []
        for chapter in chapters:
            selected = [c for c in cues if c.start >= float(chapter["start_time"]) and c.start < float(chapter.get("end_time", cues[-1].end))]
            if not selected:
                continue
            if selected[-1].end - selected[0].start <= 180:
                result.append(_make(selected, "chapter"))
            else:
                result.extend(chunk(selected, lang=lang, semantic_embedder=semantic_embedder))
        if result:
            return result

    embeddings = semantic_embedder([cue.text for cue in cues]) if semantic_embedder is not None else None
    return _window_chunks(cues, lang=lang, embeddings=embeddings)
