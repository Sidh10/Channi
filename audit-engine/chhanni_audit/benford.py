"""Benford's Law digit tests: chi-square goodness of fit and normalised Euclidean distance D*.

What this test can and cannot say (read before using it)
--------------------------------------------------------
* Applicability. Benford's Law is only expected for data spanning several
  orders of magnitude (multiplicative, scale-free processes). A single ward's
  DAILY TOTALS cluster within a fraction of a decade, so their first digits
  are not expected to follow Benford even when genuine. This engine therefore
  applies Benford per ward to TRIP-LEVEL entries pooled over the audit window.
  `benford_conformity` returns ``not_applicable`` when the P5-P95 span of a
  series is below `min_span_decades` (default 1 decade) or n < 20. In the
  per-ward audit a ward is not applicable if its span is < 1 decade or any
  expected digit count is < 5 (chi-square validity).
* Small n. `benford_conformity` simulates the chi-square p-value (Monte Carlo
  multinomial draws at the same n) when expected counts are < 5, and always
  reports its power against uniform leading digits, because at n of a few
  dozen even grossly non-Benford digits are often not detectable.
* By construction. The synthetic clean trip loads follow Benford exactly (see
  synthetic.py), so a passing result on clean synthetic data shows only that
  "the detector works IF clean data conforms". Whether real Indian municipal
  tonnage data conforms is an open empirical question; point
  `benford_conformity` at a real series to find out, and report the answer
  whichever way it goes.
* Large n. With tens of thousands of values, chi-square flags deviations too
  small to matter. D* (normalised Euclidean distance from the Benford
  proportions, 0 = exact Benford, 1 = maximal deviation) is reported as an
  effect size next to its own Monte Carlo null quantiles at the same n. No
  external "acceptable D*/MAD" threshold is used, because none is verified in
  docs/data-sources.md.
* Approximate conformity. If genuine data is only approximately Benford, the
  per-ward goodness-of-fit test flags genuine wards at large n. The optional
  ``reference='peer'`` mode tests each ward's first digits against the pooled
  digits of all OTHER wards (chi-square homogeneity) instead of against
  Benford's Law; it assumes genuine wards share one digit distribution.
* Scale invariance. Multiplying genuine values by a constant (inflation) keeps
  them Benford, so this test is NOT expected to catch over-reporting by
  inflation; that is the baseline model's job.
* A deviation is a reason to inspect, not proof of fraud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import stats

DEFAULT_ALPHA = 0.01
DEFAULT_MIN_EXPECTED = 5.0
DEFAULT_MIN_SPAN_DECADES = 1.0


def benford_probs(n_digits: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """(digits, Benford probabilities) for first (n_digits=1) or first-two (2) digits."""
    if n_digits not in (1, 2):
        raise ValueError("n_digits must be 1 or 2")
    lo = 10 ** (n_digits - 1)
    digits = np.arange(lo, 10 * lo)
    return digits, np.log10(1.0 + 1.0 / digits)


def d_star_max(probs: np.ndarray) -> float:
    """Largest possible Euclidean distance from `probs` (all mass on the least likely digit)."""
    p = np.asarray(probs, dtype=float)
    return float(np.sqrt(np.sum(p**2) - 2.0 * p.min() + 1.0))


def leading_digits(values: Any, n_digits: int = 1) -> np.ndarray:
    """First `n_digits` significant digits of each value as int; -1 where the value
    is non-positive or non-finite (those are excluded from Benford tests).

    The mantissa is rounded to 10 decimals before truncation so float artefacts
    such as 0.3 / 0.1 == 2.9999999999999996 don't move a value to the wrong digit.
    """
    if n_digits not in (1, 2):
        raise ValueError("n_digits must be 1 or 2")
    x = np.asarray(values, dtype=float)
    out = np.full(x.shape, -1, dtype=np.int64)
    ok = np.isfinite(x) & (x > 0)
    if not ok.any():
        return out
    v = x[ok]
    exponent = np.floor(np.log10(v))
    mantissa = np.round(v / np.power(10.0, exponent), 10)
    high = mantissa >= 10.0
    mantissa[high] /= 10.0
    low = mantissa < 1.0
    mantissa[low] *= 10.0
    out[ok] = np.floor(np.round(mantissa * 10 ** (n_digits - 1), 8)).astype(np.int64)
    return out


def chi2_and_distances(counts: np.ndarray, probs: np.ndarray) -> dict[str, np.ndarray]:
    """Vectorised over rows: counts has shape (..., k). Returns n, chi2, p_value, d_star, mad, min_expected."""
    counts = np.asarray(counts, dtype=float)
    n = counts.sum(axis=-1)
    expected = n[..., None] * probs
    with np.errstate(divide="ignore", invalid="ignore"):
        chi2 = np.where(n > 0, np.sum((counts - expected) ** 2 / np.where(expected > 0, expected, 1.0), axis=-1), np.nan)
        props = counts / np.where(n > 0, n, 1.0)[..., None]
    df = probs.size - 1
    p_value = np.where(n > 0, stats.chi2.sf(chi2, df), np.nan)
    dist = np.sqrt(np.sum((props - probs) ** 2, axis=-1))
    d_star = np.where(n > 0, dist / d_star_max(probs), np.nan)
    mad = np.where(n > 0, np.mean(np.abs(props - probs), axis=-1), np.nan)
    return {
        "n": n,
        "chi2": chi2,
        "df": np.full(n.shape, df),
        "p_value": p_value,
        "d_star": d_star,
        "mad": mad,
        "min_expected": n * probs.min(),
    }


@dataclass(frozen=True)
class BenfordTestResult:
    n_digits: int
    n: int
    digits: list[int]
    observed_counts: list[int]
    expected_counts: list[float]
    observed_props: list[float]
    benford_props: list[float]
    chi2: float
    df: int
    p_value: float
    d_star: float
    mad: float
    chi2_valid: bool  # every expected count >= min_expected

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def benford_test(values: Any, n_digits: int = 1, min_expected: float = DEFAULT_MIN_EXPECTED) -> BenfordTestResult:
    """Chi-square goodness of fit to Benford, plus D* and MAD, on the positive finite values."""
    digits, probs = benford_probs(n_digits)
    d = leading_digits(values, n_digits)
    d = d[d >= 0]
    counts = np.bincount(d - digits[0], minlength=digits.size)[: digits.size] if d.size else np.zeros(digits.size, int)
    r = chi2_and_distances(counts, probs)
    n = int(counts.sum())
    return BenfordTestResult(
        n_digits=n_digits,
        n=n,
        digits=[int(x) for x in digits],
        observed_counts=[int(x) for x in counts],
        expected_counts=[float(x) for x in n * probs],
        observed_props=[float(x) for x in (counts / n if n else np.zeros_like(probs))],
        benford_props=[float(x) for x in probs],
        chi2=float(r["chi2"]),
        df=int(digits.size - 1),
        p_value=float(r["p_value"]),
        d_star=float(r["d_star"]),
        mad=float(r["mad"]),
        chi2_valid=bool(n * probs.min() >= min_expected),
    )


def min_n_for_chi2(n_digits: int = 1, min_expected: float = DEFAULT_MIN_EXPECTED) -> int:
    """Smallest n with every expected Benford count >= min_expected."""
    _, probs = benford_probs(n_digits)
    return int(np.ceil(min_expected / probs.min()))


def span_decades(values: Any, lo_pct: float = 5.0, hi_pct: float = 95.0) -> float:
    """log10(P_hi / P_lo) of the positive finite values (orders of magnitude spanned)."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x) & (x > 0)]
    if x.size < 2:
        return float("nan")
    lo, hi = np.percentile(x, [lo_pct, hi_pct])
    return float(np.log10(hi / lo)) if lo > 0 else float("nan")


