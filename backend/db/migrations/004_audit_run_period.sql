-- 004: the date window an audit run covered, so escrow can tell which lots it cleared (decisions.md D17).
ALTER TABLE audit_runs
  ADD COLUMN period_start DATE,
  ADD COLUMN period_end   DATE,
  ADD CONSTRAINT audit_runs_period_order CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end);
