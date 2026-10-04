"""SYNTHETIC tampering injector for ward tonnage logs (docs/decisions.md D5, D7 beat 2).

Every tampered row gets `is_injected_tamper = True`; nothing else in the log is
touched. That column is ground truth for EVALUATION only: the audit engine
never reads it (see `pipeline.detection_view`).

Tamper kinds (each is a synthetic behavioural model, an assumption about how
fraud might look, not an observed fraud pattern):

* ``round``     A clerk writes approximate numbers. Default: round to 1
                significant figure (4.837 t -> 5 t, 0.287 t -> 0.3 t); or to a
                fixed step (``round_step_t``). Target layer: digit tests
                (roundness; Benford only weakly).
* ``fabricate`` Invented numbers. Values ~ Uniform[lo, hi] recorded to 3
                decimals, where [lo, hi] defaults to the ward's own P10-P90 trip
                load (the fabricator imitates "typical" loads). Uniform in linear
                scale => leading digits far from Benford. Last digits are uniform
                in this model, so the roundness test gets no signal from it.
                Target layer: Benford.
* ``inflate``   Over-reporting: existing entries multiplied by
                ``inflate_factor`` (re-rounded to 3 decimals). By default the
                factor is applied to the underlying continuous weight (the
                recorded kg value plus sub-kg jitter), so the inflated value's
                last digits stay uniform. Multiplying the RECORDED integer-kg
                value by a simple factor (``inflate_dequantize=False``, e.g.
                x2 makes every value even) leaves a last-digit artefact that
                the roundness test can pick up; that is a real but fragile
                signal, so the default does not hand it to the detector.
                Benford is scale-invariant, so the digit tests should NOT
                catch default inflation. Target layer: baseline.

Modes:

* ``replace``  modify existing entries of the ward (an UPDATE in the database);
* ``add``      insert new entries (phantom trips; an INSERT, as in the live
               demo). Not defined for ``inflate``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd

TAMPER_KINDS = ("round", "fabricate", "inflate")
TAMPER_MODES = ("replace", "add")


@dataclass(frozen=True)
class TamperSpec:
    """What to inject, where. Exactly one of `fraction` / `n_entries` must be set.

    fraction   share of the ward's eligible entries (replace) or number of new
               entries relative to the ward's eligible entries (add).
    dates      restrict replaced entries to / place added entries on these dates
               (ISO strings or timestamps). None = the ward's whole log (spread).
    """

    kind: str
    ward_code: str
    mode: str = "replace"
    fraction: float | None = None
    n_entries: int | None = None
    dates: tuple[str, ...] | None = None
    round_sig_figs: int = 1
    round_step_t: float | None = None
    fabricate_range_t: tuple[float, float] | None = None
    inflate_factor: float = 1.5
    inflate_dequantize: bool = True

    def __post_init__(self) -> None:
        if self.kind not in TAMPER_KINDS:
            raise ValueError(f"kind must be one of {TAMPER_KINDS}, got {self.kind!r}")
        if self.mode not in TAMPER_MODES:
            raise ValueError(f"mode must be one of {TAMPER_MODES}, got {self.mode!r}")
        if self.kind == "inflate" and self.mode == "add":
            raise ValueError("inflate modifies existing entries; use mode='replace'")
        if (self.fraction is None) == (self.n_entries is None):
            raise ValueError("set exactly one of fraction / n_entries")
        if self.fraction is not None and not (0.0 < self.fraction <= (1.0 if self.mode == "replace" else 10.0)):
            raise ValueError(f"fraction out of range: {self.fraction}")
        if self.n_entries is not None and self.n_entries < 1:
            raise ValueError("n_entries must be >= 1")
        if self.round_sig_figs < 1:
            raise ValueError("round_sig_figs must be >= 1")
        if self.round_step_t is not None and self.round_step_t <= 0:
            raise ValueError("round_step_t must be positive")
        if self.inflate_factor <= 0:
            raise ValueError("inflate_factor must be positive")
        if self.fabricate_range_t is not None:
            lo, hi = self.fabricate_range_t
            if not (0 < lo < hi):
                raise ValueError("fabricate_range_t must satisfy 0 < lo < hi")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dates"] = list(self.dates) if self.dates is not None else None
        d["fabricate_range_t"] = list(self.fabricate_range_t) if self.fabricate_range_t else None
        return d


@dataclass
class TamperReport:
    """Exactly what the injector changed. `modified_entry_ids` (replace) and
    `added_entry_ids` (add) are the only rows that differ from the input."""

    spec: TamperSpec
    modified_entry_ids: list[int] = field(default_factory=list)
    added_entry_ids: list[int] = field(default_factory=list)
    ward_tonnage_before_t: float = 0.0
    ward_tonnage_after_t: float = 0.0
    fabricate_range_used_t: tuple[float, float] | None = None

    @property
    def n_tampered(self) -> int:
        return len(self.modified_entry_ids) + len(self.added_entry_ids)

    @property
    def overreport_ratio(self) -> float:
        """Ward's reported total after / before tampering (1.0 = no change)."""
        if self.ward_tonnage_before_t <= 0:
            return float("nan")
        return self.ward_tonnage_after_t / self.ward_tonnage_before_t

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_origin": "synthetic",
            "spec": self.spec.to_dict(),
            "n_tampered": self.n_tampered,
            "modified_entry_ids": list(self.modified_entry_ids),
            "added_entry_ids": list(self.added_entry_ids),
            "ward_tonnage_before_t": round(self.ward_tonnage_before_t, 3),
            "ward_tonnage_after_t": round(self.ward_tonnage_after_t, 3),
            "overreport_ratio": round(self.overreport_ratio, 6),
            "fabricate_range_used_t": list(self.fabricate_range_used_t) if self.fabricate_range_used_t else None,
        }


