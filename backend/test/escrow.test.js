// Escrow service behaviour (decisions.md D3, D9, D17). Each test runs in a rolled-back transaction.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { pool } from '../src/db.js';
import {
  issueCredit, matureDueCredits, releaseAuditedCredits, runEscrowCycle,
  startInspection, clearFlag, confirmFlag, listCredits, ESCROW_HOLD_DAYS,
} from '../src/escrow.js';

after(() => pool.end());

const DAY = 24 * 60 * 60 * 1000;
// Scans at 10:00 IST on 2026-07-10, inside the audit window used below.
const SCAN = new Date('2026-07-10T04:30:00Z');
const AFTER_HOLD = new Date(SCAN.getTime() + (ESCROW_HOLD_DAYS + 1) * DAY);
const BEFORE_HOLD = new Date(SCAN.getTime() + (ESCROW_HOLD_DAYS - 1) * DAY);
const PERIOD = { start: '2026-07-06', end: '2026-08-02' };

async function withTx(fn) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    return await fn(client);
  } finally {
    await client.query('ROLLBACK');
    client.release();
  }
}

let seq = 0;
async function ward(c) {
  const { rows } = await c.query(
    `INSERT INTO wards (ward_code, name) VALUES ($1, 'test ward (synthetic)') RETURNING id`,
    [`TEST-EW${++seq}`]
  );
  return rows[0].id;
}

async function lotWithCredit(c, wardId, { band = 'clean', weightKg = 100, scannedAt = SCAN } = {}) {
  const pct = { clean: 5, mixed: 20, heavily_contaminated: 50 }[band];
  const { rows } = await c.query(
    `INSERT INTO lots (lot_code, ward_id, weight_kg, band, band_source, contamination_pct, scanned_at)
     VALUES ($1, $2, $3, $4, 'mock_rule_based', $5, $6) RETURNING id`,
    [`TEST-EL${++seq}`, wardId, weightKg, band, pct, scannedAt]
  );
  return issueCredit(c, rows[0].id);
}

async function auditRun(c, { finished = true, period = PERIOD } = {}) {
  const { rows } = await c.query(
    `INSERT INTO audit_runs (engine_version, period_start, period_end, finished_at)
     VALUES ('test', $1, $2, CASE WHEN $3 THEN now() END) RETURNING id`,
    [period.start, period.end, finished]
  );
  return rows[0].id;
}

async function score(c, runId, wardId, flagged = false) {
  await c.query(
    `INSERT INTO ward_audit_scores (audit_run_id, ward_id, confidence_score, flagged) VALUES ($1, $2, $3, $4)`,
    [runId, wardId, flagged ? 0.0001 : 0.9, flagged]
  );
}

async function flag(c, runId, wardId, period = PERIOD) {
  const { rows } = await c.query(
    `INSERT INTO audit_flags (audit_run_id, ward_id, check_type, period_start, period_end)
     VALUES ($1, $2, 'roundness', $3, $4) RETURNING id`,
    [runId, wardId, period.start, period.end]
  );
  return rows[0].id;
}

const stateOf = async (c, creditId) =>
  (await c.query(`SELECT state, clawback_flag_id FROM credits WHERE id = $1`, [creditId])).rows[0];

test('issueCredit prices the lot and opens a pending escrow hold', () =>
  withTx(async (c) => {
    const credit = await lotWithCredit(c, await ward(c), { band: 'mixed', weightKg: 80 });
    assert.equal(credit.state, 'pending');
    assert.equal(Number(credit.credit_value), 40);
    assert.equal(credit.pricing_version, 'placeholder-v1');
    assert.equal(credit.matures_at.getTime(), SCAN.getTime() + ESCROW_HOLD_DAYS * DAY);
  }));

test('a credit matures only after the hold elapses', () =>
  withTx(async (c) => {
    const credit = await lotWithCredit(c, await ward(c));
    assert.deepEqual(await matureDueCredits(c, BEFORE_HOLD), []);
    assert.deepEqual(await matureDueCredits(c, AFTER_HOLD), [credit.id]);
    assert.equal((await stateOf(c, credit.id)).state, 'matured');
  }));

test('a matured credit becomes tradable only once a finished audit covering its lot scored its ward', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const credit = await lotWithCredit(c, w);
    await matureDueCredits(c, AFTER_HOLD);

    assert.deepEqual(await releaseAuditedCredits(c), [], 'no audit yet');

    const unfinished = await auditRun(c, { finished: false });
    await score(c, unfinished, w);
    assert.deepEqual(await releaseAuditedCredits(c), [], 'audit not finished');

    const otherPeriod = await auditRun(c, { period: { start: '2026-08-03', end: '2026-08-30' } });
    await score(c, otherPeriod, w);
    assert.deepEqual(await releaseAuditedCredits(c), [], 'audit does not cover the scan date');

    const run = await auditRun(c);
    await score(c, run, w);
    assert.deepEqual(await releaseAuditedCredits(c), [credit.id]);
    const row = (await c.query(`SELECT state, state_reason FROM credits WHERE id = $1`, [credit.id])).rows[0];
    assert.equal(row.state, 'tradable');
    assert.equal(row.state_reason, `cleared by audit run ${run}`);
  }));

