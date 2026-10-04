// The ward integrity ranking (latestAudit) orders by strongest evidence at full precision,
// even though confidence_score is stored at 4 dp. Runs in a rolled-back transaction.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { pool } from '../src/db.js';
import { latestAudit } from '../src/auditRun.js';

after(() => pool.end());

test('several flagged wards are ranked by their smallest flag q, not by ward code', async () => {
  const c = await pool.connect();
  try {
    await c.query('BEGIN');
    const ward = async (code) =>
      (await c.query(`INSERT INTO wards (ward_code, name) VALUES ($1, 'x') RETURNING id`, [code])).rows[0].id;
    const [a, b, z] = [await ward('TEST-A'), await ward('TEST-B'), await ward('TEST-Z')];
    const run = (await c.query(
      `INSERT INTO audit_runs (engine_version, period_start, period_end, finished_at)
       VALUES ('test', '2026-07-06', '2026-08-02', now()) RETURNING id`
    )).rows[0].id;
    for (const [w, conf, flagged] of [[a, 0, true], [b, 0, true], [z, 0.7, false]]) {
      await c.query(
        `INSERT INTO ward_audit_scores (audit_run_id, ward_id, confidence_score, flagged) VALUES ($1, $2, $3, $4)`,
        [run, w, conf, flagged]
      );
    }
    for (const [w, q] of [[a, 1e-10], [b, 5e-40]]) {
      await c.query(
        `INSERT INTO audit_flags (audit_run_id, ward_id, check_type, period_start, period_end, score)
         VALUES ($1, $2, 'roundness', '2026-07-06', '2026-08-02', $3)`,
        [run, w, q]
      );
    }
    const { wards } = await latestAudit(c);
    assert.deepEqual(wards.map((w) => w.ward_code), ['TEST-B', 'TEST-A', 'TEST-Z']);
  } finally {
    await c.query('ROLLBACK');
    c.release();
  }
});
