"""Socio-economic baseline model and daily spike check (SYNTHETIC data)."""

import numpy as np
import pytest

from chhanni_audit.baseline import (
    daily_spike_check,
    design_matrix,
    huber_irls,
    lowo_predict,
    lowo_predict_loop,
    median_polish,
)
from chhanni_audit.pipeline import audit
from chhanni_audit.tamper import TamperSpec, inject


def _linear_problem(n=40, seed=0):
    rng = np.random.default_rng(seed)
    X = np.column_stack([np.ones(n), rng.normal(size=n), rng.uniform(size=n), rng.uniform(size=n)])
    beta = np.array([1.0, 0.8, 0.5, -0.3])
    y = X @ beta + rng.normal(0, 0.05, n)
    return X, y, beta


def test_huber_recovers_coefficients_despite_outliers():
    X, y, beta = _linear_problem()
    y_bad = y.copy()
    y_bad[:3] += 3.0  # 3 gross outliers
    robust = huber_irls(X, y_bad)
    ols = np.linalg.lstsq(X, y_bad, rcond=None)[0]
    assert np.max(np.abs(robust - beta)) < 0.1
    assert np.max(np.abs(robust - beta)) < np.max(np.abs(ols - beta))


def test_batched_lowo_matches_reference_loop():
    X, y, _ = _linear_problem(seed=1)
    y[5] += 2.0
    assert np.allclose(lowo_predict(X, y), lowo_predict_loop(X, y), atol=1e-8)


def test_lowo_prediction_ignores_own_value():
    X, y, _ = _linear_problem(seed=2)
    p1 = lowo_predict(X, y)
    y2 = y.copy()
    y2[7] += 10.0  # a tampered ward cannot pull its own prediction
    p2 = lowo_predict(X, y2)
    assert p2[7] == pytest.approx(p1[7], abs=1e-9)


def test_design_matrix_rejects_nonpositive_population():
    with pytest.raises(ValueError):
        design_matrix(np.array([1, 0]), np.array([0.5, 0.5]), np.array([0.5, 0.5]))


def test_median_polish_recovers_additive_structure():
    rng = np.random.default_rng(3)
    row, col = rng.normal(size=10), rng.normal(size=7)
    M = 2.0 + row[:, None] + col[None, :]
    overall, r, c, resid = median_polish(M)
    assert np.allclose(resid, 0, atol=1e-9)
    assert np.allclose(overall + r[:, None] + c[None, :], M)


def test_baseline_flags_inflated_ward(demo_dataset):
    logs, _ = inject(demo_dataset.logs, TamperSpec(kind="inflate", ward_code="SYN-W05", fraction=1.0, inflate_factor=2.5), rng=1)
    t = audit(demo_dataset.wards, logs, localise=False).ward_table.set_index("ward_code")
    assert t.loc["SYN-W05", "flag_baseline"]
    assert not t.drop(index="SYN-W05")["flag_baseline"].any()


def test_baseline_does_not_flag_clean_data(demo_dataset):
    t = audit(demo_dataset.wards, demo_dataset.logs, localise=False).ward_table
    assert not t["flag_baseline"].any() and not t["flag_baseline_daily"].any()


def test_daily_spike_flagged_on_correct_day(demo_dataset):
    ward = "SYN-W10"
    n_ward = int((demo_dataset.logs["ward_code"] == ward).sum())
    spec = TamperSpec(kind="fabricate", ward_code=ward, mode="add", n_entries=int(0.15 * n_ward), dates=("2026-07-20",))
    logs, _ = inject(demo_dataset.logs, spec, rng=2)
    res = audit(demo_dataset.wards, logs, localise=True)
    t = res.ward_table.set_index("ward_code")
    assert t.loc[ward, "flag_baseline_daily"]
    day_flags = [f for f in res.flag_records() if f["check_type"] == "baseline_daily" and f["details"]["level"] == "ward_day"]
    assert {f["period_start"] for f in day_flags if f["ward_code"] == ward} == {"2026-07-20"}


def test_daily_scale_model_handles_heteroscedastic_wards():
    rng = np.random.default_rng(4)
    trips = np.repeat(np.array([30, 300])[:, None], 28, axis=1).repeat(10, axis=0)  # 20 wards
    sd = np.sqrt(0.01 + 2.0 / trips)
    daily = 100 * np.exp(rng.normal(0, sd))
    r = daily_spike_check(daily, trips)
    small, large = r["ward_scale"][:10], r["ward_scale"][10:]
    assert small.mean() > 1.5 * large.mean()
