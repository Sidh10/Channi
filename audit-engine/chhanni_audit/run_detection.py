"""Reproducible detection report for the Chhanni audit engine (SYNTHETIC data only).

    python -m chhanni_audit.run_detection            # full run (fixed seed sets)
    python -m chhanni_audit.run_detection --quick    # smoke run, few seeds, NOT for reporting

Same command -> same output: every seed set is fixed in `evaluate.py`, every
random stream is derived from (seed, purpose), the report has no timestamps, and
results don't depend on --workers. Writes (unless --no-write), all prefixed
`synthetic_`, under <AUDIT_ENGINE_DATA_DIR>/synthetic/:

* synthetic_detection_report.json             full numbers behind the printed report
* synthetic_wards_seed2026.csv                 clean demo-seed wards (schema: wards)
* synthetic_ward_tonnage_logs_seed2026.csv     clean demo-seed log (schema: ward_tonnage_logs + entry_id)
* synthetic_audit_example_seed2026.json        audit() output for one demo-sized injection
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict
from typing import Any

import numpy as np

from . import __version__
from .benford import benford_conformity
from .calibration import CRDT_SOURCE_NOTE
from .evaluate import (
    DEMO_CALIBRATION_SEEDS,
    DEMO_GROUPS,
    DEMO_HOLDOUT_SEEDS,
    DEMO_RELIABILITY,
    DEMO_SEED,
    INFLATE_FACTOR_FOR_FRACTION_CURVE,
    POWER_SEEDS,
    SANITY_CHECK_SEEDS_USED_IN_DEVELOPMENT,
    SENSITIVITY_SEEDS,
    apply_scenario,
    attach_holdout,
    choose_targets,
    demo_calibration_tasks,
    demo_group_scenarios,
    demo_size_key,
    holdout_tasks,
    power_tasks,
    run_tasks,
    select_demo_sizes,
    sensitivity_tasks,
    summarise_power,
    summarise_sensitivity,
)
from .paths import synthetic_output_path
from .pipeline import SYNTHETIC_NOTICE, AuditConfig, audit
from .scoring import CHECKS
from .synthetic import SyntheticParams, describe, generate_dataset

BANNER = (
    "*** ALL DATA IN THIS REPORT IS SYNTHETIC (docs/decisions.md D5). ***\n"
    "*** No real MCD, census or other municipal data was used.          ***"
)

LIMITATIONS = [
    "All results are on SYNTHETIC data whose clean trip loads follow Benford's Law exactly by construction. "
    "They show the detectors work IF genuine data behaves like this; whether real Indian municipal tonnage "
    "data conforms is untested here (Phase 1 item 6). Section [8a] shows the Benford-reference check would "
    "flag most clean wards if genuine trip loads were only approximately Benford.",
    "Tamper models (1-sig-fig rounding, uniform fabrication over the ward's P10-P90, multiplicative "
    "inflation) are assumptions about how fraud looks, not observed fraud patterns. A fabricator who "
    "draws values from a Benford-like distribution, or inflates by small amounts, would evade the digit "
    "tests; only the baseline layer constrains that, and only for large over-reporting.",
    "The baseline model uses the generator's own log-linear functional form, so it partly recovers the "
    "generator by construction. Its sensitivity is set by the unexplained ward heterogeneity "
    "(synthetic ward_effect_sigma); see section [8b]. On real data that spread is unknown.",
    "The roundness test compares each ward with the other wards; if every ward rounds the same way it "
    "cannot see it. The peer-reference digit test has the same blind spot.",
    "Detection is evaluated at ward level (the unit that holds or releases credits). Entry-level and "
    "ward-day localisation hints are emitted for flagged wards but their precision is not evaluated here.",
    "confidence_score is the smallest BH-adjusted p-value across checks: a ranking of evidence, not a "
    "probability that a ward's data is genuine.",
    "The implied per-capita generation of the synthetic city is an artefact of synthetic populations "
    "and must not be quoted.",
]


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def _f(x: Any, nd: int = 2) -> str:
    return "  -  " if x is None else f"{x:.{nd}f}"


def _rate(s: dict[str, Any], nd: int = 2) -> str:
    if s["mean"] is None:
        return "n/a"
    return f"{s['mean']:.{nd}f} [{s['min']:.{nd}f}-{s['max']:.{nd}f}]"


def _hr(char: str = "-", width: int = 118) -> str:
    return char * width


def _check_table(rows: list[dict[str, Any]], label_fn, label_header: str, keys=CHECKS + ("any",)) -> list[str]:
    out = [f"{label_header:<26}     " + "".join(f"{k:<20}" for k in keys)]
    for r in rows:
        tpr = "".join(f"{_rate(r['checks'][k]['tpr']):<20}" for k in keys)
        fpr = "".join(f"{_rate(r['checks'][k]['fpr'], 3):<20}" for k in keys)
        out.append(f"{label_fn(r):<26} TPR {tpr}")
        out.append(f"{'':<26} FPR {fpr}")
    return out


# ---------------------------------------------------------------------------
# report sections
# ---------------------------------------------------------------------------


def dataset_section(params: SyntheticParams) -> tuple[dict[str, Any], list[str]]:
    ds = generate_dataset(DEMO_SEED, params)
    desc = describe(ds)
    pooled = benford_conformity(ds.logs["tonnage"].to_numpy())
    one_ward = ds.wards["ward_code"].iloc[0]
    ward_logs = ds.logs[ds.logs["ward_code"] == one_ward]
    wd = ward_logs.groupby("entry_date", observed=True)["tonnage"].sum().to_numpy()
    daily = benford_conformity(wd)
    ward_conf = benford_conformity(ward_logs["tonnage"].to_numpy())
    data = {
        "description": desc,
        "benford_pooled_clean_entries": pooled.to_dict(),
        "benford_one_ward_clean_entries": {"ward_code": one_ward, **ward_conf.to_dict()},
        "benford_one_ward_daily_totals": {"ward_code": one_ward, **daily.to_dict()},
    }
    L = [
        "[1] SYNTHETIC DATASET (default demo seed %d)" % DEMO_SEED,
        _hr(),
        f"  Calibration: {CRDT_SOURCE_NOTE}",
        f"  Wards {desc['n_wards']} | days {desc['n_days']} ({desc['period_start']} to {desc['period_end']}, synthetic window) | "
        f"entries {desc['n_entries']:,} (per ward min/median/max {desc['entries_per_ward_min']}/"
        f"{desc['entries_per_ward_median']:.0f}/{desc['entries_per_ward_max']})",
        f"  Mean daily city-wide total: {desc['mean_daily_city_total_t']:,.3f} t (target {desc['calibration_target_t']:,.0f} t)",
        f"  Trip-load P5-P95: {desc['trip_load_p5_t']} t to {desc['trip_load_p95_t']} t = {desc['trip_load_span_decades_p5_p95']} decades; "
        f"ward-day totals span only {desc['ward_day_total_span_decades_p5_p95']} decades",
        f"  Implied per-capita: {desc['implied_per_capita_kg_per_day_ARTEFACT']} kg/person/day -- ARTEFACT of synthetic "
        "populations, NOT a real figure; do not quote.",
        "  Synthetic parameters (all synthetic except city_daily_total_t):",
    ]
    line = "    "
    for it in (f"{k}={v}" for k, v in params.to_dict().items()):
        if len(line) + len(it) + 2 > 116:
            L.append(line.rstrip())
            line = "    "
        line += it + "; "
    L.append(line.rstrip())
    L += [
        "",
        "[2] WHERE BENFORD'S LAW IS APPLIED, AND WHY",
        _hr(),
        "  Benford is expected only for data spanning several orders of magnitude. A ward's DAILY TOTALS do not, so",
        "  the engine never runs Benford on them. It runs per ward on TRIP-LEVEL entries pooled over the audit window,",
        "  and marks a ward 'not applicable' if its entries span < 1 decade (P5-P95) or are too few for chi-square.",
        f"  - Pooled clean entries (n={pooled.n_used:,}): verdict '{pooled.verdict}', p={_f(pooled.p_value, 3)}, "
        f"D*={_f(pooled.test.d_star if pooled.test else None, 4)}",
        f"  - One ward's clean entries ({one_ward}, n={ward_conf.n_used:,}, span {ward_conf.span_decades_p5_p95:.2f} decades): "
        f"verdict '{ward_conf.verdict}', p={_f(ward_conf.p_value, 3)}",
        f"  - Same ward's {len(wd)} daily totals: verdict '{daily.verdict}' -- {daily.reasons[-1]}",
        "  CAVEAT: clean synthetic trip loads are exactly Benford BY CONSTRUCTION (10^Uniform over 2 whole decades x",
        "  independent fill factor). Passing here shows only that the detector works IF clean data conforms. Whether",
        "  real Indian municipal tonnage data conforms is the open question for Phase 1 item 6.",
        "",
    ]
    return data, L


def rule_section(config: AuditConfig) -> list[str]:
    return [
        "[3] DECISION RULE (fixed a priori; nothing tuned on the reported seeds)",
        _hr(),
        f"  Checks per ward: benford (first-digit chi-square vs Benford, df=8, on trip entries; reference="
        f"'{config.benford_reference}'; D* reported",
        "  as effect size); roundness (one-sided Fisher test of exact multiples of 0.01/0.1/1 t vs all other wards,",
        "  Bonferroni x3); baseline (log mean daily tonnage vs leave-one-ward-out Huber fit on log population, income,",
        "  sanitation; one-sided upper, Student t); baseline_daily (ward-day spikes after median polish, per-ward scale",
        "  from var = a + b/trips-per-day fitted across wards; Bonferroni over days).",
        f"  All (ward, check) p-values enter ONE Benjamini-Hochberg procedure at q = {config.alpha} "
        "(160 tests for 40 wards).",
        "  A ward is flagged if any of its checks survives. confidence_score = smallest q across checks (1 = consistent).",
        "  Layers: digit_layer = benford OR roundness; baseline_layer = baseline OR baseline_daily; any = flagged.",
        f"  Null calibration was sanity-checked during development on seeds {SANITY_CHECK_SEEDS_USED_IN_DEVELOPMENT}.",
        "",
    ]


def power_section(power: dict[str, Any]) -> list[str]:
    seeds = power["seeds"]
    L = [
        f"[4] FALSE POSITIVES ON FULLY CLEAN DATA ({len(seeds)} seeds {min(seeds)}-{max(seeds)} x 40 wards, no tampering)",
        _hr(),
    ]
    clean = power["clean"]
    for k in CHECKS + ("any",):
        c = clean["checks"][k]
        L.append(
            f"  {k:<16} per-ward FPR {_rate(c['fpr'], 4):<26} false flags {c['fp_pooled']}/{c['n_clean_pooled']}   "
            f"runs with >=1 false flag {c['runs_with_any_false_flag']:.2f}"
        )
    L += [
        "  (BH at q=0.01 controls the false DISCOVERY rate; with no true positives and roughly independent tests this",
        "  bounds the chance of ANY false flag in a run at about 1%. The checks share data, so treat it as approximate.)",
        "",
        f"[5] DETECTION vs TAMPER SIZE: 4 tampered + 36 clean wards per run, {len(seeds)} seeds, replace mode",
        _hr(),
        "  TPR = share of the 4 tampered wards flagged; FPR = share of the 36 clean wards flagged in the SAME runs.",
        "  Cells: mean [min-max] across seeds. 'share' = tampered fraction of the ward's entries; 'x' = ward total after/before.",
    ]
    desc = {
        "round": "ROUND: values rounded to 1 significant figure (4.837 t -> 5 t)",
        "fabricate": "FABRICATE: values replaced by Uniform[ward P10, ward P90], 3 decimals",
        "inflate": f"INFLATE: entries multiplied by {INFLATE_FACTOR_FOR_FRACTION_CURVE} (applied to the underlying weight)",
    }
    for kind, curve in power["fraction_curves"].items():
        L += ["", "  " + desc[kind]]
        L += [
            "  " + x
            for x in _check_table(
                curve, lambda r: f"share {r['scenario']['fraction']:<5} x{_f(r['overreport_ratio']['mean'], 3)}", "tamper size"
            )
        ]
    L += ["", "[6] WHOLE-WARD INFLATION SWEEP (every entry of 4 wards x factor; the baseline layer's job)", _hr()]
    L += [
        "  " + x
        for x in _check_table(
            power["inflation_factor_sweep"],
            lambda r: f"factor x{r['scenario']['inflate_factor']}",
            "inflation",
            keys=("benford", "roundness", "baseline", "baseline_daily", "digit_layer", "any"),
        )
    ]
    L.append("  Naive inflation (recorded integer-kg value x factor, no dequantisation) leaves a last-digit artefact:")
    for r in power["naive_inflation_artefact"]:
        L.append(
            f"    x{r['scenario']['inflate_factor']}: roundness TPR {_rate(r['checks']['roundness']['tpr'])}, "
            f"FPR {_rate(r['checks']['roundness']['fpr'], 3)} -- an arithmetic artefact, not relied on."
        )
    L.append("")
    return L


def _recommend(group: dict[str, Any]) -> dict[str, Any]:
    key = group["size_key"]
    rec: dict[str, Any] = {"size": None, "holdout_rate": None, "holdout_wilson95": None, "holdout_fpr_mean": None, "note": ""}
    chosen = group["min_reliable_size_calibration"]
    if chosen is None:
        rec["note"] = f"never reached {DEMO_RELIABILITY:.0%} within the tested grid"
        return rec
    for h in group["holdout"]:
        a = h["checks"]["any"]
        rate = a["tpr"]["mean"]
        if rate is not None and rate >= DEMO_RELIABILITY:
            rec.update(
                size=h["scenario"][key],
                holdout_rate=rate,
                holdout_wilson95=a.get("tpr_pooled_wilson95"),
                holdout_fpr_mean=a["fpr"]["mean"],
            )
            if h["scenario"][key] != chosen:
                rec["note"] = f"calibration minimum {chosen} fell below {DEMO_RELIABILITY:.0%} on held-out seeds; next grid size used"
            return rec
    rates = [(h["scenario"][key], h["checks"]["any"]["tpr"]["mean"]) for h in group["holdout"]]
    rec["note"] = f"calibration minimum {chosen} did not hold up on held-out seeds {rates}; no size confirmed"
    return rec


def demo_section(demo: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    cal, hold = demo["calibration_seeds"], demo["holdout_seeds"]
    L = [
        "[7] DEMO SIZING (decisions.md D7 beat 2): ONE target ward, other 39 clean",
        _hr(),
        f"  Minimum size chosen on calibration seeds {cal[0]}-{cal[1]} ({cal[2]} runs) as the smallest grid size with",
        f"  detection (any check) >= {DEMO_RELIABILITY:.0%} at that size AND every larger size; confirmed on disjoint held-out",
        f"  seeds {hold[0]}-{hold[1]} ({hold[2]} runs). Target ward drawn at random per seed. 'add' = phantom entries",
        "  INSERTed (round: genuine-looking loads rounded to 1 sig. fig.; fabricate: Uniform[P10,P90]). Add sizes are",
        "  RELATIVE: entries added / the ward's existing entries in the 28-day window (wards differ ~8x in size).",
        "  'last_day' = all added on the window's last day; 'spread' = random days across the window.",
    ]
    recs = {}
    for name, g in demo["groups"].items():
        key = g["size_key"]
        L += ["", f"  {name}  (size = {g['size_unit']})"]
        L.append(
            f"    {'size':>7} {'N added':>8} {'x total':>8}  {'detect(any)':<12}"
            + "".join(f"{c:<15}" for c in CHECKS)
            + "FPR other wards mean (max)"
        )
        for r in sorted(g["curve"], key=lambda c: c["scenario"][key]):
            a = r["checks"]["any"]
            n_add = r["n_tampered_entries_per_target"]["mean"] if key == "fraction" else None
            L.append(
                f"    {r['scenario'][key]:>7} {_f(n_add, 0) if n_add is not None else '  all':>8} "
                f"{_f(r['overreport_ratio']['mean'], 3):>8}  {_f(a['tpr']['mean']):<12}"
                + "".join(f"{_f(r['checks'][c]['tpr']['mean']):<15}" for c in CHECKS)
                + f"{_f(a['fpr']['mean'], 4)} ({_f(a['fpr']['max'], 3)})"
            )
        rec = _recommend(g)
        recs[name] = rec
        if rec["size"] is not None:
            L.append(
                f"    => minimum reliable size: {rec['size']} ({g['size_unit']}); held-out detection "
                f"{rec['holdout_rate']:.3f}, Wilson 95% [{rec['holdout_wilson95'][0]:.3f}, {rec['holdout_wilson95'][1]:.3f}], "
                f"other-ward FPR {rec['holdout_fpr_mean']:.4f}" + (f"  NOTE: {rec['note']}" if rec["note"] else "")
            )
        else:
            L.append(f"    => no reliable size: {rec['note']}")
        for h in g["holdout"]:
            a = h["checks"]["any"]
            L.append(
                f"       held-out @ {h['scenario'][key]}: detect {_f(a['tpr']['mean'], 3)} "
                f"(Wilson {a.get('tpr_pooled_wilson95')}), FPR other wards mean {_f(a['fpr']['mean'], 4)} "
                f"max {_f(a['fpr']['max'], 3)}, runs with >=1 false flag {a['runs_with_any_false_flag']:.2f}"
            )
    L.append("")
    return recs, L


def sensitivity_section(sens: dict[str, Any]) -> list[str]:
    s = sens["seeds"]
    L = [f"[8] SENSITIVITY (seeds {s[0]}-{s[1]}, {s[2]} runs per setting)", _hr()]
    L.append("  (a) What if genuine trip loads are only APPROXIMATELY Benford? Clean data from a single lognormal trip-load")
    L.append("      distribution (sigma in log10 units); false flags by the first-digit check, for both digit references.")
    L.append(f"      {'reference':<9} {'clean trip loads':<28} {'benford applicable':<20} {'benford FPR':<24} {'runs w/ false flag':<19} any-check FPR")
    for r in sens["approx_benford_clean_data"]:
        b = r["checks"]["benford"]
        L.append(
            f"      {r['reference']:<9} {r['clean_load_model']:<28} {_f(r['benford_applicable_share']['mean'], 2):<20} "
            f"{_rate(b['fpr'], 3):<24} {b['runs_with_any_false_flag']:<19.2f} {_rate(r['checks']['any']['fpr'], 3)}"
        )
    L.append("      Power cost of the peer reference on exactly-Benford data (4 tampered wards):")
    for ref, rows in sens["reference_power_on_exact_benford_data"].items():
        parts = [
            f"{r['scenario']['kind']} {r['scenario']['fraction']}: benford TPR {_rate(r['checks']['benford']['tpr'])}" for r in rows
        ]
        L.append(f"        {ref:<8} " + " | ".join(parts))
    L.append("  (b) Unexplained ward heterogeneity (ward_effect_sigma) vs whole-ward inflation of ONE ward, 'baseline' check")
    for blk in sens["ward_heterogeneity_vs_baseline"]:
        parts = []
        for r in blk["results"]:
            sc = r["scenario"]
            if sc["kind"] is None:
                parts.append(f"clean FPR {_f(r['checks']['baseline']['fpr']['mean'], 4)}")
            else:
                parts.append(f"x{sc['inflate_factor']}: TPR {_f(r['checks']['baseline']['tpr']['mean'])}")
        L.append(f"      sigma={blk['ward_effect_sigma']:<5} " + " | ".join(parts))
    L.append("  (c) Audit window length (entries per ward) vs detection (any check), 4 tampered wards")
    for blk in sens["window_length"]:
        parts = []
        for r in blk["results"]:
            sc = r["scenario"]
            if sc["kind"] is None:
                parts.append(f"clean FPR {_f(r['checks']['any']['fpr']['mean'], 4)}")
            else:
                size = sc["fraction"] if sc["kind"] != "inflate" else f"x{sc['inflate_factor']}"
                parts.append(f"{sc['kind']} {size}: {_rate(r['checks']['any']['tpr'])}")
        L.append(f"      {blk['n_days']:>2} days: " + " | ".join(parts))
    L.append("")
    return L


def default_seed_section(
    params: SyntheticParams, config: AuditConfig, recs: dict[str, Any]
) -> tuple[dict[str, Any], list[str], Any]:
    """Deterministic runs on the demo seed: the whole demo grid for the demo target ward, so the
    live demo's outcome is known in advance (this is a check of the demo config, not a claim)."""
    ds = generate_dataset(DEMO_SEED, params)
    target = choose_targets(DEMO_SEED, ds.wards["ward_code"].tolist(), 1)
    n_target = int((ds.logs["ward_code"] == target[0]).sum())
    clean_res = audit(ds.wards, ds.logs, config, localise=False)
    L = [
        f"[9] DEFAULT DEMO SEED {DEMO_SEED}: deterministic runs for the demo target ward {target[0]} "
        f"({n_target} entries in the window)",
        _hr(),
        f"  Clean log: wards flagged = {clean_res.run_summary()['flagged_wards'] or 'none'}",
        "  For each demo group: the grid sizes at which this ward is flagged on this seed, and the run at the",
        "  cross-seed minimum reliable size (the size to use; it is NOT tuned to this seed).",
    ]
    out: dict[str, Any] = {
        "target_ward": target[0],
        "target_entries": n_target,
        "clean_flagged_wards": clean_res.run_summary()["flagged_wards"],
        "groups": {},
    }
    example = None
    for kind, mode, placement in DEMO_GROUPS:
        name = f"{kind}|{mode}|{placement}"
        key = demo_size_key(kind)
        flagged_sizes, rows = [], []
        for sc in demo_group_scenarios(kind, mode, placement):
            logs, reports = apply_scenario(ds, sc, target)
            res = audit(ds.wards, logs, config, localise=True)
            tbl = res.ward_table.set_index("ward_code")
            fired = [c for c in CHECKS if tbl.loc[target[0], f"flag_{c}"]]
            others = [w for w in res.run_summary()["flagged_wards"] if w != target[0]]
            rank = int((tbl["confidence"] < tbl.loc[target[0], "confidence"]).sum()) + 1
            row = {
                "size": getattr(sc, key),
                "n_added": reports[0].n_tampered if kind != "inflate" else None,
                "target_flagged": bool(tbl.loc[target[0], "flagged"]),
                "checks_fired": fired,
                "target_confidence": float(f"{float(tbl.loc[target[0], 'confidence']):.6g}"),
                "target_rank_lowest_confidence_first": rank,
                "other_wards_flagged": others,
                "overreport_ratio": round(reports[0].overreport_ratio, 4),
            }
            rows.append(row)
            if row["target_flagged"]:
                flagged_sizes.append(row["size"])
            rec = recs.get(name, {})
            if rec.get("size") == row["size"] and (example is None or name == "fabricate|add|last_day"):
                example = (name, sc, res)
        out["groups"][name] = rows
        rec = recs.get(name, {})
        L.append(f"  {name}: flagged at sizes {flagged_sizes or 'none'}")
        at_rec = next((r for r in rows if rec.get("size") is not None and r["size"] == rec["size"]), None)
        if at_rec:
            n_txt = f"N={at_rec['n_added']} entries, " if at_rec["n_added"] is not None else ""
            L.append(
                f"      at recommended size {rec['size']}: {n_txt}flagged={at_rec['target_flagged']} by {at_rec['checks_fired'] or '-'}, "
                f"confidence {at_rec['target_confidence']:.2e} (rank {at_rec['target_rank_lowest_confidence_first']}/40), "
                f"other wards flagged: {at_rec['other_wards_flagged'] or 'none'}"
            )
        else:
            L.append("      no cross-seed reliable size for this group")
    L.append("")
    return out, L, example


