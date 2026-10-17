from __future__ import annotations

import itertools

from evals.meeting_gold.retrieval_study import (
    _EXACT_CEILING_LIMIT,
    CeilingResult,
    ceiling_coverage,
)


def test_ceiling_single_best_chunk_at_k1() -> None:
    gold = frozenset({"a", "b", "c", "d"})
    chunks = [frozenset({"a"}), frozenset({"b", "c"}), frozenset({"d"})]
    result = ceiling_coverage(chunks, gold, ks=(1, 2, 3))
    assert result[1] == CeilingResult(recall=0.5, hit=1.0, lower_bound=False)
    assert result[2].recall == 0.75  # best pair out of 3 chunks covers 3/4
    assert result[3].recall == 1.0
    assert all(not r.lower_bound for r in result.values())


def test_ceiling_prefers_disjoint_chunks_over_overlapping_ones() -> None:
    gold = frozenset({"a", "b", "c"})
    # Two disjoint chunks beat picking the overlapping pair for k=2.
    chunks = [frozenset({"a", "b"}), frozenset({"a"}), frozenset({"c"})]
    result = ceiling_coverage(chunks, gold, ks=(1, 2))
    assert result[1].recall == 2 / 3  # best single chunk {a, b}
    assert result[2].recall == 1.0  # {a, b} + {c} covers everything


def test_ceiling_is_exactly_optimal_not_merely_greedy() -> None:
    # A case where the greedy algorithm (always take the largest marginal
    # gain first) would be suboptimal for k=2, to prove the exact brute-force
    # path is actually used for small instances.
    gold = frozenset({"a", "b", "c", "d"})
    chunks = [
        frozenset({"a", "b"}),  # greedy picks this first (size 2)
        frozenset({"a", "c"}),
        frozenset({"b", "d"}),
    ]
    # Greedy: pick {a,b} (gain 2), then best remaining gain is 1 ({a,c} or
    # {b,d}) -> covers 3 of 4. Optimal: {a,c} + {b,d} covers all 4.
    result = ceiling_coverage(chunks, gold, ks=(2,))
    assert result[2].recall == 1.0
    assert result[2].lower_bound is False


def test_ceiling_empty_inputs() -> None:
    gold = frozenset({"a"})
    assert ceiling_coverage([], gold, ks=(1,))[1] == CeilingResult(0.0, 0.0, False)
    assert ceiling_coverage([frozenset({"a"})], frozenset(), ks=(1,))[1] == CeilingResult(0.0, 0.0, False)


def test_ceiling_drops_empty_chunks() -> None:
    gold = frozenset({"a", "b"})
    chunks = [frozenset(), frozenset({"a"}), frozenset(), frozenset({"b"})]
    result = ceiling_coverage(chunks, gold, ks=(1, 2))
    assert result[1].recall == 0.5
    assert result[2].recall == 1.0


def test_ceiling_falls_back_to_greedy_lower_bound_above_exact_limit() -> None:
    # One chunk per gold id, well beyond the exact brute-force limit; the
    # greedy path must still find the (here, actually optimal) full-coverage
    # answer and flag it as a lower bound.
    n = _EXACT_CEILING_LIMIT + 5
    gold = frozenset(f"g{i}" for i in range(n))
    chunks = [frozenset({f"g{i}"}) for i in range(n)]
    result = ceiling_coverage(chunks, gold, ks=(1, 5, 10))
    assert result[1].lower_bound is True
    assert result[1].recall == 1 / n
    assert result[10].recall == 10 / n


def test_ceiling_matches_bruteforce_on_a_small_random_instance() -> None:
    import random

    rng = random.Random(7)
    gold = frozenset(f"g{i}" for i in range(6))
    chunks = []
    for _ in range(8):
        size = rng.randint(1, 3)
        chunks.append(frozenset(rng.sample(sorted(gold), size)))

    result = ceiling_coverage(chunks, gold, ks=(1, 3, 5))
    for k, expected in result.items():
        best = 0
        for combo in itertools.combinations(range(len(chunks)), min(k, len(chunks))):
            covered: set[str] = set()
            for index in combo:
                covered |= chunks[index]
            best = max(best, len(covered))
        assert expected.recall == best / len(gold)