def d_star_null_quantiles(
    n: int, n_digits: int = 1, draws: int = 2000, seed: int = 20260, quantiles: tuple[float, ...] = (0.95, 0.99)
) -> dict[str, float]:
    """Monte Carlo quantiles of D* for n values drawn exactly from Benford (fixed seed)."""
    _, probs = benford_probs(n_digits)
    rng = np.random.default_rng([seed, n, n_digits])
    sims = rng.multinomial(n, probs, size=draws)
    d = chi2_and_distances(sims, probs)["d_star"]
    return {f"p{int(round(q * 100))}": float(np.quantile(d, q)) for q in quantiles}


MIN_N_ABSOLUTE = 20  # design choice: below this the conformity function refuses to give a verdict


def chi2_monte_carlo_pvalue(counts: np.ndarray, probs: np.ndarray, draws: int = 20_000, seed: int = 20261) -> float:
    """p-value of the chi-square statistic by simulating multinomial samples of the same n
    from Benford (fixed seed). Valid when expected counts are < 5."""
    counts = np.asarray(counts)
    n = int(counts.sum())
    obs = float(chi2_and_distances(counts, probs)["chi2"])
    rng = np.random.default_rng([seed, n, probs.size])
    sims = chi2_and_distances(rng.multinomial(n, probs, size=draws), probs)["chi2"]
    return float((1 + np.sum(sims >= obs - 1e-9)) / (draws + 1))


