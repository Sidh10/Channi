// The Earn-as-you-Sort table can only ever hold simulated sandbox-stub payouts (decisions.md D4, D20).
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { pool } from '../src/db.js';
import { recordStubPayout, STUB_RATE_INR_PER_CREDIT } from '../src/payouts.js';

after(() => pool.end());

async function withLot(fn) {
  const c = await pool.connect();
  try {
    await c.query('BEGIN');
    const w = await c.query(`INSERT INTO wards (ward_code, name) VALUES ('TEST-PW', 'x') RETURNING id`);
    const l = await c.query(
      `INSERT INTO lots (lot_code, ward_id, weight_kg, band, band_source, contamination_pct)
       VALUES ('TEST-PL', $1, 10, 'mixed', 'mock_rule_based', 20) RETURNING id`,
      [w.rows[0].id]
    );
    return await fn(c, l.rows[0].id);
  } finally {
    await c.query('ROLLBACK');
    c.release();
  }
}

test('a stub payout is credits x the placeholder rate, simulated on the sandbox stub rail', () =>
  withLot(async (c, lotId) => {
    const p = await recordStubPayout(c, { lotId, credits: 5 });
    assert.equal(p.amount_inr, Number((5 * STUB_RATE_INR_PER_CREDIT).toFixed(2)));
    assert.equal(p.rail, 'upi_sandbox_stub');
    assert.equal(p.status, 'simulated');
    assert.equal(p.collector_ref, 'SYN-COLLECTOR-01');
  }));

test('the database refuses anything that looks like a real payment', async () => {
  for (const [col, value, constraint] of [
    ['rail', 'upi_live', /stub_payouts_rail_check/],
    ['status', 'paid', /stub_payouts_status_check/],
  ]) {
    await withLot(async (c, lotId) => {
      await assert.rejects(
        c.query(
          `INSERT INTO stub_payouts (lot_id, collector_ref, credits, rate_inr_per_credit, amount_inr, ${col})
           VALUES ($1, 'x', 1, 1, 1, $2)`,
          [lotId, value]
        ),
        constraint
      );
    });
  }
});
