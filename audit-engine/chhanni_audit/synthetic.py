"""SYNTHETIC ward tonnage generator (docs/decisions.md D5).

Everything this module produces is synthetic. It is not MCD data, not census
data, and not derived from any real ward-level log.

Calibration
-----------
The ONLY real figure used is the city-wide total: 11,862 TPD generated
(CRDT/IIT Delhi, docs/data-sources.md row 1, Verified). After generation, all
trip loads are multiplied by one global factor so the synthetic city's mean
daily total equals exactly that figure (before 3-decimal rounding).

Every other value in `SyntheticParams` (ward count, populations, proxies,
coefficients, noise levels, day-of-week pattern, trip-load distribution, audit
window) is a synthetic modelling choice made for this demo, not an estimate of
anything real. In particular:

* the ward count (40) is deliberately NOT Delhi's real ward count;
* populations are synthetic, so the implied per-capita generation rate
  (11,862 t / total synthetic population) is an artefact of those choices and
  must NOT be quoted as a real per-capita figure (see `implied_per_capita_kg`);
* the trip-level load distribution is designed, not observed.

Data model
----------
One log row = one collection trip / weighbridge entry (the schema allows many
entries per ward per day). Trip loads are `10**U * F` with U uniform over an
integer number of decades and F an independent lognormal "fill" factor. Because
U spans whole decades, the trip loads follow Benford's Law EXACTLY, by
construction: any detector result on clean synthetic data shows "the detector
works if clean data conforms", not that real municipal data conforms. Whether
real data conforms is an open empirical question (Phase 1 item 6).

Ward-level expected generation follows a log-linear function of the
socio-economic proxies plus an unexplained ward effect. The baseline model in
`baseline.py` uses the same functional form, so it partly recovers the
generator by construction; the unexplained ward effect (`ward_effect_sigma`) is
what limits how well any proxy-based baseline can do, and its size here is a
synthetic choice.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .calibration import CRDT_DELHI_GENERATED_TPD

DATA_ORIGIN = "synthetic"

LOG_COLUMNS = ("entry_id", "ward_code", "entry_date", "tonnage", "data_origin", "is_injected_tamper")
WARD_COLUMNS = ("ward_code", "name", "zone", "population", "income_proxy", "sanitation_proxy", "data_origin")


@dataclass(frozen=True)
class SyntheticParams:
    """Every parameter of the synthetic generator.

    All fields are SYNTHETIC modelling choices except `city_daily_total_t`,
    which defaults to the Verified CRDT figure (11,862 TPD).
    """

    # --- calibration target (the only real figure) ---
    city_daily_total_t: float = CRDT_DELHI_GENERATED_TPD

    # --- synthetic city layout ---
    n_wards: int = 40
    n_zones: int = 4
    n_days: int = 28
    start_date: str = "2026-07-06"  # a Monday; arbitrary synthetic window

    # --- synthetic ward socio-economic proxies ---
    population_median: float = 200_000.0
    population_sigma_ln: float = 0.40
    income_beta_a: float = 2.0
    income_beta_b: float = 2.0
    sanitation_intercept: float = 0.25
    sanitation_income_slope: float = 0.50
    sanitation_noise_sd: float = 0.12

    # --- synthetic generation model: log G = log k + e*log P + bI*(I-.5) + bS*(S-.5) + u ---
    population_elasticity: float = 1.0
    coef_income: float = 0.60
    coef_sanitation: float = 0.30
    ward_effect_sigma: float = 0.10  # unexplained ward heterogeneity (log scale)

    # --- synthetic temporal pattern ---
    day_of_week_factors: tuple[float, ...] = (1.04, 1.00, 0.98, 0.99, 1.01, 1.03, 0.95)  # Mon..Sun
    day_shock_sigma: float = 0.03  # city-wide day-to-day shock (log scale)
    ward_day_sigma: float = 0.10  # ward-day noise (log scale)

    # --- synthetic trip-load distribution ---
    load_model: str = "benford_product"  # or "lognormal" (approximately-Benford variant, sensitivity only)
    load_log10_min: float = -1.0
    load_span_decades: float = 2.0  # integer => exactly Benford
    load_fill_sigma_ln: float = 0.30
    lognormal_load_median_t: float = 1.5
    lognormal_load_sigma_log10: float = 0.35

    def __post_init__(self) -> None:
        if self.n_wards < 5:
            raise ValueError("n_wards must be >= 5 for the cross-ward baseline")
        if self.n_days < 1:
            raise ValueError("n_days must be >= 1")
        if len(self.day_of_week_factors) != 7:
            raise ValueError("day_of_week_factors needs 7 values (Mon..Sun)")
        if self.load_model not in ("benford_product", "lognormal"):
            raise ValueError(f"unknown load_model {self.load_model!r}")
        if self.city_daily_total_t <= 0:
            raise ValueError("city_daily_total_t must be positive")

    @property
    def exactly_benford(self) -> bool:
        """True when clean trip loads follow Benford's Law exactly by construction."""
        return self.load_model == "benford_product" and float(self.load_span_decades).is_integer()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["day_of_week_factors"] = list(self.day_of_week_factors)
        return d


