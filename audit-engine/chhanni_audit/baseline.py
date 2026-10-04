"""Socio-economic baseline model: expected generation per ward from population /
income / sanitation proxies, and divergence checks against it.

Two checks (both in the "baseline" layer):

``baseline`` (ward-period level)
    Model: log(mean daily reported tonnage) = b0 + b1*log(population)
    + b2*income_proxy + b3*sanitation_proxy, fitted ACROSS WARDS.
    To stop tampered data from training the model to accept itself, each
    ward's expected value comes from a Huber-robust fit on all OTHER wards
    (leave-one-ward-out, LOWO): the ward's own (possibly tampered) number never
    enters its own prediction, and tampered peers are down-weighted by the
    Huber loss. The residual scale is a robust MAD of the LOWO residuals.
    One-sided upper test (Student t, df = n_wards - n_params): the audit exists
    to stop over-issued credits, so over-reporting is the a-priori hypothesis.
    Under-reporting is reported as a z-score in the metrics but not flagged.

``baseline_daily`` (ward-day level)
    Detects concentrated spikes: ward-day log totals are decomposed by Tukey
    median polish into ward level + city-wide day pattern + residual; each
    residual is scaled by a per-ward scale from a variance model fitted across
    wards (var = a + b / trips-per-day, because a day's total is a sum of trip
    entries and wards with fewer trips are noisier); a ward's p-value is the
    Bonferroni-adjusted minimum over its days (one-sided, upper). This compares
    a day with the ward's own robust level and the city's day-of-period pattern,
    so it catches a batch of phantom entries on one day but not a level shift
    spread evenly over the window (the ward-period check covers that).

Honest limits:
* On synthetic data the generator uses this same log-linear form, so the model
  partly recovers it by construction. Real generation may depend on proxies in
  other ways (or on things the proxies don't capture).
* Sensitivity is bounded by unexplained ward-to-ward variation (the synthetic
  `ward_effect_sigma`). The baseline can only flag over-reporting that is large
  relative to that spread; small inflation is invisible to it.
* The proxies themselves are synthetic here; with real data, their quality
  would bound the model's quality.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

HUBER_C = 1.345  # standard Huber tuning constant (95% efficiency under normal errors)
MAD_TO_SD = 1.4826


def daily_totals(tonnage: np.ndarray, ward_idx: np.ndarray, day_idx: np.ndarray, n_wards: int, n_days: int) -> np.ndarray:
    """Matrix (n_wards, n_days) of summed reported tonnage."""
    flat = ward_idx * n_days + day_idx
    return np.bincount(flat, weights=np.asarray(tonnage, dtype=float), minlength=n_wards * n_days).reshape(n_wards, n_days)


def design_matrix(population: np.ndarray, income: np.ndarray, sanitation: np.ndarray) -> np.ndarray:
    """[1, log(population), income_proxy, sanitation_proxy]."""
    pop = np.asarray(population, dtype=float)
    if np.any(pop <= 0):
        raise ValueError("population must be positive")
    return np.column_stack([np.ones_like(pop), np.log(pop), np.asarray(income, float), np.asarray(sanitation, float)])


def huber_irls(X: np.ndarray, y: np.ndarray, c: float = HUBER_C, max_iter: int = 200, tol: float = 1e-10) -> np.ndarray:
    """Huber M-estimate of linear coefficients by iteratively reweighted least squares,
    with the residual scale re-estimated by MAD at each step."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    for _ in range(max_iter):
        r = y - X @ beta
        scale = MAD_TO_SD * np.median(np.abs(r - np.median(r)))
        if scale <= 1e-12:
            break
        u = np.abs(r) / scale
        w = np.where(u <= c, 1.0, c / np.maximum(u, 1e-300))
        sw = np.sqrt(w)
        new, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
        if np.max(np.abs(new - beta)) <= tol * (1.0 + np.max(np.abs(beta))):
            beta = new
            break
        beta = new
    return beta


def lowo_predict_loop(X: np.ndarray, y: np.ndarray, c: float = HUBER_C) -> np.ndarray:
    """Reference leave-one-ward-out predictions (one Huber fit per ward). Slow; used in tests
    to check `lowo_predict`."""
    n = len(y)
    pred = np.empty(n)
    keep = np.ones(n, dtype=bool)
    for i in range(n):
        keep[i] = False
        pred[i] = X[i] @ huber_irls(X[keep], y[keep], c)
        keep[i] = True
    return pred


