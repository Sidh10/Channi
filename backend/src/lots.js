// Lot scanning: simulated scan -> placeholder band -> price -> lot + pending credit, in one
// transaction (decisions.md D15, D16, D17). Stores the classifier's and pricer's *returned*
// values so a stored lot always re-derives to the same band and value.
import { randomUUID } from 'node:crypto';
import { classifyContamination } from './banding.js';
import { priceLot } from './pricing.js';
import { issueCredit } from './escrow.js';
import { withTransaction } from './db.js';
import { ValidationError, parseTimestamp } from './errors.js';
import { recordStubPayout, DEFAULT_COLLECTOR } from './payouts.js';

// No database access: what a lot would earn. Drives the live slider (demo beat 1).
export function quoteLot({ weightKg, contaminationPct }) {
  const classification = classifyContamination(contaminationPct);
  const price = priceLot({ band: classification.band, weightKg });
  return { classification, price };
}

export async function submitLot(db, { wardCode, weightKg, contaminationPct, scannedAt, collectorRef = DEFAULT_COLLECTOR }) {
  const { classification, price } = quoteLot({ weightKg, contaminationPct });
  const scanned = scannedAt === undefined ? new Date() : parseTimestamp(scannedAt, 'scannedAt');

  return withTransaction(async (client) => {
    const ward = await client.query('SELECT id FROM wards WHERE ward_code = $1', [wardCode]);
    if (ward.rowCount === 0) throw new ValidationError(`unknown ward ${wardCode}`);
    const lotCode = `LOT-${wardCode}-${randomUUID().slice(0, 8).toUpperCase()}`;
    const lot = await client.query(
      `INSERT INTO lots (lot_code, ward_id, weight_kg, band, band_source, contamination_pct,
                         collector_ref, data_origin, scanned_at)
       VALUES ($1, $2, $3, $4, $5, $6, $7, 'synthetic', $8) RETURNING *`,
      [lotCode, ward.rows[0].id, price.weightKg, classification.band, classification.source,
       classification.contaminationPct, collectorRef, scanned]
    );
    const credit = await issueCredit(client, lot.rows[0].id);
    if (credit.credit_value !== price.creditValue) {
      throw new Error(`stored credit value ${credit.credit_value} differs from quoted ${price.creditValue}`);
    }
    // Earn-as-you-Sort: fires when the lot is scanned and scored (SPEC item 6). Simulated only (D4, D20).
    const payout = await recordStubPayout(client, {
      lotId: lot.rows[0].id, credits: credit.credit_value, collectorRef: lot.rows[0].collector_ref,
    });
    return { lot: lot.rows[0], credit, classification, price, payout };
  }, db);
}