# --- value transforms (small, isolated, testable) ---------------------------


def round_sig_figs(values: np.ndarray, sig_figs: int = 1) -> np.ndarray:
    """Round positive values to `sig_figs` significant figures, stored at 3 decimals."""
    v = np.asarray(values, dtype=float)
    out = v.copy()
    pos = v > 0
    exponent = np.floor(np.log10(v[pos]))
    unit = np.power(10.0, exponent - (sig_figs - 1))
    out[pos] = np.round(v[pos] / unit) * unit
    # Below 1 kg resolution a value can't be represented; keep at least 0.001 t.
    return np.maximum(np.round(out, 3), np.where(pos, 0.001, 0.0))


def round_to_step(values: np.ndarray, step_t: float) -> np.ndarray:
    """Round to the nearest multiple of `step_t`, never below one step."""
    v = np.asarray(values, dtype=float)
    return np.round(np.maximum(np.round(v / step_t), 1.0) * step_t, 3)


def fabricate_values(rng: np.random.Generator, n: int, lo: float, hi: float) -> np.ndarray:
    """Invented loads: Uniform[lo, hi], typed to 3 decimals."""
    return np.round(rng.uniform(lo, hi, n), 3)


def inflate_values(
    values: np.ndarray, factor: float, rng: np.random.Generator | None = None, dequantize: bool = True
) -> np.ndarray:
    """Multiply by `factor`, stored at 3 decimals. With `dequantize`, the factor is applied to
    the underlying weight (recorded value + uniform sub-kg jitter) so no last-digit artefact
    is created; without it, the recorded value itself is scaled (naive spreadsheet inflation)."""
    v = np.asarray(values, dtype=float)
    if dequantize:
        if rng is None:
            raise ValueError("dequantize=True needs an rng")
        v = np.maximum(v + rng.uniform(-0.0005, 0.0005, v.shape), 0.0)
    return np.round(v * factor, 3)


def _rounded(values: np.ndarray, spec: TamperSpec) -> np.ndarray:
    if spec.round_step_t is not None:
        return round_to_step(values, spec.round_step_t)
    return round_sig_figs(values, spec.round_sig_figs)


# --- injector ----------------------------------------------------------------


def _as_rng(rng: np.random.Generator | int | Sequence[int]) -> np.random.Generator:
    return rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)


def _normalise_dates(dates: Sequence[Any] | None) -> pd.DatetimeIndex | None:
    if dates is None:
        return None
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates))).normalize()
    if len(idx) == 0:
        raise ValueError("dates must not be empty")
    return idx


