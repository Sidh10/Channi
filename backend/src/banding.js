// Contamination banding — RULE-BASED PLACEHOLDER, not a trained model (decisions.md D2, D15).
// Input is a simulated scan: an estimated contamination share for the lot (% of lot weight that is
// non-target or soiled material), set by the operator/slider in the demo. Output is one of three
// coarse bands only; never a polymer-level purity claim.

import { ValidationError } from './errors.js';

export const BAND_SOURCE = 'mock_rule_based';

export const BANDS = ['clean', 'mixed', 'heavily_contaminated'];

// Placeholder cut-offs on the contamination estimate. These are demo design choices,
// NOT an industry, CPCB or buyer standard — never present them as one.
export const THRESHOLDS = Object.freeze({ cleanBelowPct: 10, heavyFromPct: 30 });

export const SOURCE_LABEL = 'Simulated scan — rule-based placeholder, not a trained model';

export function classifyContamination(contaminationPct) {
  if (typeof contaminationPct !== 'number' || !Number.isFinite(contaminationPct)
      || contaminationPct < 0 || contaminationPct > 100) {
    throw new ValidationError(`contaminationPct must be a number between 0 and 100 (got ${contaminationPct})`);
  }

  // Classify the value exactly as it will be stored (lots.contamination_pct is NUMERIC(5,2)), so a
  // stored lot always re-derives to the same band — e.g. 9.999 is stored as 10.00 and is 'mixed'.
  // Store the returned contaminationPct, not the raw input.
  const pct = Number(contaminationPct.toFixed(2));

  let band;
  if (pct < THRESHOLDS.cleanBelowPct) band = 'clean';
  else if (pct < THRESHOLDS.heavyFromPct) band = 'mixed';
  else band = 'heavily_contaminated';

  return {
    band,
    source: BAND_SOURCE,
    sourceLabel: SOURCE_LABEL,
    contaminationPct: pct,
    thresholds: THRESHOLDS,
  };
}