@dataclass
class SyntheticDataset:
    """A generated dataset. `latent` holds generator internals for evaluation/debug
    only; it is never passed to the audit engine."""

    seed: int
    params: SyntheticParams
    wards: pd.DataFrame
    logs: pd.DataFrame
    latent: dict[str, Any] = field(default_factory=dict)

    @property
    def period_start(self) -> pd.Timestamp:
        return pd.Timestamp(self.params.start_date)

    @property
    def period_end(self) -> pd.Timestamp:
        return pd.Timestamp(self.params.start_date) + pd.Timedelta(days=self.params.n_days - 1)


def expected_load_t(params: SyntheticParams) -> float:
    """Analytic mean of one synthetic trip load (before the global calibration scale)."""
    if params.load_model == "benford_product":
        a, s = params.load_log10_min, params.load_span_decades
        mean_u = (10.0 ** (a + s) - 10.0**a) / (s * np.log(10.0))
        return float(mean_u * np.exp(params.load_fill_sigma_ln**2 / 2.0))
    sigma_ln = params.lognormal_load_sigma_log10 * np.log(10.0)
    return float(params.lognormal_load_median_t * np.exp(sigma_ln**2 / 2.0))


def sample_loads(rng: np.random.Generator, n: int, params: SyntheticParams) -> np.ndarray:
    """Draw n synthetic trip loads (tonnes, before calibration scaling)."""
    if params.load_model == "benford_product":
        u = rng.uniform(params.load_log10_min, params.load_log10_min + params.load_span_decades, n)
        fill = rng.lognormal(0.0, params.load_fill_sigma_ln, n)
        return np.power(10.0, u) * fill
    return params.lognormal_load_median_t * np.power(10.0, rng.normal(0.0, params.lognormal_load_sigma_log10, n))


def generate_wards(params: SyntheticParams, rng: np.random.Generator) -> pd.DataFrame:
    """Synthetic wards matching the `wards` table shape (data_origin='synthetic')."""
    n = params.n_wards
    population = np.rint(params.population_median * rng.lognormal(0.0, params.population_sigma_ln, n)).astype(np.int64)
    population = np.maximum(population, 1)
    income = rng.beta(params.income_beta_a, params.income_beta_b, n)
    sanitation = (
        params.sanitation_intercept
        + params.sanitation_income_slope * income
        + rng.normal(0.0, params.sanitation_noise_sd, n)
    )
    income = np.round(np.clip(income, 0.0, 1.0), 4)
    sanitation = np.round(np.clip(sanitation, 0.02, 0.98), 4)
    idx = np.arange(n)
    zone_idx = idx * params.n_zones // n
    return pd.DataFrame(
        {
            "ward_code": [f"SYN-W{i + 1:02d}" for i in idx],
            "name": [f"Synthetic Ward {i + 1:02d}" for i in idx],
            "zone": [f"SYN-Z{z + 1}" for z in zone_idx],
            "population": population,
            "income_proxy": income,
            "sanitation_proxy": sanitation,
            "data_origin": DATA_ORIGIN,
        }
    )


def _expected_ward_generation(wards: pd.DataFrame, params: SyntheticParams, ward_effect: np.ndarray) -> np.ndarray:
    """Synthetic expected daily generation per ward (t/day), summing to the calibration target."""
    log_raw = (
        params.population_elasticity * np.log(wards["population"].to_numpy(dtype=float))
        + params.coef_income * (wards["income_proxy"].to_numpy(dtype=float) - 0.5)
        + params.coef_sanitation * (wards["sanitation_proxy"].to_numpy(dtype=float) - 0.5)
        + ward_effect
    )
    raw = np.exp(log_raw - log_raw.max())
    return params.city_daily_total_t * raw / raw.sum()


