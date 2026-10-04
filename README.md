# Chhanni

Quality-weighted recycling credits with a statistical audit layer, built for WasteChakra 2026 Round 2 (Team TREX).

A lot is banded by contamination (clean / mixed / heavily contaminated) and priced into credits. Credits wait in escrow while a statistical audit checks the ward's reported tonnage log: Benford first-digit test, round-number excess, socio-economic baseline and single-day spikes. A flag holds the credit. An inspector confirms the flag (which claws the credit back) or clears it (which releases it).

**Honesty notes:**
- All ward, log and lot data is **synthetic**. It is calibrated to one verified figure, the CRDT/IIT Delhi 11,862 TPD (`docs/data-sources.md`).
- The contamination "scan" is a rule-based placeholder, not a trained model.
- Quality factors, band cut-offs, the 28-day hold and the payout rate are labelled placeholders.
- Payouts are a sandbox stub: no money moves.
- What the audit does and doesn't catch is in `docs/decisions.md` D14. In short, it does **not** reliably catch moderate over-reporting.

## Run it

Windows, Node 22.9+, Python 3.11+. First time: `copy .env.example .env`, `cd backend && npm install`, `cd frontend && npm install`, then in `audit-engine/` run `python -m venv .venv` and `.venv\Scripts\python.exe -m pip install -r requirements.txt`.

| Terminal | Command |
|---|---|
| 1 | `cd backend && npm run db:start` (Postgres 17 via `embedded-postgres`, no Docker) |
| 2 | `cd backend && npm run db:migrate && npm run db:seed && npm start` (API on :4000) |
| 3 | `cd frontend && npm run dev` (dashboard on http://localhost:5173) |

To rehearse both demo beats with every outcome asserted, run `cd backend && npm run demo:rehearse`. The presenter's run sheet is **`docs/demo-script.md`**.

Tests:
- `cd backend && npm test` (87 tests, on a separate `chhanni_test` database);
- `cd audit-engine && .venv\Scripts\python.exe -m pytest` (130 tests).

Reproducible detection report: `cd audit-engine && .venv\Scripts\python.exe -m chhanni_audit.run_detection`.

## Layout

- `backend/`: Express API, escrow and flag rules enforced in Postgres (`db/migrations/`), engine subprocess runner.
- `audit-engine/`: Python statistical engine (`chhanni_audit`): synthetic generator, tamper injector, checks, JSON service.
- `frontend/`: React dashboard.
- `data/`: `synthetic/` (generated) and `public/` (CPCB reports + the real-data Benford result).
- `docs/`:
  - `decisions.md`: why things are built this way;
  - `SPEC.md`: scope;
  - `tasklist.md`: build status;
  - `data-sources.md`: which figures are verified;
  - `demo-script.md`: the run sheet.

## Subagents

- `stats-auditor`: builds and tests the audit engine.
- `demo-verifier`: read-only; independently checks work against decisions/SPEC/data-sources before anything is marked done.
