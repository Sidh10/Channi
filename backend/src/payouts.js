// Earn-as-you-Sort payout STUB (decisions.md D4, D20): fires when a lot is scanned and scored, and
// only records/displays a simulated amount. No payment rail is called and no money moves;
// UPI_SANDBOX_KEY is deliberately unused.

// Placeholder demo rate. Not a market price, subsidy rate or any real figure — label it wherever shown.
export const STUB_RATE_INR_PER_CREDIT = 1.0;

export const STUB_LABEL = 'Simulated UPI payout (sandbox stub, no money moves; placeholder rate)';

export const DEFAULT_COLLECTOR = 'SYN-COLLECTOR-01';

export async function recordStubPayout(client, { lotId, credits, collectorRef }) {
  const amount = Number((credits * STUB_RATE_INR_PER_CREDIT).toFixed(2));
  const { rows } = await client.query(
    `INSERT INTO stub_payouts (lot_id, collector_ref, credits, rate_inr_per_credit, amount_inr)
     VALUES ($1, $2, $3, $4, $5) RETURNING *`,
    [lotId, collectorRef || DEFAULT_COLLECTOR, credits, STUB_RATE_INR_PER_CREDIT, amount]
  );
  return { ...rows[0], label: STUB_LABEL };
}