def inject(
    logs: pd.DataFrame, spec: TamperSpec, rng: np.random.Generator | int | Sequence[int]
) -> tuple[pd.DataFrame, TamperReport]:
    """Return (tampered copy of `logs`, report). The input frame is not modified."""
    rng = _as_rng(rng)
    out = logs.copy()
    ward_mask = (out["ward_code"] == spec.ward_code).to_numpy(dtype=bool)
    if not ward_mask.any():
        raise ValueError(f"ward {spec.ward_code!r} has no entries in the log")
    dates = _normalise_dates(spec.dates)
    raw_dates = out["entry_date"]
    if not np.issubdtype(raw_dates.dtype, np.datetime64):
        raw_dates = pd.to_datetime(raw_dates)
    entry_days = raw_dates.to_numpy().astype("datetime64[D]")
    if dates is not None:
        eligible = ward_mask & np.isin(entry_days, dates.to_numpy().astype("datetime64[D]"))
    else:
        eligible = ward_mask
    eligible_idx = np.flatnonzero(eligible)

    ward_values = out.loc[ward_mask, "tonnage"].to_numpy(dtype=float)
    before = float(ward_values.sum())
    report = TamperReport(spec=spec, ward_tonnage_before_t=before)

    if spec.kind == "fabricate":
        lo, hi = spec.fabricate_range_t or tuple(float(x) for x in np.percentile(ward_values, [10, 90]))
        report.fabricate_range_used_t = (round(lo, 3), round(hi, 3))

    if spec.mode == "replace":
        if len(eligible_idx) == 0:
            raise ValueError("no eligible entries to replace (check ward_code/dates)")
        n = spec.n_entries if spec.n_entries is not None else int(round(spec.fraction * len(eligible_idx)))
        if not (1 <= n <= len(eligible_idx)):
            raise ValueError(f"cannot tamper {n} of {len(eligible_idx)} eligible entries")
        chosen = np.sort(rng.choice(eligible_idx, size=n, replace=False))
        old = out["tonnage"].to_numpy(dtype=float)[chosen]
        if spec.kind == "round":
            new = _rounded(old, spec)
        elif spec.kind == "fabricate":
            new = fabricate_values(rng, n, *report.fabricate_range_used_t)
        else:
            new = inflate_values(old, spec.inflate_factor, rng, spec.inflate_dequantize)
        col = out.columns.get_loc("tonnage")
        out.iloc[chosen, col] = new
        out.iloc[chosen, out.columns.get_loc("is_injected_tamper")] = True
        report.modified_entry_ids = [int(x) for x in out["entry_id"].to_numpy()[chosen]]
    else:  # add
        n_ref = len(eligible_idx) if len(eligible_idx) else int(ward_mask.sum())
        n = spec.n_entries if spec.n_entries is not None else int(round(spec.fraction * n_ref))
        if n < 1:
            raise ValueError("add mode resolved to zero entries")
        if dates is None:
            pool = pd.DatetimeIndex(np.unique(entry_days[ward_mask]))
        else:
            pool = dates
        new_dates = pool[rng.integers(0, len(pool), n)]
        if spec.kind == "round":
            new = _rounded(rng.choice(ward_values, size=n, replace=True), spec)
        else:
            new = fabricate_values(rng, n, *report.fabricate_range_used_t)
        start_id = int(out["entry_id"].max()) + 1
        new_ids = np.arange(start_id, start_id + n, dtype=np.int64)
        ward_col = out["ward_code"]
        ward_value = pd.Categorical([spec.ward_code] * n, categories=ward_col.cat.categories) if isinstance(
            ward_col.dtype, pd.CategoricalDtype
        ) else [spec.ward_code] * n
        added = pd.DataFrame(
            {
                "entry_id": new_ids,
                "ward_code": ward_value,
                "entry_date": new_dates,
                "tonnage": new,
                "data_origin": "synthetic",
                "is_injected_tamper": True,
            }
        )
        extra = [c for c in out.columns if c not in added.columns]
        for c in extra:
            added[c] = pd.NA
        out = pd.concat([out, added[out.columns]], ignore_index=True)
        report.added_entry_ids = [int(x) for x in new_ids]

    after_mask = (out["ward_code"] == spec.ward_code).to_numpy(dtype=bool)
    report.ward_tonnage_after_t = float(out.loc[after_mask, "tonnage"].sum())
    return out, report


def inject_many(
    logs: pd.DataFrame, specs: Sequence[TamperSpec], rng: np.random.Generator | int | Sequence[int]
) -> tuple[pd.DataFrame, list[TamperReport]]:
    """Apply several specs in order with one RNG stream."""
    rng = _as_rng(rng)
    reports = []
    for spec in specs:
        logs, rep = inject(logs, spec, rng)
        reports.append(rep)
    return logs, reports
