import { spawn } from 'child_process';
import path from 'path';
import fs from 'fs';

/**
 * Server-only bridge for invoking the Python worker CLI.
 *
 * All secret mutations (per-channel API keys, OAuth tokens) flow through the
 * worker so encryption lives in exactly one place. The dashboard never
 * encrypts/decrypts secrets itself and never returns plaintext to the client.
 */

export interface WorkerResult {
  ok: boolean;
  code: number | null;
  stdout: string;
  stderr: string;
}

function getRootDir(): string {
  let cwd = process.cwd();
  for (let i = 0; i < 4; i++) {
    if (
      fs.existsSync(path.join(cwd, 'package.json')) &&
      fs.existsSync(path.join(cwd, 'apps', 'worker'))
    ) {
      return cwd;
    }
    cwd = path.dirname(cwd);
  }
  return process.cwd();
}

function getWorkerDir(): string {
  return path.join(getRootDir(), 'apps', 'worker');
}

/**
 * Run `python -m storyfactory <args>` in the worker directory.
 * The worker resolves the same SQLite DB and master key as the dashboard via
 * the inherited environment (SQLITE_PATH / STORYFACTORY_SECRET_KEY) and cwd.
 */
export function runWorkerCommand(args: string[]): Promise<WorkerResult> {
  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  const workerDir = getWorkerDir();

  return new Promise((resolve) => {
    const child = spawn(pythonCmd, ['-m', 'storyfactory', ...args], {
      cwd: workerDir,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
      },
      windowsHide: true,
    });

    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (d) => {
      stdout += d.toString();
    });
    child.stderr?.on('data', (d) => {
      stderr += d.toString();
    });
    child.on('error', (err) => {
      resolve({ ok: false, code: null, stdout, stderr: stderr + String(err) });
    });
    child.on('close', (code) => {
      resolve({ ok: code === 0, code, stdout: stdout.trim(), stderr: stderr.trim() });
    });
  });
}