def power_vs_uniform_digits(
    n: int, n_digits: int = 1, alpha: float = DEFAULT_ALPHA, draws: int = 4000, seed: int = 20262
) -> float:
    """Simulated probability that the chi-square test rejects at `alpha` when the leading
    digits are UNIFORM (a gross reference deviation) at sample size n. Tells the reader how
    much a 'conforms' verdict at this n is worth."""
    digits, probs = benford_probs(n_digits)
    rng = np.random.default_rng([seed, n, n_digits])
    null = chi2_and_distances(rng.multinomial(n, probs, size=draws), probs)["chi2"]
    crit = np.quantile(null, 1.0 - alpha)
    uniform = np.full(digits.size, 1.0 / digits.size)
    alt = chi2_and_distances(rng.multinomial(n, uniform, size=draws), probs)["chi2"]
    return float(np.mean(alt > crit))


@dataclass(frozen=True)
class ConformityResult:
    """Verdict on whether a numeric series is consistent with Benford's Law.

    verdict: 'conforms'          no significant deviation at alpha (NOT proof the data is genuine)
             'does_not_conform'  chi-square p < alpha (a reason to inspect, not proof of fraud)
             'not_applicable'    too few values or too narrow a span for Benford to be expected
    method:  'asymptotic_chi2' when every expected count >= min_expected, else
             'monte_carlo_chi2' (p-value simulated at the same n, valid for small n).
    """

    verdict: str
    reasons: list[str]
    alpha: float
    n_digits: int
    n_input: int
    n_used: int
    n_excluded_nonpositive_or_nonfinite: int
    min_n_for_asymptotic_chi2: int
    span_decades_p5_p95: float
    min_span_decades: float
    method: str | None
    p_value: float | None
    test: BenfordTestResult | None
    power_vs_uniform_digits: float | None = None
    d_star_null: dict[str, float] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["test"] = self.test.to_dict() if self.test else None
        return d


