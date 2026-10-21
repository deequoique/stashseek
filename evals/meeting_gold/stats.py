"""Paired statistical tests for the RQ1 retrieval study.

Every function here is pure and operates on plain Python sequences so it can
be unit tested without a database or a retrieval backend.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist


@dataclass(frozen=True)
class McNemarResult:
    """Exact two-sided McNemar test on paired binary outcomes."""

    b: int  # baseline hit, setting miss
    c: int  # baseline miss, setting hit
    n_discordant: int
    p_value: float


def _binomial_cdf_le(k: int, n: int, p: float = 0.5) -> float:
    return sum(math.comb(n, i) * (p**i) * ((1 - p) ** (n - i)) for i in range(k + 1))


def mcnemar_exact(baseline: Sequence[bool], setting: Sequence[bool]) -> McNemarResult:
    """Exact McNemar test (sign test on discordant pairs, p=0.5).

    ``baseline`` and ``setting`` are paired per-case binary outcomes (for
    example Hit@5). The two-sided p-value is ``2 * P(X <= min(b, c))`` for
    ``X ~ Binomial(b + c, 0.5)``, matching ``statsmodels``' exact McNemar test.
    """

    if len(baseline) != len(setting):
        raise ValueError("paired_sequences_must_match_length")
    b = sum(1 for base, other in zip(baseline, setting) if base and not other)
    c = sum(1 for base, other in zip(baseline, setting) if not base and other)
    n = b + c
    if n == 0:
        return McNemarResult(b=b, c=c, n_discordant=0, p_value=1.0)
    k = min(b, c)
    p_value = min(1.0, 2 * _binomial_cdf_le(k, n))
    return McNemarResult(b=b, c=c, n_discordant=n, p_value=p_value)


def minimum_detectable_discordance(alpha: float = 0.05) -> int:
    """Smallest one-directional discordant count reaching ``p < alpha``.

    Independent of the total number of paired cases: McNemar's exact test
    depends only on the discordant counts ``b``/``c``, so the smallest
    all-one-direction discordance (``b = 0``) that reaches significance is a
    fixed number for a given ``alpha``.
    """

    k = 1
    while True:
        p_value = 2 * (0.5**k)
        if p_value < alpha:
            return k
        k += 1


def wilson_ci(successes: int, n: int, *, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""

    if n < 0 or successes < 0 or successes > n:
        raise ValueError("invalid_wilson_inputs")
    if n == 0:
        return (0.0, 1.0)
    z = NormalDist().inv_cdf(0.5 + confidence / 2)
    phat = successes / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    lo = (center - margin) / denom
    hi = (center + margin) / denom
    return (max(0.0, lo), min(1.0, hi))


@dataclass(frozen=True)
class BootstrapResult:
    mean_difference: float
    ci_low: float
    ci_high: float
    resamples: int
    seed: int


def paired_bootstrap_ci(
    differences: Sequence[float],
    *,
    resamples: int = 10000,
    seed: int = 20261006,
    confidence: float = 0.95,
) -> BootstrapResult:
    """Percentile bootstrap CI for the mean of paired per-case differences."""

    n = len(differences)
    if n == 0:
        raise ValueError("empty_differences")
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += differences[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    alpha = (1 - confidence) / 2
    lo_index = max(0, min(resamples - 1, math.floor(alpha * resamples)))
    hi_index = max(0, min(resamples - 1, math.ceil((1 - alpha) * resamples) - 1))
    mean_difference = sum(differences) / n
    return BootstrapResult(
        mean_difference=mean_difference,
        ci_low=means[lo_index],
        ci_high=means[hi_index],
        resamples=resamples,
        seed=seed,
    )


@dataclass(frozen=True)
class WinLossTie:
    win: int
    loss: int
    tie: int


def win_loss_tie(
    baseline: Sequence[float], setting: Sequence[float], *, eps: float = 1e-9
) -> WinLossTie:
    """Per-case win/loss/tie of ``setting`` against ``baseline``."""

    if len(baseline) != len(setting):
        raise ValueError("paired_sequences_must_match_length")
    win = sum(1 for base, other in zip(baseline, setting) if other > base + eps)
    loss = sum(1 for base, other in zip(baseline, setting) if other < base - eps)
    tie = len(baseline) - win - loss
    return WinLossTie(win=win, loss=loss, tie=tie)


__all__ = [
    "BootstrapResult",
    "McNemarResult",
    "WinLossTie",
    "mcnemar_exact",
    "minimum_detectable_discordance",
    "paired_bootstrap_ci",
    "wilson_ci",
    "win_loss_tie",
]
