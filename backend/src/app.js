import express from 'express';
import { pool } from './db.js';
import { quoteLot, submitLot } from './lots.js';
import { listCredits, runEscrowCycle, startInspection, confirmFlag, clearFlag } from './escrow.js';
import { runAudit, latestAudit } from './auditRun.js';
import { injectTamper, seedSynthetic } from './syntheticData.js';
import { callEngine, EngineError } from './auditEngine.js';
import { SOURCE_LABEL, THRESHOLDS } from './banding.js';
import { QUALITY_FACTORS, CREDIT_UNIT, PRICING_VERSION } from './pricing.js';
import { ESCROW_HOLD_DAYS } from './escrow.js';
import { NotFoundError, parseId, parseTimestamp, rejectNul } from './errors.js';
import { STUB_RATE_INR_PER_CREDIT, STUB_LABEL } from './payouts.js';

// Shown with any synthetic data the API returns (decisions.md D5).
export const DATA_NOTICE =
  'SYNTHETIC DATA: all wards, logs and lots are synthetic (docs/decisions.md D5). No live MCD data.';

// Every placeholder parameter the UI may display, with how it must be labelled (D15, D16, D17).
export const PLACEHOLDERS = {
  bandingSource: SOURCE_LABEL,
  bandCutoffsPct: { ...THRESHOLDS, label: 'placeholder cut-offs, not a standard' },
  qualityFactors: { ...QUALITY_FACTORS, label: 'placeholder factors, not market prices' },
  creditUnit: { value: CREDIT_UNIT, label: 'placeholder unit (quality-weighted kg), not a traded or regulatory unit' },
  pricingVersion: PRICING_VERSION,
  escrowHoldDays: { value: ESCROW_HOLD_DAYS, label: 'placeholder hold, not a regulatory figure' },
  stubPayoutRate: { value: STUB_RATE_INR_PER_CREDIT, unit: 'INR per credit', label: `${STUB_LABEL}; not a market price or subsidy rate` },
};

const wrap = (fn) => (req, res, next) => Promise.resolve(fn(req, res)).catch(next);

const flagId = (req) => parseId(req.params.id, 'flag id');

export function createApp({ db = pool } = {}) {
  const app = express();
  app.use(express.json({ limit: '1mb' }));
  app.use((req, _res, next) => {
    rejectNul(req.body, 'request body');
    rejectNul(req.query, 'query string');
    next();
  });

  app.get('/api/health', async (_req, res) => {
    try {
      await db.query('SELECT 1');
      res.json({ ok: true, db: 'up' });
    } catch (err) {
      res.status(503).json({ ok: false, db: 'down', error: err.message });
    }
  });

  app.get('/api/engine', wrap(async (_req, res) => res.json(await callEngine('version'))));

  app.get('/api/meta', wrap(async (_req, res) => {
    const { rows } = await db.query(
      `SELECT to_char(min(entry_date), 'YYYY-MM-DD') AS start, to_char(max(entry_date), 'YYYY-MM-DD') AS end
       FROM ward_tonnage_logs`
    );
    res.json({ dataNotice: DATA_NOTICE, placeholders: PLACEHOLDERS, logWindow: rows[0] });
  }));

  // Beat 1: live credit value for a simulated scan — no database write.
  app.post('/api/quote', (req, res) => {
    const { weightKg, contaminationPct } = req.body ?? {};
    res.json(quoteLot({ weightKg, contaminationPct }));
  });

  app.get('/api/wards', wrap(async (_req, res) => {
    const { rows } = await db.query(
      `SELECT ward_code, name, zone, data_origin FROM wards ORDER BY ward_code`
    );
    res.json({ dataNotice: DATA_NOTICE, wards: rows });
  }));

  app.post('/api/lots', wrap(async (req, res) => {
    const { wardCode, weightKg, contaminationPct, scannedAt, collectorRef } = req.body ?? {};
    res.status(201).json(await submitLot(db, { wardCode, weightKg, contaminationPct, scannedAt, collectorRef }));
  }));

  app.get('/api/credits', wrap(async (_req, res) => {
    res.json({ dataNotice: DATA_NOTICE, credits: await listCredits(db) });
  }));

  app.get('/api/credits/:id/events', wrap(async (req, res) => {
    const id = parseId(req.params.id, 'credit id');
    const credit = await db.query('SELECT 1 FROM credits WHERE id = $1', [id]);
    if (credit.rowCount === 0) throw new NotFoundError(`credit ${id} not found`);
    const { rows } = await db.query(
      `SELECT id, from_state, to_state, reason, audit_flag_id, created_at
       FROM credit_events WHERE credit_id = $1 ORDER BY id`,
      [id]
    );
    res.json({ events: rows });
  }));

  // Escrow transitions driven by time; `now` lets the demo use a simulated clock (Phase 6).
  app.post('/api/escrow/cycle', wrap(async (req, res) => {
    const now = req.body?.now === undefined ? new Date() : parseTimestamp(req.body.now, 'now');
    res.json(await runEscrowCycle(db, now));
  }));

  app.post('/api/audit/run', wrap(async (req, res) => {
    const { periodStart = null, periodEnd = null } = req.body ?? {};
    res.status(201).json(await runAudit(db, { periodStart, periodEnd }));
  }));

  app.get('/api/audit/latest', wrap(async (_req, res) => {
    const latest = await latestAudit(db);
    if (!latest) return res.status(404).json({ error: 'no finished audit run yet' });
    res.json({ dataNotice: DATA_NOTICE, ...latest });
  }));

  app.post('/api/flags/:id/inspect', wrap(async (req, res) => res.json(await startInspection(db, flagId(req)))));
  app.post('/api/flags/:id/confirm', wrap(async (req, res) => res.json(await confirmFlag(db, flagId(req)))));
  app.post('/api/flags/:id/clear', wrap(async (req, res) => res.json(await clearFlag(db, flagId(req)))));

  // Beat 2: append engine-generated rounded/fabricated entries to one ward's log.
  app.post('/api/demo/inject', wrap(async (req, res) => {
    const { wardCode, kind, placement, fraction, seed } = req.body ?? {};
    res.status(201).json(await injectTamper(db, { wardCode, kind, placement, fraction, seed }));
  }));

  // Restart the demo from the clean seed-2026 synthetic dataset (TRUNCATE + reseed; D9 allows this).
  app.post('/api/demo/reset', wrap(async (_req, res) => {
    const r = await seedSynthetic(db, { fresh: true });
    res.json({ wards: r.wards, logs: r.logs, seed: r.seed, dataNotice: r.dataNotice });
  }));

  app.use('/api', (req, res) => res.status(404).json({ error: `no route ${req.method} ${req.originalUrl}` }));

  // Map errors by type, never by message text. Typed errors (errors.js) and body-parser errors carry
  // a 4xx status; engine input errors are the caller's fault (400), other engine failures are 502;
  // anything else is a server bug (500).
  app.use((err, _req, res, _next) => {
    const status = err.status ?? err.statusCode;
    if (Number.isInteger(status) && status >= 400 && status < 500) {
      return res.status(status).json({ error: err.message });
    }
    if (err instanceof EngineError) {
      return res.status(err.errorType === 'invalid_input' ? 400 : 502).json({ error: err.message });
    }
    console.error(err);
    res.status(500).json({ error: 'internal server error', detail: err.message });
  });

  return app;
}
