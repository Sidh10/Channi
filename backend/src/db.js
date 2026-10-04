import pg from 'pg';

// DATE columns come back as 'YYYY-MM-DD' strings (not local-midnight Date objects, which shift
// across time zones), and NUMERIC as JS numbers (every NUMERIC column here fits a double exactly
// enough for display: at most 14 digits, 4 decimals).
pg.types.setTypeParser(1082, (v) => v);
pg.types.setTypeParser(1700, (v) => (v === null ? null : Number(v)));

const DEFAULT_URL = 'postgresql://chhanni:chhanni_dev@localhost:5432/chhanni';

export const databaseUrl = process.env.DATABASE_URL || DEFAULT_URL;

export const pool = new pg.Pool({ connectionString: databaseUrl });

// Runs fn(client) inside BEGIN/COMMIT, rolling back on any error.
export async function withTransaction(fn, db = pool) {
  const client = await db.connect();
  try {
    await client.query('BEGIN');
    const result = await fn(client);
    await client.query('COMMIT');
    return result;
  } catch (err) {
    await client.query('ROLLBACK').catch(() => {});
    throw err;
  } finally {
    client.release();
  }
}