def benford_conformity(
    values: Any,
    n_digits: int = 1,
    alpha: float = DEFAULT_ALPHA,
    min_span_decades: float = DEFAULT_MIN_SPAN_DECADES,
    min_expected: float = DEFAULT_MIN_EXPECTED,
    null_draws: int = 2000,
    mc_draws: int = 20_000,
) -> ConformityResult:
    """Run the Benford test on ANY numeric series (e.g. a plain list of real public tonnage
    figures) and return a conformity verdict with its applicability checks and caveats.

    Non-positive and non-finite values are excluded (and counted). For n below the
    chi-square expected-count requirement, the p-value is simulated (Monte Carlo) instead of
    taken from the asymptotic chi-square distribution. Power against uniform leading digits
    at the same n is always reported so a 'conforms' verdict on a small series is not
    over-read.
    """
    x = np.asarray(values, dtype=float).ravel()
    n_input = int(x.size)
    # For n_digits=2 every value should carry >= 2 significant digits; the recording
    # precision of an arbitrary series is unknown here, so that is the caller's job.
    usable = x[np.isfinite(x) & (x > 0)]
    n_used = int(usable.size)
    need = min_n_for_chi2(n_digits, min_expected)
    span = span_decades(usable)
    reasons: list[str] = []
    caveats = [
        "'conforms' means no statistically significant deviation at alpha; it is not proof the data is genuine.",
        "'does_not_conform' is a reason to inspect, not proof of fabrication: genuine data can deviate "
        "(e.g. caps, minimums, rounding by measuring equipment, narrow ranges, few distinct sources).",
    ]
    common = dict(
        alpha=alpha,
        n_digits=n_digits,
        n_input=n_input,
        n_used=n_used,
        n_excluded_nonpositive_or_nonfinite=n_input - n_used,
        min_n_for_asymptotic_chi2=need,
        span_decades_p5_p95=span,
        min_span_decades=min_span_decades,
    )
    if n_used < MIN_N_ABSOLUTE:
        reasons.append(f"too few positive values: {n_used} < {MIN_N_ABSOLUTE} (minimum for any verdict)")
    if not np.isfinite(span) or span < min_span_decades:
        reasons.append(
            f"values span {span:.2f} decades (P5-P95) < {min_span_decades:g}; Benford's Law is not expected "
            "to hold for data concentrated within a narrow range"
        )
    if reasons:
        return ConformityResult(
            verdict="not_applicable", reasons=reasons, method=None, p_value=None, test=None, caveats=caveats, **common
        )

    test = benford_test(usable, n_digits, min_expected)
    if test.chi2_valid:
        method, p_value = "asymptotic_chi2", test.p_value
    else:
        _, probs = benford_probs(n_digits)
        method = "monte_carlo_chi2"
        p_value = chi2_monte_carlo_pvalue(np.asarray(test.observed_counts), probs, draws=mc_draws)
        caveats.append(
            f"n = {n_used} is below {need}, so some expected digit counts are < {min_expected:g}; the p-value "
            f"is simulated ({mc_draws} multinomial draws from Benford at the same n), not asymptotic."
        )
    verdict = "does_not_conform" if p_value < alpha else "conforms"
    reasons.append(f"chi-square({test.df}) = {test.chi2:.2f}, p = {p_value:.3g} ({method}) vs alpha = {alpha:g}")
    power = power_vs_uniform_digits(n_used, n_digits, alpha)
    if power < 0.8:
        caveats.append(
            f"low power: at n = {n_used}, even leading digits that are completely uniform would be detected "
            f"only {power:.0%} of the time at alpha = {alpha:g}; a 'conforms' verdict here is weak evidence."
        )
    null = d_star_null_quantiles(n_used, n_digits, draws=null_draws)
    if n_used > 10_000:
        caveats.append(
            "large n: chi-square detects very small deviations; compare D* with its null quantiles "
            "(d_star_null) to judge whether the deviation is material."
        )
    if span < 2.0:
        caveats.append("span below 2 decades: even genuine data may deviate from Benford.")
    return ConformityResult(
        verdict=verdict,
        reasons=reasons,
        method=method,
        p_value=float(p_value),
        test=test,
        power_vs_uniform_digits=power,
        d_star_null=null,
        caveats=caveats,
        **common,
    )


