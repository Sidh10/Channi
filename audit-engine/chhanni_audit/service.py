"""JSON stdin/stdout service so the Node backend can call the audit engine as a subprocess.

    python -m chhanni_audit.service <command>      (run with cwd = audit-engine/)

Protocol: the caller writes ONE JSON object to stdin and reads ONE JSON object from stdout.
Logs and diagnostics go to stderr only. Exit code 0 = success; 2 = invalid input; 1 = internal
error. On error, stdout still carries {"error": "<message>", "error_type": ...}.

Commands
--------
version   {}  ->  {engine_version, commands}
generate  {seed?, params?}  ->  SYNTHETIC wards + trip-level log (defaults = demo seed 2026)
inject    {seed, ward_code, kind, placement?, fraction?, logs, period_start?, period_end?}
          ->  NEW phantom entries only (add mode; existing rows are never edited)
audit     {wards, logs, period_start?, period_end?, config?}
          ->  AuditResult.to_dict() content + a `db` section shaped for audit_runs /
              ward_audit_scores / audit_flags

Everything this service generates or injects is SYNTHETIC (docs/decisions.md D5). Python never
touches Postgres: Node owns every database read and write. The service adds no detection logic;
`audit` calls `pipeline.audit()` unchanged, and `tests/test_service.py` checks that the CLI result
equals a direct call on the same data.

Input conventions: dates are 'YYYY-MM-DD' strings (timestamps are rejected, so a time-zone shift
can't move an entry to another day); tonnage is in tonnes. Numbers may also arrive as decimal
strings and entry_id as a digit string, because node-pg returns NUMERIC and BIGINT as strings
unless a type parser is set. Unknown top-level keys are rejected (catches typos such as
'period_strat'); unknown keys inside a row are ignored, which is how `is_injected_tamper` is kept
out of detection even if a caller sends it.
"""

from __future__ import annotations

import time

_T_START = time.perf_counter()  # before the numpy/pandas/scipy imports, so stderr timing includes them

import datetime as _dt  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402
from dataclasses import fields, replace  # noqa: E402
from typing import Any, Callable  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from . import __version__  # noqa: E402
from .calibration import CRDT_DELHI_GENERATED_TPD, CRDT_SOURCE_NOTE  # noqa: E402
from .evaluate import DEMO_SEED, Scenario, add_count  # noqa: E402
from .pipeline import AuditConfig, AuditResult, audit  # noqa: E402
from .run_detection import to_json_safe  # noqa: E402
from .scoring import CHECKS  # noqa: E402
from .synthetic import DATA_ORIGIN, SyntheticParams, generate_dataset  # noqa: E402
from .tamper import TamperSpec  # noqa: E402
from .tamper import inject as tamper_inject  # noqa: E402

ENGINE_VERSION = __version__

INJECT_KINDS: tuple[str, ...] = ("round", "fabricate")
INJECT_PLACEMENTS: tuple[str, ...] = ("spread", "last_day")
MAX_INJECT_FRACTION = 1.0

# Default demo injection sizes (entries added / target ward's entries in the input log), per
# kind+placement, taken from the held-out demo-sizing table in
# data/synthetic/synthetic_detection_report.json (`python -m chhanni_audit.run_detection`,
# section [7]; held-out seeds 5000-5099). tests/test_service.py checks each one against that file.
# Held-out detection (any check) at these sizes, SYNTHETIC evaluation, engine 0.1.0:
#   round/spread 0.015 -> 1.00; round/last_day 0.015 -> 1.00 (minimum 0.01 -> 0.97);
#   fabricate/spread 0.30 -> 0.98; fabricate/last_day 0.075 -> 0.99 (minimum 0.05 -> 0.98).
# fabricate/spread uses the minimum, not the 0.40 margin size (1.00): at 0.40 the phantom entries
# add ~58% to the ward's tonnage and the baseline check co-fired in 41% of held-out runs (18% at
# 0.30), so the demo would show over-reporting being caught rather than isolating Benford.
DEFAULT_INJECT_FRACTIONS: dict[tuple[str, str], float] = {
    ("round", "spread"): 0.015,
    ("round", "last_day"): 0.015,
    ("fabricate", "spread"): 0.30,
    ("fabricate", "last_day"): 0.075,
}
DEFAULT_INJECT_BASIS: dict[tuple[str, str], str] = {
    ("round", "spread"): "held-out-confirmed minimum for round|add|spread (the calibration minimum 0.01 "
    "fell below 95% on held-out seeds, so the report moved to the next grid size, 0.015)",
    ("round", "last_day"): "margin size for round|add|last_day: one grid step above the held-out-confirmed "
    "minimum (0.01)",
    ("fabricate", "spread"): "held-out-confirmed minimum for fabricate|add|spread (the size decisions.md D14 "
    "states); the next grid size, 0.40, was not used because its larger tonnage increase makes the "
    "baseline check co-fire more often, blurring which layer caught the fabrication",
    ("fabricate", "last_day"): "margin size for fabricate|add|last_day: one grid step above the "
    "held-out-confirmed minimum (0.05)",
}

