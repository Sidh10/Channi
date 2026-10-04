-- 006: Earn-as-you-Sort payout STUB (decisions.md D4, D20). One simulated payout per scanned lot.
-- The constraints make it impossible to record anything but a sandbox stub: no real rail, no real money.
CREATE TABLE stub_payouts (
  id                   SERIAL PRIMARY KEY,
  lot_id               INTEGER NOT NULL UNIQUE REFERENCES lots(id),
  collector_ref        TEXT NOT NULL,
  credits              NUMERIC(14,4) NOT NULL CHECK (credits >= 0),
  rate_inr_per_credit  NUMERIC(8,2) NOT NULL CHECK (rate_inr_per_credit >= 0),
  amount_inr           NUMERIC(14,2) NOT NULL CHECK (amount_inr >= 0),
  rail                 TEXT NOT NULL DEFAULT 'upi_sandbox_stub' CHECK (rail = 'upi_sandbox_stub'),
  status               TEXT NOT NULL DEFAULT 'simulated' CHECK (status = 'simulated'),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
