"""Roundness test and BH/confidence scoring."""

import numpy as np
import pytest

from chhanni_audit.roundness import roundness_by_group, to_kg
from chhanni_audit.scoring import bh_adjust, combine, wilson_interval


def test_to_kg():
    assert list(to_kg(np.array([1.0, 0.001, 2.3456]))) == [1000, 1, 2346]


def test_roundness_flags_rounded_group_only():
    rng = np.random.default_rng(0)
    kg = rng.integers(50, 20000, 20000)
    g = np.repeat(np.arange(10), 2000)
    kg[:100] = (kg[:100] // 1000 + 1) * 1000  # 5% of group 0 are whole tonnes
    r = roundness_by_group(kg, g, 10)
    assert r["p_value"][0] < 1e-20
    assert r["best_resolution_kg"][0] == 1000
    assert np.all(r["p_value"][1:] > 1e-3)


def test_roundness_null_is_not_anticonservative():
    rng = np.random.default_rng(1)
    ps = []
    for _ in range(100):
        kg = rng.integers(50, 20000, 8000)
        ps.extend(roundness_by_group(kg, np.repeat(np.arange(8), 1000), 8)["p_value"])
    assert np.mean(np.array(ps) < 0.01) <= 0.02


def test_bh_adjust_known_values():
    p = np.array([0.01, 0.04, 0.03, 0.005, np.nan])
    q = bh_adjust(p)
    assert np.isnan(q[-1])
    assert q[:4] == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_combine_flags_and_confidence():
    p = np.array([[1e-6, 0.5], [0.4, 0.9], [np.nan, 0.8]])
    c = combine(p, 0.01)
    assert list(c["ward_flagged"]) == [True, False, False]
    assert c["confidence"][0] <= 0.01
    assert np.all((c["confidence"] >= 0) & (c["confidence"] <= 1))
    assert c["n_tests"] == 5


def test_wilson_interval():
    lo, hi = wilson_interval(100, 100)
    assert 0.96 < lo < 0.97 and hi == 1.0
