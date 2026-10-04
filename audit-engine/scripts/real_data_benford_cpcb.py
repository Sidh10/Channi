"""Benford's Law check on REAL PUBLIC DATA: CPCB state-wise MSW tables (Phase 1 item 6).

    cd audit-engine
    .venv/Scripts/python.exe scripts/real_data_benford_cpcb.py

Input  : data/public/cpcb_msw_statewise_tpd.csv (extracted by scripts/extract_cpcb_statewise.py;
         read-only here). State-level figures SELF-REPORTED by SPCBs/PCCs to CPCB.
Output : data/public/cpcb_benford_result.json and a printed report. Deterministic: no
         timestamps, Monte Carlo p-values use fixed seeds inside chhanni_audit.benford.

THIS IS REAL PUBLIC DATA, NOT SYNTHETIC. It is state-level, not ward-level, and not MCD
tonnage logs. Whatever it shows says nothing direct about ward-level trip logs.

PRIMARY ANALYSIS (fixed before any digit statistic was computed)
---------------------------------------------------------------
Report year 2021-22, metric 'generated', first significant digit, chi-square goodness of
fit to Benford's Law at alpha = 0.01 (Monte Carlo p-value because n < 110), on cells with
parse_status 'clean' or 'recovered_from_overlap'. Why this one cell of the table:
  * one value per state/UT, the closest thing to independent observations the table
    offers (pooling metrics double-counts states because collected is often equal to
    generated and treated/landfilled are parts of collected; pooling years repeats the
    same states);
  * 'generated' is the headline metric, the same kind of figure as the CRDT 11,862 TPD
    calibration figure used by the synthetic generator;
  * the 2021-22 'generated' column has no excluded cells and its sum matches the report's
    own TOTAL row exactly (data/public/README.md);
  * 2021-22 is the most recent of the three reports and has the most entities (36).
Expected power is low (n ~ 36): even completely uniform leading digits would be detected
only part of the time, so a 'conforms' verdict here is weak evidence. This was stated
before running.

Everything else is SECONDARY or DESCRIPTIVE and is reported next to the primary, never
instead of it: each report year x metric cell; each year with metrics pooled; each metric
with years pooled; everything pooled; and every variant that contains the 2 recovered
cells, re-run without them. Pooled results are descriptive only (non-independent values).
Secondary p-values are not corrected for multiple testing; a single significant
secondary result is not a finding.

Peer vs plain reference: the audit engine's optional 'peer' reference compares one
ward's digits with OTHER wards' digits. Here the question is whether real data follows
Benford's Law at all, there is no natural peer group, and years/metrics are not
independent groups, so only the plain Benford reference is used.

First-two-digit test: not run. It needs n >= 1146 for the asymptotic test and its power at
n of a few hundred is negligible.

Separate DESCRIPTIVE note (not a Benford result): exact repeated values for the same
state and metric in consecutive report years. A repeat can have innocent causes (e.g. an
estimate carried forward unchanged) and is not evidence of fabrication.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make chhanni_audit importable

from chhanni_audit.benford import benford_conformity  # noqa: E402
from chhanni_audit.paths import data_dir  # noqa: E402

LABEL = "REAL PUBLIC DATA (CPCB, state-level, self-reported)"
INPUT_NAME = "cpcb_msw_statewise_tpd.csv"
OUTPUT_NAME = "cpcb_benford_result.json"
ALPHA = 0.01
YEARS = ("2019-20", "2020-21", "2021-22")
METRICS = ("generated", "collected", "treated", "landfilled")
USABLE_STATUSES = ("clean", "recovered_from_overlap")

PRIMARY = {
    "report_year": "2021-22",
    "metric": "generated",
    "digits": "first",
    "reference": "Benford's Law (goodness of fit)",
    "alpha": ALPHA,
    "parse_status": list(USABLE_STATUSES),
}
PRIMARY_REASON = (
    "One value per state/UT (closest to independent observations); 'generated' is the headline "
    "metric and the same kind of figure as the CRDT calibration; the 2021-22 'generated' column has "
    "no excluded cells and matches the report's TOTAL row exactly; 2021-22 is the most recent report "
    "with the most entities. Fixed before any digit statistic was computed. Expected power is low (n~36)."
)

# Name harmonisation used ONLY to match the same state across report years for the
# repeated-value note. The CSV is not modified. One alias is an inference, flagged as such.
STATE_ALIASES = {
    "Andaman and": ("Andaman and Nicobar Islands", "name truncated in the 2019-20 PDF"),
    "Andaman and Nicobar": ("Andaman and Nicobar Islands", "name truncated in the 2021-22 PDF"),
    "NCihcaonbdairg aIsrlha nds": (
        "Chandigarh",
        "INFERRED: 'Chandigarh' interleaved with a wrapped 'Nicobar Islands' in the 2019-20 PDF "
        "text layer; values (450 TPD generated) are in Chandigarh's range in later years",
    ),
    "DDDNH": ("Dadra and Nagar Haveli and Daman and Diu", "same UT, different abbreviation"),
    "DNH&DD": ("Dadra and Nagar Haveli and Daman and Diu", "same UT, different abbreviation"),
}


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"report_year": str, "state": str, "metric": str, "raw_cell": str, "parse_status": str})
    expected = {"report_year", "state", "metric", "raw_cell", "value_tpd", "parse_status", "source_file", "page"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"input CSV missing columns: {sorted(missing)}")
    df["state_canonical"] = df["state"].map(lambda s: STATE_ALIASES.get(s, (s, ""))[0])
    dup = df.duplicated(["report_year", "state_canonical", "metric"])
    if dup.any():
        raise ValueError(f"more than one row per (year, state, metric) after name harmonisation: {df[dup].head()}")
    return df


def usable(df: pd.DataFrame, include_recovered: bool = True) -> pd.DataFrame:
    statuses = USABLE_STATUSES if include_recovered else ("clean",)
    out = df[df["parse_status"].isin(statuses)]
    if out["value_tpd"].isna().any():
        raise ValueError("usable cells must have a value")
    return out


def run_test(values: np.ndarray, label: str, role: str, selection: dict[str, Any], note: str = "") -> dict[str, Any]:
    """One Benford conformity test, flattened for the report."""
    r = benford_conformity(values, n_digits=1, alpha=ALPHA)
    t = r.test
    return {
        "label": label,
        "role": role,
        "selection": selection,
        "note": note,
        "n_cells": int(len(values)),
        "n_used_positive": r.n_used,
        "n_excluded_nonpositive": r.n_excluded_nonpositive_or_nonfinite,
        "span_decades_p5_p95": r.span_decades_p5_p95,
        "method": r.method,
        "chi2": t.chi2 if t else None,
        "df": t.df if t else None,
        "p_value": r.p_value,
        "d_star": t.d_star if t else None,
        "d_star_null_p95": r.d_star_null.get("p95"),
        "d_star_null_p99": r.d_star_null.get("p99"),
        "mad": t.mad if t else None,
        "verdict": r.verdict,
        "power_vs_uniform_digits": r.power_vs_uniform_digits,
        "observed_first_digit_counts": dict(zip(map(str, t.digits), t.observed_counts)) if t else None,
        "expected_first_digit_counts": {d: round(e, 3) for d, e in zip(map(str, t.digits), t.expected_counts)} if t else None,
        "reasons": r.reasons,
        "caveats": r.caveats,
    }


def benford_tests(df: pd.DataFrame) -> list[dict[str, Any]]:
    tests: list[dict[str, Any]] = []

    def add(sel: pd.DataFrame, label: str, role: str, selection: dict[str, Any], note: str = "") -> None:
        tests.append(run_test(sel["value_tpd"].to_numpy(dtype=float), label, role, selection, note))
        n_recovered = int((sel["parse_status"] == "recovered_from_overlap").sum())
        if n_recovered:
            clean = sel[sel["parse_status"] == "clean"]
            tests.append(
                run_test(
                    clean["value_tpd"].to_numpy(dtype=float),
                    f"{label} [excl. {n_recovered} recovered cell{'s' if n_recovered > 1 else ''}]",
                    role,
                    {**selection, "parse_status": ["clean"]},
                    "re-run without cells recovered from PDF text overlap",
                )
            )

    u = usable(df)
    p = u[(u["report_year"] == PRIMARY["report_year"]) & (u["metric"] == PRIMARY["metric"])]
    add(p, f"PRIMARY: {PRIMARY['report_year']} {PRIMARY['metric']}", "primary (fixed a priori)", dict(PRIMARY))

    for year in YEARS:
        for metric in METRICS:
            if (year, metric) == (PRIMARY["report_year"], PRIMARY["metric"]):
                continue  # already reported as the primary
            sel = u[(u["report_year"] == year) & (u["metric"] == metric)]
            add(sel, f"{year} {metric}", "secondary (one year x metric)", {"report_year": year, "metric": metric})
    for year in YEARS:
        sel = u[u["report_year"] == year]
        add(
            sel,
            f"{year} all metrics pooled",
            "descriptive (pooled; NOT independent)",
            {"report_year": year, "metric": "all"},
            "collected often equals generated; treated and landfilled are parts of collected",
        )
    for metric in METRICS:
        sel = u[u["metric"] == metric]
        add(
            sel,
            f"{metric} all years pooled",
            "descriptive (pooled; NOT independent)",
            {"report_year": "all", "metric": metric},
            "the same states repeat in every year",
        )
    add(
        u,
        "everything pooled",
        "descriptive (pooled; NOT independent)",
        {"report_year": "all", "metric": "all"},
        "states repeat across years and metrics",
    )
    return tests


def exact_repeats(df: pd.DataFrame) -> dict[str, Any]:
    """Exact repeated values for the same state and metric in consecutive report years.
    DESCRIPTIVE ONLY: a repeat can be an estimate carried forward; it is not evidence of fabrication."""
    u = usable(df)
    pairs = list(zip(YEARS[:-1], YEARS[1:]))
    repeats, comparable = [], 0
    by_pair: dict[str, dict[str, int]] = {}
    for y1, y2 in pairs:
        a = u[u["report_year"] == y1].set_index(["state_canonical", "metric"])
        b = u[u["report_year"] == y2].set_index(["state_canonical", "metric"])
        common = a.index.intersection(b.index).sort_values()
        n_rep = 0
        for key in common:
            comparable += 1
            va, vb = float(a.loc[key, "value_tpd"]), float(b.loc[key, "value_tpd"])
            if va == vb:
                n_rep += 1
                repeats.append(
                    {
                        "state": key[0],
                        "metric": key[1],
                        "years": [y1, y2],
                        "value_tpd": va,
                        "raw_cells": [str(a.loc[key, "raw_cell"]), str(b.loc[key, "raw_cell"])],
                        "names_in_csv": [str(a.loc[key, "state"]), str(b.loc[key, "state"])],
                        "uses_name_alias": bool(a.loc[key, "state"] != b.loc[key, "state"]),
                        "value_is_zero": va == 0.0,
                    }
                )
        by_pair[f"{y1}->{y2}"] = {"comparable_state_metric_pairs": int(len(common)), "exact_repeats": n_rep}
    by_metric = {m: sum(1 for r in repeats if r["metric"] == m) for m in METRICS}
    return {
        "role": "descriptive note, NOT a Benford result",
        "definition": "same state (harmonised name), same metric, consecutive report years, identical value_tpd; "
        "only cells with a usable value are compared",
        "n_comparable_pairs": comparable,
        "n_exact_repeats": len(repeats),
        "n_exact_repeats_nonzero": sum(1 for r in repeats if not r["value_is_zero"]),
        "by_year_pair": by_pair,
        "by_metric": by_metric,
        "repeats": repeats,
        "caution": "A repeated figure can have innocent causes (e.g. an estimate carried forward unchanged, a "
        "population-based norm not updated, a state re-submitting last year's figure). It is not evidence of "
        "fabrication, and no rate of repeats 'expected by chance' is claimed here.",
    }


def generated_vs_collected(df: pd.DataFrame) -> dict[str, Any]:
    """How often collected equals generated (why pooling metrics is not independent). Descriptive."""
    u = usable(df)
    w = u.pivot_table(index=["report_year", "state_canonical"], columns="metric", values="value_tpd", aggfunc="first")
    both = w.dropna(subset=["generated", "collected"])
    eq = int((both["generated"] == both["collected"]).sum())
    with np.errstate(divide="ignore", invalid="ignore"):
        within1 = int((np.abs(both["collected"] / both["generated"] - 1) <= 0.01).sum())
    return {
        "role": "descriptive",
        "state_years_with_both": int(len(both)),
        "collected_equals_generated_exactly": eq,
        "collected_within_1pct_of_generated": within1,
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (float, np.floating)):
        v = float(o)
        return v if np.isfinite(v) else None  # never round: some of these are real reported values
    return o


def analyse(csv_path: Path) -> dict[str, Any]:
    df = load(csv_path)
    status_counts = {k: int(v) for k, v in df["parse_status"].value_counts().sort_index().items()}
    tests = benford_tests(df)
    primary = tests[0]
    return _clean(
        {
            "data_label": LABEL,
            "data_notice": "REAL public data: state-level figures self-reported by SPCBs/PCCs to CPCB, extracted from "
            "CPCB MSW annual reports 2019-20, 2020-21, 2021-22. Not ward-level, not MCD tonnage logs, not synthetic.",
            "input_file": f"data/public/{csv_path.name}",
            "input_sha256": file_sha256(csv_path),
            "parse_status_counts": status_counts,
            "name_aliases_used_for_repeat_matching_only": {k: {"canonical": v[0], "why": v[1]} for k, v in STATE_ALIASES.items()},
            "primary_analysis": {"spec": PRIMARY, "reason": PRIMARY_REASON, "result": primary},
            "all_tests": tests,
            "multiple_testing_note": "Only the primary test is interpreted at alpha = 0.01. Secondary and descriptive "
            "p-values are uncorrected and not independent of each other or of the primary.",
            "not_run": {
                "peer_reference": "no natural peer group; the question is conformity to Benford's Law itself",
                "first_two_digits": "n far below 1146; power negligible",
            },
            "generated_vs_collected": generated_vs_collected(df),
            "exact_repeats_consecutive_years": exact_repeats(df),
            "interpretation_limits": [
                "State totals are a different data-generating process from ward-level trip logs; conformity or "
                "non-conformity here says little about whether ward trip logs would follow Benford's Law.",
                "With n ~ 36 per year x metric, power is low: 'conforms' is weak evidence of anything.",
                "'does_not_conform' would be a reason to look closer, not evidence of fabrication: state totals are "
                "shaped by state population sizes, estimation norms and rounding conventions.",
            ],
        }
    )


def _fmt(x: Any, spec: str) -> str:
    return "-" if x is None else format(x, spec)


def render(result: dict[str, Any]) -> str:
    bar = "=" * 118
    L = [
        bar,
        f"BENFORD'S LAW CHECK ON {result['data_label']}",
        "*** REAL PUBLIC DATA -- NOT SYNTHETIC. State-level figures self-reported to CPCB; NOT ward-level, NOT MCD logs. ***",
        bar,
        f"Input: {result['input_file']}  sha256 {result['input_sha256'][:16]}...  parse_status {result['parse_status_counts']}",
        "",
        "PRIMARY ANALYSIS (fixed before computing any digit statistic)",
        f"  spec  : {result['primary_analysis']['spec']}",
        f"  reason: {result['primary_analysis']['reason']}",
        "",
        f"{'test':<46} {'role':<34} {'n':>4} {'span':>5} {'method':<17} {'p':>8} {'D*':>7} {'D*99':>7} {'power':>6}  verdict",
    ]
    for t in result["all_tests"]:
        L.append(
            f"{t['label'][:46]:<46} {t['role'][:34]:<34} {t['n_used_positive']:>4} {_fmt(t['span_decades_p5_p95'], '.2f'):>5} "
            f"{(t['method'] or '-'):<17} {_fmt(t['p_value'], '.4f'):>8} {_fmt(t['d_star'], '.4f'):>7} "
            f"{_fmt(t['d_star_null_p99'], '.4f'):>7} {_fmt(t['power_vs_uniform_digits'], '.2f'):>6}  {t['verdict']}"
        )
    L += [
        "",
        "  n = positive values used (zeros excluded); span = log10(P95/P5); p = chi-square p (simulated if n < 110);",
        "  D*99 = 99th percentile of D* for exactly-Benford samples of the same n; power = chance of rejecting at",
        "  alpha=0.01 if leading digits were completely uniform. Only the PRIMARY row is a test result; other rows are",
        "  secondary/descriptive, uncorrected, and not independent.",
    ]
    p = result["primary_analysis"]["result"]
    L += [
        "",
        "PRIMARY RESULT",
        f"  {p['label']}: n={p['n_used_positive']} (+{p['n_excluded_nonpositive']} zero/non-positive excluded), span "
        f"{_fmt(p['span_decades_p5_p95'], '.2f')} decades, chi2={_fmt(p['chi2'], '.2f')} (df {p['df']}), p={_fmt(p['p_value'], '.4f')} "
        f"({p['method']}), D*={_fmt(p['d_star'], '.4f')} (null 95%/99%: {_fmt(p['d_star_null_p95'], '.4f')}/"
        f"{_fmt(p['d_star_null_p99'], '.4f')}), verdict '{p['verdict']}', power vs uniform digits {_fmt(p['power_vs_uniform_digits'], '.2f')}",
        f"  observed first digits: {p['observed_first_digit_counts']}",
        f"  expected (Benford)   : {p['expected_first_digit_counts']}",
    ]
    for c in p["caveats"]:
        L.append(f"  caveat: {c}")
    g = result["generated_vs_collected"]
    L += [
        "",
        "NON-INDEPENDENCE (descriptive)",
        f"  collected == generated exactly in {g['collected_equals_generated_exactly']} of {g['state_years_with_both']} "
        f"state-years; within 1% in {g['collected_within_1pct_of_generated']}.",
        "  The same states appear in all three report years. Pooled rows above are descriptive only.",
    ]
    r = result["exact_repeats_consecutive_years"]
    L += [
        "",
        "EXACT REPEATED VALUES, same state + metric, consecutive report years (DESCRIPTIVE NOTE -- NOT A BENFORD RESULT)",
        f"  {r['n_exact_repeats']} exact repeats among {r['n_comparable_pairs']} comparable state-metric pairs "
        f"({r['n_exact_repeats_nonzero']} with a non-zero value); by year pair {r['by_year_pair']}; by metric {r['by_metric']}",
    ]
    for rep in r["repeats"]:
        alias = "  [name harmonised]" if rep["uses_name_alias"] else ""
        L.append(f"    {rep['state']:<32} {rep['metric']:<10} {rep['years'][0]} -> {rep['years'][1]}: {rep['value_tpd']:g} TPD{alias}")
    L.append(f"  {r['caution']}")
    L += ["", "LIMITS OF INTERPRETATION"] + [f"  - {x}" for x in result["interpretation_limits"]]
    L += [bar, "*** REAL PUBLIC DATA (CPCB, state-level, self-reported) -- see data/public/README.md for provenance. ***", bar]
    return "\n".join(L)


def main() -> int:
    public = data_dir() / "public"
    csv_path = public / INPUT_NAME
    result = analyse(csv_path)
    print(render(result))
    out = public / OUTPUT_NAME
    out.write_text(json.dumps(result, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
