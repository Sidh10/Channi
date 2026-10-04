# data/public/ — real public data (not synthetic)

| File | Source | Notes |
|---|---|---|
| `cpcb_MSW_AnnualReport_2016-17.pdf` | CPCB, https://cpcb.nic.in/uploads/MSW/MSW_AnnualReport_2016-17.pdf | No consolidated state-wise table (figures are in prose) — not used. |
| `cpcb_MSW_AnnualReport_2019-20.pdf` | CPCB, https://cpcb.nic.in/uploads/MSW/MSW_AnnualReport_2019-20.pdf | State table on pp. 4–5 |
| `cpcb_MSW_AnnualReport_2020-21.pdf` | CPCB, https://cpcb.gov.in/uploads/MSW/MSW_AnnualReport_2020-21.pdf | State table on pp. 4–6 |
| `cpcb_MSW_AnnualReport_2021-22.pdf` | CPCB, https://cpcb.nic.in/uploads/MSW/MSW_AnnualReport_2021-22.pdf | State table on pp. 6–7 |
| `cpcb_msw_statewise_tpd.csv` | Extracted from the three PDFs above by `audit-engine/scripts/extract_cpcb_statewise.py` | One row per state × metric × report year (generated / collected / treated / landfilled, TPD). Every cell's parse outcome is recorded (`clean`, `recovered_from_overlap`, `excluded`). |

Downloaded 2026-10-03 with the user's approval. SHA-256 of the PDFs is reproducible with `sha256sum *.pdf`.

**What this data is:** state-level figures self-reported by SPCBs/PCCs to CPCB. It is not ward-level and not MCD tonnage logs. It is used only for the real-data Benford check (tasklist Phase 1, last item); results are recorded in `docs/data-sources.md`.

**Extraction check:** the sum of extracted state values matches each report's own TOTAL row for 10 of 12 report-year × metric columns (exactly, or within the TOTAL row's 1-decimal rounding, ≤ 0.09 TPD). The other two differ by exactly the one excluded cell in each (2020-21 Maharashtra landfilled `1355.36 (Unscientifically disposed= 6221.5)`; 2021-22 Kerala collected `1,283 and 1,048 decentralized processing`).
