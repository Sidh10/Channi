// Schema-level guarantees for the escrow state machine (decisions.md D3, D9).
// Every test runs inside a transaction that is rolled back, so the DB is left untouched.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { pool } from '../src/db.js';

after(() => pool.end());

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

// Minimal synthetic fixture: one ward, one lot, one pending credit.
async function seedCredit(client) {
  const ward = await client.query(
    `INSERT INTO wards (ward_code, name) VALUES ('TEST-W1', 'Test ward (synthetic)') RETURNING id`
  );
  const lot = await client.query(
    `INSERT INTO lots (lot_code, ward_id, weight_kg, band, band_source, contamination_pct)
     VALUES ('TEST-L1', $1, 10, 'clean', 'mock_rule_based', 5) RETURNING id`,
    [ward.rows[0].id]
  );
  const credit = await client.query(
    `INSERT INTO credits (lot_id, credit_value, pricing_version, state_reason)
     VALUES ($1, 1, 'test', 'lot scanned') RETURNING id, state`,
    [lot.rows[0].id]
  );
  return { wardId: ward.rows[0].id, lotId: lot.rows[0].id, creditId: credit.rows[0].id };
}

async function seedFlag(client, wardId) {
  const run = await client.query(`INSERT INTO audit_runs (engine_version) VALUES ('test') RETURNING id`);
  const flag = await client.query(
    `INSERT INTO audit_flags (audit_run_id, ward_id, check_type, period_start, period_end) VALUES ($1, $2, 'benford', '2026-07-06', '2026-08-02') RETURNING id`,
    [run.rows[0].id, wardId]
  );
  return flag.rows[0].id;
}

const setState = (client, creditId, state, extra = '') =>
  client.query(`UPDATE credits SET state = $2 ${extra} WHERE id = $1`, [creditId, state]);

const events = async (client, creditId) =>
  (await client.query(
    `SELECT from_state, to_state, audit_flag_id FROM credit_events WHERE credit_id = $1 ORDER BY id`,
    [creditId]
  )).rows;

test('new credit starts pending and logs its creation event', () =>
  withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    assert.deepEqual(await events(c, creditId), [{ from_state: null, to_state: 'pending', audit_flag_id: null }]);
  }));

test('pending -> matured -> tradable is allowed and fully logged', () =>
  withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await setState(c, creditId, 'matured');
    await setState(c, creditId, 'tradable');
    const log = (await events(c, creditId)).map((e) => `${e.from_state}->${e.to_state}`);
    assert.deepEqual(log, ['null->pending', 'pending->matured', 'matured->tradable']);
  }));

test('credit cannot be created outside pending', () =>
  withTx(async (c) => {
    const { wardId } = await seedCredit(c);
    const lot = await c.query(
      `INSERT INTO lots (lot_code, ward_id, weight_kg, band, band_source, contamination_pct)
       VALUES ('TEST-L1b', $1, 10, 'clean', 'mock_rule_based', 5) RETURNING id`,
      [wardId]
    );
    await assert.rejects(
      c.query(`INSERT INTO credits (lot_id, credit_value, pricing_version, state) VALUES ($1, 1, 'test', 'tradable')`, [lot.rows[0].id]),
      /must be created in pending/
    );
  }));

test('credits cannot be deleted, so a clawback cannot be erased and re-issued', () =>
  withTx(async (c) => {
    const { wardId, creditId } = await seedCredit(c);
    const flagId = await seedFlag(c, wardId);
    await setState(c, creditId, 'clawed_back', `, clawback_flag_id = ${flagId}`);
    await assert.rejects(c.query(`DELETE FROM credits WHERE id = $1`, [creditId]), /credits cannot be deleted/);
  }));

test('credit_events is append-only and written only by the trigger', async () => {
  await withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await assert.rejects(c.query(`DELETE FROM credit_events WHERE credit_id = $1`, [creditId]), /append-only \(DELETE/);
  });
  await withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await assert.rejects(
      c.query(`UPDATE credit_events SET to_state = 'tradable' WHERE credit_id = $1`, [creditId]),
      /append-only \(UPDATE/
    );
  });
  await withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await assert.rejects(
      c.query(`INSERT INTO credit_events (credit_id, from_state, to_state) VALUES ($1, 'pending', 'tradable')`, [creditId]),
      /written only by the credits trigger/
    );
  });
});

