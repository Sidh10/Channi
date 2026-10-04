// Typed errors carry their HTTP status, so the API maps errors by type, never by message text.
// Anything untyped is a server bug and becomes a 500.

export class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

// Bad caller input. Extends RangeError so existing callers/tests that expect RangeError still work.
export class ValidationError extends RangeError {
  constructor(message) {
    super(message);
    this.status = 400;
  }
}

export class NotFoundError extends HttpError {
  constructor(message) {
    super(404, message);
  }
}

export class ConflictError extends HttpError {
  constructor(message) {
    super(409, message);
  }
}

const MAX_INT4 = 2147483647;

export function parseId(value, what = 'id') {
  const n = typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : value;
  if (!Number.isInteger(n) || n < 1 || n > MAX_INT4) throw new ValidationError(`invalid ${what} ${value}`);
  return n;
}

// Strict calendar date 'YYYY-MM-DD' (rejects timestamps and impossible dates like 2026-02-30).
export function parseIsoDate(value, what = 'date') {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    throw new ValidationError(`${what} must be a YYYY-MM-DD date (got ${value})`);
  }
  const d = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== value) {
    throw new ValidationError(`${what} is not a real calendar date (got ${value})`);
  }
  const year = d.getUTCFullYear();
  if (year < 2000 || year > 2100) throw new ValidationError(`${what} must be between 2000 and 2100 (got ${value})`);
  return value;
}

// Postgres rejects NUL (U+0000) in text, so refuse it as bad input before it reaches a query.
export function rejectNul(value, where = 'request') {
  if (typeof value === 'string') {
    if (value.includes('\u0000')) throw new ValidationError(`${where} contains a NUL character`);
  } else if (Array.isArray(value)) {
    value.forEach((v) => rejectNul(v, where));
  } else if (value && typeof value === 'object') {
    for (const [k, v] of Object.entries(value)) {
      rejectNul(k, where);
      rejectNul(v, where);
    }
  }
}

// A timestamp within a sane window for this demo (rejects e.g. year 275000).
export function parseTimestamp(value, what = 'timestamp') {
  const d = new Date(value);
  const year = d.getUTCFullYear();
  if (Number.isNaN(d.getTime()) || year < 2000 || year > 2100) {
    throw new ValidationError(`${what} must be a valid time between 2000 and 2100 (got ${value})`);
  }
  return d;
}
