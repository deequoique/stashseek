from __future__ import annotations

import math

from evals.meeting_gold.stats import (
    mcnemar_exact,
    minimum_detectable_discordance,
    paired_bootstrap_ci,
    wilson_ci,
    win_loss_tie,
)


def test_mcnemar_exact_known_value() -> None:
    # b=1, c=9 (10 discordant pairs); known two-sided exact p-value from
    # statsmodels' exact McNemar test: 2 * sum_{i=0}^{1} C(10, i) * 0.5**10.
    baseline = [True] + [False] * 9 + [True] * 5 + [False] * 5
    setting = [False] + [True] * 9 + [True] * 5 + [False] * 5
    result = mcnemar_exact(baseline, setting)
    assert result.b == 1
    assert result.c == 9
    assert result.n_discordant == 10
    assert math.isclose(result.p_value, 0.021484375, rel_tol=1e-9)


def test_mcnemar_exact_no_discordant_pairs_is_not_significant() -> None:
    baseline = [True, False, True, False]
    setting = [True, False, True, False]
    result = mcnemar_exact(baseline, setting)
    assert result.n_discordant == 0
    assert result.p_value == 1.0


def test_mcnemar_exact_symmetric_in_b_and_c() -> None:
    baseline = [True] * 3 + [False] * 1
    setting = [False] * 3 + [True] * 1
    a = mcnemar_exact(baseline, setting)
    b = mcnemar_exact(setting, baseline)
    assert a.p_value == b.p_value
    assert a.b == b.c and a.c == b.b


def test_mcnemar_exact_rejects_mismatched_lengths() -> None:
    import pytest

    with pytest.raises(ValueError):
        mcnemar_exact([True, False], [True])


def test_wilson_ci_known_bounds() -> None:
    # 8/28 successes; Wilson 95% CI computed independently.
    lo, hi = wilson_ci(8, 28)
    assert 0.0 < lo < 8 / 28 < hi < 1.0
    assert math.isclose(lo, 0.15253996056669095, rel_tol=1e-9)
    assert math.isclose(hi, 0.4705928843329571, rel_tol=1e-9)


def test_wilson_ci_degenerate_cases() -> None:
    assert wilson_ci(0, 0) == (0.0, 1.0)
    lo, hi = wilson_ci(0, 10)
    assert lo == 0.0
    assert hi > 0.0
    lo, hi = wilson_ci(10, 10)
    assert hi == 1.0
    assert lo < 1.0


def test_paired_bootstrap_ci_is_deterministic_given_seed() -> None:
    differences = [0.1, -0.2, 0.3, 0.0, 0.2, -0.1]
    first = paired_bootstrap_ci(differences, resamples=2000, seed=20261006)
    second = paired_bootstrap_ci(differences, resamples=2000, seed=20261006)
    assert first == second
    assert math.isclose(first.mean_difference, sum(differences) / len(differences))
    assert first.ci_low <= first.mean_difference <= first.ci_high


def test_paired_bootstrap_ci_zero_variance_collapses_interval() -> None:
    differences = [0.25, 0.25, 0.25]
    result = paired_bootstrap_ci(differences, resamples=500, seed=1)
    assert result.ci_low == result.ci_high == 0.25


def test_win_loss_tie_counts() -> None:
    baseline = [0.1, 0.5, 0.3, 0.2]
    setting = [0.2, 0.5, 0.1, 0.2]
    result = win_loss_tie(baseline, setting)
    assert result.win == 1
    assert result.loss == 1
    assert result.tie == 2


def test_minimum_detectable_discordance_is_six_at_alpha_05() -> None:
    # 2**(1-6) = 0.03125 < 0.05 <= 2**(1-5) = 0.0625.
    assert minimum_detectable_discordance(0.05) == 6
