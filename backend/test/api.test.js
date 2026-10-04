// End-to-end through the HTTP API and the real Python engine (decisions.md D7, D17, D18), on a
// small SYNTHETIC dataset in the test database. Steps share state and run in order.
// The dataset overrides the city total, so it is NOT calibrated to the CRDT figure — fine for a test.
import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from '../src/app.js';
import { pool } from '../src/db.js';
import { seedSynthetic } from '../src/syntheticData.js';

const PARAMS = { n_wards: 8, n_days: 28, city_daily_total_t: 1000 };
const SEED = 11;
const SCAN = '2026-07-10T10:00:00+05:30'; // inside the synthetic window 2026-07-06..08-02
const AFTER_HOLD = '2026-09-01T00:00:00+05:30';

let server;
let base;
const call = async (method, path, body) => {
  const res = await fetch(base + path, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
};

before(async () => {
  await seedSynthetic(pool, { seed: SEED, params: PARAMS, fresh: true });
  server = createApp().listen(0);
  base = `http://localhost:${server.address().port}`;
});

after(async () => {
  server?.close();
  // Later test files assert on every credit that moves, so leave the test DB empty.
  await pool.query(
    `TRUNCATE stub_payouts, credit_events, credits, lots, audit_flags, ward_audit_scores, audit_runs,
              ward_tonnage_logs, wards RESTART IDENTITY CASCADE`
  );
  await pool.end();
});

describe('demo flow through the API', () => {
  const state = {};

  it('reports the engine version and labels every placeholder', async () => {
    const engine = await call('GET', '/api/engine');
    assert.equal(engine.status, 200);
    assert.ok(engine.body.engine_version);
    const meta = (await call('GET', '/api/meta')).body;
    assert.match(meta.dataNotice, /SYNTHETIC/);
    assert.match(meta.placeholders.bandingSource, /not a trained model/);
    assert.match(meta.placeholders.qualityFactors.label, /placeholder/);
    assert.match(meta.placeholders.bandCutoffsPct.label, /placeholder/);
    assert.match(meta.placeholders.escrowHoldDays.label, /placeholder/);
    assert.match(meta.placeholders.creditUnit.label, /placeholder/);
    assert.deepEqual(meta.logWindow, { start: '2026-07-06', end: '2026-08-02' });
    assert.match(meta.placeholders.stubPayoutRate.label, /no money moves/);
  });

  it('beat 1: the quote changes as only the contamination band changes', async () => {
    const clean = (await call('POST', '/api/quote', { weightKg: 100, contaminationPct: 5 })).body;
    const mixed = (await call('POST', '/api/quote', { weightKg: 100, contaminationPct: 20 })).body;
    const heavy = (await call('POST', '/api/quote', { weightKg: 100, contaminationPct: 45 })).body;
    assert.deepEqual(
      [clean, mixed, heavy].map((q) => [q.classification.band, q.price.creditValue]),
      [['clean', 100], ['mixed', 50], ['heavily_contaminated', 10]]
    );
    assert.equal(clean.classification.source, 'mock_rule_based');
    assert.equal((await call('POST', '/api/quote', { weightKg: 100, contaminationPct: 140 })).status, 400);
  });

  it('lists the seeded synthetic wards', async () => {
    const { body } = await call('GET', '/api/wards');
    assert.equal(body.wards.length, PARAMS.n_wards);
    assert.ok(body.wards.every((w) => w.data_origin === 'synthetic'));
    state.target = body.wards[2].ward_code;
    state.other = body.wards[5].ward_code;
  });

  it('scans lots into pending credits, storing the values it quoted', async () => {
    const a = await call('POST', '/api/lots', { wardCode: state.target, weightKg: 120, contaminationPct: 9.999, scannedAt: SCAN });
    const b = await call('POST', '/api/lots', { wardCode: state.other, weightKg: 80, contaminationPct: 4, scannedAt: SCAN });
    assert.equal(a.status, 201);
    assert.equal(a.body.lot.contamination_pct, 10); // 9.999 is stored (and banded) as 10.00
    assert.equal(a.body.lot.band, 'mixed');
    assert.equal(a.body.credit.credit_value, 60);
    assert.equal(a.body.credit.state, 'pending');
    // Earn-as-you-Sort stub fires with the scan: simulated only, at the placeholder rate.
    assert.deepEqual(
      [a.body.payout.amount_inr, a.body.payout.rail, a.body.payout.status, a.body.payout.collector_ref],
      [60, 'upi_sandbox_stub', 'simulated', 'SYN-COLLECTOR-01']
    );
    assert.match(a.body.payout.label, /no money moves/);
    state.creditA = a.body.credit.id;
    // Weight is stored as quoted (2 dp, JS rounding), not re-rounded by Postgres (which would give 1.01).
    const w = await call('POST', '/api/lots', { wardCode: state.other, weightKg: 1.005, contaminationPct: 20, scannedAt: SCAN });
    assert.equal(w.body.lot.weight_kg, 1);
    assert.equal(w.body.credit.credit_value, 0.5);
    state.creditW = w.body.credit.id;
    state.creditB = b.body.credit.id;
    assert.equal((await call('POST', '/api/lots', { wardCode: 'NOPE', weightKg: 1, contaminationPct: 1 })).status, 400);
  });

  it('a clean audit scores every ward and flags none', async () => {
    const { status, body } = await call('POST', '/api/audit/run', {});
    assert.equal(status, 201);
    assert.equal(body.wardsScored, PARAMS.n_wards);
    assert.deepEqual(body.flaggedWards, []);
    assert.match(body.dataNotice, /SYNTHETIC/);
  });

  it('beat 2: injected rounded entries get the target ward flagged and its credit held', async () => {
    const inj = await call('POST', '/api/demo/inject', { wardCode: state.target, kind: 'round', placement: 'spread', seed: SEED });
    assert.equal(inj.status, 201);
    assert.ok(inj.body.inserted > 0);

    const { body } = await call('POST', '/api/audit/run', {});
    assert.deepEqual(body.flaggedWards, [state.target]);
    assert.ok(body.flagIds.length >= 1);
    state.flagIds = body.flagIds;

    const credits = (await call('GET', '/api/credits')).body.credits;
    const a = credits.find((c) => c.id === state.creditA);
    const b = credits.find((c) => c.id === state.creditB);
    assert.equal(a.held, true);
    assert.deepEqual(a.holding_flag_ids, state.flagIds);
    assert.equal(b.held, false);
  });

  it('the ward integrity view ranks the flagged ward first', async () => {
    const { body } = await call('GET', '/api/audit/latest');
    assert.equal(body.wards[0].ward_code, state.target);
    assert.ok(body.wards[0].flags.every((f) => f.status === 'open'));
    const roundFlag = body.wards[0].flags.find((f) => f.check_type === 'roundness');
    assert.ok(roundFlag, 'rounded injection should raise a roundness flag');
    assert.equal(roundFlag.hints.level, 'entry');
    assert.ok(roundFlag.hints.n > 0);
    assert.equal(body.wards.length, PARAMS.n_wards);
  });

  it('time passes: the clean ward\'s credit becomes tradable, the held one stays pending', async () => {
    const { body } = await call('POST', '/api/escrow/cycle', { now: AFTER_HOLD });
    assert.deepEqual(body.matured.sort(), [state.creditB, state.creditW].sort());
    assert.deepEqual(body.released.sort(), [state.creditB, state.creditW].sort());
    const a = (await call('GET', '/api/credits')).body.credits.find((c) => c.id === state.creditA);
    assert.equal(a.state, 'pending');
  });

  it('inspector confirms: the held credit is clawed back, citing the flag', async () => {
    const [first, ...rest] = state.flagIds;
    assert.equal((await call('POST', `/api/flags/${first}/inspect`)).body.status, 'under_inspection');
    const confirm = await call('POST', `/api/flags/${first}/confirm`);
    assert.deepEqual(confirm.body.clawedBack, [state.creditA]);
    for (const id of rest) await call('POST', `/api/flags/${id}/confirm`);
    assert.equal((await call('POST', `/api/flags/${first}/confirm`)).status, 409);
    assert.equal((await call('POST', '/api/flags/999999/confirm')).status, 404);

    const events = (await call('GET', `/api/credits/${state.creditA}/events`)).body.events;
    assert.deepEqual(events.map((e) => e.to_state), ['pending', 'clawed_back']);
    assert.equal(events.at(-1).audit_flag_id, first);
  });

  it('the audit never sees the ground-truth label: flipping it changes nothing', async () => {
    const before = (await call('GET', '/api/audit/latest')).body.wards
      .map((w) => [w.ward_code, w.confidence_score, w.flagged, JSON.stringify(w.metrics)]).sort();
    await pool.query('UPDATE ward_tonnage_logs SET is_injected_tamper = NOT is_injected_tamper');
    await call('POST', '/api/audit/run', {});
    const afterFlip = (await call('GET', '/api/audit/latest')).body.wards
      .map((w) => [w.ward_code, w.confidence_score, w.flagged, JSON.stringify(w.metrics)]).sort();
    assert.deepEqual(afterFlip, before);
  });
});

describe('demo reset', () => {
  it('truncates and reloads the clean seed-2026 synthetic dataset', async () => {
    const { status, body } = await call('POST', '/api/demo/reset');
    assert.equal(status, 200);
    assert.deepEqual([body.wards, body.logs, body.seed], [40, 147206, 2026]);
    assert.match(body.dataNotice, /SYNTHETIC/);
    assert.deepEqual((await call('GET', '/api/credits')).body.credits, []);
    assert.equal((await call('GET', '/api/audit/latest')).status, 404);
  });
});

describe('bad input gets a 4xx with a JSON error, never a 500', () => {
  const cases = [
    ['GET', '/api/credits/abc/events', undefined, 400],
    ['GET', '/api/credits/1.5/events', undefined, 400],
    ['GET', '/api/credits/99999999999999999999/events', undefined, 400],
    ['GET', '/api/credits/2147483647/events', undefined, 404],
    ['POST', '/api/flags/99999999999/clear', undefined, 400],
    ['POST', '/api/flags/0/confirm', undefined, 400],
    ['POST', '/api/audit/run', { periodStart: 'garbage' }, 400],
    ['POST', '/api/audit/run', { periodStart: 12345 }, 400],
    ['POST', '/api/audit/run', { periodStart: '2026-02-30' }, 400],
    ['POST', '/api/audit/run', { periodStart: '2026-07-20T00:00:00Z' }, 400],
    ['POST', '/api/audit/run', { periodStart: '2026-08-02', periodEnd: '2026-07-06' }, 400],
    ['POST', '/api/audit/run', { periodStart: '2030-01-01' }, 400],
    ['POST', '/api/audit/run', { periodStart: '0000-01-01' }, 400],
    ['POST', '/api/lots', { wardCode: 'SYN-W01\u0000', weightKg: 10, contaminationPct: 5 }, 400],
    ['POST', '/api/demo/inject', { wardCode: 'SYN-W01\u0000', kind: 'round' }, 400],
    ['POST', '/api/lots', { wardCode: 'SYN-W01', weightKg: 1e9, contaminationPct: 5 }, 400],
    ['POST', '/api/quote', { weightKg: 1e9, contaminationPct: 5 }, 400],
    ['POST', '/api/lots', { wardCode: 'SYN-W01', weightKg: 10, contaminationPct: 5, scannedAt: '275000-01-01' }, 400],
    ['POST', '/api/escrow/cycle', { now: 'not a time' }, 400],
    ['POST', '/api/demo/inject', { wardCode: 'SYN-W01', kind: 'round', seed: 'abc' }, 400],
    ['POST', '/api/demo/inject', { wardCode: 'SYN-W01', kind: 'round', seed: -1 }, 400],
    ['POST', '/api/demo/inject', { wardCode: 'SYN-W01', kind: 'inflate' }, 400],
    ['GET', '/api/no-such-route', undefined, 404],
  ];
  for (const [method, path, body, expected] of cases) {
    it(`${method} ${path} ${body ? JSON.stringify(body) : ''} -> ${expected}`, async () => {
      const r = await call(method, path, body);
      assert.equal(r.status, expected, JSON.stringify(r.body));
      assert.equal(typeof r.body.error, 'string');
    });
  }

  it('malformed and oversized JSON bodies get 400 and 413', async () => {
    const bad = await fetch(`${base}/api/quote`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"weightKg":' });
    assert.equal(bad.status, 400);
    const big = await fetch(`${base}/api/quote`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ pad: 'x'.repeat(1_100_000) }),
    });
    assert.equal(big.status, 413);
  });
});
