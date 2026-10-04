export const BAND_LABEL = {
  clean: 'Clean',
  mixed: 'Mixed',
  heavily_contaminated: 'Heavily contaminated',
};

export const STATE_LABEL = {
  pending: 'Pending · in escrow',
  matured: 'Matured',
  tradable: 'Tradable',
  clawed_back: 'Clawed back',
};

export const CHECK_LABEL = {
  benford: 'Benford first-digit test',
  roundness: 'Round-number excess',
  baseline: 'Above socio-economic baseline',
  baseline_daily: 'Single-day spike vs baseline',
};

export const FLAG_STATUS_LABEL = {
  open: 'Open',
  under_inspection: 'Under inspection',
  confirmed: 'Confirmed',
  cleared: 'Cleared',
};

export function fmtNum(n, maxDigits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Number(n).toLocaleString('en-IN', { maximumFractionDigits: maxDigits });
}

const SUPERSCRIPT = { '-': '⁻', 0: '⁰', 1: '¹', 2: '²', 3: '³', 4: '⁴', 5: '⁵', 6: '⁶', 7: '⁷', 8: '⁸', 9: '⁹' };

// BH-adjusted p-value (q). Small values in scientific form: 5.4 × 10⁻⁴⁶. Never a percentage.
export function fmtQ(q) {
  if (q === null || q === undefined || Number.isNaN(q)) return '—';
  if (q === 0) return '< 10⁻³⁰⁰';
  if (q >= 0.001) return q.toFixed(3);
  const [mantissa, exp] = q.toExponential(1).split('e');
  const sup = String(Number(exp)).split('').map((c) => SUPERSCRIPT[c]).join('');
  return `${mantissa} × 10${sup}`;
}

const IST = { timeZone: 'Asia/Kolkata' };

// The simulated demo clock, shown as an IST calendar date and time.
export function fmtDemo(iso) {
  return new Date(iso).toLocaleString('en-IN', {
    ...IST, weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  }) + ' IST';
}

export function fmtDemoDate(iso) {
  return new Date(iso).toLocaleDateString('en-IN', { ...IST, day: 'numeric', month: 'short', year: 'numeric' });
}

// Real wall-clock times (event logs) are labelled as system time so they aren't confused with the demo clock.
export function fmtSystem(ts) {
  return new Date(ts).toLocaleTimeString('en-IN', { ...IST, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
