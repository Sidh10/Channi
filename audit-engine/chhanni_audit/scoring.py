"""Combine per-ward check p-values into flags and a confidence score.

Decision rule (fixed a priori, never tuned on evaluation data):
* every applicable (ward, check) p-value enters ONE Benjamini-Hochberg
  procedure (joint across all wards and all checks) at q = alpha (0.01);
* a (ward, check) is flagged when its BH-adjusted p-value (q-value) <= alpha;
* a ward is flagged when any of its checks is flagged.

confidence_score = the smallest q-value across the ward's checks, in [0, 1]:
1 means no evidence of inconsistency; <= alpha means flagged. It ranks wards by
strength of evidence. It is NOT the probability that the ward's data is genuine.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

CHECKS: tuple[str, ...] = ("benford", "roundness", "baseline", "baseline_daily")
LAYERS: dict[str, tuple[str, ...]] = {
    "digit_layer": ("benford", "roundness"),
    "baseline_layer": ("baseline", "baseline_daily"),
}


def bh_adjust(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values over all finite entries of `p` (any shape).
    NaN entries are not tests and stay NaN."""
    p = np.asarray(p, dtype=float)
    flat = p.ravel()
    ok = np.isfinite(flat)
    q = np.full(flat.shape, np.nan)
    m = int(ok.sum())
    if m == 0:
        return q.reshape(p.shape)
    vals = flat[ok]
    order = np.argsort(vals, kind="stable")
    ranked = vals[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    adj = np.clip(adj, 0.0, 1.0)
    out = np.empty(m)
    out[order] = adj
    q[np.flatnonzero(ok)] = out
    return q.reshape(p.shape)


def combine(p_matrix: np.ndarray, alpha: float) -> dict[str, np.ndarray]:
    """p_matrix: (n_wards, n_checks). Returns q-values, per-check flags, ward flags, confidence."""
    q = bh_adjust(p_matrix)
    flagged_check = np.where(np.isfinite(q), q <= alpha, False)
    ward_flagged = flagged_check.any(axis=1)
    with np.errstate(invalid="ignore"):
        conf = np.nanmin(np.where(np.isfinite(q), q, np.inf), axis=1)
    conf = np.where(np.isfinite(conf), conf, 1.0)
    return {
        "q": q,
        "flagged_check": flagged_check,
        "ward_flagged": ward_flagged,
        "confidence": np.clip(conf, 0.0, 1.0),
        "n_tests": int(np.isfinite(p_matrix).sum()),
    }


def wilson_interval(successes: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (float("nan"), float("nan"))
    z = stats.norm.ppf(0.5 + level / 2.0)
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * np.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (float(max(0.0, centre - half)), float(min(1.0, centre + half)))
