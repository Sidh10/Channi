// Credit pricing: band + lot weight -> credit value (decisions.md D16).
// Credits are denominated in clean-equivalent kilograms, not money: a lot earns its weight
// times a quality factor for its contamination band. The factors are PLACEHOLDER demo
// parameters, not market prices or a buyer/CPCB standard — never present them as one.

import { BANDS } from './banding.js';
import { ValidationError } from './errors.js';

// lots.weight_kg is NUMERIC(10,2).
export const MAX_WEIGHT_KG = 99999999.99;

export const PRICING_VERSION = 'placeholder-v1';

export const CREDIT_UNIT = 'clean-equivalent kg';

export const QUALITY_FACTORS = Object.freeze({
  clean: 1.0,
  mixed: 0.5,
  heavily_contaminated: 0.1,
});

export function priceLot({ band, weightKg }) {
  if (!BANDS.includes(band)) {
    throw new ValidationError(`unknown band "${band}" (expected one of ${BANDS.join(', ')})`);
  }
  if (typeof weightKg !== 'number' || !Number.isFinite(weightKg) || weightKg <= 0) {
    throw new ValidationError(`weightKg must be a positive number (got ${weightKg})`);
  }
  // Price the weight as lots.weight_kg stores it (2 decimals), and round the value to
  // credits.credit_value's 4 decimals, so stored values equal returned ones. Store both returned values.
  const weight = Number(weightKg.toFixed(2));
  if (weight <= 0) throw new ValidationError(`weightKg rounds to 0 at 2 decimals (got ${weightKg})`);
  if (weight > MAX_WEIGHT_KG) throw new ValidationError(`weightKg must be at most ${MAX_WEIGHT_KG} (got ${weightKg})`);
  const creditValue = Number((weight * QUALITY_FACTORS[band]).toFixed(4));
  return {
    weightKg: weight,
    creditValue,
    unit: CREDIT_UNIT,
    qualityFactor: QUALITY_FACTORS[band],
    pricingVersion: PRICING_VERSION,
  };
}
