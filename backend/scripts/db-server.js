// Runs a local Postgres 17 (embedded-postgres binaries, no Docker needed) using the
// user/password/port/database from DATABASE_URL. Data lives in <repo>/.pgdata.
// Leave it running in its own terminal; Ctrl+C stops it cleanly. See decisions.md D11.
import EmbeddedPostgres from 'embedded-postgres';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { databaseUrl } from '../src/db.js';

const dataDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../.pgdata');
const url = new URL(databaseUrl);
const dbName = url.pathname.slice(1);

const server = new EmbeddedPostgres({
  databaseDir: dataDir,
  user: decodeURIComponent(url.username),
  password: decodeURIComponent(url.password),
  port: Number(url.port) || 5432,
  persistent: true,
  onLog: () => {},
  onError: (msg) => console.error(String(msg).trim()),
});

async function main() {
  if (!existsSync(path.join(dataDir, 'PG_VERSION'))) {
    console.log(`initialising new cluster in ${dataDir}`);
    await server.initialise();
  }
  await server.start();

  const client = server.getPgClient();
  await client.connect();
  const { rowCount } = await client.query('SELECT 1 FROM pg_database WHERE datname = $1', [dbName]);
  await client.end();
  if (rowCount === 0) {
    await server.createDatabase(dbName);
    console.log(`created database ${dbName}`);
  }

  console.log(`postgres ready on localhost:${url.port || 5432} (db: ${dbName}) — Ctrl+C to stop`);

  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    await server.stop();
    console.log('postgres stopped');
    process.exit(0);
  };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
}

main().catch(async (err) => {
  console.error(`db-server failed: ${err.message}`);
  await server.stop().catch(() => {});
  process.exit(1);
});