INJECT_NOTICE = (
    "SYNTHETIC TAMPERING (docs/decisions.md D5, D7 beat 2): these phantom entries were invented by "
    "chhanni_audit.tamper to demonstrate detection. They are not real records. Insert them with "
    "data_origin='synthetic' and is_injected_tamper=true (ground truth for evaluation only; the "
    "audit never reads it)."
)

DB_WRITE_RULE = (
    "Write db.run -> audit_runs, db.ward_scores -> ward_audit_scores and db.flags -> audit_flags, "
    "all in one transaction with finished_at. db.flags holds ONLY ward-level findings (one row per "
    "flagged ward x check); entry-level and ward-day localisation hints are inside each flag's "
    "details.localisation_hints and must not be written as separate flags. The top-level 'flags' "
    "list is the engine's transparency view (hints as rows) and must not be inserted."
)

_NUMBER_RE = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\Z")
_DIGITS_RE = re.compile(r"\d+\Z", re.ASCII)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z", re.ASCII)


class InvalidInput(ValueError):
    """The request is malformed or inconsistent (exit code 2)."""


# ---------------------------------------------------------------------------
# input parsing (small, isolated, testable)
# ---------------------------------------------------------------------------


def _reject_constant(name: str) -> Any:
    raise InvalidInput(f"non-finite number {name} is not allowed in the request")


def parse_request(raw: bytes, allow_empty: bool = False) -> dict[str, Any]:
    """Decode ONE JSON object from stdin bytes."""
    text = raw.decode("utf-8-sig").strip()
    if not text:
        if allow_empty:
            return {}
        raise InvalidInput("expected one JSON object on stdin, got nothing")
    try:
        payload = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise InvalidInput(f"stdin is not valid JSON: {e}") from None
    if not isinstance(payload, dict):
        raise InvalidInput(f"expected a JSON object on stdin, got {type(payload).__name__}")
    return payload


def check_keys(payload: dict[str, Any], allowed: set[str], required: set[str] = frozenset()) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise InvalidInput(f"unknown field(s) {unknown}; allowed: {sorted(allowed)}")
    missing = sorted(k for k in required if payload.get(k) is None)
    if missing:
        raise InvalidInput(f"missing required field(s) {missing}")


def parse_int(value: Any, name: str, minimum: int | None = None) -> int:
    """JSON integer or digit string (node-pg returns BIGINT as a string). Booleans rejected."""
    if type(value) is int:
        out = value
    elif type(value) is str and _DIGITS_RE.match(value):
        out = int(value)
    else:
        raise InvalidInput(f"{name} must be an integer (got {value!r})")
    if minimum is not None and out < minimum:
        raise InvalidInput(f"{name} must be >= {minimum} (got {out})")
    return out


def parse_number(value: Any, name: str) -> float:
    """Finite JSON number or decimal string (node-pg returns NUMERIC as a string). Booleans rejected."""
    if type(value) is int or type(value) is float:
        out = float(value)
    elif type(value) is str and _NUMBER_RE.match(value):
        out = float(value)
    else:
        raise InvalidInput(f"{name} must be a number (got {value!r})")
    if not math.isfinite(out):
        raise InvalidInput(f"{name} must be finite (got {value!r})")
    return out


def parse_date(value: Any, name: str) -> str:
    """Strict 'YYYY-MM-DD'. Timestamps are rejected so no time-zone shift can change the day."""
    if type(value) is not str or not _DATE_RE.match(value):
        raise InvalidInput(f"{name} must be a 'YYYY-MM-DD' date string (got {value!r})")
    try:
        _dt.date.fromisoformat(value)
    except ValueError:
        raise InvalidInput(f"{name} is not a valid calendar date (got {value!r})") from None
    return value


