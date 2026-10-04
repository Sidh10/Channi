"""Evaluation harness for the audit engine on SYNTHETIC data.

This is the only place ground truth (`is_injected_tamper`) is read: it is joined
to the audit output AFTER `audit()` has returned, to score detection.

Every scenario runs on many seeds; results are reported as mean / min / max
across seeds, always with the false-positive rate on clean wards from the same
runs. Thresholds are fixed a priori in `AuditConfig`; nothing here tunes them.
The one calibrated quantity, the minimum demo injection size, is chosen on one
seed set and confirmed on a disjoint held-out seed set.
"""

from __future__ import annotations

import sys
import zlib
from concurrent.futures import Executor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from .pipeline import AuditConfig, audit
from .scoring import CHECKS, LAYERS, wilson_interval
from .synthetic import SyntheticDataset, SyntheticParams, generate_dataset
from .tamper import TamperSpec, inject

OUTCOME_KEYS: tuple[str, ...] = CHECKS + tuple(LAYERS) + ("any",)

# Disjoint seed sets (documented in the report).
POWER_SEEDS = tuple(range(0, 30))
DEMO_CALIBRATION_SEEDS = tuple(range(1000, 1060))
DEMO_HOLDOUT_SEEDS = tuple(range(5000, 5100))
SENSITIVITY_SEEDS = tuple(range(3000, 3020))
DEMO_SEED = 2026
SANITY_CHECK_SEEDS_USED_IN_DEVELOPMENT = "9000-9059 and 9100-9109 (null-calibration checks during development; never reported)"

POWER_FRACTIONS = (0.002, 0.005, 0.01, 0.02, 0.05, 0.10, 0.20, 0.35, 0.50, 1.0)
INFLATE_FACTOR_FOR_FRACTION_CURVE = 1.5
INFLATION_FACTORS = (1.1, 1.2, 1.3, 1.5, 1.75, 2.0, 2.5, 3.0)
N_TARGETS_MULTI = 4
# Demo injections (add mode) are sized RELATIVE to the target ward's entries in the window,
# because detection depends on relative size and synthetic wards differ ~8x in entry count.
DEMO_ADD_FRACTIONS = (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50)
DEMO_RELIABILITY = 0.95
APPROX_BENFORD_SIGMAS = (0.25, 0.30, 0.35, 0.40, 0.45, 0.50)


@dataclass(frozen=True)
class Scenario:
    """One audited configuration. kind=None means no tampering (clean run).

    For mode='add' with `fraction`, the number of added entries is
    round(fraction * target ward's entries in the window), whatever the placement."""

    kind: str | None
    mode: str = "replace"
    fraction: float | None = None
    n_entries: int | None = None
    placement: str = "spread"  # 'spread' over the window, or 'last_day'
    inflate_factor: float = 1.5
    inflate_dequantize: bool = True
    n_targets: int = N_TARGETS_MULTI

    @property
    def key(self) -> str:
        return "|".join(str(v) for v in asdict(self).values())

    def stream_id(self) -> int:
        return zlib.crc32(self.key.encode("utf-8"))


def choose_targets(seed: int, ward_codes: Sequence[str], k: int) -> list[str]:
    """Target wards for a seed (own RNG stream, independent of the data generator)."""
    if k == 0:
        return []
    rng = np.random.default_rng([int(seed), 7])
    idx = np.sort(rng.choice(len(ward_codes), size=k, replace=False))
    return [str(ward_codes[i]) for i in idx]


def add_count(fraction: float, ward_entries: int) -> int:
    """Entries to add for a relative add-mode size."""
    return max(1, int(round(fraction * ward_entries)))


