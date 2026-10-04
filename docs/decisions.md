# Decisions — Chhanni

ADR-style log. Each entry: Context / Decision / Why. If a decision is later superseded, mark it superseded here rather than deleting it — history is useful.

## D1 — MVP scope: Layers B+C only
**Context:** The full concept combines contamination banding (vision), a statistical audit layer (Benford's Law + socio-economic baseline), escrow settlement, and a UPI incentive layer. Build window is about one week.
**Decision:** Build the statistical audit engine and escrow state machine as the working, demo-ready core. Contamination banding ships as coarse 3-band classification. UPI is a sandboxed stub, not real payments.
**Why:** The audit layer is the stated differentiator vs Recykal SmartSkan and vs blockchain credit systems (Alamsyah & Ramdhani, 2025) — it's the part a better classifier alone can't fake, and it's realistically buildable and testable in the time available.

## D2 — Contamination banding, not polymer-level classification
> *2026-10-04: this **Context** (the evidence) is **superseded by D21**. The numbers below misstate Thielmann et al. The **Decision** is unchanged.*

**Context:** Thielmann et al. (2025) report CV accuracy collapsing from >87% on lab benchmarks (TrashNet, TACO) to ~24% mAP on cluttered real waste streams (ZeroWaste-f).
**Decision:** Classify into 3 bands only — clean / mixed / heavily contaminated. Never claim polymer-level purity (PET vs HDPE etc.) anywhere in the demo, UI, or slides.
**Why:** Coarse banding is defensible at achievable accuracy. Fine-grained claims are not, and overclaiming here is the single most exploitable weakness a technical judge would find — Recykal's own public figures (>90% at controlled collection-centre scanning) make the gap easy to spot if we overreach.

## D3 — No real blockchain or smart contract
> *2026-10-04: the description of Alamsyah & Ramdhani in this Context is **corrected by D22**. The paper says "validator collusion or reporting errors", not "fraudulent documentation", and its dual authorization was simulated. The Decision is unchanged.*

**Context:** Escrow/clawback is core to "Why This Is Different" — it closes the oracle-problem gap cited from Alamsyah & Ramdhani (2025), whose own dual-authorization workflow still admits residual vulnerability to collusion and fraudulent documentation.
**Decision:** Model escrow and clawback as a Postgres-backed state machine (credit states: `pending` → `matured` → `tradable`, or → `clawed_back`), not an actual smart contract.
**Why:** A toy smart contract demo adds complexity without adding credibility. The mechanism — hold, then mature, then settle — is the point, not the ledger technology. Real blockchain settlement stays explicit roadmap (see SPEC.md).

## D4 — UPI payments: sandbox/stub only
**Decision:** The "Earn-as-you-Sort" payment flow is simulated — no real money movement, no real KYC or payment-rail integration.
**Why:** The concept note commits to this explicitly. Claiming a working payment rail would be unverifiable in a judged demo and isn't needed to prove the mechanism works.

## D5 — Synthetic ward data with injected tampering
> *2026-10-04: in practice the calibration uses **only** the Verified CRDT 11,862 TPD figure. No CPCB figure is used (D23).*

**Context:** No direct access to real MCD ward-level tonnage logs.
**Decision:** Generate synthetic ward-level daily tonnage data, calibrated to real CPCB/CRDT aggregate figures where possible (see data-sources.md), and inject synthetic tampering (rounded/fabricated entries) to demonstrate detection.
**Why:** This is the honest, buildable version of the demo. State this plainly in the deck and demo narration — never imply live MCD data access we don't have.

## D6 — Ground truth sources
> *2026-10-04: source statuses and what each claim rests on are **superseded by D23**. "CPCB MSW composition benchmarks" are not verified and can't be traced to a primary source. Thomaz et al. covers composition, not generation.*

**Primary sources:** CPCB MSW composition benchmarks, CRDT/IIT Delhi MCD SWM proposal (2026) tonnage figures, Thielmann et al. (2025), Zahran et al. (2014), Thomaz et al. (2023), Alamsyah & Ramdhani (2025), Recykal SmartSkan public disclosures.
**Status:** See `docs/data-sources.md` for per-citation verification status. Do NOT present an unverified figure as confirmed fact in any judge-facing material — soften the claim or omit it instead.

## D7 — Two-beat demo is the fixed target
**Decision:** The demo must show, in sequence: (1) a sample lot scanned, credit value changing live as the contamination band changes; (2) tampered ward data injected into the log and flagged by the audit layer before the credit matures to tradable.
**Why:** This is what's promised by the concept note's "Build and Feasibility" section. Every build task should trace to making one of these two beats work reliably — not to unrelated polish.

## D8 — Team
TREX — Siddharth Raj, Parth Badgujar. Theme: Circular Economy, Compliance & Credit Trading. Round 1 concept note submitted and selected (top 150).

## D9 — Escrow transitions enforced in the database; `tradable` is terminal
**Context:** D3 defines `pending` → `matured` → `tradable`, or → `clawed_back`, but doesn't say which states a clawback can come from.
**Decision:** Clawback is allowed only from `pending` or `matured`. `tradable` and `clawed_back` are both terminal (no further updates of any kind). Every clawback must reference the `audit_flags` row that caused it, and only clawbacks carry a flag (two-way `clawback_requires_flag` constraint). A credit's lot, value and pricing version are fixed at creation; its reason and clawback flag change only as part of a state transition; credits can't be deleted. Every state change is appended to `credit_events` by a trigger; that table rejects UPDATE/DELETE and rejects direct INSERTs (it only accepts inserts made from inside a trigger — today that's only the credits trigger, so any future migration adding another inserting trigger must keep that true).
**Why:** The pitch is that the audit catches bad data *before* a credit becomes tradable (D7 beat 2) — escrow is the hold period, and once released the credit is settled. Allowing post-tradable clawback would blur that story. Enforcing it in the DB means a row-level edit from app code or a demo script can't produce an illegal transition, silently rewrite or erase a clawback, or leave the credit and its history disagreeing — `backend/test/schema.test.js` covers each case. Scope of the guarantee, stated honestly: it covers row-level INSERT/UPDATE/DELETE only. `TRUNCATE` (of any table, including `credit_events` on its own) and `npm run db:reset` are deliberately still possible so the demo can be rerun from clean, and the local app role (`chhanni`) is itself a Postgres superuser, so it could disable triggers (e.g. `session_replication_role = replica`) — this is a data-integrity guard against bugs and demo-script shortcuts, not a security boundary. Don't describe it as tamper-proof in judge-facing material.

## D10 — Repo layout and tooling
**Decision:** Root-level `frontend/` (Vite + React), `backend/` (Express 5, `pg`, ESM, Node's built-in `node:test` runner, plain-SQL migrations in `backend/db/migrations/` applied by `npm run db:migrate`, tracked by filename — `001_init.sql` was revised in place before any data existed; from here on, schema changes go in new numbered files, never edits to applied ones), `audit-engine/` (Python package `chhanni_audit`, own `.venv`, pinned `requirements.txt`, pytest), `data/` (`synthetic/` vs `public/`). Postgres 17 runs locally (see D11). One root `.env` (copied from `.env.example`) is read by the backend via Node's `--env-file-if-exists`.
**Why:** Fewest moving parts for a one-week build: no ORM, no test framework dependency, no dotenv. The ward tonnage log lives in Postgres (`ward_tonnage_logs`) so D7 beat 2 is a real write into the log followed by an audit run, not a file swap. Its `is_injected_tamper` column is ground truth for scoring detection on synthetic data only — the audit engine must never read it.

## D11 — Local Postgres via `embedded-postgres`, not Docker
**Context:** On the primary dev/demo laptop (Windows Insider build 26300, Docker Desktop 4.69), Docker Desktop crashes on every start: Unix-socket files it created on the previous run (`%LOCALAPPDATA%\Docker\run\dockerInference`, `%LOCALAPPDATA%\docker-secrets-engine\engine.sock`) become unreadable to Windows, so it can't remove them and aborts.
**Decision:** The default local database is real Postgres 17 binaries from the `embedded-postgres` npm package (pinned dev dependency), started with `npm run db:start` in `backend/`; the cluster lives in `<repo>/.pgdata` (gitignored). `docker-compose.yml` stays as an equivalent alternative for machines where Docker works — same credentials, port and DB name, so `.env` doesn't change.
**Why:** The live demo can't depend on Docker Desktop starting cleanly on this machine. Same Postgres major version and wire protocol either way, so nothing downstream (backend, audit engine) cares which one is running.

## D12 — Where the digit tests apply: per ward, on trip-level entries
**Context:** Benford's Law only holds for data spanning several orders of magnitude. One ward's daily totals span ~0.2 orders of magnitude in the synthetic data, so a first-digit test on them is invalid.
**Decision:** One `ward_tonnage_logs` row = one collection trip. The first-digit Benford test (chi-square plus D*) runs per ward over its trip entries in the audit window (default 28 days), never on daily totals. In the per-ward audit, a ward is marked not applicable (no p-value) when its smallest expected digit count is too low for the chi-square approximation (roughly < 110 entries) or its values span less than the minimum orders of magnitude. The synthetic data never hits this (smallest ward: 1,143 entries), but smaller or real wards in Phase 4 will. The separate arbitrary-series function `benford_conformity` (used for the real CPCB data) behaves differently: simulated p-value for 20 ≤ n < 110, `not_applicable` for n < 20 or a span < 1 order of magnitude. A second digit-analysis test, `roundness` (excess of values that are exact multiples of 10 kg / 100 kg / 1 t vs other wards), was added because rounding barely moves first digits. Default Benford reference is the plain law (SPEC item 3); a `peer` reference (compare against other wards' digits) exists as a setting.
**Why:** Keeps the test inside its validity conditions. Stated plainly: clean synthetic trip loads are Benford by construction, so synthetic results show "the detector works *if* clean data conforms". The sensitivity run showed that if real trip loads are only approximately Benford, plain-Benford false flags can be very high (up to ~98% of clean wards in one lognormal setting) while the peer reference stays at 0 — so for any real ward data, switch to `benford_reference='peer'` first.

## D13 — Baseline model, decision rule, and confidence score
**Decision:** Expected ward tonnage comes from a robust fit on population/income/sanitation using only the *other* wards (leave-one-ward-out), so tampered data can't train the model to accept itself. It is one-sided (flags over-reporting only, fixed before testing). It flags whole wards for the period, plus single-day spikes (`baseline_daily`) — not individual trips, since one trip can't be judged against a ward-level prediction (a deliberate narrowing of SPEC item 3's "flags entries"). All ward×check p-values go through one Benjamini–Hochberg correction at q = 0.01, fixed in advance. `confidence_score` = the smallest BH-adjusted p-value across a ward's checks.
**Why:** Pre-fixed thresholds and multiple-testing control keep the false-flag rate honest (1 falsely flagged ward in 1,200 clean ward audits — 40 wards × 30 seeds, 4 checks each). The confidence score ranks wards by strength of evidence; it is **not** a probability that a ward's data is genuine — never label it that way in UI or slides. The generator and model share a functional form, so the baseline partly recovers it by construction.

## D14 — What the audit catches, and what the demo and deck may claim
**Context:** Phase 1 results (synthetic, 30 seeds; held-out seeds for demo sizing) — see `data/synthetic/synthetic_detection_report.json`, reproducible with `python -m chhanni_audit.run_detection`.
**Decision:** Claims are limited to what the numbers support:
- Rounded entries: caught by `roundness` (≥1.5% of a ward's entries → 100% on 100 held-out seeds).
- Fabricated entries: caught by Benford when spread (≥30% of entries → 98%) or by `baseline_daily` when concentrated on one day (≥5% → 98%).
- Over-reporting: only gross inflation is caught. Whole-ward ×2.5: 100% for a single target ward (held-out seeds), 97.5% when 4 wards are inflated at once (power seeds). ×2.0: 93% single-target (calibration seeds), 77% with 4 targets (power seeds). These rates hold only for the synthetic setting of unexplained ward-to-ward variation (σ = 0.10). At σ = 0.20, detection at ×2.0 falls to 10%, and the real-world spread is unknown. **Moderate inflation is not reliably detected** (at the default σ = 0.10: ×1.3 caught in ≤ 5% of cases, ×1.5 in ≤ 27%, across the reported runs and the verifier's independent fresh-seed re-check; with less ward-to-ward variation, σ = 0.05, ×1.5 was caught in 90% of sensitivity runs and ×1.2 in 10%, so detection depends on a real-world spread we don't know). The deck and demo must not claim otherwise; "compare each ward with its own history" is the stated next step, not a built feature.
- These rates hold for the default 28-day audit window only. In verification, a custom 5-day window also flagged a ward with no injected rows (`baseline_daily`, q = 0.0086), so the demo must audit the default window.
- D7 beat 2 injects rounded or fabricated entries into one ward, sized from the held-out table (e.g. rounded phantom entries at 1.5% of the target ward's entries), not inflation. Demo default: seed 2026, target ward SYN-W27 (the largest ward). Misses in re-checks were all small wards, so if the seed or target changes, re-check that ward's row in the detection report rather than assuming the headline rate.
- The synthetic populations imply a per-capita generation rate that is an artefact of synthetic choices; no per-capita figure is ever displayed (data-sources.md has no verified one).
**Why:** The single most damaging thing in a judged demo is a claim a technical judge can break. The two-layer story ("digit tests catch rounding/fabrication; the baseline catches gross over-reporting; neither substitutes for the other") is true as stated and is backed by the per-tamper-type table.

## D15 — Contamination banding is a rule-based placeholder, labelled as such
**Context:** SPEC item 2 allows a rule-based placeholder or a simple model trained on a public dataset, time-boxed. The public waste-image datasets we considered (TrashNet, TACO, ZeroWaste) label or segment individual objects; we found none with lot-level contamination labels (not an exhaustive survey). A proxy could be derived from segmentation masks, but it would measure image area, not weight, from a narrow set of scenes, and the band cut-offs would still be ours. So a "trained" band classifier would rest on labels we'd effectively be inventing. (If this reasoning goes into the deck, add and verify a data-sources.md row first.)
**Decision:** `backend/src/banding.js` maps a simulated-scan input — an estimated contamination share (0–100% of lot weight) — to one of the three D2 bands using placeholder cut-offs: clean < 10%, mixed 10–<30%, heavily contaminated ≥ 30%. Every result carries `source = 'mock_rule_based'` (also stored in `lots.band_source`) and the label "Simulated scan — rule-based placeholder, not a trained model", which the UI and demo narration must show. The input % is stored on the lot (`lots.contamination_pct`, migration 002) so each band is traceable to its input. The classifier rounds the input to the column's 2 decimals before banding (e.g. 9.999 → 10.00 → mixed; rounding is on the binary value, so exact half-cent inputs can go either way — which is why callers must store the *returned* `contaminationPct`, never the raw input), migration 003 requires the input on every mock-banded lot, and `backend/test/banding-db.test.js` checks that stored lots re-derive to the same band at the cut-offs.
**Why:** A transparent rule is more honest than a model trained on labels we made up, and it costs nothing against the Phase 1 time budget. The 10% / 30% cut-offs are demo design parameters, not an industry, CPCB or buyer standard, and must never be presented as one. Swapping in a real classifier later only has to produce the same `{band, source}` shape.

## D16 — Credit pricing: clean-equivalent kilograms with placeholder quality factors
**Decision:** `backend/src/pricing.js` prices a lot as `credit_value = weight_kg × quality_factor(band)`, in **clean-equivalent kg** (not rupees). Factors: clean 1.0, mixed 0.5, heavily contaminated 0.1. `pricing_version = 'placeholder-v1'` is stored on every credit. Weight is priced as stored (2 decimals) and the value is rounded to the column's 4 decimals.
**Why:** A quantity unit makes the band → value effect visible (demo beat 1) without inventing a market price, which data-sources.md can't back. The factors are placeholder demo parameters, not market prices or a buyer/CPCB standard, and must be labelled that way wherever they're shown. Heavily contaminated lots keep a small non-zero factor so the slider shows a continuous drop rather than a cliff — a presentation choice, not a claim about recycler economics. Any rupee figure (e.g. the Earn-as-you-Sort stub, Phase 6) needs its own clearly-labelled simulated rate.

## D17 — Escrow timing, holds, and how audit flags reach credits
**Context:** D3 fixes the states; it doesn't say what moves a credit between them. D13 shows statistical flags still produce occasional false alarms (about 1 run in 30 had a false flag on clean data).
**Decision:** `backend/src/escrow.js`:
- A credit is issued `pending` with `matures_at = scanned_at + 28 days` (placeholder hold = one audit window). Maturing needs only the hold to elapse with no holding flag; it's becoming *tradable* that requires audit coverage (next bullets).
- **Held** is derived, not a new state: a credit is held while an `open` or `under_inspection` audit flag covers its lot's ward and scan date (local IST calendar date). Held credits can't mature or become tradable.
- `pending → matured` when the hold has elapsed and the credit isn't held. `matured → tradable` when it isn't held and a *finished* audit run covering the lot's scan date has scored its ward. A ward with no entries in the window gets no score, so its credits are never released by default.
- A statistical flag **holds**; it doesn't claw back. An inspector moves the flag to `confirmed`, which claws back every pending/matured credit it covers, each pointing at that flag. `cleared` lifts the hold, unless another open or under-inspection flag also covers the lot. Confirm and clawback happen in one SQL statement, so no escrow cycle can see the flag confirmed before its credits are clawed back.
- Flags are guarded in the database like credits (migration 005; same scope limits as D9):
  - new flags start `open`;
  - status only moves forward: `open` → `under_inspection` / `confirmed` / `cleared`, and `under_inspection` → `confirmed` / `cleared`. Inspection is optional, so `open` may go straight to either outcome;
  - every flag has a bounded, ordered period, both ends inclusive (no NULL "holds everything in the ward" flags);
  - apart from status, a flag is immutable and can't be deleted, since deleting one would silently lift a hold.
- Phase 3 implements the database side of "audit flags trigger clawback". Writing the Python engine's `audit()` output into `audit_runs` / `ward_audit_scores` / `audit_flags` (including the new `audit_runs.period_start/end`, migration 004) is the Phase 4 integration.
**Why:** Clawback stays tied to a *failed* audit as D3 requires, where "failed" means confirmed on inspection, so one false alarm can't destroy legitimate credits. This matches SPEC items 4 and 5 ("visible state transition … when a flagged lot is held", "flaggable for inspection"). Demo beat 2 becomes: inject tampered entries → run audit → ward flagged and its credit visibly **held** before it can mature → inspector confirms → **clawed back**. The 28-day hold is a design parameter, not a regulatory figure.

## D18 — Node ↔ Python integration: subprocess with JSON over stdin/stdout
**Decision:** The backend runs `audit-engine/.venv/Scripts/python.exe -m chhanni_audit.service <generate|inject|audit|version>`, writes one JSON request to stdin and reads one JSON response from stdout (diagnostics on stderr; non-zero exit = error). Node owns every database read and write. The Python engine stays a pure function of the data it's given, with no DB driver. Node never sends `is_injected_tamper` to the engine. Demo tampering uses the engine's own injector in add mode (new phantom entries appended to the log, never edits), with the held-out-validated default sizes (D14).
**Why:** It's the simplest integration that works in a week: no queue, no extra service to keep running, and no new dependencies. Keeping DB writes in Node means the escrow and flag rules (D9, D17) are applied in one place, and the engine's detection results stay identical to the evaluated `pipeline.audit()` (tested). The cost is about 18 MB of JSON per full audit, which is fine at demo scale (measured: the full seed-2026 audit takes ~3.5 s in the engine and ~3–7 s end to end, with ~225 MiB peak; seeding takes ~5–11 s).
**Details that matter later:**
- The service must run with cwd = `audit-engine/` (the package isn't pip-installed).
- `audit` clamps the window to the first and last entry dates actually present inside the requested bounds, because `pipeline.audit()` would count empty days as zero tonnage and bias the baseline. That clamped window is what `audit_runs.period_start/end` records.
- The service checks its own `db` section before returning it: every flagged ward has at least one ward-level flag, there are no hint rows, and every flag carries the run period. If any check fails it exits non-zero, so Node never writes a malformed run.
- Default demo injection sizes, each checked by a test against the held-out table:
  - round / spread: 1.5%;
  - round / last_day: 1.5%;
  - fabricate / spread: **30%**;
  - fabricate / last_day: 7.5%.
  For fabricate / spread we took 30% rather than the 40% margin size. At 40% the phantom rows add ~58% to the ward's tonnage, and the baseline co-fires in 41% of held-out runs, so the demo would stop showing what the digit test alone catches.
- `npm test` runs against a separate `chhanni_test` database (`backend/scripts/run-tests.js`), and the end-to-end API test drives the real engine.
- Roundness localisation hints in a flag's `details` are every entry in the ward that is an exact multiple of the flagged resolution, so they include genuine round values. Demo seed, default round/spread injection into SYN-W27: 79 hints = 76 injected + 3 genuine, covering 76 of the 144 injected rows. Precision and recall are unevaluated, so present the hints as "entries worth inspecting first", never as "the tampered entries".
- API errors are mapped by type (`backend/src/errors.js`), not by message text. Validation errors return 400, missing resources 404, flag-status conflicts 409, engine input errors 400, other engine failures 502, and anything untyped 500. Known gap: the Python service reports *any* `ValueError` as `invalid_input`, so an engine bug that raises a numpy/pandas `ValueError` would surface as a 400. Node validates inputs first, so API callers can't currently reach that path.

## D19 — Dashboard: one projector-first page, simulated clock, evidence shown as q
**Decision:** `frontend/` is one page sized for a projector:
- **Left column:** ① *Scan a lot* (beat 1), then the escrow ledger and an activity log.
- **Right column:** ② *Audit the ward logs* (beat 2), with the demo-tampering controls next to the ward integrity table they affect.
- **Persistent banner:** shows the API's SYNTHETIC data notice.
- **Placeholder labels** come from `/api/meta`, so they can't drift from the backend:
  - band cut-offs and quality factors;
  - the credit unit and the 28-day hold;
  - "Simulated scan — rule-based placeholder, not a trained model".
- **Demo clock:** a simulated date kept in the browser (localStorage, falling back to memory). It starts at 2026-08-02 10:00 IST, the last day of the synthetic log window, so "today" lines up with the audit window. New lots are stamped with it.
  - "+1 / +7 / +30 days" advance it and run the escrow cycle. They're disabled until a credit exists.
  - Run audit and Clear flag also run the escrow cycle at the current demo time (`settle()`).
  - When the dataset is fresh (no credits and no audits, e.g. after a CLI reseed), the app rewinds the clock to the start date.
  - Once the clock passes the last log date (from `/api/meta` `logWindow`), the scanner warns that new lots fall outside every audit window. It shows at the +30-day step of the scripted demo, which is expected.
  - Real wall-clock times appear only in event and activity logs, labelled "system time".
- **Ward evidence** is shown as the BH-adjusted *q* value: the smallest of the ward's flag scores, in full precision, e.g. 5.4 × 10⁻⁴⁶. It's labelled "lower = stronger evidence … not a probability", never as a percentage or "confidence".
- **Flag actions** sit on each flag: start inspection, confirm (claws back), clear.
- **Held** is a badge on the credit ("Held · flag #N"), and the credit history adds the hold step. Known limitation: a hold that ended because its flag was *cleared* doesn't appear in the history, and an inferred hold before a clawback has no time.
- **Ranking:** the ward table is ordered by each ward's smallest flag q at full precision, then by stored confidence, because strongly flagged wards all round to 0 at 4 dp.
- **Localisation hints** read "entries worth inspecting first … not a list of tampered entries" (D18).
- **Reset demo** calls `POST /api/demo/reset` (truncate + reseed 2026) and resets the clock.
**Why:** Both beats have to be visible at once on one screen, with every number traceable (data-sources.md, or a labelled synthetic/placeholder value). A simulated clock is the only honest way to show a 28-day escrow hold in a live demo. The narration must say the time jump is simulated (Phase 6).

## D20 — Earn-as-you-Sort stub, and an automated rehearsal of the two beats
**Decision:**
- **Earn-as-you-Sort.** Every scanned lot records one `stub_payouts` row in the same transaction as the lot and credit (migration 006, `backend/src/payouts.js`): amount = credits × a placeholder rate of ₹1.00 per credit, to collector `SYN-COLLECTOR-01` by default. CHECK constraints allow only `rail = 'upi_sandbox_stub'` and `status = 'simulated'`, so the table can't record anything resembling a real payment. No payment API is called, and `UPI_SANDBOX_KEY` is unused. The UI shows the payout as "simulated UPI payout (sandbox stub, no money moves; placeholder rate)". The payout isn't reversed on clawback: reversals belong to a real payment rail, which is out of scope (SPEC).
- **Rehearsal.** `npm run demo:rehearse` (`backend/scripts/demo-rehearsal.js`) drives both beats through the same HTTP API the dashboard uses, on the demo database: reset → quotes → two scans → clean audit → default injection → re-audit → +30 days → inspect/confirm. It asserts every outcome the narration relies on and resets the data afterwards (`--runs N` for a repeatability check, `--keep` to inspect the final state). The run sheet and narration are in `docs/demo-script.md`.
**Why:** SPEC item 6 asks for a mocked payment trigger "when a lot is scanned and scored". Making the stub enforceable in the schema keeps D4 true even if someone edits the code. The rehearsal turns "repeatable without manual fudging" (SPEC success criteria) into something that can be checked: three back-to-back runs gave identical results (144 injected rows, only SYN-W27 flagged at q ≈ 5.4 × 10⁻⁴⁶, the same clawback), at about 22 s per run.

## D21 — D2's evidence corrected after re-reading Thielmann et al. (2025)
**Context:** D2 cited Thielmann et al. (2025) as ">87% on lab benchmarks (TrashNet, TACO) to ~24% mAP on … ZeroWaste-f". On 2026-10-04 we read the paper in full. In *IEEE Access* 13:151934–151953, p. 151944, the comparison actually pairs different datasets, metrics and models:
- Mask R-CNN: 87.2% mAP on ReSORT-IT (a lab-setting dataset) vs 24.3% mAP on ZeroWaste.
- DeepLabV3: 90.9% mIoU on TACO, vs its successor DeepLabV3+ at 52.1% mIoU on ZeroWaste.
- TrashNet's figure is 94.7% *classification accuracy*, a different task.

The 24.3% is ZeroWaste's original baseline. The review's best ZeroWaste-f detection is 34.25% mAP, and Abid et al. (2025, arXiv:2508.18799, accepted at BMVC 2025 per its arXiv record) report 51.6 mAP with fine-tuned transformer detectors. Other industrial-grade datasets in the review score higher than ZeroWaste: WaRP reaches 60.2% mAP (TOOD) and SpectralWaste 69.96% mIoU (COSNet).

**Decision:** D2's decision stands: three coarse bands only, and never polymer-level purity. The evidence is restated as the exact pairs above, each with its own model, metric and dataset. The 51.6 mAP result (Abid et al.) is the caveat. We retire the "collapses to ~24%" headline and the ">87% on TrashNet/TACO" phrasing everywhere. Verbatim quotes are in `docs/data-sources.md`.

**Why:** The gap the review documents is still large. On ZeroWaste the representative figures are 24.3% mAP and 52.1% mIoU, with 51.6 mAP for 2025 detectors. Other industrial datasets reach 60–70. In lab and in-the-wild settings the figures are 87–91 (94.7% for TrashNet classification, a different task). Quote figures only per dataset, never as one "industrial" range. Coarse banding therefore remains the defensible scope. But quoting only the 24% baseline would be easy for a judge to break with the 2025 results, and pairing TrashNet/TACO with an mAP figure was simply wrong. The argument is now somewhat weaker as a single number and stronger as a citation.

## D22 — Alamsyah & Ramdhani (2025) verified; D3's description corrected
**Context:** D1 cites the paper as a blockchain credit system, and D3 cites it for the oracle-problem gap. Read in full on 2026-10-04 (*Waste Management Bulletin* 3:100250). The paper:
- builds a blockchain plastic-credit system (dual-token ERC-20/ERC-721 on Polygon PoS);
- *simulates* a dual-authorization workflow, needing a third-party validator and a government representative to approve (p. 8);
- states that this "does not entirely eliminate reliance on off-chain data, introducing residual vulnerabilities such as validator collusion or reporting errors, commonly referred to as the oracle problem" (p. 9).

**Decision:** Mark the source Verified. D1's description is accurate. D3's phrase "collusion and fraudulent documentation" is replaced by the paper's own words, "validator collusion or reporting errors", noting the workflow was simulated. The paper's "15–20 %" latency/cost figure for decentralized oracles is a hedged "may" with no measurement behind it. Quote it only with "may", or leave it out.

**Why:** The escrow pitch (audit before credits trade) leans on this paper's own admission that dual authorization leaves an oracle problem. Using its exact wording keeps that claim unbreakable.

## D23 — Source statuses after the citation pass (supersedes D6's list; clarifies D5)
**Context:** D6 listed seven "primary sources", three of them never checked. The earlier note said Zahran (2014) and Thomaz (2023) might be mis-cited or hallucinated. Both turned out to be real papers; their PDFs were in `D:\Hackathons\WasteCHakra\papers\`, and their DOIs resolve.

**Decision:** Statuses as of 2026-10-04 (details and quotes in `docs/data-sources.md`):
- **Zahran et al. (2014), *J Risk Uncertain* 49:235–257: Verified.**
  - What it shows: Benford first-digit tests applied to US EPA TRI self-reported lead emissions.
  - Use it as precedent that Benford is used on regulatory self-reports. The paper does describe a validation, but an *indirect* one: Benford deviation compared across emission categories with more or less reporting discretion. There is none against confirmed fraud.
- **Thomaz et al. (2023), *Waste Management Bulletin* 1:11–17: Corrected.**
  - What it shows: an ANN on population, GDP, water supply and sanitation predicts the gravimetric *composition* and *specific weight* of MSW in one city (Rio de Janeiro). The authors propose it for auditing third-party composition data.
  - What it doesn't show: waste *generation*.
- **CPCB Delhi composition figures: still Unverified.**
  - They're absent from the CPCB MSW Annual Reports for 2016-17, 2019-20, 2020-21 and 2021-22.
  - They appear only in Meena et al. (2023), Table 3, captioned "(CPCB, 2007)". That review's reference list has no CPCB 2007 entry.
  - They're used nowhere in the build. Don't use them in judge-facing material.
- **What the audit-engine pitch (D1) now rests on:**
  - *Benford layer:* Zahran (2014) as precedent; our own reproducible synthetic results (D14); our weak real-data check on CPCB state tables (data-sources "Own result").
  - *Baseline layer:* our own synthetic evaluation (D13, D14) only. Thomaz is at most a loose precedent that socio-economic models can cross-check reported waste data. It doesn't support predicting tonnage.
- **D5 calibration:** the synthetic generator is calibrated only to CRDT's 11,862 TPD (Verified; `audit-engine/chhanni_audit/calibration.py`). D5's "CPCB/CRDT aggregate figures" is broader than what's built. No code change is needed, because no unverified figure enters the data.

**Why:** D6 told us not to present unverified figures as fact. Now every literature claim is either quoted from its primary text or explicitly restricted. A judge who opens any cited paper will find what we said it says.