def parse_optional_date(value: Any, name: str) -> str | None:
    return None if value is None else parse_date(value, name)


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise InvalidInput(f"{key} must be a list of objects")
    for i, r in enumerate(rows):
        if type(r) is not dict:
            raise InvalidInput(f"{key}[{i}] must be an object")
    return rows


def _column(rows: list[dict[str, Any]], key: str, where: str) -> list[Any]:
    out = []
    for i, r in enumerate(rows):
        v = r.get(key)
        if v is None:
            raise InvalidInput(f"{where}[{i}].{key} is missing or null")
        out.append(v)
    return out


def _str_column(rows: list[dict[str, Any]], key: str, where: str) -> np.ndarray:
    vals = _column(rows, key, where)
    for i, v in enumerate(vals):
        if type(v) is not str or not v:
            raise InvalidInput(f"{where}[{i}].{key} must be a non-empty string (got {v!r})")
    return np.asarray(vals, dtype=object)


def _float_column(rows: list[dict[str, Any]], key: str, where: str) -> np.ndarray:
    vals = _column(rows, key, where)
    out = np.empty(len(vals), dtype=float)
    for i, v in enumerate(vals):
        if type(v) is float or type(v) is int:
            out[i] = v
        else:
            out[i] = parse_number(v, f"{where}[{i}].{key}")
    bad = np.flatnonzero(~np.isfinite(out))
    if bad.size:
        raise InvalidInput(f"{where}[{int(bad[0])}].{key} must be finite")
    return out


def _int_column(rows: list[dict[str, Any]], key: str, where: str) -> np.ndarray:
    vals = _column(rows, key, where)
    out = np.empty(len(vals), dtype=np.int64)
    for i, v in enumerate(vals):
        x = v if type(v) is int else parse_int(v, f"{where}[{i}].{key}")
        if not (0 <= x < 2**63):
            raise InvalidInput(f"{where}[{i}].{key} out of range (got {v!r})")
        out[i] = x
    return out


def _date_column(rows: list[dict[str, Any]], key: str, where: str) -> np.ndarray:
    vals = _column(rows, key, where)
    cache: dict[str, np.datetime64] = {}
    out = np.empty(len(vals), dtype="datetime64[D]")
    for i, v in enumerate(vals):
        d = cache.get(v) if type(v) is str else None
        if d is None:
            d = np.datetime64(parse_date(v, f"{where}[{i}].{key}"), "D")
            cache[v] = d
        out[i] = d
    return out


def logs_frame(rows: list[dict[str, Any]], where: str = "logs", require_origin: bool = True) -> pd.DataFrame:
    """Validated log DataFrame with ONLY the columns detection may see (+ data_origin for labelling).

    Any other key in a row (e.g. is_injected_tamper) is never read."""
    if not rows:
        raise InvalidInput(f"{where} must not be empty")
    entry_id = _int_column(rows, "entry_id", where)
    if len(np.unique(entry_id)) != len(entry_id):
        raise InvalidInput(f"{where}: entry_id values must be unique")
    tonnage = _float_column(rows, "tonnage", where)
    neg = np.flatnonzero(tonnage < 0)
    if neg.size:
        raise InvalidInput(f"{where}[{int(neg[0])}].tonnage must be >= 0")
    data = {
        "entry_id": entry_id,
        "ward_code": _str_column(rows, "ward_code", where),
        "entry_date": _date_column(rows, "entry_date", where).astype("datetime64[ns]"),
        "tonnage": tonnage,
    }
    if require_origin:
        data["data_origin"] = _str_column(rows, "data_origin", where)
    return pd.DataFrame(data)


def wards_frame(rows: list[dict[str, Any]], where: str = "wards") -> pd.DataFrame:
    """Validated ward DataFrame (socio-economic proxies + data_origin). Other keys are ignored."""
    if not rows:
        raise InvalidInput(f"{where} must not be empty")
    codes = _str_column(rows, "ward_code", where)
    if len(set(codes.tolist())) != len(codes):
        raise InvalidInput(f"{where}: ward_code values must be unique")
    population = _float_column(rows, "population", where)
    if np.any(population <= 0):
        raise InvalidInput(f"{where}[{int(np.flatnonzero(population <= 0)[0])}].population must be > 0")
    return pd.DataFrame(
        {
            "ward_code": codes,
            "population": population,
            "income_proxy": _float_column(rows, "income_proxy", where),
            "sanitation_proxy": _float_column(rows, "sanitation_proxy", where),
            "data_origin": _str_column(rows, "data_origin", where),
        }
    )


