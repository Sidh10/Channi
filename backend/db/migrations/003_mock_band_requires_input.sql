-- 003: a lot banded by the rule-based placeholder must keep the input it was banded from,
-- so the band is always traceable (decisions.md D15).
ALTER TABLE lots
  ADD CONSTRAINT lots_mock_band_has_input
  CHECK (band_source <> 'mock_rule_based' OR contamination_pct IS NOT NULL);