def apply_scenario(ds: SyntheticDataset, scenario: Scenario, targets: Sequence[str]) -> tuple[pd.DataFrame, list[Any]]:
    """Inject the scenario's tampering into each target ward."""
    if scenario.kind is None:
        return ds.logs, []
    rng = np.random.default_rng([ds.seed, 11, scenario.stream_id()])
    if scenario.placement == "last_day":
        dates = (ds.period_end.date().isoformat(),)
    elif scenario.placement == "spread":
        dates = None
    else:
        raise ValueError(f"unknown placement {scenario.placement!r}")
    counts = ds.logs.groupby("ward_code", observed=True).size()
    logs = ds.logs
    reports = []
    for ward in targets:
        fraction, n_entries = scenario.fraction, scenario.n_entries
        if scenario.mode == "add" and fraction is not None:
            fraction, n_entries = None, add_count(scenario.fraction, int(counts[ward]))
        spec = TamperSpec(
            kind=scenario.kind,
            ward_code=ward,
            mode=scenario.mode,
            fraction=fraction,
            n_entries=n_entries,
            dates=dates if scenario.mode == "add" else None,
            inflate_factor=scenario.inflate_factor,
            inflate_dequantize=scenario.inflate_dequantize,
        )
        logs, rep = inject(logs, spec, rng)
        reports.append(rep)
    return logs, reports


def score_run(result_table: pd.DataFrame, logs: pd.DataFrame) -> dict[str, Any]:
    """Join ground truth to the audit output. Returns per-check TP/FP counts."""
    truth = logs.groupby("ward_code", observed=True)["is_injected_tamper"].any()
    tampered = result_table["ward_code"].map(truth).fillna(False).to_numpy(dtype=bool)
    flags = {c: result_table[f"flag_{c}"].to_numpy(dtype=bool) for c in CHECKS}
    for layer, members in LAYERS.items():
        flags[layer] = np.logical_or.reduce([flags[m] for m in members])
    flags["any"] = result_table["flagged"].to_numpy(dtype=bool)
    out: dict[str, Any] = {"n_tampered": int(tampered.sum()), "n_clean": int((~tampered).sum())}
    for k in OUTCOME_KEYS:
        out[f"tp_{k}"] = int(np.sum(flags[k] & tampered))
        out[f"fp_{k}"] = int(np.sum(flags[k] & ~tampered))
    out["n_benford_applicable"] = int(np.isfinite(result_table["p_benford"].to_numpy(dtype=float)).sum())
    out["tampered_wards"] = [str(w) for w in result_table["ward_code"][tampered]]
    out["flagged_wards"] = [str(w) for w in result_table["ward_code"][flags["any"]]]
    return out


Task = tuple  # (tag, seed, params, scenarios, config)


def run_seed(task: Task) -> list[dict[str, Any]]:
    """All scenarios for one seed (one dataset). Top-level so it can run in a worker process."""
    tag, seed, params, scenarios, config = task
    ds = generate_dataset(seed, params)
    codes = ds.wards["ward_code"].tolist()
    entries_per_ward = ds.logs.groupby("ward_code", observed=True).size()
    outcomes = []
    for sc in scenarios:
        targets = choose_targets(seed, codes, sc.n_targets if sc.kind is not None else 0)
        logs, reports = apply_scenario(ds, sc, targets)
        res = audit(ds.wards, logs, config, localise=False)
        table = res.ward_table
        o = score_run(table, logs)
        if sorted(o["tampered_wards"]) != sorted(targets):
            raise AssertionError("ground truth does not match injected targets")
        o["tag"] = tag
        o["seed"] = seed
        o["scenario"] = sc.key
        o["targets"] = targets
        o["n_tampered_entries"] = [r.n_tampered for r in reports]
        o["overreport_ratio"] = [r.overreport_ratio for r in reports]
        o["target_entries_before"] = [int(entries_per_ward[t]) for t in targets]
        outcomes.append(o)
    return outcomes


def run_tasks(tasks: list[Task], executor: Executor | None = None) -> list[dict[str, Any]]:
    """Run seed tasks (in parallel if an executor is given). Output order = task order.

    If the worker pool breaks (e.g. Windows runs out of commit memory while spawning
    workers), the tasks are re-run serially: every task is independently seeded, so the
    result is identical either way."""
    if executor is None:
        results = [run_seed(t) for t in tasks]
    else:
        try:
            results = list(executor.map(run_seed, tasks, chunksize=1))
        except BrokenProcessPool:
            print("warning: worker pool broke (low memory?); re-running these tasks serially", file=sys.stderr)
            results = [run_seed(t) for t in tasks]
    return [o for chunk in results for o in chunk]


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def _summ(values: Iterable[float], nd: int = 4) -> dict[str, float | None]:
    v = np.asarray([x for x in values if x is not None and np.isfinite(x)], dtype=float)
    if v.size == 0:
        return {"mean": None, "min": None, "max": None}
    return {"mean": round(float(v.mean()), nd), "min": round(float(v.min()), nd), "max": round(float(v.max()), nd)}