def restrict_window(logs: pd.DataFrame, period_start: str | None, period_end: str | None) -> pd.DataFrame:
    """Rows with period_start <= entry_date <= period_end (either bound may be None)."""
    if period_start is not None and period_end is not None and period_end < period_start:
        raise InvalidInput(f"period_end {period_end} is before period_start {period_start}")
    days = logs["entry_date"].to_numpy().astype("datetime64[D]")
    sel = np.ones(len(logs), dtype=bool)
    if period_start is not None:
        sel &= days >= np.datetime64(period_start, "D")
    if period_end is not None:
        sel &= days <= np.datetime64(period_end, "D")
    if not sel.any():
        raise InvalidInput(f"no log entries between {period_start or 'start'} and {period_end or 'end'}")
    return logs.loc[sel].reset_index(drop=True)


def _coerce_like(default: Any, value: Any, name: str) -> Any:
    """Coerce an override to the type of the dataclass default it replaces."""
    if type(default) is int:
        return parse_int(value, name) if type(value) is int else _fail(f"{name} must be an integer (got {value!r})")
    if type(default) is float:
        if type(value) not in (int, float):
            _fail(f"{name} must be a number (got {value!r})")
        return parse_number(value, name)
    if type(default) is str:
        if type(value) is not str:
            _fail(f"{name} must be a string (got {value!r})")
        return value
    if type(default) is tuple:
        if not isinstance(value, list):
            _fail(f"{name} must be a list (got {value!r})")
        if default and type(default[0]) is int:
            return tuple(parse_int(v, f"{name}[{i}]") if type(v) is int else _fail(f"{name}[{i}] must be an integer") for i, v in enumerate(value))
        return tuple(parse_number(v, f"{name}[{i}]") if type(v) in (int, float) else _fail(f"{name}[{i}] must be a number") for i, v in enumerate(value))
    _fail(f"{name} cannot be overridden")


def _fail(msg: str) -> Any:
    raise InvalidInput(msg)


def synthetic_params(overrides: Any) -> SyntheticParams:
    """SyntheticParams with validated overrides; {} or None gives the demo defaults."""
    base = SyntheticParams()
    if overrides is None:
        return base
    if not isinstance(overrides, dict):
        raise InvalidInput("params must be an object of SyntheticParams overrides")
    names = {f.name for f in fields(SyntheticParams)}
    unknown = sorted(set(overrides) - names)
    if unknown:
        raise InvalidInput(f"unknown params {unknown}; allowed: {sorted(names)}")
    kwargs = {k: _coerce_like(getattr(base, k), v, f"params.{k}") for k, v in overrides.items()}
    if "start_date" in kwargs:
        parse_date(kwargs["start_date"], "params.start_date")
    try:
        params = replace(base, **kwargs)
    except ValueError as e:
        raise InvalidInput(f"invalid params: {e}") from None
    if params.n_days > 366 or params.n_wards > 1000:
        raise InvalidInput("params too large for one request (n_days <= 366, n_wards <= 1000)")
    return params


def audit_config(overrides: Any) -> AuditConfig:
    """AuditConfig with validated overrides; {} or None gives the fixed a-priori defaults."""
    base = AuditConfig()
    if overrides is None:
        return base
    if not isinstance(overrides, dict):
        raise InvalidInput("config must be an object of AuditConfig overrides")
    if "multiple_testing" in overrides:
        raise InvalidInput("config.multiple_testing is a descriptive label of the fixed procedure, not a setting")
    names = {f.name for f in fields(AuditConfig)} - {"multiple_testing"}
    unknown = sorted(set(overrides) - names)
    if unknown:
        raise InvalidInput(f"unknown config {unknown}; allowed: {sorted(names)}")
    kw = {k: _coerce_like(getattr(base, k), v, f"config.{k}") for k, v in overrides.items()}
    if "alpha" in kw and not (0.0 < kw["alpha"] < 1.0):
        raise InvalidInput("config.alpha must be in (0, 1)")
    if "benford_reference" in kw and kw["benford_reference"] not in ("benford", "peer"):
        raise InvalidInput("config.benford_reference must be 'benford' or 'peer'")
    if "baseline_direction" in kw and kw["baseline_direction"] not in ("upper", "two_sided"):
        raise InvalidInput("config.baseline_direction must be 'upper' or 'two_sided'")
    if "roundness_resolutions_kg" in kw and (not kw["roundness_resolutions_kg"] or min(kw["roundness_resolutions_kg"]) < 1):
        raise InvalidInput("config.roundness_resolutions_kg must be a non-empty list of positive integers")
    for k in ("huber_c", "benford_min_expected"):
        if k in kw and kw[k] <= 0:
            raise InvalidInput(f"config.{k} must be > 0")
    if "benford_min_span_decades" in kw and kw["benford_min_span_decades"] < 0:
        raise InvalidInput("config.benford_min_span_decades must be >= 0")
    return replace(base, **kw)