def peer_homogeneity_pvalues(counts: np.ndarray) -> dict[str, np.ndarray]:
    """Per-group chi-square test of homogeneity: each group's digit counts vs the pooled
    counts of all OTHER groups (2 x k contingency table, df = k - 1).

    Unlike the Benford goodness-of-fit test this does not assume the population follows
    Benford's Law; it asks whether a ward's digits differ from its peers'. It assumes
    genuine wards share one digit distribution, and it cannot see tampering that every
    ward shares. Digits with zero total count are dropped from the table."""
    counts = np.asarray(counts, dtype=float)
    total = counts.sum(axis=0, keepdims=True)
    rest = total - counts
    n_w = counts.sum(axis=1, keepdims=True)
    n_r = rest.sum(axis=1, keepdims=True)
    col = counts + rest  # == total for every row
    N = n_w + n_r
    keep = col > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        e_w = n_w * col / N
        e_r = n_r * col / N
        terms = np.where(keep & (e_w > 0), (counts - e_w) ** 2 / e_w, 0.0) + np.where(
            keep & (e_r > 0), (rest - e_r) ** 2 / e_r, 0.0
        )
    chi2 = terms.sum(axis=1)
    df = int(keep[0].sum()) - 1
    valid = (n_w[:, 0] > 0) & (n_r[:, 0] > 0) & (df > 0)
    p = np.where(valid, stats.chi2.sf(chi2, max(df, 1)), np.nan)
    min_exp = np.min(np.where(keep, e_w, np.inf), axis=1)
    return {"chi2": chi2, "df": np.full(counts.shape[0], df), "p_value": p, "min_expected": min_exp}


def group_percentiles(
    values: np.ndarray, group_idx: np.ndarray, n_groups: int, pcts: tuple[float, ...]
) -> tuple[np.ndarray, ...]:
    """Per-group percentiles (linear interpolation, as np.percentile).
    Groups with fewer than 2 values get NaN."""
    v = np.asarray(values, dtype=float)
    g = np.asarray(group_idx)
    order = np.argsort(g, kind="stable")
    v_grouped = v[order]
    bounds = np.searchsorted(g[order], np.arange(n_groups + 1))
    out = np.full((len(pcts), n_groups), np.nan)
    for i in range(n_groups):
        seg = v_grouped[bounds[i] : bounds[i + 1]]
        if seg.size >= 2:
            out[:, i] = np.percentile(seg, pcts)
    return tuple(out)


def benford_by_group(
    values: np.ndarray,
    group_idx: np.ndarray,
    n_groups: int,
    n_digits: int = 1,
    min_expected: float = DEFAULT_MIN_EXPECTED,
    min_span_decades: float = DEFAULT_MIN_SPAN_DECADES,
    reference: str = "benford",
) -> dict[str, np.ndarray]:
    """Per-group (per-ward) first-digit test, vectorised. p_value is NaN where the test is
    not applicable (too few values or span below `min_span_decades`).

    reference='benford' (default): chi-square goodness of fit to Benford's Law.
    reference='peer': chi-square homogeneity vs the pooled digits of all other groups
    (`peer_homogeneity_pvalues`), for data that is not exactly Benford city-wide.
    D* and MAD are always measured against Benford."""
    if reference not in ("benford", "peer"):
        raise ValueError("reference must be 'benford' or 'peer'")
    digits, probs = benford_probs(n_digits)
    d = leading_digits(values, n_digits)
    ok = d >= 0
    flat = group_idx[ok] * digits.size + (d[ok] - digits[0])
    counts = np.bincount(flat, minlength=n_groups * digits.size).reshape(n_groups, digits.size)
    r = chi2_and_distances(counts, probs)
    lo, hi = group_percentiles(np.asarray(values, dtype=float)[ok], group_idx[ok], n_groups, (5.0, 95.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        span = np.where(lo > 0, np.log10(hi / np.where(lo > 0, lo, 1.0)), np.nan)
    if reference == "peer":
        peer = peer_homogeneity_pvalues(counts)
        r["chi2"], r["df"], r["p_value"], r["min_expected"] = peer["chi2"], peer["df"], peer["p_value"], peer["min_expected"]
    r["reference"] = reference
    applicable = (r["min_expected"] >= min_expected) & np.isfinite(span) & (span >= min_span_decades)
    r["p_value"] = np.where(applicable, r["p_value"], np.nan)
    r["applicable"] = applicable
    r["span_decades"] = span
    r["counts"] = counts
    return r