test('an open flag holds credits in its ward and period: no maturing, no release', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const held = await lotWithCredit(c, w);
    const outsidePeriod = await lotWithCredit(c, w, { scannedAt: new Date('2026-08-10T04:30:00Z') });
    const otherWard = await lotWithCredit(c, await ward(c));
    const run = await auditRun(c);
    const flagId = await flag(c, run, w);

    const matured = await matureDueCredits(c, new Date('2026-09-30T00:00:00Z'));
    assert.ok(!matured.includes(held.id), 'held credit must not mature');
    assert.ok(matured.includes(outsidePeriod.id) && matured.includes(otherWard.id));

    const view = Object.fromEntries((await listCredits(c)).map((r) => [r.id, r]));
    assert.equal(view[held.id].held, true);
    assert.deepEqual(view[held.id].holding_flag_ids, [flagId]);
    assert.equal(view[outsidePeriod.id].held, false);
    assert.equal(view[otherWard.id].held, false);
  }));

test('a hold placed after maturing blocks release to tradable', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const credit = await lotWithCredit(c, w);
    await matureDueCredits(c, AFTER_HOLD);
    const run = await auditRun(c);
    await score(c, run, w, true);
    await flag(c, run, w);
    assert.deepEqual(await releaseAuditedCredits(c), []);
    assert.equal((await stateOf(c, credit.id)).state, 'matured');
  }));

test('confirming a flag claws back pending and matured credits it covers, citing the flag', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const pending = await lotWithCredit(c, w);
    const matured = await lotWithCredit(c, w, { scannedAt: new Date('2026-07-01T04:30:00Z') });
    const outside = await lotWithCredit(c, w, { scannedAt: new Date('2026-08-10T04:30:00Z') });
    const otherWard = await lotWithCredit(c, await ward(c));
    await matureDueCredits(c, new Date('2026-08-01T00:00:00Z')); // matures only the 07-01 lot

    const run = await auditRun(c, { period: { start: '2026-06-29', end: '2026-08-02' } });
    const flagId = await flag(c, run, w, { start: '2026-06-29', end: '2026-08-02' });
    const { clawedBack } = await confirmFlag(c, flagId);

    assert.deepEqual(clawedBack.sort(), [pending.id, matured.id].sort());
    for (const id of [pending.id, matured.id]) {
      assert.deepEqual(await stateOf(c, id), { state: 'clawed_back', clawback_flag_id: flagId });
      const ev = (await c.query(
        `SELECT from_state, to_state, audit_flag_id FROM credit_events WHERE credit_id = $1 ORDER BY id DESC LIMIT 1`, [id]
      )).rows[0];
      assert.equal(ev.to_state, 'clawed_back');
      assert.equal(ev.audit_flag_id, flagId);
    }
    assert.equal((await stateOf(c, outside.id)).state, 'pending');
    assert.equal((await stateOf(c, otherWard.id)).state, 'pending');
  }));

test('confirming a flag leaves already-tradable credits alone (tradable is terminal, D9)', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const credit = await lotWithCredit(c, w);
    await matureDueCredits(c, AFTER_HOLD);
    const run = await auditRun(c);
    await score(c, run, w);
    await releaseAuditedCredits(c);
    const later = await auditRun(c);
    const flagId = await flag(c, later, w);
    assert.deepEqual((await confirmFlag(c, flagId)).clawedBack, []);
    assert.equal((await stateOf(c, credit.id)).state, 'tradable');
  }));

test('clearing a flag lifts the hold, unless another open flag still covers the lot', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const credit = await lotWithCredit(c, w);
    const run = await auditRun(c);
    await score(c, run, w, true);
    const f1 = await flag(c, run, w);
    const f2 = await flag(c, run, w);

    await startInspection(c, f1);
    await clearFlag(c, f1);
    assert.deepEqual(await matureDueCredits(c, AFTER_HOLD), [], 'f2 still holds it');

    await clearFlag(c, f2);
    const { matured, released } = await runEscrowCycle(c, AFTER_HOLD);
    assert.deepEqual(matured, [credit.id]);
    assert.deepEqual(released, [credit.id], 'the flagged run still counts as an audit once its flags are cleared');
  }));

test('flag status moves only forward', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const run = await auditRun(c);
    const f = await flag(c, run, w);
    await clearFlag(c, f);
    await assert.rejects(confirmFlag(c, f), /is cleared; cannot move it to confirmed/);
    await assert.rejects(startInspection(c, f), /is cleared; cannot move it to under_inspection/);
    await assert.rejects(confirmFlag(c, 999999), /not found/);
  }));

