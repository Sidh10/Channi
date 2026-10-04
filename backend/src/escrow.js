// Escrow service: issues credits and moves them through the D3 state machine in response to
// time and audit results (decisions.md D3, D9, D17). The database triggers enforce which
// transitions are legal; this module decides *when* each one happens.
//
//   pending  --(hold elapsed, not held)-->               matured
//   matured  --(covered by a finished audit, not held)--> tradable
//   pending|matured --(inspector confirms an audit flag)--> clawed_back
//
// "Held" is derived, not a state: a credit is held while an open or under-inspection audit
// flag covers its lot's ward and scan date. Held credits can't mature or become tradable.
// Every state change is a single SQL statement (atomic on its own), so each function is safe to
// call with a pooled client or inside a caller's transaction.

import { priceLot } from './pricing.js';
import { NotFoundError, ConflictError } from './errors.js';

// Placeholder: one full audit window. Maturing needs only the hold to elapse (and no hold
// flag); becoming tradable additionally needs an audit covering the lot. A demo design
// parameter, not a regulatory figure.
export const ESCROW_HOLD_DAYS = 28;

// Log dates and scan dates are compared as local calendar dates.
export const LOCAL_TZ = 'Asia/Kolkata';

const HOLDING_FLAG_STATUSES = ['open', 'under_inspection'];

// SQL fragment: true when audit flag f covers lot l (same ward, local scan date inside the
// flag period, both ends inclusive; periods are NOT NULL, migration 005). $tz = LOCAL_TZ.
const flagCoversLot = (tz) => `
  f.ward_id = l.ward_id
  AND (l.scanned_at AT TIME ZONE ${tz})::date BETWEEN f.period_start AND f.period_end`;

const isHeld = (tz, statuses) => `EXISTS (
  SELECT 1 FROM audit_flags f
  WHERE f.status = ANY(${statuses}) AND ${flagCoversLot(tz)})`;

export async function issueCredit(client, lotId) {
  const lot = await client.query(
    `SELECT id, band, weight_kg, scanned_at FROM lots WHERE id = $1`,
    [lotId]
  );
  if (lot.rowCount === 0) throw new Error(`lot ${lotId} not found`);
  const { band, weight_kg, scanned_at } = lot.rows[0];
  const price = priceLot({ band, weightKg: Number(weight_kg) });
  const { rows } = await client.query(
    `INSERT INTO credits (lot_id, credit_value, pricing_version, state_reason, matures_at)
     VALUES ($1, $2, $3, 'lot scanned and priced', $4::timestamptz + make_interval(days => $5))
     RETURNING *`,
    [lotId, price.creditValue, price.pricingVersion, scanned_at, ESCROW_HOLD_DAYS]
  );
  return rows[0];
}

export async function matureDueCredits(client, now = new Date()) {
  const { rows } = await client.query(
    `UPDATE credits c SET state = 'matured', state_reason = 'escrow hold elapsed'
     FROM lots l
     WHERE l.id = c.lot_id AND c.state = 'pending' AND c.matures_at <= $1
       AND NOT ${isHeld('$2', '$3')}
     RETURNING c.id`,
    [now, LOCAL_TZ, HOLDING_FLAG_STATUSES]
  );
  return rows.map((r) => r.id);
}

// Matured credits whose lot falls inside a finished audit run that scored the lot's ward,
// and that no open/under-inspection flag holds, become tradable.
export async function releaseAuditedCredits(client) {
  const { rows } = await client.query(
    `UPDATE credits c SET state = 'tradable',
       state_reason = 'cleared by audit run ' || cover.run_id
     FROM lots l,
     LATERAL (
       SELECT max(r.id) AS run_id
       FROM audit_runs r JOIN ward_audit_scores s ON s.audit_run_id = r.id
       WHERE s.ward_id = l.ward_id AND r.finished_at IS NOT NULL
         AND (l.scanned_at AT TIME ZONE $1)::date BETWEEN r.period_start AND r.period_end
     ) cover
     WHERE l.id = c.lot_id AND c.state = 'matured' AND cover.run_id IS NOT NULL
       AND NOT ${isHeld('$1', '$2')}
     RETURNING c.id`,
    [LOCAL_TZ, HOLDING_FLAG_STATUSES]
  );
  return rows.map((r) => r.id);
}