def _median_rows_excluding_diag(A: np.ndarray) -> np.ndarray:
    """Median of each row of a square matrix, ignoring the diagonal element."""
    n = A.shape[0]
    S = A.copy()
    S[np.arange(n), np.arange(n)] = np.inf
    S.sort(axis=1)
    m = n - 1
    if m % 2:
        return S[:, m // 2]
    return 0.5 * (S[:, m // 2 - 1] + S[:, m // 2])


def lowo_predict(X: np.ndarray, y: np.ndarray, c: float = HUBER_C, max_iter: int = 200, tol: float = 1e-10) -> np.ndarray:
    """Leave-one-ward-out predictions: row i is predicted from a Huber fit on all OTHER rows.

    All n folds are solved together (same IRLS as `huber_irls`, batched): fold i gives
    observation i zero weight, so its own value never enters its own prediction."""
    n, k = X.shape
    eye = np.eye(n, dtype=bool)
    W = np.where(eye, 0.0, 1.0)  # (fold, obs)
    beta = np.linalg.solve(np.einsum("fo,oi,oj->fij", W, X, X), np.einsum("fo,oi,o->fi", W, X, y)[..., None])[..., 0]
    for _ in range(max_iter):
        R = y[None, :] - beta @ X.T  # (fold, obs)
        med = _median_rows_excluding_diag(R)
        scale = MAD_TO_SD * _median_rows_excluding_diag(np.abs(R - med[:, None]))
        safe = np.where(scale > 1e-12, scale, 1.0)
        U = np.abs(R) / safe[:, None]
        Wn = np.where(U <= c, 1.0, c / np.maximum(U, 1e-300))
        Wn = np.where(scale[:, None] > 1e-12, Wn, W)  # perfect-fit folds keep current weights
        Wn[eye] = 0.0
        new = np.linalg.solve(np.einsum("fo,oi,oj->fij", Wn, X, X), np.einsum("fo,oi,o->fi", Wn, X, y)[..., None])[..., 0]
        done = np.max(np.abs(new - beta)) <= tol * (1.0 + np.max(np.abs(beta)))
        beta = new
        if done:
            break
    return np.einsum("ij,ij->i", X, beta)


def period_check(daily: np.ndarray, X: np.ndarray, c: float = HUBER_C, direction: str = "upper") -> dict[str, np.ndarray]:
    """Ward-period divergence from the socio-economic baseline (LOWO + Huber)."""
    n_wards, n_days = daily.shape
    observed = daily.sum(axis=1) / n_days
    valid = observed > 0
    out = {k: np.full(n_wards, np.nan) for k in ("observed_mean_t", "predicted_mean_t", "log_ratio", "z", "p_value")}
    out["observed_mean_t"] = observed
    n_params = X.shape[1]
    if valid.sum() < n_params + 3:
        out["scale"] = np.nan
        out["df"] = 0
        return out
    y = np.log(observed[valid])
    pred = lowo_predict(X[valid], y, c)
    resid = y - pred
    scale = MAD_TO_SD * np.median(np.abs(resid))
    scale = scale if scale > 1e-12 else np.nan
    z = resid / scale
    df = int(valid.sum() - n_params)
    if direction == "upper":
        p = stats.t.sf(z, df)
    elif direction == "two_sided":
        p = 2.0 * stats.t.sf(np.abs(z), df)
    else:
        raise ValueError("direction must be 'upper' or 'two_sided'")
    out["predicted_mean_t"][valid] = np.exp(pred)
    out["log_ratio"][valid] = resid
    out["z"][valid] = z
    out["p_value"][valid] = p
    out["scale"] = scale
    out["df"] = df
    return out


def median_polish(M: np.ndarray, n_iter: int = 10) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """Tukey median polish of a (rows, cols) matrix, NaN-aware.
    Returns (overall, row_effects, col_effects, residuals)."""
    R = np.array(M, dtype=float)
    row = np.zeros(R.shape[0])
    col = np.zeros(R.shape[1])
    overall = 0.0
    median = np.nanmedian if np.isnan(R).any() else np.median
    for _ in range(n_iter):
        rmed = median(R, axis=1)
        rmed = np.where(np.isfinite(rmed), rmed, 0.0)
        R -= rmed[:, None]
        row += rmed
        cm = np.median(col)
        col -= cm
        overall += cm
        cmed = median(R, axis=0)
        cmed = np.where(np.isfinite(cmed), cmed, 0.0)
        R -= cmed[None, :]
        col += cmed
        rm = np.median(row)
        row -= rm
        overall += rm
    return overall, row, col, R


def ward_day_scales(resid: np.ndarray, entries_per_day: np.ndarray, c: float = HUBER_C) -> tuple[np.ndarray, dict[str, float]]:
    """Per-ward residual scale from a variance model fitted across wards.

    A day's reported total is a sum of K trip entries, so the variance of its log is
    roughly a + b / K (day-level noise + trip-sampling noise). Wards with fewer trips per
    day are noisier; one pooled scale would over-flag them. We fit
    MAD_w^2 = a + b / K_w by Huber regression across wards (K_w = the ward's median
    entries per day), which is stable with 28 days per ward and robust to a few tampered
    wards, and fall back to the pooled MAD if the fit is degenerate."""
    mad = MAD_TO_SD * np.nanmedian(np.abs(resid), axis=1)
    pooled = MAD_TO_SD * np.nanmedian(np.abs(resid))
    ok = np.isfinite(mad) & np.isfinite(entries_per_day) & (entries_per_day > 0)
    info = {"model": "var = a + b / median_entries_per_day", "a": float("nan"), "b": float("nan"), "pooled_scale": float(pooled)}
    scales = np.full(resid.shape[0], pooled)
    if ok.sum() >= 5:
        X = np.column_stack([np.ones(ok.sum()), 1.0 / entries_per_day[ok]])
        a, b = huber_irls(X, mad[ok] ** 2, c)
        info["a"], info["b"] = float(a), float(b)
        var = a + b / np.where(entries_per_day > 0, entries_per_day, np.nan)
        floor = (0.25 * pooled) ** 2
        scales = np.where(np.isfinite(var), np.sqrt(np.maximum(var, floor)), pooled)
    return scales, info


def daily_spike_check(daily: np.ndarray, entries: np.ndarray | None = None, n_iter: int = 10) -> dict[str, np.ndarray]:
    """Ward-day spike check (one-sided upper), Bonferroni over each ward's days.

    `entries` (same shape as `daily`) is the number of log entries per ward-day; it sets
    each ward's residual scale (see `ward_day_scales`). Without it a pooled scale is used."""
    with np.errstate(divide="ignore"):
        L = np.where(daily > 0, np.log(np.where(daily > 0, daily, 1.0)), np.nan)
    overall, row, col, resid = median_polish(L, n_iter)
    if entries is not None:
        k = np.nanmedian(np.where(daily > 0, np.asarray(entries, dtype=float), np.nan), axis=1)
        scales, info = ward_day_scales(resid, k)
    else:
        pooled = MAD_TO_SD * np.nanmedian(np.abs(resid))
        scales, info = np.full(resid.shape[0], pooled), {"model": "pooled", "pooled_scale": float(pooled)}
    scales = np.where(scales > 1e-12, scales, np.nan)
    z = resid / scales[:, None]
    p_day = np.where(np.isfinite(z), stats.norm.sf(z), np.nan)
    n_valid = np.sum(np.isfinite(p_day), axis=1)
    with np.errstate(invalid="ignore"):
        min_p = np.nanmin(np.where(np.isfinite(p_day), p_day, np.inf), axis=1)
    p_ward = np.where(n_valid > 0, np.minimum(1.0, n_valid * min_p), np.nan)
    p_day_adj = np.minimum(1.0, p_day * n_valid[:, None])
    expected = np.exp(overall + row[:, None] + col[None, :])
    max_idx = np.argmax(np.where(np.isfinite(z), z, -np.inf), axis=1)
    return {
        "z": z,
        "p_day": p_day,
        "p_day_adjusted": p_day_adj,
        "expected_total_t": expected,
        "p_value": p_ward,
        "max_z": z[np.arange(z.shape[0]), max_idx],
        "max_z_day": max_idx,
        "ward_scale": scales,
        "scale_model": info,
        "n_valid_days": n_valid,
    }
