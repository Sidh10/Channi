import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { BAND_LABEL, fmtNum } from '../format.js';

// Demo beat 1: the credit value changes live as only the contamination estimate changes.
export default function LotScanner({ wards, meta, demoNow, defaultWard, onScanned, log, appBusy, outsideWindow }) {
  const [wardCode, setWardCode] = useState(defaultWard);
  const [weightKg, setWeightKg] = useState(250);
  const [pct, setPct] = useState(5);
  const [quote, setQuote] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [lastScan, setLastScan] = useState(null);

  useEffect(() => {
    if (!wardCode && defaultWard) setWardCode(defaultWard);
  }, [defaultWard, wardCode]);

  useEffect(() => {
    let stale = false;
    const t = setTimeout(() => {
      api.quote({ weightKg: Number(weightKg), contaminationPct: Number(pct) })
        .then((q) => { if (!stale) { setQuote(q); setError(null); } })
        .catch((e) => { if (!stale) { setQuote(null); setError(e.message); } });
    }, 80);
    return () => { stale = true; clearTimeout(t); };
  }, [weightKg, pct]);

  const cut = meta?.placeholders.bandCutoffsPct;
  const band = quote?.classification.band;

  async function scan() {
    setBusy(true);
    try {
      const r = await api.submitLot({ wardCode, weightKg: Number(weightKg), contaminationPct: Number(pct), scannedAt: demoNow });
      log(`Lot ${r.lot.lot_code} scanned in ${wardCode}: ${BAND_LABEL[r.lot.band]} (simulated scan), ${fmtNum(r.credit.credit_value)} credits → credit #${r.credit.id} pending in escrow. `
        + `Simulated UPI payout ₹${r.payout.amount_inr.toFixed(2)} to ${r.payout.collector_ref} (sandbox stub, no money moved).`);
      setLastScan(r);
      onScanned();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <header className="card-head">
        <h2><span className="step">1</span> Scan a lot</h2>
        <span className="tag tag-mock">{meta?.placeholders.bandingSource ?? 'Simulated scan'}</span>
      </header>

      <div className="scanner-inputs">
        <label>
          Ward
          <select value={wardCode ?? ''} onChange={(e) => setWardCode(e.target.value)}>
            {wards.map((w) => <option key={w.ward_code} value={w.ward_code}>{w.ward_code} · {w.name}</option>)}
          </select>
        </label>
        <label>
          Lot weight (kg)
          <input type="number" min="0.01" step="0.01" value={weightKg} onChange={(e) => setWeightKg(e.target.value)} />
        </label>
      </div>

      <label className="slider-label" htmlFor="contamination">
        Estimated contamination: <strong>{fmtNum(Number(pct), 1)}%</strong> of lot weight
      </label>
      <div className="slider-wrap">
        <input id="contamination" type="range" min="0" max="100" step="0.5" value={pct}
          onChange={(e) => setPct(e.target.value)} aria-describedby="cutoff-note"
          aria-valuetext={`${pct}% contamination${band ? `, ${BAND_LABEL[band]}` : ''}`} />
        {cut && (
          <div className="zones" aria-hidden="true">
            <div className="zone zone-clean" style={{ width: `${cut.cleanBelowPct}%` }} />
            <div className="zone zone-mixed" style={{ width: `${cut.heavyFromPct - cut.cleanBelowPct}%` }} />
            <div className="zone zone-heavy" style={{ width: `${100 - cut.heavyFromPct}%` }} />
          </div>
        )}
      </div>
      {cut && (
        <p id="cutoff-note" className="fine">
          Bands: clean &lt; {cut.cleanBelowPct}% · mixed {cut.cleanBelowPct}–{cut.heavyFromPct}% · heavily contaminated ≥ {cut.heavyFromPct}% — {cut.label}
        </p>
      )}

      <div className="quote" aria-live="polite">
        {quote ? (
          <>
            <span className={`band band-${band}`}>{BAND_LABEL[band]}</span>
            <div className="quote-value">
              <span className="big">{fmtNum(quote.price.creditValue)}</span>
              <span className="unit">credits · {meta?.placeholders.creditUnit.value}</span>
            </div>
            <p className="fine">
              {fmtNum(quote.price.weightKg)} kg × quality factor {quote.price.qualityFactor.toFixed(1)} ({meta?.placeholders.qualityFactors.label}).
              Unit: {meta?.placeholders.creditUnit.label}.
            </p>
          </>
        ) : <p className="muted">{error ?? 'Pricing…'}</p>}
      </div>

      <div className="actions">
        <button className="primary" onClick={scan} disabled={busy || appBusy || !quote || !wardCode}>
          {busy ? 'Scanning…' : 'Scan lot into escrow'}
        </button>
        {error && quote && <span className="error">{error}</span>}
      </div>
      {lastScan && (
        <div className="receipt">
          <strong>Earn-as-you-Sort:</strong> simulated UPI payout of ₹{lastScan.payout.amount_inr.toFixed(2)} to{' '}
          {lastScan.payout.collector_ref} for lot …{lastScan.lot.lot_code.split('-').at(-1)} (credit #{lastScan.credit.id}).
          <div className="fine">{meta?.placeholders.stubPayoutRate.label} (₹{meta?.placeholders.stubPayoutRate.value.toFixed(2)} per credit).</div>
        </div>
      )}
      {outsideWindow && (
        <p className="warning" role="alert">
          The demo clock is past the end of the synthetic log ({outsideWindow}). Lots scanned now fall outside every audit
          window, so they will mature but never be held or become tradable. Use Reset demo to rewind the clock.
        </p>
      )}
    </section>
  );
}
