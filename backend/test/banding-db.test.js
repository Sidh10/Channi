// Banding vs the live schema: enum/constraint drift, and band traceability after a DB round trip.
// Runs inside rolled-back transactions.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { pool } from '../src/db.js';
import { classifyContamination, BANDS, BAND_SOURCE } from '../src/banding.js';

after(() => pool.end());

test('BANDS matches the contamination_band enum and BAND_SOURCE is accepted by lots', async () => {
  const { rows } = await pool.query(
    `SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
     WHERE t.typname = 'contamination_band' ORDER BY e.enumsortorder`
  );
  assert.deepEqual(rows.map((r) => r.enumlabel), BANDS);
  const check = await pool.query(
    `SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conname = 'lots_band_source_check'`
  );
  assert.match(check.rows[0].def, new RegExp(`'${BAND_SOURCE}'`));
});

test('a stored lot re-derives to the same band, including values near the cut-offs', async () => {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const ward = await client.query(`INSERT INTO wards (ward_code, name) VALUES ('TEST-BW', 'x') RETURNING id`);
    const inputs = [0.004, 9.99, 9.994, 9.995, 9.999, 10, 29.99, 29.995, 29.999, 30, 99.995, 100];
    for (const [i, input] of inputs.entries()) {
      const r = classifyContamination(input);
      const { rows } = await client.query(
        `INSERT INTO lots (lot_code, ward_id, weight_kg, band, band_source, contamination_pct)
         VALUES ($1, $2, 1, $3, $4, $5) RETURNING band, contamination_pct`,
        [`TEST-BL${i}`, ward.rows[0].id, r.band, r.source, r.contaminationPct]
      );
      // node-pg returns NUMERIC as a string; parse before re-classifying.
      const again = classifyContamination(Number(rows[0].contamination_pct));
      assert.equal(again.band, rows[0].band, `input ${input} stored as ${rows[0].contamination_pct}`);
    }
  } finally {
    await client.query('ROLLBACK');
    client.release();
  }
});

test('a mock-banded lot without its input percentage is rejected', async () => {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const ward = await client.query(`INSERT INTO wards (ward_code, name) VALUES ('TEST-BW2', 'x') RETURNING id`);
    await assert.rejects(
      client.query(
        `INSERT INTO lots (lot_code, ward_id, weight_kg, band, band_source) VALUES ('TEST-BL', $1, 1, 'clean', 'mock_rule_based')`,
        [ward.rows[0].id]
      ),
      /lots_mock_band_has_input/
    );
  } finally {
    await client.query('ROLLBACK');
    client.release();
  }
});
