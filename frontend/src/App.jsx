import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api.js';
import { fmtDemo, fmtDemoDate, fmtNum, fmtSystem } from './format.js';
import LotScanner from './components/LotScanner.jsx';
import EscrowLedger from './components/EscrowLedger.jsx';
import WardIntegrity from './components/WardIntegrity.jsx';

// Simulated demo clock: starts on the last day of the synthetic log window (2026-07-06..08-02), so
// "today" matches the audit window and scanned lots fall inside the audited period. Real wall-clock
// time is only used for event logs (labelled "system time").
const DEMO_START = '2026-08-02T10:00:00+05:30';
// The documented demo target (decisions.md D14): the largest synthetic ward.
const DEMO_WARD = 'SYN-W27';
const CLOCK_KEY = 'chhanni.demoNow';

function loadClock() {
  try {
    return localStorage.getItem(CLOCK_KEY) || DEMO_START;
  } catch {
    return DEMO_START;
  }
}

function saveClock(iso) {
  try {
    localStorage.setItem(CLOCK_KEY, iso);
  } catch {
    // private mode etc.: the clock just won't survive a reload
  }
}

export default function App() {
  const [meta, setMeta] = useState(null);
  const [wards, setWards] = useState([]);
  const [credits, setCredits] = useState([]);
  const [audit, setAudit] = useState(null);
  const [demoNow, setDemoNow] = useState(loadClock);
  const [activity, setActivity] = useState([]);
  const [busy, setBusy] = useState(false);
  const [fatal, setFatal] = useState(null);
  // Bumped on Reset demo so the scanner, tamper controls and ledger return to their defaults
  // (credit ids restart at 1 after a reset, so stale UI state would attach to new credits).
  const [resetKey, setResetKey] = useState(0);

  const log = useCallback((text, tone = 'info') => {
    setActivity((a) => [{ id: Date.now() + Math.random(), at: new Date(), text, tone }, ...a].slice(0, 30));
  }, []);

  // Overlapping reloads can finish out of order; only the latest one may write state.
  const reloadSeq = useRef(0);
  const reload = useCallback(async () => {
    const seq = ++reloadSeq.current;
    try {
      const [m, w, c, a] = await Promise.all([api.meta(), api.wards(), api.credits(), api.latestAudit()]);
      if (seq !== reloadSeq.current) return;
      // A fresh dataset (e.g. reseeded from the CLI) with an advanced clock would silently break
      // beat 2, so rewind the clock whenever there are no credits and no audits yet.
      if (c.credits.length === 0 && a === null) {
        setDemoNow((now) => {
          if (now !== DEMO_START) saveClock(DEMO_START);
          return DEMO_START;
        });
      }
      setMeta(m);
      setWards(w.wards);
      setCredits(c.credits);
      setAudit(a);
      setFatal(null);
    } catch (e) {
      if (seq === reloadSeq.current) setFatal(`Backend unreachable or failing: ${e.message}`);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const defaultWard = wards.some((w) => w.ward_code === DEMO_WARD) ? DEMO_WARD : wards[0]?.ward_code;

  // IST calendar date of the demo clock vs the last synthetic log date.
  const demoDay = new Date(new Date(demoNow).getTime() + 5.5 * 3600000).toISOString().slice(0, 10);
  const logEnd = meta?.logWindow?.end;
  const outsideWindow = logEnd && demoDay > logEnd ? `log ends ${fmtDemoDate(`${logEnd}T12:00:00+05:30`)}` : null;

  // Audits and flag decisions change what may mature or trade, so settle escrow at the current demo time.
  async function settle() {
    const r = await api.escrowCycle(demoNow);
    if (r.matured.length || r.released.length) {
      log(`Escrow cycle at ${fmtDemo(demoNow)}: ${r.matured.length} matured, ${r.released.length} became tradable.`);
    }
  }

  async function act(fn) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      log(e.message, 'error');
    } finally {
      setBusy(false);
      await reload();
    }
  }

  const advance = (days) => act(async () => {
    const next = new Date(new Date(demoNow).getTime() + days * 86400000).toISOString();
    setDemoNow(next);
    saveClock(next);
    const r = await api.escrowCycle(next);
    log(`Clock +${days} day${days === 1 ? '' : 's'} → ${fmtDemo(next)}. Escrow cycle: ${r.matured.length} matured, ${r.released.length} became tradable.`);
  });

  const runAudit = () => act(async () => {
    log('Running audit over the 28-day ward log…');
    const r = await api.runAudit();
    log(`Audit run #${r.runId}: ${r.wardsScored} wards scored, flagged: ${r.flaggedWards.join(', ') || 'none'}.`,
      r.flaggedWards.length ? 'warn' : 'info');
    await settle();
  });

  const inject = (body) => act(async () => {
    const r = await api.inject(body);
    const s = r.sizing;
    log(`Injected ${r.inserted} ${body.kind === 'round' ? 'rounded' : 'fabricated'} phantom entries into ${r.wardCode} `
      + `(${fmtNum(s.fraction * 100, 1)}% of its ${fmtNum(s.target_ward_entries, 0)} entries, ${s.fraction_source} size). Run the audit next.`, 'warn');
  });

  const flagAction = (action, flag) => act(async () => {
    if (action === 'inspect') {
      await api.inspectFlag(flag.id);
      log(`Flag #${flag.id}: inspection started. Covered credits stay held.`);
    } else if (action === 'confirm') {
      const r = await api.confirmFlag(flag.id);
      log(`Flag #${flag.id} confirmed on inspection: ${r.clawedBack.length} credit${r.clawedBack.length === 1 ? '' : 's'} clawed back${r.clawedBack.length ? ` (#${r.clawedBack.join(', #')})` : ''}.`, 'error');
    } else {
      await api.clearFlag(flag.id);
      log(`Flag #${flag.id} cleared: data found genuine, hold lifted unless another flag covers it.`);
      await settle();
    }
  });

  const reset = () => {
    if (!window.confirm('Reset the demo? This wipes all lots, credits and audits and reloads the clean synthetic dataset.')) return;
    act(async () => {
      log('Resetting demo data…');
      const r = await api.reset();
      setDemoNow(DEMO_START);
      saveClock(DEMO_START);
      setActivity([]);
      setResetKey((k) => k + 1);
      log(`Demo reset: ${r.wards} synthetic wards, ${fmtNum(r.logs, 0)} trip log entries (seed ${r.seed}).`);
    });
  };

  return (
    <div className="page">
      <header className="top">
        <div>
          <h1>Chhanni</h1>
          <p className="subtitle">Quality-weighted recycling credits with a statistical audit layer · Team TREX</p>
        </div>
        <div className="clock">
          <span className="clock-label">Demo clock (simulated)</span>
          <span className="clock-value">{fmtDemo(demoNow)}</span>
        </div>
        <button onClick={reset} disabled={busy}>Reset demo</button>
      </header>

      <div className="banner" role="note">
        {meta?.dataNotice ?? 'SYNTHETIC DATA: all wards, logs and lots are synthetic. No live MCD data.'}
        {' '}Payments, if shown, are simulated.
      </div>
      {fatal && <div className="banner banner-error" role="alert">{fatal}</div>}

      <main className="grid">
        <div className="col">
          <LotScanner key={`scanner-${resetKey}`} wards={wards} meta={meta} demoNow={demoNow} defaultWard={defaultWard} onScanned={reload} log={log}
            appBusy={busy} outsideWindow={outsideWindow} />
          <EscrowLedger key={`ledger-${resetKey}`} credits={credits} meta={meta} demoNow={demoNow} onCycle={advance} busy={busy} />
          <section className="card" aria-live="polite">
            <header className="card-head"><h2>Activity</h2></header>
            {activity.length === 0 ? <p className="muted">Actions you take appear here.</p> : (
              <ul className="activity">
                {activity.map((a) => (
                  <li key={a.id} className={`act act-${a.tone}`}>
                    <span className="fine">{fmtSystem(a.at)}</span> {a.text}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
        <div className="col">
          <WardIntegrity key={`wards-${resetKey}`} audit={audit} wards={wards} defaultWard={defaultWard} busy={busy}
            onRunAudit={runAudit} onInject={inject} onFlagAction={flagAction} />
        </div>
      </main>
    </div>
  );
}
