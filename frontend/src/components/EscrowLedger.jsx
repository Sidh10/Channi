import { Fragment, useEffect, useState } from 'react';
import { api } from '../api.js';
import { BAND_LABEL, STATE_LABEL, fmtDemoDate, fmtNum, fmtSystem } from '../format.js';

function Timeline({ credit }) {
  const [events, setEvents] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    let stale = false;
    api.creditEvents(credit.id)
      .then((r) => { if (!stale) setEvents(r.events); })
      .catch((e) => { if (!stale) setError(e.message); });
    return () => { stale = true; };
  }, [credit.id, credit.state, credit.held]);
  if (error) return <p className="error">{error}</p>;
  if (!events) return <p className="muted">Loading history…</p>;
  return (
    <ol className="timeline">
      {events.map((e) => (
        <Fragment key={e.id}>
        {e.to_state === 'clawed_back' && e.audit_flag_id && (
          <li className="tl tl-held">
            <strong>Held</strong> — audit flag #{e.audit_flag_id} was raised on this ward and date, so the credit could not mature or trade while it was inspected
          </li>
        )}
        <li className={`tl tl-${e.to_state}`}>
          <strong>{STATE_LABEL[e.to_state]}</strong> — {e.reason ?? '—'}
          {e.audit_flag_id && <> · flag #{e.audit_flag_id}</>}
          <span className="fine"> (logged {fmtSystem(e.created_at)} system time)</span>
        </li>
        </Fragment>
      ))}
      {credit.held && (
        <li className="tl tl-held">
          <strong>Held</strong> — audit flag{credit.holding_flag_ids.length > 1 ? 's' : ''} #{credit.holding_flag_ids.join(', #')} open or under inspection on this ward and date; can't mature or trade until resolved
          <span className="fine"> (since {fmtSystem(credit.held_since)} system time)</span>
        </li>
      )}
    </ol>
  );
}

export default function EscrowLedger({ credits, meta, demoNow, onCycle, busy }) {
  // Moving time with nothing in escrow does nothing useful, and the app rewinds the clock on a
  // fresh dataset, so keep the time buttons off until a credit exists.
  const timeOff = busy || credits.length === 0;
  const [open, setOpen] = useState(null);
  const hold = meta?.placeholders.escrowHoldDays;

  return (
    <section className="card">
      <header className="card-head">
        <h2>Escrow ledger</h2>
        <div className="head-actions">
          <button onClick={() => onCycle(1)} disabled={timeOff}>+1 day</button>
          <button onClick={() => onCycle(7)} disabled={timeOff}>+7 days</button>
          <button onClick={() => onCycle(30)} disabled={timeOff}>+30 days</button>
        </div>
      </header>
      <p className="fine">
        Credits wait in escrow for {hold?.value} days ({hold?.label}) before maturing, and become tradable only after an
        audit covering the lot's ward and date has run. An open or under-inspection audit flag puts them on hold (badge "Held").
        Time buttons move the <em>simulated</em> demo clock and run the escrow cycle.
      </p>

      {credits.length === 0 ? (
        <p className="muted">No credits yet. Scan a lot to issue one.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr><th>#</th><th>Lot</th><th>Ward</th><th title={meta?.placeholders.bandingSource}>Band (simulated scan)</th><th className="num">Credits</th><th>State</th><th>Matures</th><th><span className="sr-only">Details</span></th></tr>
            </thead>
            <tbody>
              {[...credits].reverse().map((c) => (
                <Fragment key={c.id}>
                  <tr className={c.held ? 'row-held' : c.state === 'clawed_back' ? 'row-clawed' : ''}>
                    <td>{c.id}</td>
                    <td className="mono nowrap" title={c.lot_code}>…{c.lot_code.split('-').at(-1)}</td>
                    <td>{c.ward_code}</td>
                    <td><span className={`band band-${c.band}`} title={c.band_source === 'mock_rule_based' ? meta?.placeholders.bandingSource : c.band_source}>{BAND_LABEL[c.band]}</span></td>
                    <td className="num">{fmtNum(c.credit_value)}</td>
                    <td>
                      <span className={`state state-${c.state}`}>{STATE_LABEL[c.state]}</span>
                      {c.held && <span className="state state-held" title="An open or under-inspection audit flag covers this lot's ward and date">Held · flag #{c.holding_flag_ids.join(', #')}</span>}
                      {c.state === 'clawed_back' && <span className="fine"> flag #{c.clawback_flag_id}</span>}
                    </td>
                    <td className={new Date(c.matures_at) <= new Date(demoNow) ? '' : 'muted'}>{fmtDemoDate(c.matures_at)}</td>
                    <td>
                      <button className="link" onClick={() => setOpen(open === c.id ? null : c.id)} aria-expanded={open === c.id}>
                        {open === c.id ? 'Hide' : 'History'}
                      </button>
                    </td>
                  </tr>
                  {open === c.id && (
                    <tr className="row-detail"><td colSpan={8}><Timeline key={`${c.id}-${c.state}-${c.held}`} credit={c} /></td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
