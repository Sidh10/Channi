import { test } from 'node:test';
import assert from 'node:assert/strict';
import { priceLot, QUALITY_FACTORS, PRICING_VERSION, CREDIT_UNIT } from '../src/pricing.js';
import { BANDS } from '../src/banding.js';

test('credit value = weight x the band quality factor, in clean-equivalent kg', () => {
  for (const band of BANDS) {
    const r = priceLot({ band, weightKg: 100 });
    assert.equal(r.creditValue, 100 * QUALITY_FACTORS[band]);
    assert.equal(r.unit, CREDIT_UNIT);
    assert.equal(r.pricingVersion, PRICING_VERSION);
  }
});

test('a cleaner band never earns less for the same weight', () => {
  const v = BANDS.map((band) => priceLot({ band, weightKg: 37.5 }).creditValue);
  assert.ok(v[0] > v[1] && v[1] > v[2], `expected clean > mixed > heavy, got ${v}`);
});

test('changing only the band changes the value (demo beat 1)', () => {
  const clean = priceLot({ band: 'clean', weightKg: 250 }).creditValue;
  const mixed = priceLot({ band: 'mixed', weightKg: 250 }).creditValue;
  assert.notEqual(clean, mixed);
});

test('weight is priced as stored (2 decimals) and the value fits credit_value (4 decimals)', () => {
  const r = priceLot({ band: 'mixed', weightKg: 12.345678 });
  assert.equal(r.weightKg, 12.35);
  assert.equal(r.creditValue, 6.175);
});

test('rejects unknown bands and non-positive or non-numeric weights', () => {
  assert.throws(() => priceLot({ band: 'PET', weightKg: 10 }), RangeError);
  for (const weightKg of [0, -5, 0.001, NaN, '10', undefined]) {
    assert.throws(() => priceLot({ band: 'clean', weightKg }), RangeError, `should reject ${String(weightKg)}`);
  }
});
