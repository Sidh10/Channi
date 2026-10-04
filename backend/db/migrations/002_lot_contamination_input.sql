-- 002: keep the simulated-scan input next to the band it produced, so every lot's band
-- can be traced back to what the placeholder classifier saw (decisions.md D15).
ALTER TABLE lots
  ADD COLUMN contamination_pct NUMERIC(5,2) CHECK (contamination_pct BETWEEN 0 AND 100);
