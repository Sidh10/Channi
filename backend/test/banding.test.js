import { test } from 'node:test';
import assert from 'node:assert/strict';
import { classifyContamination, BANDS, BAND_SOURCE, THRESHOLDS } from '../src/banding.js';

test('only the three coarse bands exist (D2)', () => {
  assert.deepEqual(BANDS, ['clean', 'mixed', 'heavily_contaminated']);
});

test('band boundaries follow the placeholder thresholds', () => {
  const cases = [
    [0, 'clean'],
    [THRESHOLDS.cleanBelowPct - 0.01, 'clean'],
    [THRESHOLDS.cleanBelowPct, 'mixed'],
    [THRESHOLDS.heavyFromPct - 0.01, 'mixed'],
    [THRESHOLDS.heavyFromPct, 'heavily_contaminated'],
    [100, 'heavily_contaminated'],
  ];
  for (const [pct, band] of cases) {
    assert.equal(classifyContamination(pct).band, band, `${pct}% should be ${band}`);
  }
});

test('every result is labelled as mock output, not a trained model (D2)', () => {
  const r = classifyContamination(15);
  assert.equal(r.source, BAND_SOURCE);
  assert.equal(r.source, 'mock_rule_based');
  assert.match(r.sourceLabel, /not a trained model/);
});

test('rejects inputs outside 0-100 or non-numeric', () => {
  for (const bad of [-1, 100.01, NaN, Infinity, '20', null, undefined]) {
    assert.throws(() => classifyContamination(bad), RangeError, `should reject ${String(bad)}`);
  }
});