# ---------------------------------------------------------------------------
# generate
# ---------------------------------------------------------------------------


def generate_notice(seed: int, params: SyntheticParams) -> str:
    if params.city_daily_total_t == CRDT_DELHI_GENERATED_TPD:
        calib = f"Calibration: {CRDT_SOURCE_NOTE}"
    else:
        calib = (
            f"Calibration OVERRIDDEN: city mean daily total set to {params.city_daily_total_t:g} t by the request "
            "(a synthetic choice, NOT the CRDT figure)."
        )
    return (
        f"SYNTHETIC DATA (docs/decisions.md D5): generated by chhanni_audit.synthetic with seed {seed}. "
        "Not MCD, census or any other real municipal data; ward names, populations and proxies are synthetic. "
        + calib
    )


def generate_payload(seed: int, params: SyntheticParams) -> dict[str, Any]:
    ds = generate_dataset(seed, params)
    w = ds.wards
    wards = [
        {
            "ward_code": c,
            "name": n,
            "zone": z,
            "population": int(p),
            "income_proxy": float(i),
            "sanitation_proxy": float(s),
            "data_origin": DATA_ORIGIN,
        }
        for c, n, z, p, i, s in zip(
            w["ward_code"].tolist(),
            w["name"].tolist(),
            w["zone"].tolist(),
            w["population"].tolist(),
            w["income_proxy"].tolist(),
            w["sanitation_proxy"].tolist(),
        )
    ]
    lg = ds.logs
    dates = lg["entry_date"].dt.strftime("%Y-%m-%d").tolist()
    # Rows keep the generator's order (by day, then ward). Inserted in this order into an empty
    # table, BIGSERIAL ids equal the generator's entry_id (1..N).
    logs = [
        {"ward_code": c, "entry_date": d, "tonnage": t, "data_origin": DATA_ORIGIN}
        for c, d, t in zip(lg["ward_code"].astype(str).tolist(), dates, lg["tonnage"].tolist())
    ]
    return {
        "data_notice": generate_notice(seed, params),
        "engine_version": ENGINE_VERSION,
        "seed": int(seed),
        "params": params.to_dict(),
        "summary": {
            "data_origin": DATA_ORIGIN,
            "n_wards": len(wards),
            "n_entries": len(logs),
            "period_start": ds.period_start.date().isoformat(),
            "period_end": ds.period_end.date().isoformat(),
            "clean_loads_exactly_benford_by_construction": params.exactly_benford,
        },
        "wards": wards,
        "logs": logs,
    }


def cmd_generate(payload: dict[str, Any]) -> dict[str, Any]:
    check_keys(payload, {"seed", "params"})
    seed = parse_int(payload.get("seed", DEMO_SEED), "seed", minimum=0)
    return generate_payload(seed, synthetic_params(payload.get("params")))


# ---------------------------------------------------------------------------
# inject (targeted single-ward phantom entries, ADD mode only)
# ---------------------------------------------------------------------------


