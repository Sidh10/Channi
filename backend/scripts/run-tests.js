// Runs the backend tests against a separate database (<db>_test, or TEST_DATABASE_URL), dropped
// and re-migrated first, so API tests that commit rows can never touch the demo database.
// Test files run one at a time: several assert on "every credit that moved", which concurrent
// files committing rows would break.
//   npm test                         all tests
//   npm test -- test/escrow.test.js  one file (default: test/**/*.test.js)
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import pg from 'pg';
import { databaseUrl } from '../src/db.js';

const backendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const main = new URL(databaseUrl);
const testUrl = new URL(process.env.TEST_DATABASE_URL || databaseUrl);
if (!process.env.TEST_DATABASE_URL) testUrl.pathname = `${main.pathname}_test`;
const testDb = testUrl.pathname.slice(1);

if (testUrl.href === main.href) {
  console.error('refusing to run tests against the main database; set TEST_DATABASE_URL to a different one');
  process.exit(1);
}
if (!/^[a-z0-9_]+$/.test(testDb)) {
  console.error(`unexpected test database name "${testDb}"`);
  process.exit(1);
}

const admin = new pg.Client({ connectionString: main.href });
await admin.connect();
const { rowCount } = await admin.query('SELECT 1 FROM pg_database WHERE datname = $1', [testDb]);
if (rowCount === 0) {
  await admin.query(`CREATE DATABASE ${testDb}`);
  console.log(`created test database ${testDb}`);
}
await admin.end();

const env = { ...process.env, DATABASE_URL: testUrl.href };
const run = (args) => spawnSync(process.execPath, args, { cwd: backendDir, stdio: 'inherit', env });

const migrated = run(['scripts/migrate.js', '--reset']);
if (migrated.status !== 0) process.exit(migrated.status ?? 1);

const files = process.argv.slice(2);
const tested = run(['--test', '--test-concurrency=1', ...(files.length ? files : ['test/**/*.test.js'])]);
process.exit(tested.status ?? 1);
