# Build Tasklist — Chhanni Demo

Status legend: `[ ]` todo, `[~]` in progress, `[x]` done. This file is the source of truth for where the build stands — read it before planning any session, and keep it updated as work completes.

## Phase 0 — Setup
- [x] Scaffold repo structure (`frontend/`, `backend/`, `audit-engine/`, `data/`)
- [x] Postgres schema: wards, lots, credits (with escrow state), audit_flags — plus ward_tonnage_logs, audit_runs, ward_audit_scores, credit_events; escrow rules enforced by DB triggers (decisions.md D9), 16 schema/health tests in `backend/test/`
- [x] Local Postgres setup (docker-compose or equivalent) — `npm run db:start` (embedded Postgres 17, decisions.md D11); `docker-compose.yml` kept as alternative
- Phase 0 verified by `demo-verifier` (one D9 integrity gap found and fixed, re-verified)

## Phase 1 — Statistical audit engine (core differentiator — build this first)
- [x] Synthetic ward tonnage generator, calibrated to CRDT aggregate figures where possible — 40 synthetic wards, trip-level entries, city total calibrated to 11,862 TPD (only verified figure used)
- [x] Tampering injector (rounded/fabricated entries) for the test dataset — rounding, fabrication, inflation; targeted single-ward injection for the demo
- [x] Benford's Law test implementation (leading-digit chi-square / D*) — per ward on trip entries (D12), plus `roundness` digit test
- [x] Socio-economic baseline model (population/income/sanitation → expected generation) — leave-one-ward-out robust fit, one-sided (D13)
- [x] Reproducible script: run both checks, report detection rate on the tampered set — `python -m chhanni_audit.run_detection`, 30+ seeds, byte-identical reruns; results and claim limits in D14 (moderate inflation NOT reliably detected)
- [x] Once real public data is tried, record the result in `docs/data-sources.md` — CPCB state-wise tables 2019-22: conforms to Benford, weak test (n=36, ~60% power); no public ward-level series found
- Phase 1 verified by `demo-verifier` (all numbers reproduced byte-identically; 5 doc-wording fixes applied). 82 audit-engine tests pass.

## Phase 2 — Contamination banding
- [x] Decide: rule-based placeholder vs simple trained model on a public dataset (time-box this) — rule-based placeholder (D15)
- [x] 3-band classifier (clean / mixed / heavily contaminated) — `backend/src/banding.js`, placeholder cut-offs 10% / 30% (design parameters, not a standard)
- [x] Mock/real source label attached to every classifier result and stored in `lots.band_source` (+ input % in `lots.contamination_pct`, required by migration 003). Rendering it in the UI is carried to Phase 5 and saying it in the demo narration to Phase 6 (both since done). *(Original wording: "Label clearly in UI/demo whether output is from a real model or a mock (decisions.md D2)".)*
- Phase 2 verified by `demo-verifier` (item 3 reworded to what was delivered; store/re-classify edge case at the cut-offs fixed; 23 backend tests pass)

## Phase 3 — Credit pricing + escrow
- [x] Pricing function: band + weight → credit value — `backend/src/pricing.js`, clean-equivalent kg × placeholder quality factors (D16)
- [x] Escrow state machine: `pending` → `matured` → `tradable`, or → `clawed_back` — `backend/src/escrow.js` over the DB-enforced states (D9, D17)
- [x] Audit flags → hold → clawback, **database side**: open/under-inspection flags hold covered credits; confirming a flag claws them back atomically; flag integrity enforced by migration 005. The Python `audit()` → DB writer that creates those rows was done in Phase 4. *(Original wording: "Wire audit engine flags (Phase 1) to trigger clawback".)*
- Phase 3 verified by `demo-verifier` (mutation-tested; atomic-confirm race found and fixed; 48 backend tests pass)

## Phase 4 — Backend API
- [x] Endpoints: submit lot, get credit value, list ward audit scores, trigger escrow transition — `backend/src/app.js`: `POST /api/quote`, `POST /api/lots`, `GET /api/credits` (+ `/:id/events`), `GET /api/wards`, `POST /api/audit/run`, `GET /api/audit/latest`, `POST /api/escrow/cycle` (optional simulated `now`), `POST /api/flags/:id/inspect|confirm|clear`, `POST /api/demo/inject`, `GET /api/meta` (data notice + placeholder labels), `GET /api/engine`
- [x] Connect audit engine (Python) to Node backend — pick the simplest integration that works in a week (subprocess, queue, or small internal API) — subprocess, JSON over stdin/stdout (D18): `chhanni_audit.service` + `backend/src/auditEngine.js`
- [x] Write `audit()` results into `audit_runs` (incl. `period_start/end`, `finished_at`), `ward_audit_scores`, `audit_flags` (D17; carried over from Phase 3). Holds take effect as soon as the run commits, because they're derived at read time (`listCredits`, escrow queries), so no escrow cycle is needed for a flag to hold credits. Releasing clean wards' matured credits to tradable still needs an explicit `POST /api/escrow/cycle`. The writer must:
  - write scores and flags in the same transaction as `finished_at`, so release never sees a finished run without its flags;
  - write a ward-level `audit_flags` row for every `ward_audit_scores.flagged = true`, because escrow reads flags, not the `flagged` column;
  - set `period_start/end` to the dates actually audited;
  - write only ward-level findings as holding flags. Entry-level roundness hints and ward-day hints go in that flag's `details`, not as extra open flags; otherwise clearing the ward flag would leave stray holds.