def inject_entries(
    logs: pd.DataFrame, seed: int, ward_code: str, kind: str, placement: str = "spread", fraction: float | None = None
) -> dict[str, Any]:
    """New phantom entries for ONE ward, appended (never edits). `logs` is not modified.

    Mirrors `evaluate.apply_scenario` for a single target in add mode, including its random
    stream (default_rng([seed, 11, scenario.stream_id()])), so with seed 2026 on the clean
    seed-2026 log this returns exactly the injection that detection-report section [9] audited.
    Rows are sorted by entry_id first, so the result doesn't depend on input row order."""
    if kind not in INJECT_KINDS:
        hint = " (inflation edits existing rows; the demo log is append-only)" if kind == "inflate" else ""
        raise InvalidInput(f"kind must be one of {list(INJECT_KINDS)}, got {kind!r}{hint}")
    if placement not in INJECT_PLACEMENTS:
        raise InvalidInput(f"placement must be one of {list(INJECT_PLACEMENTS)}, got {placement!r}")
    default = DEFAULT_INJECT_FRACTIONS[(kind, placement)]
    frac = default if fraction is None else float(fraction)
    if not (0.0 < frac <= MAX_INJECT_FRACTION):
        raise InvalidInput(f"fraction must be in (0, {MAX_INJECT_FRACTION}], got {fraction!r}")

    ordered = logs.sort_values("entry_id", kind="stable")
    ward_logs = ordered.loc[(ordered["ward_code"] == ward_code).to_numpy(dtype=bool)].reset_index(drop=True)
    n_ward = len(ward_logs)
    if n_ward == 0:
        raise InvalidInput(f"ward {ward_code!r} has no entries in the input log")
    n_add = add_count(frac, n_ward)
    last_day = pd.Timestamp(ordered["entry_date"].max()).date().isoformat()
    dates = (last_day,) if placement == "last_day" else None

    scenario = Scenario(kind=kind, mode="add", fraction=frac, placement=placement, n_targets=1)
    rng = np.random.default_rng([int(seed), 11, scenario.stream_id()])
    spec = TamperSpec(kind=kind, ward_code=ward_code, mode="add", n_entries=n_add, dates=dates)
    tampered, report = tamper_inject(ward_logs, spec, rng)
    added = tampered.iloc[n_ward:]
    if len(added) != n_add or report.modified_entry_ids:
        raise RuntimeError("injector did not return exactly the requested appended rows")

    entries = [
        {
            "ward_code": ward_code,
            "entry_date": d,
            "tonnage": float(t),
            "data_origin": DATA_ORIGIN,
            "is_injected_tamper": True,
        }
        for d, t in zip(pd.DatetimeIndex(added["entry_date"]).strftime("%Y-%m-%d").tolist(), added["tonnage"].tolist())
    ]
    rep = report.to_dict()
    # The injector numbers new rows after the input's max entry_id; the database assigns the real
    # ids on insert, so the provisional ones are dropped rather than risk being used as references.
    rep.pop("added_entry_ids", None)
    rep["n_added"] = n_add
    rep["entry_id_note"] = "new rows carry no entry_id; the database assigns ids on insert"
    return {
        "data_notice": INJECT_NOTICE,
        "engine_version": ENGINE_VERSION,
        "sizing": {
            "kind": kind,
            "placement": placement,
            "fraction": frac,
            "fraction_source": "default" if fraction is None else "request",
            "default_fraction": default,
            "default_basis": DEFAULT_INJECT_BASIS[(kind, placement)],
            "source_table": "data/synthetic/synthetic_detection_report.json -> demo_sizing (held-out seeds 5000-5099)",
            "target_ward_entries": n_ward,
            "n_added": n_add,
            "size_rule": "n_added = max(1, round(fraction x target ward's entries in the input log))",
            "dates": [last_day] if dates else "random days among the ward's existing entry dates",
            "seed": int(seed),
        },
        "entries": entries,
        "report": rep,
    }


def cmd_inject(payload: dict[str, Any]) -> dict[str, Any]:
    check_keys(
        payload,
        {"seed", "ward_code", "kind", "placement", "fraction", "logs", "period_start", "period_end"},
        required={"seed", "ward_code", "kind", "logs"},
    )
    seed = parse_int(payload["seed"], "seed", minimum=0)
    ward_code = payload["ward_code"]
    if type(ward_code) is not str or not ward_code:
        raise InvalidInput("ward_code must be a non-empty string")
    kind, placement = payload["kind"], payload.get("placement") or "spread"
    fraction = payload.get("fraction")
    if fraction is not None:
        if type(fraction) not in (int, float):
            raise InvalidInput(f"fraction must be a number (got {fraction!r})")
        fraction = parse_number(fraction, "fraction")
    logs = logs_frame(_rows(payload, "logs"), require_origin=False)
    ps = parse_optional_date(payload.get("period_start"), "period_start")
    pe = parse_optional_date(payload.get("period_end"), "period_end")
    logs = restrict_window(logs, ps, pe)
    return inject_entries(logs, seed, ward_code, kind, placement, fraction)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def _hints_skeleton(check: str, hint_details: dict[str, Any]) -> dict[str, Any]:
    if check == "roundness":
        return {
            "level": "entry",
            "is_flag": False,
            "multiple_of_t": hint_details.get("multiple_of_t"),
            "score_meaning": hint_details.get("score_meaning"),
            "note": hint_details.get("note"),
            "entries": [],
        }
    return {"level": "ward_day", "is_flag": False, "score_meaning": hint_details.get("score_meaning"), "days": []}