def build_report(quick: bool, workers: int) -> tuple[dict[str, Any], str, Any]:
    params = SyntheticParams()
    config = AuditConfig()
    if quick:
        power_seeds, cal, hold, sens = POWER_SEEDS[:3], DEMO_CALIBRATION_SEEDS[:5], DEMO_HOLDOUT_SEEDS[:5], SENSITIVITY_SEEDS[:2]
    else:
        power_seeds, cal, hold, sens = POWER_SEEDS, DEMO_CALIBRATION_SEEDS, DEMO_HOLDOUT_SEEDS, SENSITIVITY_SEEDS

    lines = ["=" * 118, f"CHHANNI AUDIT ENGINE -- DETECTION REPORT (engine {__version__})", BANNER]
    if quick:
        lines.append("*** QUICK MODE: too few seeds for any claim. Use the full run for reported numbers.  ***")
    lines += ["=" * 118, ""]

    ds_data, L = dataset_section(params)
    lines += L
    lines += rule_section(config)

    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else nullcontext(None)
    with pool as ex:
        # Batch 1 (heaviest tasks first for load balance; output order is fixed by task order).
        tasks = demo_calibration_tasks(cal, params, config) + power_tasks(power_seeds, params, config) + sensitivity_tasks(
            sens, params, config
        )
        outcomes = run_tasks(tasks, ex)
        groups, hold_scenarios = select_demo_sizes(outcomes)
        # Batch 2: held-out confirmation of the sizes chosen on the calibration seeds.
        hold_out = run_tasks(holdout_tasks(hold, hold_scenarios, params, config), ex)
    attach_holdout(groups, hold_out)

    power = summarise_power(outcomes, power_seeds)
    lines += power_section(power)
    demo = {
        "calibration_seeds": [min(cal), max(cal), len(cal)],
        "holdout_seeds": [min(hold), max(hold), len(hold)],
        "reliability_threshold": DEMO_RELIABILITY,
        "groups": groups,
    }
    recs, L = demo_section(demo)
    lines += L
    sensitivity = summarise_sensitivity(outcomes, sens)
    lines += sensitivity_section(sensitivity)
    default_run, L, example = default_seed_section(params, config, recs)
    lines += L
    lines += ["[10] LIMITATIONS", _hr()] + [f"  - {x}" for x in LIMITATIONS] + ["", BANNER, "=" * 118]

    report = {
        "data_notice": SYNTHETIC_NOTICE,
        "quick_mode": quick,
        "engine_version": __version__,
        "command": "python -m chhanni_audit.run_detection" + (" --quick" if quick else ""),
        "calibration": CRDT_SOURCE_NOTE,
        "synthetic_params": params.to_dict(),
        "audit_config": config.to_dict(),
        "seed_sets": {
            "power_curves": list(power_seeds),
            "demo_calibration": list(cal),
            "demo_holdout": list(hold),
            "sensitivity": list(sens),
            "default_demo_seed": DEMO_SEED,
            "null_sanity_checks_during_development": SANITY_CHECK_SEEDS_USED_IN_DEVELOPMENT,
        },
        "dataset_default_seed": ds_data,
        "power": power,
        "demo_sizing": {**demo, "recommendations": recs},
        "sensitivity": sensitivity,
        "default_seed_demo_runs": default_run,
        "limitations": LIMITATIONS,
    }
    return report, "\n".join(lines), example