def generate_logs(
    wards: pd.DataFrame, params: SyntheticParams, rng: np.random.Generator
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Synthetic trip-level tonnage log matching `ward_tonnage_logs` (+ an entry_id)."""
    n_w, n_d = len(wards), params.n_days
    ward_effect = rng.normal(0.0, params.ward_effect_sigma, n_w)
    expected = _expected_ward_generation(wards, params, ward_effect)

    start = pd.Timestamp(params.start_date)
    weekday = (start.dayofweek + np.arange(n_d)) % 7
    day_factor = np.asarray(params.day_of_week_factors)[weekday] * np.exp(rng.normal(0.0, params.day_shock_sigma, n_d))
    day_factor = day_factor / day_factor.mean()

    sig = params.ward_day_sigma
    ward_day_target = (
        expected[:, None] * day_factor[None, :] * np.exp(rng.normal(-sig * sig / 2.0, sig, (n_w, n_d)))
    )
    trips = rng.poisson(ward_day_target / expected_load_t(params))  # (ward, day)

    # Rows ordered by (day, ward) like a log that accumulates over time.
    counts_dw = trips.T.ravel()
    day_of_row = np.repeat(np.repeat(np.arange(n_d), n_w), counts_dw)
    ward_of_row = np.repeat(np.tile(np.arange(n_w), n_d), counts_dw)
    loads = sample_loads(rng, int(counts_dw.sum()), params)

    # Global calibration: realised mean daily city total == city_daily_total_t.
    calibration_scale = params.city_daily_total_t * n_d / loads.sum()
    tonnage = np.round(loads * calibration_scale, 3)

    codes = wards["ward_code"].to_numpy()
    logs = pd.DataFrame(
        {
            "entry_id": np.arange(1, len(tonnage) + 1, dtype=np.int64),
            "ward_code": pd.Categorical.from_codes(ward_of_row, categories=list(codes)),
            "entry_date": start + pd.to_timedelta(day_of_row, unit="D"),
            "tonnage": tonnage,
            "data_origin": DATA_ORIGIN,
            "is_injected_tamper": np.zeros(len(tonnage), dtype=bool),
        }
    )
    latent = {
        "ward_effect": ward_effect,
        "expected_daily_t": expected,
        "day_factor": day_factor,
        "calibration_scale": float(calibration_scale),
        "trips_per_ward_day": trips,
    }
    return logs, latent


def generate_dataset(seed: int, params: SyntheticParams | None = None) -> SyntheticDataset:
    """Deterministic SYNTHETIC dataset for `seed`."""
    params = params or SyntheticParams()
    ward_ss, log_ss = np.random.SeedSequence(int(seed)).spawn(2)
    wards = generate_wards(params, np.random.default_rng(ward_ss))
    logs, latent = generate_logs(wards, params, np.random.default_rng(log_ss))
    return SyntheticDataset(seed=int(seed), params=params, wards=wards, logs=logs, latent=latent)


def implied_per_capita_kg(dataset: SyntheticDataset) -> float:
    """kg/person/day implied by the synthetic populations. An ARTEFACT of synthetic
    choices; never present it as a real per-capita rate."""
    return float(dataset.params.city_daily_total_t * 1000.0 / dataset.wards["population"].sum())


def describe(dataset: SyntheticDataset) -> dict[str, Any]:
    """Summary statistics of a synthetic dataset (all synthetic)."""
    logs = dataset.logs
    daily_city = logs.groupby("entry_date", observed=True)["tonnage"].sum()
    per_ward = logs.groupby("ward_code", observed=True).size()
    positive = logs.loc[logs["tonnage"] > 0, "tonnage"].to_numpy()
    p5, p95 = np.percentile(positive, [5, 95])
    ward_day = logs.groupby(["ward_code", "entry_date"], observed=True)["tonnage"].sum().to_numpy()
    wd5, wd95 = np.percentile(ward_day, [5, 95])
    return {
        "data_origin": DATA_ORIGIN,
        "seed": dataset.seed,
        "n_wards": int(len(dataset.wards)),
        "n_days": int(dataset.params.n_days),
        "period_start": dataset.period_start.date().isoformat(),
        "period_end": dataset.period_end.date().isoformat(),
        "n_entries": int(len(logs)),
        "entries_per_ward_min": int(per_ward.min()),
        "entries_per_ward_median": float(per_ward.median()),
        "entries_per_ward_max": int(per_ward.max()),
        "mean_daily_city_total_t": round(float(daily_city.mean()), 3),
        "calibration_target_t": dataset.params.city_daily_total_t,
        "trip_load_p5_t": round(float(p5), 3),
        "trip_load_p95_t": round(float(p95), 3),
        "trip_load_span_decades_p5_p95": round(float(np.log10(p95 / p5)), 3),
        "ward_day_total_span_decades_p5_p95": round(float(np.log10(wd95 / wd5)), 3),
        "total_population_synthetic": int(dataset.wards["population"].sum()),
        "implied_per_capita_kg_per_day_ARTEFACT": round(implied_per_capita_kg(dataset), 3),
        "clean_loads_exactly_benford_by_construction": dataset.params.exactly_benford,
    }
