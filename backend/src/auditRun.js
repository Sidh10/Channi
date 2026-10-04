// Runs the Python audit over the ward log and records the result (decisions.md D13, D17, D18).
// Scores, flags and finished_at are written in ONE transaction, so escrow never sees a finished
// run without its flags. Only ward-level findings become holding flags; the engine puts
// localisation hints in each flag's details.
import { callEngine } from './auditEngine.js';
import { withTransaction } from './db.js';
import { ValidationError, parseIsoDate } from './errors.js';

export async function runAudit(db, { periodStart = null, periodEnd = null, config } = {}) {
  periodStart = parseIsoDate(periodStart, 'periodStart');
  periodEnd = parseIsoDate(periodEnd, 'periodEnd');
  if (periodStart && periodEnd && periodStart > periodEnd) {
    throw new ValidationError(`periodStart ${periodStart} is after periodEnd ${periodEnd}`);
  }
  const startedAt = new Date();
  const wards = await db.query(
    `SELECT ward_code, population, income_proxy::float8 AS income_proxy,
            sanitation_proxy::float8 AS sanitation_proxy, data_origin
     FROM wards ORDER BY ward_code`
  );
  // Deliberately no is_injected_tamper: ground truth never reaches the engine (D10, D12).
  const logs = await db.query(
    `SELECT l.id AS entry_id, w.ward_code, to_char(l.entry_date, 'YYYY-MM-DD') AS entry_date,
            l.tonnage::float8 AS tonnage, l.data_origin
     FROM ward_tonnage_logs l JOIN wards w ON w.id = l.ward_id
     WHERE ($1::date IS NULL OR l.entry_date >= $1::date) AND ($2::date IS NULL OR l.entry_date <= $2::date)
     ORDER BY l.id`,
    [periodStart, periodEnd]
  );
  if (logs.rowCount === 0) throw new ValidationError('no ward log entries in the requested period');

  const request = { wards: wards.rows, logs: logs.rows, period_start: periodStart, period_end: periodEnd };
  if (config) request.config = config;
  const result = await callEngine('audit', request);
  const { run, ward_scores: scores, flags } = result.db;

  return withTransaction(async (client) => {
    const ids = new Map(
      (await client.query('SELECT id, ward_code FROM wards')).rows.map((r) => [r.ward_code, r.id])
    );
    const wardId = (code) => {
      const id = ids.get(code);
      if (id === undefined) throw new Error(`engine returned unknown ward_code ${code}`);
      return id;
    };

    const runRow = await client.query(
      `INSERT INTO audit_runs (engine_version, params, summary, period_start, period_end, started_at)
       VALUES ($1, $2, $3, $4, $5, $6) RETURNING id`,
      [run.engine_version, run.params, run.summary, run.period_start, run.period_end, startedAt]
    );
    const runId = runRow.rows[0].id;

    for (const s of scores) {
      await client.query(
        `INSERT INTO ward_audit_scores (audit_run_id, ward_id, confidence_score, flagged, metrics)
         VALUES ($1, $2, $3, $4, $5)`,
        [runId, wardId(s.ward_code), s.confidence_score, s.flagged, s.metrics]
      );
    }

    const flagged = new Set(scores.filter((s) => s.flagged).map((s) => s.ward_code));
    const withFlag = new Set(flags.map((f) => f.ward_code));
    for (const code of flagged) {
      if (!withFlag.has(code)) throw new Error(`engine flagged ward ${code} but returned no flag row for it`);
    }
    const flagIds = [];
    for (const f of flags) {
      const { rows } = await client.query(
        `INSERT INTO audit_flags (audit_run_id, ward_id, check_type, period_start, period_end, score, details)
         VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id`,
        [runId, wardId(f.ward_code), f.check_type, f.period_start, f.period_end, f.score, f.details]
      );
      flagIds.push(rows[0].id);
    }

    await client.query('UPDATE audit_runs SET finished_at = now() WHERE id = $1', [runId]);
    return {
      runId,
      periodStart: run.period_start,
      periodEnd: run.period_end,
      wardsScored: scores.length,
      flaggedWards: [...flagged].sort(),
      flagIds,
      dataNotice: result.data_notice,
    };
  }, db);
}

// Ward integrity view: the latest finished run's wards ranked lowest confidence first, with flags.
export async function latestAudit(db) {
  const run = await db.query(
    `SELECT id, engine_version, period_start, period_end, started_at, finished_at, summary
     FROM audit_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1`
  );
  if (run.rowCount === 0) return null;
  const r = run.rows[0];
  const wards = await db.query(
    `SELECT w.id AS ward_id, w.ward_code, w.name, w.zone, w.data_origin,
            s.confidence_score::float8 AS confidence_score, s.flagged, s.metrics,
            COALESCE(json_agg(json_build_object(
              'id', f.id, 'check_type', f.check_type, 'status', f.status, 'score', f.score::float8,
              'period_start', to_char(f.period_start, 'YYYY-MM-DD'), 'period_end', to_char(f.period_end, 'YYYY-MM-DD'),
              'created_at', f.created_at,
              'hints', CASE WHEN jsonb_typeof(f.details->'localisation_hints') = 'object' THEN jsonb_build_object(
                'level', f.details->'localisation_hints'->>'level',
                'n', (f.details->'localisation_hints'->>'n')::int,
                'multiple_of_t', f.details->'localisation_hints'->'multiple_of_t') END
              ) ORDER BY f.id) FILTER (WHERE f.id IS NOT NULL), '[]') AS flags
     FROM ward_audit_scores s
     JOIN wards w ON w.id = s.ward_id
     LEFT JOIN audit_flags f ON f.audit_run_id = s.audit_run_id AND f.ward_id = s.ward_id
     WHERE s.audit_run_id = $1
     GROUP BY w.id, s.id
     -- confidence_score is stored at 4 dp, so strongly flagged wards all tie at 0; rank them by
     -- their smallest flag q at full precision instead.
     ORDER BY COALESCE(min(f.score), s.confidence_score) ASC, w.ward_code`,
    [r.id]
  );
  return {
    run: {
      id: r.id, engineVersion: r.engine_version,
      periodStart: r.period_start, periodEnd: r.period_end,
      startedAt: r.started_at, finishedAt: r.finished_at, summary: r.summary,
    },
    wards: wards.rows,
  };
}
