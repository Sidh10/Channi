"""Extract CPCB's state-wise MSW table (generated / collected / treated / landfilled, TPD)
from the Annual Report PDFs in data/public/ into data/public/cpcb_msw_statewise_tpd.csv.

REAL PUBLIC DATA (not synthetic): source is CPCB's Annual Reports on Solid Waste
Management, as self-reported by SPCBs/PCCs. Used for the Phase 1 real-data Benford check.

Parsing rules (every cell's outcome is written to the CSV, nothing is dropped silently):
  clean                   a single number (thousands separators removed)
  recovered_from_overlap  the PDF text layer interleaves a number with a word from the row
                          above (e.g. 'p1r3o8v5id ed' = 'provided' + '1385'); the digits are
                          kept in order. Flagged so it can be excluded in a sensitivity check.
  excluded                anything else: '-', 'Nil', 'Not provided', 'Yes', two numbers in
                          one cell, a number plus a parenthetical note, etc.
The 'Gap' column (2021-22) is derived (generated - treated - landfilled), not reported, so it is
skipped. The TOTAL row is used only as a checksum against the sum of extracted state values.
The 2016-17 report has no consolidated state table (figures are embedded in prose), so it is not used.

Usage (from audit-engine/): .venv/Scripts/python.exe scripts/extract_cpcb_statewise.py
Needs: pip install -r requirements-extract.txt
"""

import csv
import re
from pathlib import Path

import pdfplumber

REPO = Path(__file__).resolve().parents[2]
PUBLIC = REPO / "data" / "public"
OUT = PUBLIC / "cpcb_msw_statewise_tpd.csv"

# report year -> (pdf file, 1-based pages holding the state-wise table)
REPORTS = {
    "2019-20": ("cpcb_MSW_AnnualReport_2019-20.pdf", [4, 5]),
    "2020-21": ("cpcb_MSW_AnnualReport_2020-21.pdf", [4, 5, 6]),
    "2021-22": ("cpcb_MSW_AnnualReport_2021-22.pdf", [6, 7]),
}

METRIC_PATTERNS = [  # header text -> metric name (order matters: first match wins)
    (re.compile(r"(?i)gap"), None),
    (re.compile(r"(?i)generat"), "generated"),
    (re.compile(r"(?i)collect"), "collected"),
    (re.compile(r"(?i)treat|process"), "treated"),
    (re.compile(r"(?i)landfill"), "landfilled"),
]

CLEAN_NUMBER = re.compile(r"^\d{1,3}(,\d{2,3})*(\.\d+)?$|^\d+(\.\d+)?$")


def metric_for(header: str):
    for pattern, metric in METRIC_PATTERNS:
        if pattern.search(header or ""):
            return metric
    return None


def parse_cell(raw: str):
    """Return (value or None, status)."""
    text = " ".join((raw or "").split())
    if CLEAN_NUMBER.match(text):
        return float(text.replace(",", "")), "clean"
    digits = re.findall(r"[\d.]", text)
    letters = re.findall(r"[A-Za-z]", text)
    numbers_in_text = re.findall(r"\d[\d,]*\.?\d*", text)
    # Overlap signature: digits interleaved letter-by-letter with a word, no spaces between digit runs.
    if digits and letters and len(numbers_in_text) >= 2 and " " not in re.sub(r"[A-Za-z]", "", text).strip():
        candidate = "".join(digits)
        if re.fullmatch(r"\d+(\.\d+)?", candidate):
            return float(candidate), "recovered_from_overlap"
    return None, "excluded"


def clean_state(raw: str) -> str:
    return " ".join((raw or "").split())


def extract():
    rows, totals = [], {}
    for year, (pdf_name, pages) in REPORTS.items():
        with pdfplumber.open(PUBLIC / pdf_name) as pdf:
            for page_no in pages:
                for table in pdf.pages[page_no - 1].extract_tables():
                    header = [" ".join((c or "").split()) for c in table[0]]
                    if not (any(h.lower() == "state" for h in header)
                            and any(metric_for(h) == "generated" for h in header)):
                        continue
                    col_metric = {i: metric_for(h) for i, h in enumerate(header) if i >= 2}
                    for r in table[1:]:
                        # 2021-22 puts the TOTAL label in the serial-number column.
                        is_total = any(clean_state(c).upper() == "TOTAL" for c in r[:2])
                        state = "TOTAL" if is_total else clean_state(r[1])
                        if not state:
                            continue
                        for i, metric in col_metric.items():
                            if metric is None:
                                continue
                            raw = " ".join((r[i] or "").split())
                            value, status = parse_cell(raw)
                            if state.upper() == "TOTAL":
                                totals[(year, metric)] = value
                                continue
                            rows.append({
                                "report_year": year, "state": state, "metric": metric,
                                "raw_cell": raw, "value_tpd": "" if value is None else value,
                                "parse_status": status, "source_file": pdf_name, "page": page_no,
                            })
    return rows, totals


def main():
    rows, totals = extract()
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} cells to {OUT.relative_to(REPO)}")
    by_status = {}
    for r in rows:
        by_status[r["parse_status"]] = by_status.get(r["parse_status"], 0) + 1
    print("parse status counts:", by_status)
    print("\nchecksum: sum of extracted state values vs the report's own TOTAL row")
    for (year, metric), total in sorted(totals.items()):
        vals = [r["value_tpd"] for r in rows
                if r["report_year"] == year and r["metric"] == metric and r["value_tpd"] != ""]
        s = sum(vals)
        diff = "n/a" if not total else f"{100 * (s - total) / total:+.2f}%"
        n_states = len({r["state"] for r in rows if r["report_year"] == year and r["metric"] == metric})
        print(f"  {year} {metric:<10} states={n_states:<3} numeric={len(vals):<3} sum={s:>12,.2f}  TOTAL={total!s:>12}  diff={diff}")
    print("\nexcluded / recovered cells:")
    for r in rows:
        if r["parse_status"] != "clean":
            print(f"  {r['report_year']} {r['state']:<28} {r['metric']:<10} {r['parse_status']:<22} raw={r['raw_cell']!r}")


if __name__ == "__main__":
    main()
