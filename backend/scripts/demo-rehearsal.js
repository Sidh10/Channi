// Rehearses the two-beat demo (decisions.md D7) end to end through the same HTTP API the dashboard
// uses, on the DEMO database, asserting every outcome the narration relies on. It resets the demo
// data before each run and after the last one, so it leaves the clean seed-2026 dataset behind.
//   npm run demo:rehearse              one run
//   npm run demo:rehearse -- --runs 3  repeatability check
//   npm run demo:rehearse -- --keep    leave the final run's state in place (for inspection)
// All data is SYNTHETIC (D5); payouts are simulated stubs (D4, D20).
import assert from 'node:assert/strict';
import { createApp } from '../src/app.js';
import { pool } from '../src/db.js';

// Keep in step with frontend/src/App.jsx (DEMO_START, DEMO_WARD) and docs/demo-script.md.
const DEMO_START = '2026-08-02T10:00:00+05:30';
const AFTER_HOLD = '2026-09-01T10:00:00+05:30'; // +30 days, past the 28-day placeholder hold
const TARGET = 'SYN-W27';
const CONTRAST = 'SYN-W01';

const args = process.argv.slice(2);
const runsArg = args.indexOf('--runs');
const runs = runsArg >= 0 ? Number(args[runsArg + 1]) : 1;
if (!Number.isInteger(runs) || runs < 1) {
  console.error(`--runs must be a positive integer (got ${args[runsArg + 1]})`);
  process.exit(2);
}
const keep = args.includes('--keep');

const server = createApp().listen(0);
const base = `http://localhost:${server.address().port}`;