- [x] Seed synthetic wards + trip logs into Postgres (bulk COPY — ~147k rows for the demo seed); pass `ward_tonnage_logs.id` as `entry_id` so flags reference real rows — `npm run db:seed` (bulk `unnest` inserts, not COPY: 147,206 rows in ~5–11 s)
- [x] Submit-lot endpoint stores the classifier's *returned* `contaminationPct` and pricing's returned `weightKg`/`creditValue`, with a test (D15, D16)
- Phase 4 verified by `demo-verifier` twice (first pass: nothing blocking, 2 doc/test fixes plus robustness findings; second pass after typed errors and validation: all 5 checks pass, 2 tiny input gaps fixed). 83 backend + 130 audit-engine tests pass

## Phase 5 — Frontend dashboard
- [x] Contamination slider → live credit value — `frontend/` one-page dashboard (D19); slider + weight → `/api/quote` live
- [x] Ward integrity view (ranked by audit confidence) — ranked by strongest evidence (smallest q at full precision), with inspect / confirm / clear on each flag
- [x] Visible escrow state / clawback event — ledger state badges + per-credit history; simulated demo clock drives maturing
- [x] Show the banding source label ("Simulated scan — rule-based placeholder, not a trained model") wherever a band is shown (D15; carried over from Phase 2). If the 10% / 30% cut-offs are displayed, label them "placeholder cut-offs, not a standard"
- [x] Label all ward/lot data as synthetic in the UI; never show a per-capita figure or call `confidence_score` a probability (D13, D14). Strongly flagged wards have `confidence_score` 0.0 (q ≈ 1e-46 rounded); show it as e.g. "q < 0.0001", not "0% confidence"
- [x] Roundness localisation hints in a flag's `details` are the ward's entries that are exact multiples of the flagged resolution: injected *and* genuine ones. On the demo seed, the default injection gives 79 hints, which are 76 injected rows plus 3 genuine, and cover only 76 of the 144 injected rows. Precision and recall were never evaluated. Never label them "the tampered entries"; at most "entries worth inspecting first" (D18)
- [x] Label the quality factors, the "clean-equivalent kg" unit and the 28-day hold as placeholders wherever shown (D16, D17)
- [x] Render the derived **held** status (from `listCredits().held` / `holding_flag_ids`) with a "held since" time taken from the holding flag's `created_at`. Holds don't write `credit_events` rows, so the timeline must add them (D17) — done for current holds; known limitation: a hold later lifted by *clearing* its flag isn't shown in history, and the hold before a clawback is shown without a time
- Phase 5 verified by `demo-verifier` (first pass: 1 overclaim + 2 fixes + stage risks; all fixed and re-verified). Browser walkthrough of both beats at 1600×1000; no horizontal scroll at 375 px. 85 backend tests pass

## Phase 6 — Demo flow
- [x] Scripted two-beat demo (decisions.md D7), repeatable without manual fudging — run sheet `docs/demo-script.md`; `npm run demo:rehearse` asserts every step (3 identical back-to-back runs) — reset via `npm run db:seed -- --fresh` (TRUNCATE) or `npm run db:reset`; targeted DELETEs are blocked once flags exist (migration 005)
- [x] Earn-as-you-Sort stub payment trigger (mocked) — `stub_payouts` row per scanned lot, schema allows only `upi_sandbox_stub` / `simulated` (D20)
- [x] Simulated demo clock: pass `now` to `runEscrowCycle` to get past the 28-day hold, and have the narration say the time jump is simulated. Beat 2 includes the inspector-confirm step (flag holds → confirm → clawed back, D17)
- [x] Demo narration script states plainly: bands come from a simulated scan and a rule-based placeholder (not a trained model), all ward/lot data is synthetic, and the audit doesn't catch moderate over-reporting (D5, D14, D15; carried over from Phase 2)
- Phase 6 verified by `demo-verifier` (1 blocking narration fix + 5 non-blocking fixes applied, re-verified). 87 backend tests pass; rehearsal passes

## Phase 7 — Verification pass (do not skip)
- [x] Use the `demo-verifier` subagent to check every stat shown against `docs/data-sources.md` — UI, API fields, run sheet and README swept; every number is Verified (CRDT 11,862 TPD; CPCB own result) or labelled synthetic/placeholder
- [x] Confirm nothing out-of-scope (SPEC.md) crept into the build — manifests, lockfiles, code and docs checked; payouts schema-limited to the sandbox stub
- [x] Dry run the full demo end to end — browser dry run of `docs/demo-script.md` (19 on-screen checks) plus the verifier's independent 23-check API replay; `npm run demo:rehearse` passes; detection report and CPCB result regenerate byte-identically
- Phase 7 completed 2026-10-03: nothing blocking; 4 doc fixes applied (README refreshed, stale tasklist notes, D14 σ qualifier, run-sheet wording)