export async function runEscrowCycle(client, now = new Date()) {
  const matured = await matureDueCredits(client, now);
  const released = await releaseAuditedCredits(client);
  return { matured, released };
}

async function setFlagStatus(client, flagId, to, allowedFrom) {
  const { rows } = await client.query(
    `UPDATE audit_flags SET status = $2 WHERE id = $1 AND status = ANY($3) RETURNING *`,
    [flagId, to, allowedFrom]
  );
  if (rows.length === 0) {
    const cur = await client.query(`SELECT status FROM audit_flags WHERE id = $1`, [flagId]);
    if (cur.rowCount === 0) throw new NotFoundError(`audit flag ${flagId} not found`);
    throw new ConflictError(`audit flag ${flagId} is ${cur.rows[0].status}; cannot move it to ${to}`);
  }
  return rows[0];
}

export const startInspection = (client, flagId) =>
  setFlagStatus(client, flagId, 'under_inspection', ['open']);

// Inspector finds the data genuine: the flag stops holding credits (others may still hold them).
export const clearFlag = (client, flagId) =>
  setFlagStatus(client, flagId, 'cleared', HOLDING_FLAG_STATUSES);

// Inspector confirms the audit flag: every non-terminal credit it covers is clawed back,
// pointing at this flag (D3: clawback only on a failed audit). One statement, so no escrow
// cycle can see the flag confirmed (no longer holding) before its credits are clawed back.
export async function confirmFlag(client, flagId) {
  const { rows } = await client.query(
    `WITH f AS (
       UPDATE audit_flags SET status = 'confirmed'
       WHERE id = $1 AND status = ANY($3)
       RETURNING *
     ), cb AS (
       UPDATE credits c SET state = 'clawed_back', clawback_flag_id = f.id,
         state_reason = 'audit flag ' || f.id || ' (' || f.check_type || ') confirmed on inspection'
       FROM lots l, f
       WHERE l.id = c.lot_id AND c.state IN ('pending', 'matured') AND ${flagCoversLot('$2')}
       RETURNING c.id
     )
     SELECT (SELECT row_to_json(f) FROM f) AS flag,
            COALESCE((SELECT array_agg(id ORDER BY id) FROM cb), '{}') AS clawed_back`,
    [flagId, LOCAL_TZ, HOLDING_FLAG_STATUSES]
  );
  if (!rows[0].flag) {
    const cur = await client.query(`SELECT status FROM audit_flags WHERE id = $1`, [flagId]);
    if (cur.rowCount === 0) throw new NotFoundError(`audit flag ${flagId} not found`);
    throw new ConflictError(`audit flag ${flagId} is ${cur.rows[0].status}; cannot move it to confirmed`);
  }
  return { flag: rows[0].flag, clawedBack: rows[0].clawed_back };
}

// Read model for the API/dashboard: each credit with its lot, derived hold, holding flags, and
// held_since (earliest holding flag's created_at; holds write no credit_events row, D17).
export async function listCredits(client) {
  const { rows } = await client.query(
    `SELECT c.id, c.state, c.state_reason, c.credit_value, c.pricing_version, c.matures_at,
            c.clawback_flag_id, c.created_at, c.state_changed_at,
            l.id AS lot_id, l.lot_code, l.ward_id, w.ward_code, l.weight_kg, l.band, l.band_source,
            l.contamination_pct, l.scanned_at,
            COALESCE(h.flag_ids, '{}') AS holding_flag_ids,
            (c.state IN ('pending', 'matured') AND h.flag_ids IS NOT NULL) AS held,
            CASE WHEN c.state IN ('pending', 'matured') THEN h.held_since END AS held_since
     FROM credits c
     JOIN lots l ON l.id = c.lot_id
     JOIN wards w ON w.id = l.ward_id
     LEFT JOIN LATERAL (
       SELECT array_agg(f.id ORDER BY f.id) AS flag_ids, min(f.created_at) AS held_since FROM audit_flags f
       WHERE f.status = ANY($2) AND ${flagCoversLot('$1')}
     ) h ON true
     ORDER BY c.id`,
    [LOCAL_TZ, HOLDING_FLAG_STATUSES]
  );
  return rows;
}
