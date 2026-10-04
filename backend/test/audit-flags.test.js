// Audit-flag integrity enforced by the database (migration 005, decisions.md D17).
// Each test runs in a rolled-back transaction.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { pool } from '../src/db.js';

after(() => pool.end());

async function withFlag(fn) {
  const c = await pool.connect();
  try {
    await c.query('BEGIN');
    const w = await c.query(`INSERT INTO wards (ward_code, name) VALUES ('TEST-FW', 'x') RETURNING id`);
    const r = await c.query(
      `INSERT INTO audit_runs (engine_version, period_start, period_end) VALUES ('test', '2026-07-06', '2026-08-02') RETURNING id`
    );
    const ids = { wardId: w.rows[0].id, runId: r.rows[0].id };
    const f = await c.query(
      `INSERT INTO audit_flags (audit_run_id, ward_id, check_type, period_start, period_end)
       VALUES ($1, $2, 'benford', '2026-07-06', '2026-08-02') RETURNING id`,
      [ids.runId, ids.wardId]
    );
    return await fn(c, { ...ids, flagId: f.rows[0].id });
  } finally {
    await c.query('ROLLBACK');
    c.release();
  }
}

const setStatus = (c, id, status) => c.query(`UPDATE audit_flags SET status = $2 WHERE id = $1`, [id, status]);

test('flags must be created open', () =>
  withFlag(async (c, { runId, wardId }) => {
    await assert.rejects(
      c.query(
        `INSERT INTO audit_flags (audit_run_id, ward_id, check_type, period_start, period_end, status)
         VALUES ($1, $2, 'benford', '2026-07-06', '2026-08-02', 'cleared')`,
        [runId, wardId]
      ),
      /must be created open/
    );
  }));

test('flags need a bounded, ordered period', async () => {
  await withFlag(async (c, { runId, wardId }) => {
    await assert.rejects(
      c.query(`INSERT INTO audit_flags (audit_run_id, ward_id, check_type) VALUES ($1, $2, 'benford')`, [runId, wardId]),
      /null value in column "period_start"/
    );
  });
  await withFlag(async (c, { runId, wardId }) => {
    await assert.rejects(
      c.query(
        `INSERT INTO audit_flags (audit_run_id, ward_id, check_type, period_start, period_end)
         VALUES ($1, $2, 'benford', '2026-08-02', '2026-07-06')`,
        [runId, wardId]
      ),
      /audit_flags_period_order/
    );
  });
});

test('status only moves forward', async () => {
  for (const [path, illegal] of [
    [['confirmed'], 'open'],
    [['cleared'], 'under_inspection'],
    [['under_inspection'], 'open'],
    [['confirmed'], 'cleared'],
  ]) {
    await withFlag(async (c, { flagId }) => {
      for (const s of path) await setStatus(c, flagId, s);
      await assert.rejects(setStatus(c, flagId, illegal), /illegal audit flag status transition/);
    });
  }
});

test('a flag is immutable apart from its status', async () => {
  for (const sql of [
    `UPDATE audit_flags SET period_end = '2026-07-07' WHERE id = $1`,
    `UPDATE audit_flags SET ward_id = ward_id + 1 WHERE id = $1`,
    `UPDATE audit_flags SET details = '{"x":1}' WHERE id = $1`,
  ]) {
    await withFlag(async (c, { flagId }) => {
      await assert.rejects(c.query(sql, [flagId]), /immutable apart from its status/);
    });
  }
});

test('flags cannot be deleted, directly or by deleting their audit run', async () => {
  await withFlag(async (c, { flagId }) => {
    await assert.rejects(c.query(`DELETE FROM audit_flags WHERE id = $1`, [flagId]), /cannot be deleted/);
  });
  await withFlag(async (c, { runId }) => {
    await assert.rejects(c.query(`DELETE FROM audit_runs WHERE id = $1`, [runId]), /cannot be deleted/);
  });
});