def db_flags(result: AuditResult) -> list[dict[str, Any]]:
    """audit_flags rows: ONE ward-level row per (flagged ward, check that fired).

    Localisation hints (roundness entries, baseline_daily days) go into that row's
    details.localisation_hints, never into rows of their own (tasklist Phase 4 rule (d))."""
    out: list[dict[str, Any]] = []
    parent: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in result.flag_records(include_localisation=True):
        key = (rec["ward_code"], rec["check_type"])
        det = rec["details"]
        if det.get("level") == "ward":
            row = {
                "ward_code": rec["ward_code"],
                "check_type": rec["check_type"],
                "period_start": rec["period_start"],
                "period_end": rec["period_end"],
                "score": rec["score"],
                "details": {**det, "localisation_hints": None},
            }
            parent[key] = row
            out.append(row)
            continue
        if key not in parent:
            raise RuntimeError(f"localisation hint for {key} has no ward-level flag")
        details = parent[key]["details"]
        if details["localisation_hints"] is None:
            details["localisation_hints"] = _hints_skeleton(rec["check_type"], det)
        hints = details["localisation_hints"]
        if det.get("level") == "entry":
            hints["entries"].append({"entry_id": rec["log_entry_ref"], "entry_date": rec["period_start"], "value_t": det["value_t"]})
        elif det.get("level") == "ward_day":
            hints["days"].append(
                {
                    "date": rec["period_start"],
                    "observed_total_t": det["observed_total_t"],
                    "expected_total_t": det["expected_total_t"],
                    "z": det["z"],
                    "p_adjusted": rec["score"],
                }
            )
        else:
            raise RuntimeError(f"unexpected flag level {det.get('level')!r}")
    for row in out:
        hints = row["details"]["localisation_hints"]
        if hints is not None:
            hints["n"] = len(hints.get("entries", hints.get("days", [])))
    return out


def check_db_section(db: dict[str, Any]) -> None:
    """Internal consistency of what Node will insert; raises RuntimeError if violated."""
    run = db["run"]
    ps, pe = run["period_start"], run["period_end"]
    if not ps or not pe or ps > pe:
        raise RuntimeError(f"bad run period {ps!r}..{pe!r}")
    flagged = {s["ward_code"] for s in db["ward_scores"] if s["flagged"]}
    scored = {s["ward_code"] for s in db["ward_scores"]}
    with_flag = {f["ward_code"] for f in db["flags"]}
    if flagged - with_flag:
        raise RuntimeError(f"flagged wards without a flag row: {sorted(flagged - with_flag)}")
    if with_flag - flagged:
        raise RuntimeError(f"flag rows for wards not flagged: {sorted(with_flag - flagged)}")
    if len(scored) != len(db["ward_scores"]):
        raise RuntimeError("duplicate ward in ward_scores")
    seen = set()
    for f in db["flags"]:
        key = (f["ward_code"], f["check_type"])
        if key in seen:
            raise RuntimeError(f"duplicate flag row {key}")
        seen.add(key)
        if f["check_type"] not in CHECKS or f["details"].get("level") != "ward":
            raise RuntimeError(f"non-ward-level or unknown flag {key}")
        if f["period_start"] != ps or f["period_end"] != pe:
            raise RuntimeError(f"flag {key} period differs from the run period")


def run_audit(
    wards: pd.DataFrame,
    logs: pd.DataFrame,
    period_start: str | None = None,
    period_end: str | None = None,
    config: AuditConfig | None = None,
) -> dict[str, Any]:
    """`pipeline.audit()` on the requested window plus the `db` section.

    The window passed to audit() is the dates actually present inside the requested bounds
    (first..last entry date), so db.run.period_start/end are the dates audited and audit() never
    pads the window with days that have no data at all."""
    window = restrict_window(logs, period_start, period_end)
    days = window["entry_date"].to_numpy().astype("datetime64[D]")
    start, end = str(days.min()), str(days.max())
    result = audit(wards, window, config or AuditConfig(), period_start=start, period_end=end, localise=True)
    out = result.to_dict(include_localisation=True)
    db = {
        "write_rule": DB_WRITE_RULE,
        "run": {
            "engine_version": result.engine_version,
            "params": {
                **result.run_params(),
                "requested_period_start": period_start,
                "requested_period_end": period_end,
                "window_rule": "audited window = first..last entry_date inside the requested bounds",
                "n_log_rows_received": int(len(logs)),
                "n_log_rows_in_window": int(len(window)),
            },
            "summary": result.run_summary(),
            "period_start": result.period_start.date().isoformat(),
            "period_end": result.period_end.date().isoformat(),
        },
        "ward_scores": result.ward_score_records(),
        "flags": db_flags(result),
    }
    if (db["run"]["period_start"], db["run"]["period_end"]) != (start, end):
        raise RuntimeError("audit period does not match the dates audited")
    check_db_section(db)
    out["db"] = db
    return out


