# Functional Spec — Chhanni Demo (Round 2)

## Goal
A working, testable demo of the two-beat flow from the Round 1 concept note (see decisions.md D7), for a live run plus an optional 2-minute video and a 10-slide deck.

## In scope (must work)

1. **Credit pricing engine** — given a contamination band (clean/mixed/heavily-contaminated) and a lot weight, compute a credit value. Changing the band changes the value, visibly, in the dashboard.
2. **Contamination banding** — a lightweight classifier (rule-based placeholder or a simple trained model on a public dataset — time-box this choice) that outputs one of the 3 bands. If the input is mocked rather than a real trained model, label that clearly in the UI and in the demo narration.
3. **Statistical audit engine (Python)**
   - Benford's Law test: leading-digit distribution, chi-square and normalised Euclidean distance D*, over a set of ward tonnage entries.
   - Socio-economic baseline model: predicted expected generation per ward from population/income/sanitation proxies; flags entries diverging from the predicted range.
   - Both checks run against the synthetic ward dataset (decisions.md D5) with injected tampering, and correctly flag the tampered entries in a reproducible test run.
4. **Escrow state machine** — credit states (`pending` → `matured` → `tradable`, or → `clawed_back` on a failed audit) persisted in Postgres, with a visible state transition in the dashboard when a flagged lot is held.
5. **Ward integrity dashboard** — a view ranking wards by audit confidence score, flaggable for inspection.
6. **Earn-as-you-Sort stub** — a mocked payment trigger (log/display only, no real money) firing when a lot is scanned and scored.

## Out of scope — do not build, and say so plainly if asked

- Real blockchain / smart contract settlement
- Real UPI integration, KYC, or payment rails
- QR-tagged physical custody trail
- IoT weighbridge integration
- CPCB portal linkage
- Fine-grained polymer-level purity classification
- Live MCD data access — all data is synthetic or calibrated to public benchmarks

## Success criteria for the demo

- The two beats in decisions.md D7 run live and repeatably, with no manual data fudging mid-demo.
- Every number shown traces to `docs/data-sources.md` (marked Verified) or is clearly labeled synthetic.
- The audit engine's detection result (tampered vs clean) is reproducible by running a script — not a one-off result eyeballed once and never rerun.
