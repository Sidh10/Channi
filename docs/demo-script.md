# Demo run sheet — Chhanni (WasteChakra 2026, Round 2)

The fixed target is the two-beat demo in decisions.md D7. Every number said aloud below either comes off the screen (synthetic or placeholder, and labelled there) or traces to a **Verified** row in `docs/data-sources.md`. If you're unsure about a claim, leave it out.

---

## 1. Setup (about 2 minutes, before judges arrive)

Use four terminals, each from the repo root.

| # | Command | Leave running? |
|---|---|---|
| 1 | `cd backend && npm run db:start` | yes: Postgres 17 on :5432 (decisions.md D11; Docker is not used) |
| 2 | `cd backend && npm start` | yes: API on :4000. Use `start`, not `dev`: `dev` restarts on every file save |
| 3 | `cd frontend && npm run dev` | yes: dashboard on http://localhost:5173 |
| 4 | `cd backend && npm run demo:rehearse` | no: runs both beats through an in-process copy of the API on the demo database, asserting every outcome, then resets the data. It must print `REHEARSAL PASSED`. It doesn't check that terminal 2 is up; the dashboard does |

Then, in the browser:
1. Open http://localhost:5173. The window must be at least ~1100 px wide so both columns show side by side.
2. Click **Reset demo** and accept the browser's confirmation dialog.
3. Check the screen:
   - the clock reads **Sun, 2 Aug, 2026, 10:00 am IST**;
   - the scanner shows SYN-W27, 250 kg, 5%;
   - the ward panel says "No audit run yet";
   - the ledger is empty.

If the rehearsal fails, don't present the live flow; use the fallback in section 5.

---

## 2. The demo (about 3 minutes)

Each line is the action, then what to say. Say the bold sentences word for word: they are the honesty statements the decisions log requires.

### Opening (10 s)
Point at the dark banner.
> "Everything you'll see runs on **synthetic ward data**. We don't have MCD's ward logs. The synthetic city is scaled to one real figure: 11,862 tonnes per day of waste generated, as reported in CRDT/IIT Delhi's 2026 MCD solid-waste proposal. Nothing else is real data."

### Beat 1: quality-weighted credits (45 s)
1. In **① Scan a lot**, ward SYN-W27 and 250 kg are preselected.
   > "A lot arrives at a collection point. **The scan is simulated: a rule-based placeholder, not a trained model**, and it only sorts lots into three coarse bands: clean, mixed, heavily contaminated. We deliberately don't claim polymer-level purity."
2. Drag the slider slowly from 5% to 20%, then to 45%. The value goes 250 → 125 → 25.
   > "Same weight, different quality, different credit. The credit is weight times a quality factor. **The factors and the band cut-offs are placeholders we chose, not market prices or a standard.** The screen labels them."
3. Set the slider to **12%** (Mixed) and click **Scan lot into escrow**.
4. Switch the ward to **SYN-W01**, set **80 kg** and **4%**, and scan again.
   > "Each scan issues a credit into escrow. Earn-as-you-Sort fires a payout to the collector. **That payout is a sandbox stub: no money moves, and the rate is a placeholder.**"

### Beat 2: the audit catches tampered data before the credit can trade (90 s)
1. Click **Run audit** (takes about 3–7 s).
   > "Every ward's 28 days of trip logs go through four statistical checks: Benford's first-digit test, a round-number test, a socio-economic baseline, and a single-day spike test."

   **On screen:** "0 of 40 wards flagged".
2. In **Demo tampering**, keep **SYN-W27 · Rounded phantom entries · spread** and click **Inject into log**.
   > "Now someone pads SYN-W27's log with invented, rounded entries: 144 of them, 1.5% of the ward's entries. The audit is never told which rows were added."
3. Click **Run audit** again.

   **On screen:**
   - "1 of 40 wards flagged";
   - SYN-W27 ranked first, round-number excess, q ≈ 5 × 10⁻⁴⁶;
   - the ledger shows SYN-W27's credit as **Held · flag #1**.

   > "Only the tampered ward is flagged. The q value is evidence strength after correcting for running 160 tests (40 wards × 4 checks); **it's not a probability**. Its credit is now **held**: it can't mature or trade while the flag is open."
4. In the ledger, click **+30 days**.

   **On screen:**
   - the clock reads **Tue, 1 Sept, 2026**;
   - SYN-W01 is **Tradable**;
   - SYN-W27 still shows **Pending · in escrow** plus a **Held · flag #1** badge.
   - A light-orange note appears in the scanner saying the clock is past the end of the synthetic log. That is expected; ignore it.

   > "**The clock is simulated**, so we can jump past the 28-day escrow hold, which is also a placeholder. The clean ward's credit is released to tradable. The flagged ward's credit is still held."
5. On SYN-W27's flag, click **Start inspection**, then **Confirm → claw back credits**.

   **On screen:**
   - SYN-W27's credit shows **Clawed back** with flag #1 beside it;
   - its **History** shows pending → held → clawed back.

   > "A statistical flag only holds the credit. **An inspector confirms before anything is clawed back**, so one false alarm can't destroy a legitimate credit. That's the escrow: hold, then mature, then settle, or claw back."

### Close (20 s), both sentences required
> "So the audit layer checks the ward's reported tonnage that each credit sits on, before the credit can trade. A scanner can't do that, because a scanner never sees the ward's log."

> "**At the sizes we tested, it catches rounded entries and large-scale fabrication. It does not reliably catch moderate over-reporting**, say 10 to 50% inflation. That's our next step: comparing each ward with its own history."