async function call(method, path, body) {
  const res = await fetch(base + path, {
    method,
    headers: body === undefined ? undefined : { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const json = await res.json();
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}: ${json.error}`);
  return json;
}

async function step(name, fn) {
  const t = Date.now();
  try {
    const note = await fn();
    console.log(`  PASS  ${name}${note ? ` — ${note}` : ''} (${((Date.now() - t) / 1000).toFixed(1)} s)`);
  } catch (err) {
    console.log(`  FAIL  ${name}: ${err.message}`);
    throw err;
  }
}

const creditFor = (credits, ward) => credits.find((c) => c.ward_code === ward);

async function rehearse(run) {
  console.log(`\nRun ${run}/${runs}`);
  const s = {};

  await step('reset to clean synthetic dataset', async () => {
    const r = await call('POST', '/api/demo/reset');
    assert.deepEqual([r.wards, r.logs], [40, 147206]);
    assert.match(r.dataNotice, /SYNTHETIC/);
    return `${r.wards} wards, ${r.logs} trip entries`;
  });

  await step('beat 1: credit value changes live with the contamination band', async () => {
    const q = async (pct) => (await call('POST', '/api/quote', { weightKg: 250, contaminationPct: pct }));
    const got = [];
    for (const pct of [5, 20, 45]) {
      const r = await q(pct);
      assert.equal(r.classification.source, 'mock_rule_based');
      got.push([r.classification.band, r.price.creditValue]);
    }
    assert.deepEqual(got, [['clean', 250], ['mixed', 125], ['heavily_contaminated', 25]]);
    return '250 kg: 5% → 250, 20% → 125, 45% → 25 credits';
  });

  await step('scan two lots into escrow (with simulated payouts)', async () => {
    const a = await call('POST', '/api/lots', { wardCode: TARGET, weightKg: 250, contaminationPct: 12, scannedAt: DEMO_START });
    const b = await call('POST', '/api/lots', { wardCode: CONTRAST, weightKg: 80, contaminationPct: 4, scannedAt: DEMO_START });
    assert.deepEqual([a.credit.state, a.credit.credit_value, b.credit.state, b.credit.credit_value], ['pending', 125, 'pending', 80]);
    assert.deepEqual([a.payout.status, a.payout.amount_inr, b.payout.amount_inr], ['simulated', 125, 80]);
    s.a = a.credit.id;
    s.b = b.credit.id;
    return `${TARGET} mixed 125, ${CONTRAST} clean 80`;
  });

  await step('clean audit flags no ward', async () => {
    const r = await call('POST', '/api/audit/run', {});
    assert.equal(r.wardsScored, 40);
    assert.deepEqual(r.flaggedWards, []);
    const cycle = await call('POST', '/api/escrow/cycle', { now: DEMO_START });
    assert.deepEqual(cycle, { matured: [], released: [] });
    return `window ${r.periodStart}..${r.periodEnd}`;
  });

  await step(`beat 2: inject rounded phantom entries into ${TARGET}`, async () => {
    const r = await call('POST', '/api/demo/inject', { wardCode: TARGET, kind: 'round', placement: 'spread' });
    assert.equal(r.inserted, 144);
    assert.equal(r.sizing.fraction_source, 'default');
    return `${r.inserted} rows (${(r.sizing.fraction * 100).toFixed(1)}% of ${r.sizing.target_ward_entries})`;
  });

  await step(`re-audit flags only ${TARGET} and its credit is held`, async () => {
    const r = await call('POST', '/api/audit/run', {});
    assert.deepEqual(r.flaggedWards, [TARGET]);
    const { credits } = await call('GET', '/api/credits');
    assert.equal(creditFor(credits, TARGET).held, true);
    assert.equal(creditFor(credits, CONTRAST).held, false);
    const latest = await call('GET', '/api/audit/latest');
    assert.equal(latest.wards[0].ward_code, TARGET);
    s.flags = latest.wards[0].flags.map((f) => f.id);
    assert.equal(s.flags[0], 1, 'the run sheet says "flag #1"');
    assert.equal(creditFor(credits, TARGET).holding_flag_ids[0], 1);
    const q = Math.min(...latest.wards[0].flags.map((f) => f.score));
    assert.ok(q < 1e-10, `expected overwhelming evidence, got q = ${q}`);
    return `flag(s) #${s.flags.join(', #')} (${latest.wards[0].flags.map((f) => f.check_type).join(', ')}), q = ${q.toExponential(1)}`;
  });

  await step('+30 days: clean ward credit becomes tradable, held one stays pending', async () => {
    const r = await call('POST', '/api/escrow/cycle', { now: AFTER_HOLD });
    assert.deepEqual([r.matured, r.released], [[s.b], [s.b]]);
    const { credits } = await call('GET', '/api/credits');
    assert.equal(creditFor(credits, CONTRAST).state, 'tradable');
    assert.equal(creditFor(credits, TARGET).state, 'pending');
    assert.equal(creditFor(credits, TARGET).held, true);
  });

  await step('inspector confirms: held credit is clawed back, citing the flag', async () => {
    await call('POST', `/api/flags/${s.flags[0]}/inspect`);
    const r = await call('POST', `/api/flags/${s.flags[0]}/confirm`);
    assert.deepEqual(r.clawedBack, [s.a]);
    for (const id of s.flags.slice(1)) await call('POST', `/api/flags/${id}/confirm`);
    const { events } = await call('GET', `/api/credits/${s.a}/events`);
    assert.deepEqual(events.map((e) => e.to_state), ['pending', 'clawed_back']);
    assert.equal(events.at(-1).audit_flag_id, s.flags[0]);
  });
}

let failed = false;
try {
  for (let i = 1; i <= runs; i++) await rehearse(i);
  console.log(`\nREHEARSAL PASSED (${runs} run${runs === 1 ? '' : 's'}). All data synthetic; payouts simulated.`);
} catch {
  failed = true;
  console.log('\nREHEARSAL FAILED — fix before presenting.');
} finally {
  if (!keep) {
    await call('POST', '/api/demo/reset').then(() => console.log('Demo data reset to the clean seed.')).catch((e) => {
      console.log(`Final reset failed: ${e.message}`);
      failed = true;
    });
  }
  server.close();
  await pool.end();
  process.exitCode = failed ? 1 : 0;
}