def aggregate(outcomes: list[dict[str, Any]], scenario: Scenario, tag: str | None = None) -> dict[str, Any]:
    """Mean/min/max across seeds of per-seed TPR (tampered wards) and FPR (clean wards)."""
    rows = [o for o in outcomes if o["scenario"] == scenario.key and (tag is None or o["tag"] == tag)]
    if not rows:
        raise ValueError(f"no outcomes for scenario {scenario.key} tag {tag}")
    checks = {}
    for k in OUTCOME_KEYS:
        tpr = [o[f"tp_{k}"] / o["n_tampered"] for o in rows if o["n_tampered"] > 0]
        fpr = [o[f"fp_{k}"] / o["n_clean"] for o in rows if o["n_clean"] > 0]
        tp_total = sum(o[f"tp_{k}"] for o in rows)
        n_t_total = sum(o["n_tampered"] for o in rows)
        checks[k] = {
            "tpr": _summ(tpr),
            "fpr": _summ(fpr),
            "tp_pooled": tp_total,
            "n_tampered_pooled": n_t_total,
            "fp_pooled": sum(o[f"fp_{k}"] for o in rows),
            "n_clean_pooled": sum(o["n_clean"] for o in rows),
            "runs_with_any_false_flag": round(float(np.mean([o[f"fp_{k}"] > 0 for o in rows])), 4),
        }
        if n_t_total:
            lo, hi = wilson_interval(tp_total, n_t_total)
            checks[k]["tpr_pooled_wilson95"] = [round(lo, 4), round(hi, 4)]
    ratios = [r for o in rows for r in o["overreport_ratio"]]
    ntamp = [n for o in rows for n in o["n_tampered_entries"]]
    before = [n for o in rows for n in o["target_entries_before"]]
    frac = [n / (b + (n if scenario.mode == "add" else 0)) for n, b in zip(ntamp, before)]
    return {
        "scenario": asdict(scenario),
        "tag": tag,
        "n_seeds": len(rows),
        "seeds": [o["seed"] for o in rows],
        "n_targets_per_run": scenario.n_targets if scenario.kind is not None else 0,
        "n_clean_wards_per_run": _summ([o["n_clean"] for o in rows], 1)["mean"],
        "benford_applicable_share": _summ([o["n_benford_applicable"] / (o["n_clean"] + o["n_tampered"]) for o in rows]),
        "overreport_ratio": _summ(ratios),
        "n_tampered_entries_per_target": _summ(ntamp, 1),
        "tampered_share_of_target_entries": _summ(frac),
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# experiments
# ---------------------------------------------------------------------------


def _unique(scenarios: list[Scenario]) -> list[Scenario]:
    seen, out = set(), []
    for sc in scenarios:
        if sc.key not in seen:
            seen.add(sc.key)
            out.append(sc)
    return out


CLEAN = Scenario(kind=None, n_targets=0)


def power_scenarios() -> list[Scenario]:
    sc = [CLEAN]
    for kind in ("round", "fabricate", "inflate"):
        for f in POWER_FRACTIONS:
            sc.append(Scenario(kind=kind, fraction=f, inflate_factor=INFLATE_FACTOR_FOR_FRACTION_CURVE))
    for factor in INFLATION_FACTORS:
        sc.append(Scenario(kind="inflate", fraction=1.0, inflate_factor=factor))
    for factor in (1.5, 2.0):
        sc.append(Scenario(kind="inflate", fraction=1.0, inflate_factor=factor, inflate_dequantize=False))
    return _unique(sc)


DEMO_GROUPS: tuple[tuple[str, str, str], ...] = (
    ("round", "add", "spread"),
    ("round", "add", "last_day"),
    ("fabricate", "add", "spread"),
    ("fabricate", "add", "last_day"),
    ("inflate", "replace", "whole_ward"),
)


def demo_group_scenarios(kind: str, mode: str, placement: str) -> list[Scenario]:
    if kind == "inflate":
        return [Scenario(kind="inflate", fraction=1.0, inflate_factor=f, n_targets=1) for f in INFLATION_FACTORS]
    return [Scenario(kind=kind, mode="add", fraction=f, placement=placement, n_targets=1) for f in DEMO_ADD_FRACTIONS]


def demo_size_key(kind: str) -> str:
    return "inflate_factor" if kind == "inflate" else "fraction"


def demo_scenarios() -> list[Scenario]:
    return _unique([sc for g in DEMO_GROUPS for sc in demo_group_scenarios(*g)])


def min_reliable(curve: list[dict[str, Any]], size_of, threshold: float = DEMO_RELIABILITY) -> Any:
    """Smallest size whose detection rate (any check) is >= threshold AND stays >= threshold
    for every larger size in the grid. None if no size qualifies."""
    ordered = sorted(curve, key=size_of)
    rates = [c["checks"]["any"]["tpr"]["mean"] for c in ordered]
    for i in range(len(ordered)):
        if all(r is not None and r >= threshold for r in rates[i:]):
            return size_of(ordered[i])
    return None


def power_tasks(seeds: Sequence[int], params: SyntheticParams, config: AuditConfig) -> list[Task]:
    return [("power", s, params, tuple(power_scenarios()), config) for s in seeds]


def summarise_power(outcomes: list[dict[str, Any]], seeds: Sequence[int]) -> dict[str, Any]:
    scenarios = power_scenarios()
    agg = {sc.key: aggregate(outcomes, sc, "power") for sc in scenarios}

    def on_curve(sc: Scenario, kind: str) -> bool:
        if sc.kind != kind or sc.fraction not in POWER_FRACTIONS or not sc.inflate_dequantize:
            return False
        return kind != "inflate" or sc.inflate_factor == INFLATE_FACTOR_FOR_FRACTION_CURVE

    curves = {k: [agg[sc.key] for sc in scenarios if on_curve(sc, k)] for k in ("round", "fabricate", "inflate")}
    sweep = [agg[Scenario(kind="inflate", fraction=1.0, inflate_factor=f).key] for f in INFLATION_FACTORS]
    naive = [agg[sc.key] for sc in scenarios if sc.kind == "inflate" and not sc.inflate_dequantize]
    return {
        "seeds": list(seeds),
        "clean": agg[CLEAN.key],
        "fraction_curves": curves,
        "inflation_factor_sweep": sweep,
        "naive_inflation_artefact": naive,
    }


def demo_calibration_tasks(seeds: Sequence[int], params: SyntheticParams, config: AuditConfig) -> list[Task]:
    return [("demo_cal", s, params, tuple(demo_scenarios()), config) for s in seeds]


def select_demo_sizes(outcomes: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[Scenario]]:
    """Per demo group: calibration curve, chosen minimum size, and the held-out scenarios to run."""
    groups: dict[str, dict[str, Any]] = {}
    holdout: list[Scenario] = []
    for kind, mode, placement in DEMO_GROUPS:
        key = demo_size_key(kind)
        scs = demo_group_scenarios(kind, mode, placement)
        curve = [aggregate(outcomes, sc, "demo_cal") for sc in scs]
        chosen = min_reliable(curve, lambda c, key=key: c["scenario"][key])
        sizes = sorted(c["scenario"][key] for c in curve)
        hold_sizes = sizes[sizes.index(chosen) : sizes.index(chosen) + 2] if chosen is not None else []
        name = f"{kind}|{mode}|{placement}"
        groups[name] = {
            "size_key": key,
            "size_unit": "inflation factor (whole ward)" if kind == "inflate" else "entries added / ward's entries in window",
            "curve": curve,
            "min_reliable_size_calibration": chosen,
            "holdout_sizes": hold_sizes,
        }
        holdout += [sc for sc in scs if getattr(sc, key) in hold_sizes]
    return groups, _unique(holdout)


def holdout_tasks(seeds: Sequence[int], scenarios: list[Scenario], params: SyntheticParams, config: AuditConfig) -> list[Task]:
    if not scenarios:
        return []
    return [("demo_holdout", s, params, tuple(scenarios), config) for s in seeds]


def attach_holdout(groups: dict[str, dict[str, Any]], outcomes: list[dict[str, Any]]) -> None:
    for name, g in groups.items():
        kind, mode, placement = name.split("|")
        key = g["size_key"]
        g["holdout"] = [
            aggregate(outcomes, sc, "demo_holdout")
            for sc in demo_group_scenarios(kind, mode, placement)
            if getattr(sc, key) in g["holdout_sizes"]
        ]


def sensitivity_tasks(seeds: Sequence[int], params: SyntheticParams, config: AuditConfig) -> list[Task]:
    tasks: list[Task] = []
    peer = replace(config, benford_reference="peer")
    s1_scen = (CLEAN, Scenario(kind="fabricate", fraction=0.20), Scenario(kind="fabricate", fraction=0.35), Scenario(kind="round", fraction=0.01))
    for ref_name, cfg in (("benford", config), ("peer", peer)):
        tasks += [(f"s1|exact|{ref_name}", s, params, s1_scen, cfg) for s in seeds]
        for sig in APPROX_BENFORD_SIGMAS:
            p = replace(params, load_model="lognormal", lognormal_load_sigma_log10=sig)
            tasks += [(f"s1|{sig}|{ref_name}", s, p, (CLEAN,), cfg) for s in seeds]
    for sigma in (0.05, 0.10, 0.20):
        p = replace(params, ward_effect_sigma=sigma)
        scs = (CLEAN,) + tuple(Scenario(kind="inflate", fraction=1.0, inflate_factor=f, n_targets=1) for f in (1.2, 1.5, 2.0))
        tasks += [(f"s2|{sigma}", s, p, scs, config) for s in seeds]
    for days in (7, 14, 28):
        p = replace(params, n_days=days)
        tasks += [(f"s3|{days}", s, p, S3_SCENARIOS, config) for s in seeds]
    return tasks


S3_SCENARIOS = (
    CLEAN,
    Scenario(kind="round", fraction=0.01),
    Scenario(kind="fabricate", fraction=0.20),
    Scenario(kind="inflate", fraction=1.0, inflate_factor=1.5),
)


def summarise_sensitivity(outcomes: list[dict[str, Any]], seeds: Sequence[int]) -> dict[str, Any]:
    out: dict[str, Any] = {"seeds": [min(seeds), max(seeds), len(seeds)]}
    s1 = []
    for ref_name in ("benford", "peer"):
        s1.append({"reference": ref_name, "clean_load_model": "exact_benford (default)", **aggregate(outcomes, CLEAN, f"s1|exact|{ref_name}")})
        for sig in APPROX_BENFORD_SIGMAS:
            s1.append(
                {"reference": ref_name, "clean_load_model": f"lognormal sigma_log10={sig}", **aggregate(outcomes, CLEAN, f"s1|{sig}|{ref_name}")}
            )
    out["approx_benford_clean_data"] = s1
    out["reference_power_on_exact_benford_data"] = {
        ref_name: [
            aggregate(outcomes, sc, f"s1|exact|{ref_name}")
            for sc in (Scenario(kind="fabricate", fraction=0.20), Scenario(kind="fabricate", fraction=0.35), Scenario(kind="round", fraction=0.01))
        ]
        for ref_name in ("benford", "peer")
    }
    out["ward_heterogeneity_vs_baseline"] = [
        {
            "ward_effect_sigma": sigma,
            "results": [
                aggregate(outcomes, sc, f"s2|{sigma}")
                for sc in (CLEAN,) + tuple(Scenario(kind="inflate", fraction=1.0, inflate_factor=f, n_targets=1) for f in (1.2, 1.5, 2.0))
            ],
        }
        for sigma in (0.05, 0.10, 0.20)
    ]
    out["window_length"] = [
        {"n_days": days, "results": [aggregate(outcomes, sc, f"s3|{days}") for sc in S3_SCENARIOS]} for days in (7, 14, 28)
    ]
    return out
