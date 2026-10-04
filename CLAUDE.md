# Chhanni — WasteChakra 2026 Round 2 Build

One-line: quality-weighted recycling credits (contamination banding) with a statistical audit layer (Benford's Law + socio-economic baseline) that verifies the data behind each credit, plus an escrow/clawback settlement model. Team TREX (Siddharth Raj, Parth Badgujar).

This repo builds a testable MVP for the Round 2 demo and 10-slide deck (up to 10 slides, optional 2-minute video). The Round 1 concept note is already submitted and selected — do not re-litigate the idea here, only implement and validate it.

@docs/decisions.md
@docs/SPEC.md

Before planning or implementing a phase, also read `docs/tasklist.md` (current build status). Before using any statistic in UI copy, slide content, or code comments, read `docs/data-sources.md` and check the verification status of that figure.

## Autonomy — work through this without stopping for approval on every step

The person running this is not going to relay your questions to another chat and bring back answers. Decide things yourself using the files below, and only interrupt for what's genuinely blocking.

- Default to proceeding through `docs/tasklist.md` in phase order. Mark items `[~]` when starting, `[x]` when done, in the file itself, as you go — that file is how progress gets reviewed later, not a chat summary.
- Before asking a question, check whether `docs/decisions.md` or `docs/SPEC.md` already answers it. Most "which approach should I use" questions are already decided there.
- For anything not covered — a library choice, a folder layout detail, a minor API shape — make the reasonable call yourself, note it briefly in `docs/decisions.md` as a new entry if it's the kind of thing someone would ask about later, and keep moving.
- Only stop and ask when: a choice is genuinely destructive or hard to reverse (e.g. a schema decision that's expensive to migrate later), something contradicts `docs/decisions.md`/`docs/SPEC.md` and you can't tell which should win, or you're blocked on something only the person can provide (a credential, a real dataset, a product decision outside this repo's scope).
- When you do stop, ask one specific question with your recommended default, not an open-ended "what should I do?"

## Hard constraints

- MVP scope is Layers B+C only: Benford's Law digit analysis + socio-economic baseline divergence, plus contamination banding and the escrow state machine. Do NOT build real blockchain settlement, real UPI payments, IoT/QR integration, or fine-grained polymer classification — see `docs/SPEC.md` "Out of scope."
- Never substitute a plausible-looking number for a real one. If a figure isn't marked "Verified" in `docs/data-sources.md`, either use clearly-labeled synthetic data or stop and ask rather than guess.
- Every number shown in the demo UI, logs, or slide content must trace to `docs/data-sources.md` or be clearly labeled synthetic. No invented statistics, ever.

## Stack

- Frontend: React (dashboard — contamination slider, ward integrity view)
- Backend: Node + Express, PostgreSQL
- Statistical engine: Python (scipy, scikit-learn) — Benford's Law tests, socio-economic baseline model
- Payments: UPI sandbox stub only, no real payment rail
- No infra beyond this — no blockchain, no real IoT, no QR/custody chain

## Commands

Run from the repo root unless noted. Copy `.env.example` to `.env` once. No linter is configured yet.

- Database (`cd backend`): `npm run db:start` — runs Postgres 17 on localhost:5432 in the foreground, data in `.pgdata/`; leave it running in its own terminal (decisions.md D11). Alternative where Docker works: `docker compose up -d db` from the root.
- Backend (`cd backend`): `npm install`, `npm run db:migrate`, `npm run db:reset` (drops + re-migrates local DB), `npm run db:seed` (loads synthetic wards + trip logs from the audit engine; `-- --fresh` truncates first), `npm run dev` (API on :4000), `npm test` (needs the DB server up; runs against a separate `chhanni_test` database that it drops and re-migrates, never the demo DB; one file: `npm test -- test/escrow.test.js`)
- Audit engine (`cd audit-engine`): `python -m venv .venv` then `.venv/Scripts/python.exe -m pip install -r requirements.txt` (Windows path); tests: `.venv/Scripts/python.exe -m pytest`; detection report (multi-seed, ~4–8 min, writes `data/synthetic/synthetic_*`): `.venv/Scripts/python.exe -m chhanni_audit.run_detection` (`--quick` smoke test, `--no-write` print only; keep `--workers` ≤ 4 on this laptop)
- Real-data extraction (`cd audit-engine`, needs `pip install -r requirements-extract.txt`): `.venv/Scripts/python.exe scripts/extract_cpcb_statewise.py` → `data/public/cpcb_msw_statewise_tpd.csv`; real-data Benford check: `.venv/Scripts/python.exe scripts/real_data_benford_cpcb.py` → `data/public/cpcb_benford_result.json`
- Frontend (`cd frontend`): `npm install`, `npm run dev` (Vite on :5173, proxies `/api` to :4000), `npm run build`
- Demo (`cd backend`): `npm run demo:rehearse` runs both demo beats through the API on the demo DB, asserting every outcome, then resets the data (`-- --runs 3` for repeatability). On stage use `npm start` for the API, not `dev`. Run sheet: `docs/demo-script.md`

## Working conventions

- Follow `docs/tasklist.md` phase order. Don't build frontend polish before the audit engine (Phase 1) has a working, reproducible detection result — that's the differentiator, everything else supports it.
- Use the `stats-auditor` subagent for anything in the statistical/audit engine.
- Use the `demo-verifier` subagent before marking any tasklist.md phase complete — it independently checks work against decisions.md, SPEC.md, and data-sources.md rather than trusting a self-report.
- Flag and stop on any error or inconsistency rather than silently working around it.
- Keep demo claims conservative: coarse contamination banding, not fine polymer purity (decisions.md D2). When in doubt, understate rather than overstate.
