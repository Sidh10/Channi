"""Top-level audit pipeline: `audit(wards, logs) -> AuditResult`.

Output maps onto the database schema (backend/db/migrations/001_init.sql):
* `AuditResult.ward_score_records()` -> ward_audit_scores rows
  (ward_code, confidence_score in [0,1] with 1 = consistent, flagged, metrics)
* `AuditResult.flag_records()`       -> audit_flags rows
  (ward_code, check_type, log_entry_ref, period_start, period_end, score, details)
* `AuditResult.run_params()` / `run_summary()` -> audit_runs.params / summary

Ground-truth isolation: detection only ever sees the columns in
`DETECTION_LOG_COLUMNS` / `DETECTION_WARD_COLUMNS`. `is_injected_tamper` is
dropped in `detection_view` before any check runs (docs/decisions.md D10).

Checks (see their modules for method and caveats): ``benford`` (first-digit
chi-square per ward on trip-level entries), ``roundness`` (trailing-zero
Fisher test), ``baseline`` (LOWO Huber socio-economic model, ward-period) and
``baseline_daily`` (ward-day spikes). Decision rule: one joint
Benjamini-Hochberg procedure over every (ward, check) p-value at alpha = 0.01.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import __version__
from .baseline import HUBER_C, daily_spike_check, daily_totals, design_matrix, period_check
from .benford import DEFAULT_MIN_EXPECTED, DEFAULT_MIN_SPAN_DECADES, benford_by_group, benford_probs
from .roundness import DEFAULT_RESOLUTIONS_KG, roundness_by_group, to_kg
from .scoring import CHECKS, combine

DETECTION_LOG_COLUMNS: tuple[str, ...] = ("entry_id", "ward_code", "entry_date", "tonnage")
DETECTION_WARD_COLUMNS: tuple[str, ...] = ("ward_code", "population", "income_proxy", "sanitation_proxy")

SYNTHETIC_NOTICE = (
    "SYNTHETIC DATA: every ward and log entry in this audit has data_origin='synthetic' "
    "(docs/decisions.md D5). No real MCD or other municipal data was used."
)


@dataclass(frozen=True)
class AuditConfig:
    """Audit thresholds. Fixed a priori; never tuned on evaluation seeds."""

    alpha: float = 0.01
    multiple_testing: str = "benjamini_hochberg_joint_over_all_ward_check_pairs"
    roundness_resolutions_kg: tuple[int, ...] = DEFAULT_RESOLUTIONS_KG
    huber_c: float = HUBER_C
    baseline_direction: str = "upper"
    benford_min_expected: float = DEFAULT_MIN_EXPECTED
    benford_min_span_decades: float = DEFAULT_MIN_SPAN_DECADES
    # 'benford' = goodness of fit to Benford's Law (spec default); 'peer' = each ward's first
    # digits vs all other wards' (does not assume the city's data is exactly Benford).
    benford_reference: str = "benford"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["roundness_resolutions_kg"] = list(self.roundness_resolutions_kg)
        d["checks"] = list(CHECKS)
        return d


def detection_view(logs: pd.DataFrame) -> pd.DataFrame:
    """The only log columns detection may read. Ground truth (is_injected_tamper)
    and anything else is dropped here."""
    missing = {"ward_code", "entry_date", "tonnage"} - set(logs.columns)
    if missing:
        raise ValueError(f"logs missing required columns: {sorted(missing)}")
    cols = [c for c in DETECTION_LOG_COLUMNS if c in logs.columns]
    view = logs.loc[:, cols]
    if "entry_id" not in view.columns:
        view = view.assign(entry_id=np.arange(len(view), dtype=np.int64))
    return view


def _ward_indices(series: pd.Series, code_index: pd.Index) -> np.ndarray:
    if isinstance(series.dtype, pd.CategoricalDtype):
        cat_map = code_index.get_indexer(series.cat.categories.astype(str))
        codes = series.cat.codes.to_numpy()
        return np.where(codes >= 0, cat_map[np.maximum(codes, 0)], -1)
    return code_index.get_indexer(series.astype(str))


def _py(x: Any) -> Any:
    """JSON-safe python scalar (NaN/inf -> None)."""
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        x = float(x)
        return x if math.isfinite(x) else None
    return x


def _sig(x: Any, digits: int = 6) -> Any:
    """Round to significant digits (for p/q-values, which can be ~1e-50; fixed decimals would print 0)."""
    v = _py(x)
    return float(f"{v:.{digits}g}") if isinstance(v, float) else v


def _r(x: Any, nd: int = 6) -> Any:
    v = _py(x)
    return round(v, nd) if isinstance(v, float) else v


def _origin_counts(df: pd.DataFrame) -> dict[str, int]:
    if "data_origin" not in df.columns:
        return {"unspecified": int(len(df))}
    counts = df["data_origin"].value_counts(dropna=False)
    return dict(sorted((str(k), int(v)) for k, v in counts.items()))


@dataclass
class AuditResult:
    config: AuditConfig
    period_start: pd.Timestamp
    period_end: pd.Timestamp
    n_days: int
    ward_codes: np.ndarray
    n_entries: np.ndarray
    has_data: np.ndarray
    checks: dict[str, dict[str, Any]]
    p: np.ndarray
    q: np.ndarray
    flagged_check: np.ndarray
    ward_flagged: np.ndarray
    confidence: np.ndarray
    n_tests: int
    origin_wards: dict[str, int]
    origin_logs: dict[str, int]
    _entries: dict[str, np.ndarray] | None = field(default=None, repr=False)
    engine_version: str = __version__

    # ---- tabular view (used by evaluation) ----
    @property
    def ward_table(self) -> pd.DataFrame:
        data: dict[str, Any] = {
            "ward_code": self.ward_codes,
            "n_entries": self.n_entries,
            "has_data": self.has_data,
            "flagged": self.ward_flagged,
            "confidence": self.confidence,
        }
        for j, c in enumerate(CHECKS):
            data[f"p_{c}"] = self.p[:, j]
            data[f"q_{c}"] = self.q[:, j]
            data[f"flag_{c}"] = self.flagged_check[:, j]
        return pd.DataFrame(data)

    @property
    def data_notice(self) -> str:
        origins = set(self.origin_wards) | set(self.origin_logs)
        if origins <= {"synthetic"}:
            return SYNTHETIC_NOTICE
        return (
            f"MIXED/NON-SYNTHETIC DATA: data_origin values present = {sorted(origins)}. "
            "Label every figure by its origin; synthetic rows remain synthetic."
        )

    # ---- schema-shaped records ----
    def _ward_metrics(self, i: int) -> dict[str, Any]:
        bf, rd, bl, bd = (self.checks[c] for c in CHECKS)
        qi = {c: _sig(self.q[i, j]) for j, c in enumerate(CHECKS)}
        pi = {c: _sig(self.p[i, j]) for j, c in enumerate(CHECKS)}
        fi = {c: bool(self.flagged_check[i, j]) for j, c in enumerate(CHECKS)}
        digits, _ = benford_probs(1)
        max_day = bd["max_z_day"][i]
        return {
            "data_notice": self.data_notice,
            "n_entries": int(self.n_entries[i]),
            "period_start": self.period_start.date().isoformat(),
            "period_end": self.period_end.date().isoformat(),
            "confidence_basis": "smallest BH-adjusted p-value (q) across checks; 1 = consistent, "
            f"<= {self.config.alpha} = flagged. Not a probability that the data is genuine.",
            "benford": {
                "reference": self.config.benford_reference,
                "applicable": bool(bf["applicable"][i]),
                "n": int(bf["n"][i]),
                "chi2": _r(bf["chi2"][i], 4),
                "df": int(bf["df"][i]),
                "p_value": pi["benford"],
                "q_value": qi["benford"],
                "flagged": fi["benford"],
                "d_star": _r(bf["d_star"][i]),
                "mad": _r(bf["mad"][i]),
                "span_decades_p5_p95": _r(bf["span_decades"][i], 4),
                "first_digit_counts": {str(int(d)): int(c) for d, c in zip(digits, bf["counts"][i])},
            },
            "roundness": {
                "p_value": pi["roundness"],
                "q_value": qi["roundness"],
                "flagged": fi["roundness"],
                "best_resolution_t": _r(rd["best_resolution_kg"][i] / 1000.0, 3),
                "by_resolution": [
                    {
                        "resolution_t": r / 1000.0,
                        "eligible": int(rd["eligible"][i, j]),
                        "round": int(rd["round"][i, j]),
                        "rate": _r(rd["rate"][i, j]),
                        "reference_rate_other_wards": _r(rd["reference_rate"][i, j]),
                        "p_value": _sig(rd["p_by_resolution"][i, j]),
                    }
                    for j, r in enumerate(self.config.roundness_resolutions_kg)
                ],
            },
            "baseline": {
                "observed_mean_daily_t": _r(bl["observed_mean_t"][i], 3),
                "predicted_mean_daily_t": _r(bl["predicted_mean_t"][i], 3),
                "observed_over_predicted": _r(math.exp(bl["log_ratio"][i]) if np.isfinite(bl["log_ratio"][i]) else None, 4),
                "z": _r(bl["z"][i], 4),
                "residual_scale_log": _r(bl["scale"], 5),
                "t_df": int(bl["df"]),
                "model": "log(mean daily t) ~ 1 + log(population) + income_proxy + sanitation_proxy; "
                "leave-one-ward-out Huber fit",
                "direction": self.config.baseline_direction,
                "p_value": pi["baseline"],
                "q_value": qi["baseline"],
                "flagged": fi["baseline"],
            },
            "baseline_daily": {
                "max_z": _r(bd["max_z"][i], 4),
                "ward_scale_log": _r(bd["ward_scale"][i], 5),
                "max_z_date": (self.period_start + pd.Timedelta(days=int(max_day))).date().isoformat()
                if np.isfinite(bd["max_z"][i])
                else None,
                "p_value": pi["baseline_daily"],
                "q_value": qi["baseline_daily"],
                "flagged": fi["baseline_daily"],
            },
        }

    def ward_score_records(self) -> list[dict[str, Any]]:
        """ward_audit_scores rows. Wards with no entries in the window are omitted
        (listed in run_summary()['wards_without_entries'])."""
        out = []
        for i in np.flatnonzero(self.has_data):
            out.append(
                {
                    "ward_code": str(self.ward_codes[i]),
                    "confidence_score": round(float(self.confidence[i]), 4),
                    "flagged": bool(self.ward_flagged[i]),
                    "metrics": self._ward_metrics(int(i)),
                }
            )
        return out

    def flag_records(self, include_localisation: bool = True) -> list[dict[str, Any]]:
        """audit_flags rows: one ward-level row per flagged (ward, check), plus optional
        localisation hints (entry-level for roundness, ward-day for baseline_daily)."""
        ps, pe = self.period_start.date().isoformat(), self.period_end.date().isoformat()
        alpha = self.config.alpha
        records: list[dict[str, Any]] = []
        bd = self.checks["baseline_daily"]
        rd = self.checks["roundness"]
        for i in np.flatnonzero(self.ward_flagged):
            code = str(self.ward_codes[i])
            metrics = self._ward_metrics(int(i))
            for j, check in enumerate(CHECKS):
                if not self.flagged_check[i, j]:
                    continue
                records.append(
                    {
                        "ward_code": code,
                        "check_type": check,
                        "log_entry_ref": None,
                        "period_start": ps,
                        "period_end": pe,
                        "score": _sig(self.q[i, j]),
                        "details": {
                            "level": "ward",
                            "score_meaning": "BH-adjusted p-value (q); smaller = stronger evidence",
                            "alpha": alpha,
                            "multiple_testing": self.config.multiple_testing,
                            "data_notice": self.data_notice,
                            **metrics[check],
                        },
                    }
                )
                if not include_localisation or self._entries is None:
                    continue
                if check == "roundness":
                    r = int(rd["best_resolution_kg"][i])
                    e = self._entries
                    sel = (e["ward_idx"] == i) & (e["kg"] >= r) & (e["kg"] % r == 0)
                    j_res = list(self.config.roundness_resolutions_kg).index(r)
                    ref = rd["reference_rate"][i, j_res]
                    order = np.argsort(e["entry_id"][sel], kind="stable")  # independent of input row order
                    for eid, day, kg in zip(e["entry_id"][sel][order], e["day_idx"][sel][order], e["kg"][sel][order]):
                        d = (self.period_start + pd.Timedelta(days=int(day))).date().isoformat()
                        records.append(
                            {
                                "ward_code": code,
                                "check_type": "roundness",
                                "log_entry_ref": int(eid),
                                "period_start": d,
                                "period_end": d,
                                "score": _sig(self.q[i, j]),
                                "details": {
                                    "level": "entry",
                                    "localisation_hint": True,
                                    "value_t": round(int(kg) / 1000.0, 3),
                                    "multiple_of_t": r / 1000.0,
                                    "score_meaning": "ward-level roundness q-value (an entry has no p-value of its own)",
                                    "note": "This entry is an exact multiple of the flagged resolution. "
                                    f"{_r(ref * 100 if ref is not None and np.isfinite(ref) else float('nan'), 3)}% "
                                    "of eligible entries in other wards are too, so one round value is not evidence on its own.",
                                    "data_notice": self.data_notice,
                                },
                            }
                        )
                elif check == "baseline_daily":
                    days = np.flatnonzero(bd["p_day_adjusted"][i] <= alpha)
                    for day in days:
                        d = (self.period_start + pd.Timedelta(days=int(day))).date().isoformat()
                        records.append(
                            {
                                "ward_code": code,
                                "check_type": "baseline_daily",
                                "log_entry_ref": None,
                                "period_start": d,
                                "period_end": d,
                                "score": _sig(bd["p_day_adjusted"][i, day]),
                                "details": {
                                    "level": "ward_day",
                                    "localisation_hint": True,
                                    "observed_total_t": _r(bd["observed_total_t"][i, day], 3),
                                    "expected_total_t": _r(bd["expected_total_t"][i, day], 3),
                                    "z": _r(bd["z"][i, day], 4),
                                    "score_meaning": "day p-value, Bonferroni-adjusted over the ward's days",
                                    "data_notice": self.data_notice,
                                },
                            }
                        )
        return records

    def run_params(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "config": self.config.to_dict(),
            "period_start": self.period_start.date().isoformat(),
            "period_end": self.period_end.date().isoformat(),
            "n_days": int(self.n_days),
        }

    def run_summary(self) -> dict[str, Any]:
        flagged_by = {
            c: [str(x) for x in self.ward_codes[self.flagged_check[:, j]]] for j, c in enumerate(CHECKS)
        }
        return {
            "data_notice": self.data_notice,
            "data_origin_wards": self.origin_wards,
            "data_origin_logs": self.origin_logs,
            "n_wards_scored": int(self.has_data.sum()),
            "n_entries_audited": int(self.n_entries.sum()),
            "n_tests": int(self.n_tests),
            "alpha": self.config.alpha,
            "n_wards_flagged": int(self.ward_flagged.sum()),
            "flagged_wards": [str(x) for x in self.ward_codes[self.ward_flagged]],
            "flagged_by_check": flagged_by,
            "wards_without_entries": [str(x) for x in self.ward_codes[~self.has_data]],
        }

    def to_dict(self, include_localisation: bool = True) -> dict[str, Any]:
        return {
            "data_notice": self.data_notice,
            "engine_version": self.engine_version,
            "params": self.run_params(),
            "summary": self.run_summary(),
            "ward_scores": self.ward_score_records(),
            "flags": self.flag_records(include_localisation),
        }


def _expand(values: np.ndarray, mask: np.ndarray, fill: float = np.nan) -> np.ndarray:
    out = np.full((mask.size,) + np.shape(values)[1:], fill, dtype=float)
    out[mask] = values
    return out


def audit(
    wards: pd.DataFrame,
    logs: pd.DataFrame,
    config: AuditConfig | None = None,
    period_start: Any = None,
    period_end: Any = None,
    localise: bool = True,
) -> AuditResult:
    """Run every check on `logs` (restricted to [period_start, period_end], default:
    the full date range of the log) and combine them per ward."""
    config = config or AuditConfig()
    missing = set(DETECTION_WARD_COLUMNS) - set(wards.columns)
    if missing:
        raise ValueError(f"wards missing required columns: {sorted(missing)}")
    w = wards.loc[:, list(DETECTION_WARD_COLUMNS)]
    view = detection_view(logs)

    codes = w["ward_code"].astype(str).to_numpy()
    if len(set(codes)) != len(codes):
        raise ValueError("ward_code values must be unique")
    code_index = pd.Index(codes)
    ward_idx = _ward_indices(view["ward_code"], code_index)
    if np.any(ward_idx < 0):
        unknown = sorted(set(view["ward_code"].astype(str).to_numpy()[ward_idx < 0]))
        raise ValueError(f"log entries reference unknown wards: {unknown[:10]}")

    raw_dates = view["entry_date"]
    if not np.issubdtype(raw_dates.dtype, np.datetime64):
        raw_dates = pd.to_datetime(raw_dates)
    days = raw_dates.to_numpy().astype("datetime64[D]")
    if days.size == 0:
        raise ValueError("log is empty")
    if np.isnat(days).any():
        raise ValueError("entry_date has missing values")
    start_d = np.datetime64(pd.Timestamp(period_start).date(), "D") if period_start is not None else days.min()
    end_d = np.datetime64(pd.Timestamp(period_end).date(), "D") if period_end is not None else days.max()
    if end_d < start_d:
        raise ValueError("period_end before period_start")
    start, end = pd.Timestamp(start_d), pd.Timestamp(end_d)
    n_days = int((end_d - start_d).astype(np.int64)) + 1
    sel = (days >= start_d) & (days <= end_d)

    tonnage = view["tonnage"].to_numpy(dtype=float)[sel]
    if np.any(~np.isfinite(tonnage)) or np.any(tonnage < 0):
        raise ValueError("tonnage must be finite and >= 0")
    widx = ward_idx[sel]
    didx = (days[sel] - start_d).astype(np.int64)
    entry_ids = view["entry_id"].to_numpy()[sel]
    n_wards = len(codes)
    kg = to_kg(tonnage)
    n_entries = np.bincount(widx, minlength=n_wards)
    has_data = n_entries > 0

    # --- checks (values only; no labels) ---
    bf = benford_by_group(
        kg.astype(float),
        widx,
        n_wards,
        1,
        config.benford_min_expected,
        config.benford_min_span_decades,
        config.benford_reference,
    )
    rd = roundness_by_group(kg, widx, n_wards, config.roundness_resolutions_kg)
    # Sum integer kilograms (exact in float64), so totals do not depend on row order.
    daily = daily_totals(kg.astype(float), widx, didx, n_wards, n_days) / 1000.0
    X = design_matrix(w["population"].to_numpy(float), w["income_proxy"].to_numpy(float), w["sanitation_proxy"].to_numpy(float))
    bl_raw = period_check(daily[has_data], X[has_data], config.huber_c, config.baseline_direction)
    bl = {k: (_expand(v, has_data) if isinstance(v, np.ndarray) else v) for k, v in bl_raw.items()}
    entries_per_day = daily_totals(np.ones_like(tonnage), widx, didx, n_wards, n_days)
    bd_raw = daily_spike_check(daily[has_data], entries_per_day[has_data])
    bd = {k: (_expand(v, has_data) if isinstance(v, np.ndarray) else v) for k, v in bd_raw.items()}
    bd["observed_total_t"] = daily

    p = np.column_stack([bf["p_value"], rd["p_value"], bl["p_value"], bd["p_value"]])
    p[~has_data, :] = np.nan
    comb = combine(p, config.alpha)

    entries = None
    if localise:
        entries = {"ward_idx": widx, "day_idx": didx, "entry_id": entry_ids, "kg": kg}
    return AuditResult(
        config=config,
        period_start=start,
        period_end=end,
        n_days=n_days,
        ward_codes=codes,
        n_entries=n_entries,
        has_data=has_data,
        checks={"benford": bf, "roundness": rd, "baseline": bl, "baseline_daily": bd},
        p=p,
        q=comb["q"],
        flagged_check=comb["flagged_check"],
        ward_flagged=comb["ward_flagged"] & has_data,
        confidence=comb["confidence"],
        n_tests=comb["n_tests"],
        origin_wards=_origin_counts(wards),
        origin_logs=_origin_counts(logs),
        _entries=entries,
    )