**Reset after the demo:** click **Reset demo**, accept the dialog, and check the scanner is back to SYN-W27 / 250 kg / 5%.

---

## 3. Numbers you may say, and where they come from

| Claim | Source |
|---|---|
| 11,862 TPD generated (CRDT/IIT Delhi 2026 MCD solid-waste proposal) | data-sources.md row 1 (CRDT/IIT Delhi 2026), **Verified** |
| 250 → 125 → 25 credits; 144 injected rows (1.5%); "1 of 40 flagged"; q ≈ 5 × 10⁻⁴⁶ | On screen; synthetic seed 2026, deterministic (`npm run demo:rehearse`) |
| Rounded entries caught at ≥ 1.5% of a ward's entries; fabricated at ≥ 30% spread or ≥ 5% on one day; moderate over-reporting not reliably caught | decisions.md D14 (synthetic, held-out seeds) |
| 1 falsely flagged ward in 1,200 clean ward audits | decisions.md D13 (synthetic) |
| CPCB state-wise figures show no Benford red flags | data-sources.md "Own result" row, **Verified**. Only with its caveat: "a weak test at 36 states, and not a test of ward logs" |

**Do not say:**
- any accuracy figure for our banding;
- any per-capita generation figure;
- "tamper-proof" (decisions.md D9: it's an integrity guard, not a security boundary);
- "real-time MCD data";
- the CPCB Delhi composition percentages (e.g. "51.5% inerts, 31.78% compostable"). They're still **Unverified** in data-sources.md, and their cited CPCB source can't be traced;
- the old Thielmann wording ">87% on TrashNet/TACO → 24%". It's wrong (decisions.md D21). Use only the corrected pairs below;
- "Benford's Law is proven to detect fraud". Zahran et al. (2014) validated it only *indirectly*, on US EPA emissions self-reports, not against confirmed fraud;
- that Thomaz et al. (2023) predicts waste *generation*. It predicts composition and specific weight, in one city;
- Alamsyah & Ramdhani's "15–20%" oracle overhead as a measured result. The paper says it "may" add that.

---

## 4. Likely judge questions: honest answers

- **"Is this real data?"** No. The ward data is synthetic and scaled to the verified 11,862 TPD total from the CRDT/IIT Delhi MCD proposal. We also ran the Benford check on CPCB's published state-wise figures, and found no red flags. That test is weak (36 states) and isn't evidence about ward logs.
- **"Why would real trip weights follow Benford's Law?"** That's an assumption we can't test without real trip logs. Our synthetic loads follow it by construction. If real loads only roughly follow it, the plain Benford test would false-alarm, so for real data we'd switch to comparing each ward with its peers (D12).
- **"What doesn't it catch?"** Moderate inflation (10–50%), and fabricated entries spread over the month unless they're roughly 30% of a ward's entries. Rounding is caught from about 1.5% of a ward's entries. All of this is from synthetic tests (D14).
- **"How does a problem in the ward's log affect this lot's credit?"** The audit checks the ward's trip log, not the lot itself. A credit is held when a flag covers its ward and scan date. The lot may be fine, but the reported tonnage it rests on is in question, so it waits for inspection (D17).
- **"What does the scanner actually do?"** In this demo, nothing visual: the operator sets a contamination estimate and a transparent rule maps it to a band (D15).
- **"Why only three bands? Isn't computer vision good at this now?"** On industrial conveyor data it's much weaker than on lab datasets. A 2025 IEEE Access review (Thielmann et al.) reports Mask R-CNN at 87.2% mAP on a lab dataset (ReSORT-IT) versus 24.3% on industrial ZeroWaste data. In segmentation, DeepLabV3 scores 90.9% mIoU on TACO litter photos versus DeepLabV3+ at 52.1% on ZeroWaste. Newer detectors reach about 52 mAP on ZeroWaste (Abid et al., BMVC 2025): better, but still far from lab scores. So we only claim coarse bands (D2, D21).
- **"Has anyone used Benford's Law like this before?"** Yes, to check the accuracy of self-reported US EPA Toxics Release Inventory lead emissions (Zahran et al. 2014). The authors validated it indirectly, by comparing emission categories where firms have more or less reporting discretion, not against confirmed fraud. So it's precedent, not proof that it catches fraud.
- **"Why not blockchain?"** The mechanism (hold, mature, settle or claw back) is the point. It's a Postgres state machine whose rules are enforced in the database (D3, D9). Blockchain settlement stays on the roadmap.
- **"Is the payout real?"** No, it's a sandbox stub, and the database only accepts simulated stub payouts (D4, D20). It also isn't reversed when a credit is clawed back: reversals belong to a real payment rail, which is out of scope for this build.

---

## 5. If something goes wrong on stage

| Symptom | Do this |
|---|---|
| Red banner "Backend unreachable" | Terminal 2 has stopped. Restart it with `npm start`, then reload the page |
| Run audit hangs for more than 15 s | Talk through the four checks; the full audit normally takes 3–7 s. If it errors, check terminal 2 |
| A clean audit flags a ward | It shouldn't: seed 2026 is deterministic and was verified clean. Click **Reset demo** and restart from beat 1 |
| Clock isn't 2 Aug at the start | Click **Reset demo** (accept the dialog) |
| Nothing works | Show the `npm run demo:rehearse` output in a terminal: it runs both beats through the API code on the demo database and asserts each step's outcome |