test('demo beat 2 end to end: tampered ward held then clawed back, clean ward released', () =>
  withTx(async (c) => {
    const tampered = await ward(c);
    const clean = await ward(c);
    const a = await lotWithCredit(c, tampered, { band: 'clean', weightKg: 120 });
    const b = await lotWithCredit(c, clean, { band: 'mixed', weightKg: 90 });

    const run = await auditRun(c);
    await score(c, run, tampered, true);
    await score(c, run, clean, false);
    const flagId = await flag(c, run, tampered);

    const cycle = await runEscrowCycle(c, AFTER_HOLD);
    assert.deepEqual(cycle.matured, [b.id]);
    assert.deepEqual(cycle.released, [b.id]);
    assert.equal((await stateOf(c, a.id)).state, 'pending', 'tampered ward credit is held in escrow');

    await startInspection(c, flagId);
    await confirmFlag(c, flagId);
    assert.deepEqual(await stateOf(c, a.id), { state: 'clawed_back', clawback_flag_id: flagId });
    assert.equal((await stateOf(c, b.id)).state, 'tradable');
  }));

test('confirmFlag is one atomic statement: no escrow cycle can slip between confirm and clawback', () =>
  withTx(async (c) => {
    const w = await ward(c);
    await lotWithCredit(c, w);
    const flagId = await flag(c, await auditRun(c), w);
    let writes = 0;
    const counting = { query: (...args) => { writes += 1; return c.query(...args); } };
    const { clawedBack } = await confirmFlag(counting, flagId);
    assert.equal(clawedBack.length, 1);
    assert.equal(writes, 1, 'confirm + clawback must be a single SQL statement');
  }));

test('release requires the covering run to have scored this ward, not just any ward', () =>
  withTx(async (c) => {
    const audited = await ward(c);
    const notAudited = await ward(c);
    const a = await lotWithCredit(c, audited);
    const b = await lotWithCredit(c, notAudited);
    await score(c, await auditRun(c), audited);
    const { released } = await runEscrowCycle(c, AFTER_HOLD);
    assert.deepEqual(released, [a.id]);
    assert.equal((await stateOf(c, b.id)).state, 'matured');
  }));

test('an under-inspection flag still holds credits', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const credit = await lotWithCredit(c, w);
    const run = await auditRun(c);
    await score(c, run, w, true);
    const f = await flag(c, run, w);
    await startInspection(c, f);
    assert.deepEqual(await runEscrowCycle(c, AFTER_HOLD), { matured: [], released: [] });
    assert.equal((await stateOf(c, credit.id)).state, 'pending');
    const row = (await listCredits(c)).find((r) => r.id === credit.id);
    assert.equal(row.held, true, 'the read model the dashboard renders must also show the hold');
    assert.deepEqual(row.holding_flag_ids, [f]);
    const flagCreated = (await c.query('SELECT created_at FROM audit_flags WHERE id = $1', [f])).rows[0].created_at;
    assert.equal(row.held_since.getTime(), flagCreated.getTime());
  }));

test('release uses inclusive IST dates for the audit run period, whatever the session time zone', () =>
  withTx(async (c) => {
    await c.query(`SET LOCAL TIME ZONE 'UTC'`);
    const w = await ward(c);
    const at = (iso) => lotWithCredit(c, w, { scannedAt: new Date(iso) });
    const startIn = await at('2026-07-05T18:30:00Z');   // 07-06 00:00 IST
    const startOut = await at('2026-07-05T18:29:59Z');  // 07-05 23:59 IST
    const endIn = await at('2026-08-02T18:29:59Z');     // 08-02 23:59 IST
    const endOut = await at('2026-08-02T18:30:00Z');    // 08-03 00:00 IST
    await score(c, await auditRun(c), w);
    const { released } = await runEscrowCycle(c, new Date('2026-12-31T00:00:00Z'));
    assert.deepEqual(released.sort(), [startIn.id, endIn.id].sort());
    for (const cr of [startOut, endOut]) assert.equal((await stateOf(c, cr.id)).state, 'matured');
  }));

test('flag periods are inclusive IST calendar dates, whatever the session time zone', () =>
  withTx(async (c) => {
    await c.query(`SET LOCAL TIME ZONE 'UTC'`);
    const w = await ward(c);
    const at = (iso) => lotWithCredit(c, w, { scannedAt: new Date(iso) });
    const startIn = await at('2026-07-05T18:30:00Z');   // 07-06 00:00 IST — first covered instant
    const startOut = await at('2026-07-05T18:29:59Z');  // 07-05 23:59 IST
    const endIn = await at('2026-08-02T18:29:59Z');     // 08-02 23:59 IST — last covered instant
    const endOut = await at('2026-08-02T18:30:00Z');    // 08-03 00:00 IST
    const f = await flag(c, await auditRun(c), w);
    const held = Object.fromEntries((await listCredits(c)).map((r) => [r.id, r.held]));
    assert.deepEqual(
      [held[startIn.id], held[startOut.id], held[endIn.id], held[endOut.id]],
      [true, false, true, false]
    );
    const { clawedBack } = await confirmFlag(c, f);
    assert.deepEqual(clawedBack.sort(), [startIn.id, endIn.id].sort());
  }));

test('a confirmed flag cannot be cleared', () =>
  withTx(async (c) => {
    const w = await ward(c);
    const run = await auditRun(c);
    const f1 = await flag(c, run, w);
    await confirmFlag(c, f1);
    await assert.rejects(clearFlag(c, f1), /is confirmed; cannot move it to cleared/);
  }));
