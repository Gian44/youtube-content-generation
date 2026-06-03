const path = require('path');
const fs = require('fs');
const { spawnSync } = require('child_process');

const CONTENT_TABLES = [
  'daily_batches',
  'stories',
  'render_jobs',
  'youtube_uploads',
  'analytics_snapshots',
];

function getRuntimePaths({ app, isDev, workerPath }) {
  const dataRoot = isDev
    ? path.join(workerPath, 'data')
    : path.join(app.getPath('userData'), 'data');
  const logDir = isDev
    ? path.join(workerPath, 'logs')
    : path.join(app.getPath('userData'), 'logs');

  return {
    dataRoot,
    sqlitePath: path.join(dataRoot, 'storyfactory.db'),
    localStoragePath: path.join(dataRoot, 'storage'),
    logDir,
  };
}

function ensureRuntimeDirs(runtimePaths) {
  for (const dir of [
    runtimePaths.dataRoot,
    runtimePaths.localStoragePath,
    runtimePaths.logDir,
  ]) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function getSeedDatabaseCandidates({ isDev, resourcesPath, workerPath }) {
  if (isDev) return [];

  const candidates = [
    resourcesPath ? path.join(resourcesPath, 'seed-data', 'storyfactory.db') : null,
    workerPath ? path.join(workerPath, 'data', 'storyfactory.db') : null,
    resourcesPath
      ? path.resolve(resourcesPath, '..', '..', '..', '..', 'worker', 'data', 'storyfactory.db')
      : null,
  ].filter(Boolean);

  return [...new Set(candidates)];
}

function normalizeForCompare(filePath) {
  return path.resolve(filePath).toLowerCase();
}

function getSqliteContentCount(dbPath, { pythonCmd } = {}) {
  if (!dbPath || !fs.existsSync(dbPath)) return 0;

  const command = pythonCmd || (process.platform === 'win32' ? 'python' : 'python3');
  const script = `
import sqlite3
import sys

db_path = sys.argv[1]
tables = ${JSON.stringify(CONTENT_TABLES)}
total = 0

try:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        for table in tables:
            cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
            if cursor.fetchone():
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                total += int(cursor.fetchone()[0] or 0)
except Exception:
    total = 0

print(total)
`.trim();

  const result = spawnSync(command, ['-c', script, dbPath], {
    encoding: 'utf8',
    windowsHide: true,
  });

  if (result.status !== 0) return 0;
  const count = Number.parseInt(result.stdout.trim(), 10);
  return Number.isFinite(count) ? count : 0;
}

function makeRuntimeDatabaseBackupPath(sqlitePath, now = () => new Date()) {
  const parsed = path.parse(sqlitePath);
  const stamp = now().toISOString().replace(/[:.]/g, '-');
  return path.join(parsed.dir, `${parsed.name}.empty-${stamp}${parsed.ext}`);
}

function bootstrapRuntimeDatabase({
  runtimePaths,
  seedPaths = [],
  inspectDatabase = getSqliteContentCount,
  now = () => new Date(),
  logger = console,
}) {
  const sqlitePath = runtimePaths?.sqlitePath;
  if (!sqlitePath) return { status: 'skipped-missing-runtime-path' };

  fs.mkdirSync(path.dirname(sqlitePath), { recursive: true });

  const runtimeExists = fs.existsSync(sqlitePath);
  const runtimeContentCount = runtimeExists ? inspectDatabase(sqlitePath) : 0;
  if (runtimeContentCount > 0) {
    return { status: 'skipped-runtime-has-content', contentCount: runtimeContentCount };
  }

  const runtimeKey = normalizeForCompare(sqlitePath);
  const source = seedPaths.find((candidate) => {
    if (!candidate || normalizeForCompare(candidate) === runtimeKey) return false;
    if (!fs.existsSync(candidate)) return false;
    return inspectDatabase(candidate) > 0;
  });

  if (!source) return { status: 'skipped-no-populated-seed' };

  let backupPath = null;
  if (runtimeExists) {
    backupPath = makeRuntimeDatabaseBackupPath(sqlitePath, now);
    fs.copyFileSync(sqlitePath, backupPath);
  }

  fs.copyFileSync(source, sqlitePath);
  logger.log?.(`[Startup] Bootstrapped runtime database from ${source}`);

  return {
    status: 'copied',
    source,
    destination: sqlitePath,
    backupPath,
  };
}

function buildWorkerEnv({ baseEnv = process.env, envVars = {}, runtimePaths }) {
  return {
    ...baseEnv,
    ...envVars,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    SQLITE_PATH: runtimePaths.sqlitePath,
    LOCAL_STORAGE_PATH: runtimePaths.localStoragePath,
    STORYFACTORY_LOG_DIR: runtimePaths.logDir,
  };
}

module.exports = {
  getRuntimePaths,
  ensureRuntimeDirs,
  getSeedDatabaseCandidates,
  getSqliteContentCount,
  bootstrapRuntimeDatabase,
  buildWorkerEnv,
};
