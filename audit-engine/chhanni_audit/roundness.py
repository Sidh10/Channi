"""Roundness (trailing-zero / last-digit) test — part of the digit-analysis layer.

Why it exists: Benford's first-digit test is weak against ROUNDED entries
(rounding 4.837 t to 5 t mostly keeps the leading digit). Rounding instead
shows up at the other end of the number: too many values that are exact
multiples of 10 kg / 100 kg / 1 t.

Test: values are converted to integer kilograms (tonnage is stored with 3
decimals). For each resolution r in (10, 100, 1000) kg, an entry is
"eligible" if it is at least r kg and "round" if it is an exact multiple of r.
Each ward's round-entry rate is compared with the rate in all OTHER wards by a
one-sided Fisher exact test (hypergeometric tail, alternative: this ward is
rounder). The ward's roundness p-value is the Bonferroni-combined minimum over
the resolutions: min(1, R * min_r p_r).

Comparing against the other wards (rather than assuming 1-in-10 per trailing
zero) makes the test self-calibrating to the recording resolution of the data
source: if a real weighbridge logs in 10 kg steps, every ward is equally
"round" at r = 10 kg and that resolution carries no signal. Tampering in
other wards raises the reference rate, which makes the test more conservative,
never less. Limitation: if EVERY ward rounds identically, this test cannot see
it (Benford and the baseline still can).
"""

from __future__ import annotations

import numpy as np
from scipy import stats

DEFAULT_RESOLUTIONS_KG: tuple[int, ...] = (10, 100, 1000)


def to_kg(tonnage: np.ndarray) -> np.ndarray:
    """Tonnes (3 decimals) -> integer kilograms."""
    return np.rint(np.asarray(tonnage, dtype=float) * 1000.0).astype(np.int64)


def roundness_counts(
    kg: np.ndarray, group_idx: np.ndarray, n_groups: int, resolutions_kg: tuple[int, ...] = DEFAULT_RESOLUTIONS_KG
) -> tuple[np.ndarray, np.ndarray]:
    """(eligible, round) counts with shape (n_groups, len(resolutions))."""
    kg = np.asarray(kg, dtype=np.int64)
    eligible = np.empty((n_groups, len(resolutions_kg)), dtype=np.int64)
    rounded = np.empty_like(eligible)
    for j, r in enumerate(resolutions_kg):
        elig = kg >= r
        rnd = elig & (kg % r == 0)
        eligible[:, j] = np.bincount(group_idx[elig], minlength=n_groups)
        rounded[:, j] = np.bincount(group_idx[rnd], minlength=n_groups)
    return eligible, rounded


def fisher_greater(round_w: np.ndarray, eligible_w: np.ndarray, round_total: np.ndarray, eligible_total: np.ndarray) -> np.ndarray:
    """One-sided Fisher exact p-value that a group's round rate exceeds the rest.

    Under H0 the group's round count is hypergeometric: population = all eligible
    entries, successes = all round entries, draws = this group's eligible entries.
    """
    p = stats.hypergeom.sf(round_w - 1, eligible_total, round_total, eligible_w)
    return np.where(eligible_w > 0, np.clip(p, 0.0, 1.0), 1.0)


def roundness_by_group(
    kg: np.ndarray, group_idx: np.ndarray, n_groups: int, resolutions_kg: tuple[int, ...] = DEFAULT_RESOLUTIONS_KG
) -> dict[str, np.ndarray]:
    """Per-group roundness test. Returns per-resolution counts/rates/p-values and the
    combined p-value (Bonferroni over resolutions)."""
    eligible, rounded = roundness_counts(kg, group_idx, n_groups, resolutions_kg)
    elig_tot = eligible.sum(axis=0, keepdims=True)
    round_tot = rounded.sum(axis=0, keepdims=True)
    p_by_res = fisher_greater(rounded, eligible, np.broadcast_to(round_tot, rounded.shape), np.broadcast_to(elig_tot, eligible.shape))
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = np.where(eligible > 0, rounded / np.maximum(eligible, 1), np.nan)
        rest_elig = elig_tot - eligible
        ref_rate = np.where(rest_elig > 0, (round_tot - rounded) / np.maximum(rest_elig, 1), np.nan)
    n_res = len(resolutions_kg)
    best = np.argmin(p_by_res, axis=1)
    p_combined = np.minimum(1.0, n_res * p_by_res.min(axis=1))
    has_data = eligible.sum(axis=1) > 0
    return {
        "eligible": eligible,
        "round": rounded,
        "rate": rate,
        "reference_rate": ref_rate,
        "p_by_resolution": p_by_res,
        "best_resolution_kg": np.asarray(resolutions_kg)[best],
        "p_value": np.where(has_data, p_combined, np.nan),
    }