test('a clawed-back credit cannot have its flag repointed (terminal, no silent edits)', () =>
  withTx(async (c) => {
    const { wardId, creditId } = await seedCredit(c);
    const flagA = await seedFlag(c, wardId);
    const flagB = await seedFlag(c, wardId);
    await setState(c, creditId, 'clawed_back', `, clawback_flag_id = ${flagA}`);
    await assert.rejects(
      c.query(`UPDATE credits SET clawback_flag_id = $2 WHERE id = $1`, [creditId, flagB]),
      /terminal state clawed_back/
    );
  }));

test('reason and clawback flag cannot change without a state transition', async () => {
  await withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await assert.rejects(
      c.query(`UPDATE credits SET state_reason = 'edited' WHERE id = $1`, [creditId]),
      /can only change with a state transition/
    );
  });
  await withTx(async (c) => {
    const { wardId, creditId } = await seedCredit(c);
    const flagId = await seedFlag(c, wardId);
    // A flag on a non-clawed-back credit is rejected by the two-way clawback constraint.
    await assert.rejects(
      c.query(`UPDATE credits SET state = 'matured', clawback_flag_id = $2 WHERE id = $1`, [creditId, flagId]),
      /clawback_requires_flag/
    );
  });
});

test('credit value is fixed at creation', () =>
  withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await assert.rejects(
      c.query(`UPDATE credits SET credit_value = 999 WHERE id = $1`, [creditId]),
      /fixed at creation/
    );
  }));

test('matures_at can still be adjusted while a credit is in escrow', () =>
  withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await c.query(`UPDATE credits SET matures_at = now() + interval '7 days' WHERE id = $1`, [creditId]);
    assert.equal((await events(c, creditId)).length, 1);
  }));

test('pending cannot skip straight to tradable', () =>
  withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await assert.rejects(setState(c, creditId, 'tradable'), /illegal credit state transition: pending -> tradable/);
  }));

test('clawback without an audit flag is rejected', () =>
  withTx(async (c) => {
    const { creditId } = await seedCredit(c);
    await assert.rejects(setState(c, creditId, 'clawed_back'), /clawback_requires_flag/);
  }));

test('pending and matured credits can be clawed back on an audit flag, and the flag is logged', async () => {
  for (const from of ['pending', 'matured']) {
    await withTx(async (c) => {
      const { wardId, creditId } = await seedCredit(c);
      if (from === 'matured') await setState(c, creditId, 'matured');
      const flagId = await seedFlag(c, wardId);
      await setState(c, creditId, 'clawed_back', `, clawback_flag_id = ${flagId}, state_reason = 'failed audit'`);
      const last = (await events(c, creditId)).at(-1);
      assert.deepEqual(last, { from_state: from, to_state: 'clawed_back', audit_flag_id: flagId });
    });
  }
});

test('tradable is terminal: it cannot be clawed back', () =>
  withTx(async (c) => {
    const { wardId, creditId } = await seedCredit(c);
    await setState(c, creditId, 'matured');
    await setState(c, creditId, 'tradable');
    const flagId = await seedFlag(c, wardId);
    await assert.rejects(
      setState(c, creditId, 'clawed_back', `, clawback_flag_id = ${flagId}`),
      /terminal state tradable/
    );
  }));

test('clawed_back is terminal', () =>
  withTx(async (c) => {
    const { wardId, creditId } = await seedCredit(c);
    const flagId = await seedFlag(c, wardId);
    await setState(c, creditId, 'clawed_back', `, clawback_flag_id = ${flagId}`);
    await assert.rejects(setState(c, creditId, 'pending'), /terminal state clawed_back/);
  }));

test('only the three coarse contamination bands are accepted (D2)', () =>
  withTx(async (c) => {
    const ward = await c.query(`INSERT INTO wards (ward_code, name) VALUES ('TEST-W2', 'x') RETURNING id`);
    await assert.rejects(
      c.query(
        `INSERT INTO lots (lot_code, ward_id, weight_kg, band, band_source) VALUES ('TEST-L2', $1, 5, 'PET', 'manual')`,
        [ward.rows[0].id]
      ),
      /invalid input value for enum contamination_band/
    );
  }));
