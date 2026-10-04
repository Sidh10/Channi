-- 001_init: core schema for the Chhanni demo.
-- All ward/lot data in this database is synthetic unless data_origin = 'public'
-- (docs/decisions.md D5). Nothing here represents live MCD data.

-- ---------------------------------------------------------------------------
-- Wards and their reported tonnage logs (the data the audit engine checks)
-- ---------------------------------------------------------------------------

CREATE TABLE wards (
  id               SERIAL PRIMARY KEY,
  ward_code        TEXT NOT NULL UNIQUE,
  name             TEXT NOT NULL,
  zone             TEXT,
  -- Socio-economic proxies feeding the baseline model (SPEC.md item 3).
  -- For synthetic wards these are generated values, not census figures.
  population       INTEGER CHECK (population > 0),
  income_proxy     NUMERIC(6,4) CHECK (income_proxy BETWEEN 0 AND 1),
  sanitation_proxy NUMERIC(6,4) CHECK (sanitation_proxy BETWEEN 0 AND 1),
  data_origin      TEXT NOT NULL DEFAULT 'synthetic' CHECK (data_origin IN ('synthetic', 'public')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reported waste tonnage entries per ward. Multiple entries per day are
-- allowed (e.g. per-trip logs, or duplicate submissions — itself a tamper signal).
CREATE TABLE ward_tonnage_logs (
  id                 BIGSERIAL PRIMARY KEY,
  ward_id            INTEGER NOT NULL REFERENCES wards(id) ON DELETE CASCADE,
  entry_date         DATE NOT NULL,
  tonnage            NUMERIC(12,3) NOT NULL CHECK (tonnage >= 0),
  data_origin        TEXT NOT NULL DEFAULT 'synthetic' CHECK (data_origin IN ('synthetic', 'public')),
  -- Ground-truth label for evaluating detection on synthetic data ONLY.
  -- The audit engine must never read this column when deciding what to flag.
  is_injected_tamper BOOLEAN NOT NULL DEFAULT false,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ward_tonnage_logs_ward_date_idx ON ward_tonnage_logs (ward_id, entry_date);

-- ---------------------------------------------------------------------------
-- Lots and credits (escrow state machine — decisions.md D3)
-- ---------------------------------------------------------------------------

CREATE TYPE contamination_band AS ENUM ('clean', 'mixed', 'heavily_contaminated');

CREATE TABLE lots (
  id            SERIAL PRIMARY KEY,
  lot_code      TEXT NOT NULL UNIQUE,
  ward_id       INTEGER NOT NULL REFERENCES wards(id),
  weight_kg     NUMERIC(10,2) NOT NULL CHECK (weight_kg > 0),
  band          contamination_band NOT NULL,
  -- Where the band came from, so the UI can label mock vs model output (D2).
  band_source   TEXT NOT NULL CHECK (band_source IN ('mock_rule_based', 'trained_model', 'manual')),
  collector_ref TEXT,
  data_origin   TEXT NOT NULL DEFAULT 'synthetic' CHECK (data_origin IN ('synthetic', 'public')),
  scanned_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX lots_ward_idx ON lots (ward_id, scanned_at);

-- ---------------------------------------------------------------------------
-- Audit runs, per-ward scores, and flags (statistical audit layer)
-- ---------------------------------------------------------------------------

CREATE TABLE audit_runs (
  id             SERIAL PRIMARY KEY,
  engine_version TEXT NOT NULL,
  params         JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary        JSONB NOT NULL DEFAULT '{}'::jsonb,
  started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at    TIMESTAMPTZ
);

-- One row per ward per audit run; drives the ward integrity ranking (SPEC.md item 5).
-- confidence_score: 1 = data looks consistent, 0 = strongly anomalous.
CREATE TABLE ward_audit_scores (
  id               SERIAL PRIMARY KEY,
  audit_run_id     INTEGER NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
  ward_id          INTEGER NOT NULL REFERENCES wards(id) ON DELETE CASCADE,
  confidence_score NUMERIC(5,4) NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
  flagged          BOOLEAN NOT NULL DEFAULT false,
  metrics          JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (audit_run_id, ward_id)
);

CREATE TABLE audit_flags (
  id           SERIAL PRIMARY KEY,
  audit_run_id INTEGER NOT NULL REFERENCES audit_runs(id) ON DELETE CASCADE,
  ward_id      INTEGER NOT NULL REFERENCES wards(id) ON DELETE CASCADE,
  -- e.g. 'benford', 'baseline'. TEXT rather than an enum so new checks don't need a migration.
  check_type   TEXT NOT NULL,
  -- Set for entry-level flags (e.g. one log entry outside the baseline range); NULL for ward-level flags.
  log_entry_id BIGINT REFERENCES ward_tonnage_logs(id) ON DELETE CASCADE,
  period_start DATE,
  period_end   DATE,
  score        NUMERIC,
  details      JSONB NOT NULL DEFAULT '{}'::jsonb,
  status       TEXT NOT NULL DEFAULT 'open'
               CHECK (status IN ('open', 'under_inspection', 'cleared', 'confirmed')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_flags_ward_idx ON audit_flags (ward_id, status);

-- ---------------------------------------------------------------------------
-- Credits + escrow
-- ---------------------------------------------------------------------------

CREATE TYPE credit_state AS ENUM ('pending', 'matured', 'tradable', 'clawed_back');

CREATE TABLE credits (
  id               SERIAL PRIMARY KEY,
  lot_id           INTEGER NOT NULL UNIQUE REFERENCES lots(id),
  credit_value     NUMERIC(14,4) NOT NULL CHECK (credit_value >= 0),
  pricing_version  TEXT NOT NULL,
  state            credit_state NOT NULL DEFAULT 'pending',
  state_reason     TEXT,
  -- Every clawback points at the audit flag that caused it, and only clawbacks carry a flag
  -- (D3: clawback only on a failed audit).
  clawback_flag_id INTEGER REFERENCES audit_flags(id),
  matures_at       TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  state_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT clawback_requires_flag CHECK ((state = 'clawed_back') = (clawback_flag_id IS NOT NULL))
);
CREATE INDEX credits_state_idx ON credits (state);

-- Append-only history of every state a credit has been in (drives the visible
-- escrow timeline in the dashboard). Rows are written only by the credits trigger
-- and can never be updated or deleted.
CREATE TABLE credit_events (
  id            BIGSERIAL PRIMARY KEY,
  credit_id     INTEGER NOT NULL REFERENCES credits(id) ON DELETE RESTRICT,
  from_state    credit_state,
  to_state      credit_state NOT NULL,
  reason        TEXT,
  audit_flag_id INTEGER REFERENCES audit_flags(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX credit_events_credit_idx ON credit_events (credit_id, created_at);

-- Escrow rules (decisions.md D3, D9):
--   pending -> matured -> tradable
--   pending -> clawed_back, matured -> clawed_back
--   tradable and clawed_back are terminal: no further updates of any kind.
--   lot, value and pricing version are fixed at creation.
--   reason and clawback flag only change as part of a state transition (so the row
--   and its credit_events history can't disagree). Only matures_at may change in place.
--   credits are never deleted.
-- A whole-database reset (TRUNCATE / npm run db:reset) is still possible for local demo reruns;
-- these rules guard against row-level edits, they are not a security boundary against a DB superuser.
CREATE FUNCTION credits_validate_change() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.state <> 'pending' THEN
      RAISE EXCEPTION 'credits must be created in pending state (got %)', NEW.state
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'credits cannot be deleted (credit %); use an escrow state transition', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;

  IF OLD.state IN ('tradable', 'clawed_back') THEN
    RAISE EXCEPTION 'credit % is in terminal state % and cannot be changed', OLD.id, OLD.state
      USING ERRCODE = 'check_violation';
  END IF;

  IF (NEW.id, NEW.lot_id, NEW.credit_value, NEW.pricing_version, NEW.created_at)
     IS DISTINCT FROM (OLD.id, OLD.lot_id, OLD.credit_value, OLD.pricing_version, OLD.created_at) THEN
    RAISE EXCEPTION 'credit % lot, value and pricing version are fixed at creation', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.state = OLD.state THEN
    IF (NEW.state_reason, NEW.clawback_flag_id, NEW.state_changed_at)
       IS DISTINCT FROM (OLD.state_reason, OLD.clawback_flag_id, OLD.state_changed_at) THEN
      RAISE EXCEPTION 'credit % reason/clawback flag can only change with a state transition', OLD.id
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF NOT (
       (OLD.state = 'pending' AND NEW.state IN ('matured', 'clawed_back'))
    OR (OLD.state = 'matured' AND NEW.state IN ('tradable', 'clawed_back'))
  ) THEN
    RAISE EXCEPTION 'illegal credit state transition: % -> %', OLD.state, NEW.state
      USING ERRCODE = 'check_violation';
  END IF;

  NEW.state_changed_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER credits_validate_change
  BEFORE INSERT OR UPDATE OR DELETE ON credits
  FOR EACH ROW EXECUTE FUNCTION credits_validate_change();

CREATE FUNCTION credits_log_transition() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    INSERT INTO credit_events (credit_id, from_state, to_state, reason)
    VALUES (NEW.id, NULL, NEW.state, NEW.state_reason);
  ELSIF NEW.state <> OLD.state THEN
    INSERT INTO credit_events (credit_id, from_state, to_state, reason, audit_flag_id)
    VALUES (NEW.id, OLD.state, NEW.state, NEW.state_reason, NEW.clawback_flag_id);
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER credits_log_transition
  AFTER INSERT OR UPDATE OF state ON credits
  FOR EACH ROW EXECUTE FUNCTION credits_log_transition();

-- credit_events: insert only from inside a trigger (pg_trigger_depth() >= 2 — today only the
-- credits trigger inserts here; keep it that way), never update/delete.
CREATE FUNCTION credit_events_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF pg_trigger_depth() < 2 THEN
      RAISE EXCEPTION 'credit_events rows are written only by the credits trigger'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'credit_events is append-only (% rejected)', TG_OP
    USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER credit_events_append_only
  BEFORE INSERT OR UPDATE OR DELETE ON credit_events
  FOR EACH ROW EXECUTE FUNCTION credit_events_append_only();