def cmd_audit(payload: dict[str, Any]) -> dict[str, Any]:
    check_keys(payload, {"wards", "logs", "period_start", "period_end", "config"}, required={"wards", "logs"})
    wards = wards_frame(_rows(payload, "wards"))
    logs = logs_frame(_rows(payload, "logs"), require_origin=True)
    ps = parse_optional_date(payload.get("period_start"), "period_start")
    pe = parse_optional_date(payload.get("period_end"), "period_end")
    return run_audit(wards, logs, ps, pe, audit_config(payload.get("config")))


# ---------------------------------------------------------------------------
# version / dispatch / CLI
# ---------------------------------------------------------------------------


def cmd_version(payload: dict[str, Any]) -> dict[str, Any]:
    return {"engine_version": ENGINE_VERSION, "commands": sorted(COMMANDS)}


COMMANDS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "version": cmd_version,
    "generate": cmd_generate,
    "inject": cmd_inject,
    "audit": cmd_audit,
}


def run_command(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    """In-process entry point (same code path as the CLI, minus stdin/stdout)."""
    handler = COMMANDS.get(command)
    if handler is None:
        raise InvalidInput(f"unknown command {command!r}; expected one of {sorted(COMMANDS)}")
    return handler(payload)


def encode(obj: Any) -> bytes:
    """Compact, strict JSON (numpy -> python, NaN/inf -> null, ASCII only)."""
    return json.dumps(to_json_safe(obj), allow_nan=False, separators=(",", ":")).encode("ascii")


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process in MiB (None if unavailable)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            fn = kernel32.K32GetProcessMemoryInfo
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
            fn.restype = wintypes.BOOL
            if fn(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return pmc.PeakWorkingSetSize / 2**20
            return None
        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss / 2**20 if sys.platform == "darwin" else rss / 1024
    except Exception:  # diagnostics must never break the response
        return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    t_cmd = time.perf_counter()
    out_stream = sys.stdout.buffer
    command = argv[0] if len(argv) == 1 else None
    code = 0
    try:
        # Anything printed by library code goes to stderr, so stdout stays exactly one JSON object.
        with redirect_stdout(sys.stderr):
            if command is None or command.startswith("-"):
                raise InvalidInput(f"usage: python -m chhanni_audit.service <{'|'.join(sorted(COMMANDS))}>  (JSON on stdin)")
            if command not in COMMANDS:
                raise InvalidInput(f"unknown command {command!r}; expected one of {sorted(COMMANDS)}")
            if command == "version" and (sys.stdin is None or sys.stdin.isatty()):
                payload: dict[str, Any] = {}
            else:
                payload = parse_request(sys.stdin.buffer.read(), allow_empty=(command == "version"))
            body = encode(run_command(command, payload))
    except InvalidInput as e:
        code = 2
        body = encode({"error": str(e), "error_type": "invalid_input"})
    except ValueError as e:  # raised by the engine for inconsistent data (e.g. unknown ward)
        code = 2
        traceback.print_exc(file=sys.stderr)
        body = encode({"error": str(e), "error_type": "invalid_input"})
    except Exception as e:  # noqa: BLE001 - report every failure as JSON, never a bare traceback
        code = 1
        traceback.print_exc(file=sys.stderr)
        body = encode({"error": f"{type(e).__name__}: {e}", "error_type": "internal"})
    out_stream.write(body + b"\n")
    out_stream.flush()
    mem = peak_memory_mb()
    status = "ok" if code == 0 else f"error (exit {code})"
    mem_txt = f", peak memory {mem:.0f} MiB" if mem is not None else ""
    now = time.perf_counter()
    print(
        f"chhanni_audit.service {command or '-'}: {status} in {now - _T_START:.2f}s incl. imports "
        f"(command {now - t_cmd:.2f}s){mem_txt}, {len(body):,} bytes out",
        file=sys.stderr,
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
