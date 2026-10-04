// Seeds SYNTHETIC wards + trip logs from the audit engine's generator (decisions.md D5, D18).
//   npm run db:seed              seed an empty database (demo seed 2026)
//   npm run db:seed -- --fresh   truncate all demo tables first, then seed
//   npm run db:seed -- --seed 7  use another generator seed
import { pool } from '../src/db.js';
import { seedSynthetic, DEMO_SEED } from '../src/syntheticData.js';

const args = process.argv.slice(2);
const fresh = args.includes('--fresh');
const seedArg = args.indexOf('--seed');
const seed = seedArg >= 0 ? Number(args[seedArg + 1]) : DEMO_SEED;

try {
  const started = Date.now();
  const r = await seedSynthetic(pool, { seed, fresh });
  console.log(r.dataNotice);
  console.log(`seeded ${r.wards} wards and ${r.logs} trip log entries (seed ${r.seed}) in ${((Date.now() - started) / 1000).toFixed(1)} s`);
} catch (err) {
  console.error(`seed failed: ${err.message}`);
  process.exitCode = 1;
} finally {
  await pool.end();
}
