// Loads SYNTHETIC ward data into Postgres and injects demo tampering (decisions.md D5, D14, D18).
// Everything written here has data_origin = 'synthetic'; nothing is live MCD data.
import { callEngine } from './auditEngine.js';
import { withTransaction } from './db.js';
import { ValidationError } from './errors.js';

export const DEMO_SEED = 2026;

const LOG_CHUNK = 20000;

async function insertLogs(client, wardIds, logs, { tamper = false } = {}) {
  let inserted = 0;
  for (let i = 0; i < logs.length; i += LOG_CHUNK) {
    const chunk = logs.slice(i, i + LOG_CHUNK);
    const { rowCount } = await client.query(
      `INSERT INTO ward_tonnage_logs (ward_id, entry_date, tonnage, data_origin, is_injected_tamper)
       SELECT w, d, t, 'synthetic', $4
       FROM unnest($1::int[], $2::date[], $3::numeric[]) AS x(w, d, t)`,
      [
        chunk.map((r) => {
          const id = wardIds.get(r.ward_code);
          if (id === undefined) throw new Error(`unknown ward_code ${r.ward_code} in engine output`);
          return id;
        }),
        chunk.map((r) => r.entry_date),
        chunk.map((r) => r.tonnage),
        tamper,
      ]
    );
    inserted += rowCount;
  }
  return inserted;
}

// Seeds wards + trip logs from the engine's generator. Refuses to run on a non-empty ward table
// unless `fresh` is set, in which case all demo tables are truncated first (D9 allows a
// whole-database reset for demo reruns).
export async function seedSynthetic(db, { seed = DEMO_SEED, params = {}, fresh = false } = {}) {
  const data = await callEngine('generate', { seed, params });
  if (!/SYNTHETIC/i.test(data.data_notice || '')) {
    throw new Error('engine generate output is missing its SYNTHETIC data notice');
  }
  return withTransaction(async (client) => {
    if (fresh) {
      await client.query(
        `TRUNCATE stub_payouts, credit_events, credits, lots, audit_flags, ward_audit_scores, audit_runs,
                  ward_tonnage_logs, wards RESTART IDENTITY CASCADE`
      );
    } else {
      const { rows } = await client.query('SELECT count(*)::int AS n FROM wards');
      if (rows[0].n > 0) throw new Error('wards already seeded; pass fresh to truncate and reseed');
    }
    const wards = await client.query(
      `INSERT INTO wards (ward_code, name, zone, population, income_proxy, sanitation_proxy, data_origin)
       SELECT * FROM unnest($1::text[], $2::text[], $3::text[], $4::int[], $5::numeric[], $6::numeric[], $7::text[])
       RETURNING id, ward_code`,
      [
        data.wards.map((w) => w.ward_code),
        data.wards.map((w) => w.name),
        data.wards.map((w) => w.zone),
        data.wards.map((w) => w.population),
        data.wards.map((w) => w.income_proxy),
        data.wards.map((w) => w.sanitation_proxy),
        data.wards.map(() => 'synthetic'),
      ]
    );
    const wardIds = new Map(wards.rows.map((r) => [r.ward_code, r.id]));
    const logs = await insertLogs(client, wardIds, data.logs);
    return { seed, wards: wards.rowCount, logs, params: data.params, dataNotice: data.data_notice };
  }, db);
}

// Demo beat 2: appends engine-generated phantom entries (rounded or fabricated) to one ward's log,
// marked is_injected_tamper = true as ground truth. The audit never reads that column.
export const INJECT_KINDS = ['round', 'fabricate'];
export const INJECT_PLACEMENTS = ['spread', 'last_day'];

export async function injectTamper(db, { wardCode, kind, placement = 'spread', fraction, seed = DEMO_SEED }) {
  if (!INJECT_KINDS.includes(kind)) throw new ValidationError(`kind must be one of ${INJECT_KINDS.join(', ')}`);
  if (!INJECT_PLACEMENTS.includes(placement)) {
    throw new ValidationError(`placement must be one of ${INJECT_PLACEMENTS.join(', ')}`);
  }
  if (fraction !== undefined && !(typeof fraction === 'number' && fraction > 0 && fraction <= 1)) {
    throw new ValidationError('fraction must be a number in (0, 1]');
  }
  if (!Number.isSafeInteger(seed) || seed < 0) throw new ValidationError(`seed must be a non-negative integer (got ${seed})`);
  const ward = await db.query('SELECT id FROM wards WHERE ward_code = $1', [wardCode]);
  if (ward.rowCount === 0) throw new ValidationError(`unknown ward ${wardCode}`);
  const logs = await db.query(
    `SELECT id AS entry_id, $2::text AS ward_code, to_char(entry_date, 'YYYY-MM-DD') AS entry_date,
            tonnage::float8 AS tonnage
     FROM ward_tonnage_logs WHERE ward_id = $1 ORDER BY id`,
    [ward.rows[0].id, wardCode]
  );
  const request = { seed, ward_code: wardCode, kind, placement, logs: logs.rows };
  if (fraction !== undefined) request.fraction = fraction;
  const result = await callEngine('inject', request);
  const inserted = await withTransaction(
    (client) => insertLogs(client, new Map([[wardCode, ward.rows[0].id]]), result.entries, { tamper: true }),
    db
  );
  return { wardCode, inserted, dataNotice: result.data_notice, sizing: result.sizing, report: result.report };
}