def to_json_safe(o: Any) -> Any:
    """Recursively convert numpy types to python and NaN/inf to None (JSON has no NaN)."""
    if isinstance(o, dict):
        return {str(k): to_json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_json_safe(v) for v in o]
    if isinstance(o, np.ndarray):
        return [to_json_safe(v) for v in o.tolist()]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        v = float(o)
        return v if np.isfinite(v) else None
    return o


def dumps(o: Any) -> str:
    """Deterministic JSON text for a report (NaN -> null)."""
    return json.dumps(to_json_safe(o), indent=1, allow_nan=False) + "\n"


def _default_workers() -> int:
    # 4 keeps peak memory modest on a 16 GB laptop (each worker loads numpy/scipy/pandas).
    return max(1, min(4, (os.cpu_count() or 2) - 1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reproducible detection report on SYNTHETIC data.")
    ap.add_argument("--quick", action="store_true", help="few seeds; smoke test only, not for reporting")
    ap.add_argument("--workers", type=int, default=_default_workers(), help="parallel processes (does not change results)")
    ap.add_argument("--no-write", action="store_true", help="print only; write no files")
    args = ap.parse_args(argv)

    t0 = time.time()
    report, text, example = build_report(args.quick, args.workers)
    print(text)
    if not args.no_write:
        suffix = "_quick" if args.quick else ""
        path = synthetic_output_path(f"synthetic_detection_report{suffix}.json")
        path.write_text(dumps(report), encoding="utf-8")
        written = [path]
        if not args.quick:
            ds = generate_dataset(DEMO_SEED)
            p_w = synthetic_output_path(f"synthetic_wards_seed{DEMO_SEED}.csv")
            ds.wards.to_csv(p_w, index=False, lineterminator="\n")
            p_l = synthetic_output_path(f"synthetic_ward_tonnage_logs_seed{DEMO_SEED}.csv")
            ds.logs.to_csv(p_l, index=False, date_format="%Y-%m-%d", float_format="%.3f", lineterminator="\n")
            written += [p_w, p_l]
            if example is not None:
                name, sc, res = example
                p_e = synthetic_output_path(f"synthetic_audit_example_seed{DEMO_SEED}.json")
                payload = {
                    "data_notice": SYNTHETIC_NOTICE,
                    "example": f"seed {DEMO_SEED}, injection group {name}, scenario {asdict(sc)}",
                    **res.to_dict(),
                }
                p_e.write_text(dumps(payload), encoding="utf-8")
                written.append(p_e)
        print("\nFiles written (all synthetic):", file=sys.stderr)
        for p in written:
            print(f"  {p}", file=sys.stderr)
    print(f"elapsed {time.time() - t0:.1f}s with {args.workers} workers", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
