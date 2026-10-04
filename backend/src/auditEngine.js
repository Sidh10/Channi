// Calls the Python audit engine as a subprocess: one JSON request on stdin, one JSON response
// on stdout, diagnostics on stderr (decisions.md D18).
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const engineDir = path.join(repoRoot, 'audit-engine');

export const pythonPath = process.env.AUDIT_ENGINE_PYTHON
  || (process.platform === 'win32'
    ? path.join(engineDir, '.venv', 'Scripts', 'python.exe')
    : path.join(engineDir, '.venv', 'bin', 'python'));

// errorType 'invalid_input' (engine exit 2) means the request was bad; anything else is an engine fault.
export class EngineError extends Error {
  constructor(message, errorType = 'internal') {
    super(message);
    this.errorType = errorType;
  }
}

export function callEngine(command, payload = {}, { timeoutMs = 10 * 60 * 1000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(pythonPath, ['-m', 'chhanni_audit.service', command], {
      cwd: engineDir,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
    });
    const out = [];
    const err = [];
    let settled = false;
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn(value);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish(reject, new EngineError(`audit engine "${command}" timed out after ${timeoutMs} ms`));
    }, timeoutMs);

    child.stdout.on('data', (d) => out.push(d));
    child.stderr.on('data', (d) => err.push(d));
    child.on('error', (e) =>
      finish(reject, new EngineError(`could not start audit engine at ${pythonPath}: ${e.message}`)));
    child.on('close', (code) => {
      const stdout = Buffer.concat(out).toString('utf8');
      const stderr = Buffer.concat(err).toString('utf8').trim();
      let parsed;
      try {
        parsed = stdout.trim() ? JSON.parse(stdout) : undefined;
      } catch {
        parsed = undefined;
      }
      if (code !== 0) {
        const detail = parsed?.error || stderr.split('\n').slice(-5).join('\n') || `exit code ${code}`;
        return finish(reject, new EngineError(`audit engine "${command}" failed: ${detail}`, parsed?.error_type));
      }
      if (parsed === undefined) {
        return finish(reject, new EngineError(`audit engine "${command}" returned no JSON`));
      }
      finish(resolve, parsed);
    });
    child.stdin.on('error', () => {}); // a dead child is reported by 'close'
    child.stdin.end(JSON.stringify(payload));
  });
}
