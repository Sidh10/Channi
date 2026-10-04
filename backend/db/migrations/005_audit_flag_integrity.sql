-- 005: audit flags decide which credits are held (decisions.md D17), so guard them like credits (D9):
--   * every flag has a bounded period (no NULL = "holds everything in the ward forever")
--   * status only moves forward: open -> under_inspection -> confirmed | cleared (open may skip straight
--     to confirmed or cleared); new flags start open
--   * apart from status, a flag is immutable, and flags can't be deleted (deleting one would silently
--     lift a hold). TRUNCATE / db:reset still work for local demo reruns.
ALTER TABLE audit_flags
  ALTER COLUMN period_start SET NOT NULL,
  ALTER COLUMN period_end SET NOT NULL,
  ADD CONSTRAINT audit_flags_period_order CHECK (period_start <= period_end);

CREATE FUNCTION audit_flags_validate_change() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'open' THEN
      RAISE EXCEPTION 'audit flags must be created open (got %)', NEW.status
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit flags cannot be deleted (flag %)', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;

  IF (NEW.id, NEW.audit_run_id, NEW.ward_id, NEW.check_type, NEW.log_entry_id,
      NEW.period_start, NEW.period_end, NEW.score, NEW.details, NEW.created_at)
     IS DISTINCT FROM
     (OLD.id, OLD.audit_run_id, OLD.ward_id, OLD.check_type, OLD.log_entry_id,
      OLD.period_start, OLD.period_end, OLD.score, OLD.details, OLD.created_at) THEN
    RAISE EXCEPTION 'audit flag % is immutable apart from its status', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status <> OLD.status AND NOT (
       (OLD.status = 'open' AND NEW.status IN ('under_inspection', 'confirmed', 'cleared'))
    OR (OLD.status = 'under_inspection' AND NEW.status IN ('confirmed', 'cleared'))
  ) THEN
    RAISE EXCEPTION 'illegal audit flag status transition: % -> %', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_flags_validate_change
  BEFORE INSERT OR UPDATE OR DELETE ON audit_flags
  FOR EACH ROW EXECUTE FUNCTION audit_flags_validate_change();
