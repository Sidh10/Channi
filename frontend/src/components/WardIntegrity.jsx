import { useState } from 'react';
import { CHECK_LABEL, FLAG_STATUS_LABEL, fmtDemoDate, fmtQ } from '../format.js';

const KIND_LABEL = { round: 'Rounded phantom entries', fabricate: 'Fabricated phantom entries' };
const PLACEMENT_LABEL = { spread: 'spread over the window', last_day: 'all on the last day' };

// Strongest evidence for a ward: its smallest q. Flag scores keep full precision; the stored
// confidence_score is rounded to 4 dp (so strong flags read 0 there).
function wardQ(w) {
  const qs = w.flags.map((f) => f.score).filter((q) => q !== null && q !== undefined);
  return qs.length ? Math.min(...qs) : w.confidence_score;
}

function Hints({ hints }) {
  if (!hints?.n) return null;
  const what = hints.level === 'entry'
    ? `${hints.n} entries worth inspecting first (exact multiples of ${hints.multiple_of_t} t)`
    : `${hints.n} day${hints.n === 1 ? '' : 's'} worth inspecting first`;
  return <div className="fine">{what}. May include genuine values and miss tampered ones, so this is not a list of tampered entries.</div>;
}

function FlagRow({ flag, onAction, busy }) {
  const active = flag.status === 'open' || flag.status === 'under_inspection';
  return (
    <div className={`flag flag-${flag.status}`}>
      <div className="flag-line">
        <strong>#{flag.id} {CHECK_LABEL[flag.check_type] ?? flag.check_type}</strong>
        <span className="mono">q = {fmtQ(flag.score)}</span>
        <span className={`fstatus fstatus-${flag.status}`}>{FLAG_STATUS_LABEL[flag.status]}</span>
      </div>
      <Hints hints={flag.hints} />
      {active && (
        <div className="flag-actions">
          {flag.status === 'open' && <button onClick={() => onAction('inspect', flag)} disabled={busy}>Start inspection</button>}
          <button className="danger" onClick={() => onAction('confirm', flag)} disabled={busy}>Confirm → claw back credits</button>
          <button onClick={() => onAction('clear', flag)} disabled={busy}>Clear (data genuine)</button>
        </div>
      )}
    </div>
  );
}

export default function WardIntegrity({ audit, wards, defaultWard, busy, onRunAudit, onInject, onFlagAction }) {
  const [wardCode, setWardCode] = useState(defaultWard);
  const [kind, setKind] = useState('round');
  const [placement, setPlacement] = useState('spread');
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const target = wardCode ?? defaultWard;

  const rows = audit ? audit.wards.filter((w) => !flaggedOnly || w.flagged) : [];
  const nFlagged = audit ? audit.wards.filter((w) => w.flagged).length : 0;

  return (
    <section className="card card-tall">
      <header className="card-head">
        <h2><span className="step">2</span> Audit the ward logs</h2>
        <button className="primary" onClick={onRunAudit} disabled={busy}>{busy ? 'Working…' : 'Run audit'}</button>
      </header>

      <div className="inject">
        <span className="inject-title">Demo tampering:</span>
        <select value={target ?? ''} onChange={(e) => setWardCode(e.target.value)} aria-label="Ward to tamper">
          {wards.map((w) => <option key={w.ward_code} value={w.ward_code}>{w.ward_code}</option>)}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Tamper kind">
          {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={placement} onChange={(e) => setPlacement(e.target.value)} aria-label="Placement">
          {Object.entries(PLACEMENT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <button onClick={() => onInject({ wardCode: target, kind, placement })} disabled={busy || !target}>Inject into log</button>
      </div>
      <p className="fine">
        Injection appends synthetic phantom entries to the ward's trip log, at the default size validated on held-out runs.
        The audit never sees which entries were injected.
      </p>

      {!audit ? (
        <p className="muted">No audit run yet. Run the audit to score every ward.</p>
      ) : (
        <>
          <div className="audit-summary">
            <span>Audit run #{audit.run.id} · window {fmtDemoDate(audit.run.periodStart)} – {fmtDemoDate(audit.run.periodEnd)}</span>
            <span className={nFlagged ? 'flag-count hot' : 'flag-count'}>{nFlagged} of {audit.wards.length} wards flagged</span>
            <label className="check"><input type="checkbox" checked={flaggedOnly} onChange={(e) => setFlaggedOnly(e.target.checked)} /> flagged only</label>
          </div>
          <div className="table-scroll tall">
            <table>
              <thead>
                <tr><th>Rank</th><th>Ward</th><th>Zone</th><th className="num">Strongest evidence (q)</th><th>Result</th></tr>
              </thead>
              <tbody>
                {rows.map((w) => (
                  <tr key={w.ward_code} className={w.flagged ? 'row-flagged' : ''}>
                    <td>{audit.wards.indexOf(w) + 1}</td>
                    <td className="nowrap" title={w.name}><strong>{w.ward_code}</strong></td>
                    <td className="nowrap">{w.zone}</td>
                    <td className="num mono">{fmtQ(wardQ(w))}</td>
                    <td>
                      {w.flagged ? (
                        <div className="flags">
                          {w.flags.map((f) => <FlagRow key={f.id} flag={f} onAction={onFlagAction} busy={busy} />)}
                        </div>
                      ) : <span className="ok">No anomaly at q ≤ 0.01</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="fine">
            q = Benjamini–Hochberg-adjusted p-value, the smallest across the ward's four checks (Benford first digit,
            round-number excess, socio-economic baseline, single-day spikes). Lower = stronger evidence of an anomaly; a ward is
            flagged at q ≤ 0.01. It ranks evidence. It is not a probability that the data is genuine. On synthetic data these checks caught
            rounded entries (≥ 1.5% of a ward's entries) and fabricated entries when numerous (≥ 30%) or bunched on one day (≥ 5%);
            only gross over-reporting is caught, and moderate over-reporting is not reliably detected.
          </p>
        </>
      )}
    </section>
  );
}
