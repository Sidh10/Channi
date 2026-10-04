"""Benford's Law tests. Samples here are SYNTHETIC (drawn from known distributions)."""

import numpy as np
import pytest

from chhanni_audit.benford import (
    MIN_N_ABSOLUTE,
    benford_by_group,
    benford_conformity,
    benford_probs,
    benford_test,
    d_star_max,
    leading_digits,
    min_n_for_chi2,
    peer_homogeneity_pvalues,
)


def benford_sample(n, seed, decades=4):
    """Exactly-Benford sample: 10**Uniform over whole decades."""
    return 10 ** np.random.default_rng(seed).uniform(0, decades, n)


def test_benford_probabilities():
    digits, p = benford_probs(1)
    assert list(digits) == list(range(1, 10))
    assert p.sum() == pytest.approx(1.0)
    assert p[0] == pytest.approx(np.log10(2))
    d2, p2 = benford_probs(2)
    assert d2[0] == 10 and d2[-1] == 99 and p2.sum() == pytest.approx(1.0)


@pytest.mark.parametrize(
    "value,first,two",
    [(0.3, 3, 30), (0.003, 3, 30), (1000.0, 1, 10), (9.999, 9, 99), (0.1, 1, 10), (123.456, 1, 12), (7e-5, 7, 70), (0.29, 2, 29)],
)
def test_leading_digits_edge_cases(value, first, two):
    assert leading_digits([value], 1)[0] == first
    assert leading_digits([value], 2)[0] == two


def test_leading_digits_excludes_nonpositive():
    d = leading_digits([0.0, -5.0, np.nan, np.inf, 5.0])
    assert list(d) == [-1, -1, -1, -1, 5]


def test_passes_on_benford_sample():
    r = benford_test(benford_sample(5000, 1))
    assert r.chi2_valid and r.p_value > 0.01
    assert r.d_star < 0.03


def test_false_positive_rate_on_benford_samples_is_near_alpha():
    rejections = sum(benford_test(benford_sample(2000, s)).p_value < 0.01 for s in range(300))
    assert rejections / 300 <= 0.03  # nominal 0.01; generous bound for 300 draws


def test_fails_on_uniform_data():
    r = benford_test(np.random.default_rng(2).uniform(1, 10, 3000))
    assert r.p_value < 1e-10
    assert benford_conformity(np.random.default_rng(2).uniform(1, 1000, 3000)).verdict == "does_not_conform"


def test_fails_on_rounded_data():
    x = benford_sample(5000, 3)
    exponent = np.floor(np.log10(x))
    rounded = np.round(x / 10**exponent) * 10**exponent  # 1 significant figure
    assert benford_test(rounded).p_value < 1e-10


def test_d_star_range():
    _, p = benford_probs(1)
    assert d_star_max(p) == pytest.approx(1.0363, abs=1e-3)
    exact = benford_test(benford_sample(200000, 4))
    assert 0 <= exact.d_star < 0.01
    all_nines = benford_test([9.0, 90.0, 0.9, 9.5] * 50)
    assert all_nines.d_star == pytest.approx(1.0)
    uniform = benford_test(np.random.default_rng(5).uniform(1, 10, 20000))
    assert 0.15 < uniform.d_star < 0.35


def test_min_n_for_chi2():
    assert min_n_for_chi2(1) == 110
    assert min_n_for_chi2(2) == 1146  # 5 / log10(1 + 1/99) = 1145.5


def test_conformity_not_applicable_for_narrow_span():
    rng = np.random.default_rng(6)
    daily_totals = rng.normal(300, 20, 200)  # e.g. one ward's daily totals
    r = benford_conformity(daily_totals)
    assert r.verdict == "not_applicable"
    assert any("span" in reason for reason in r.reasons)


def test_conformity_not_applicable_for_too_few_values():
    r = benford_conformity(benford_sample(MIN_N_ABSOLUTE - 1, 7))
    assert r.verdict == "not_applicable"


def test_conformity_small_n_uses_monte_carlo_and_reports_power():
    r = benford_conformity(list(benford_sample(36, 8)))  # a plain list, like a state-wise table
    assert r.method == "monte_carlo_chi2"
    assert r.verdict in ("conforms", "does_not_conform")
    assert 0 < r.power_vs_uniform_digits < 0.8
    assert any("low power" in c for c in r.caveats)


def test_conformity_counts_excluded_values_and_is_deterministic():
    vals = [0, -3, float("nan")] + list(benford_sample(500, 9))
    a, b = benford_conformity(vals), benford_conformity(vals)
    assert a.n_excluded_nonpositive_or_nonfinite == 3 and a.n_used == 500
    assert a.to_dict() == b.to_dict()
    assert a.method == "asymptotic_chi2"


def test_by_group_matches_single_test():
    x = np.concatenate([benford_sample(3000, 10), np.random.default_rng(11).uniform(1, 100, 3000)])
    g = np.repeat([0, 1], 3000)
    r = benford_by_group(x, g, 2)
    assert r["p_value"][0] == pytest.approx(benford_test(x[:3000]).p_value)
    assert r["p_value"][1] < 1e-10


def test_peer_homogeneity_matches_scipy():
    from scipy import stats

    counts = np.random.default_rng(12).integers(5, 80, (5, 9))
    p = peer_homogeneity_pvalues(counts)["p_value"]
    for i in range(5):
        table = np.vstack([counts[i], counts.sum(axis=0) - counts[i]])
        assert p[i] == pytest.approx(stats.chi2_contingency(table, correction=False)[1])
