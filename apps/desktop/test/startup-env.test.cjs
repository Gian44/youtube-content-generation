const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const {
  getRuntimePaths,
  getSeedDatabaseCandidates,
  bootstrapRuntimeDatabase,
  buildWorkerEnv,
} = require('../startup-env');

test('getRuntimePaths uses worker directory in development', () => {
  const workerPath = path.resolve('apps/worker');
  const app = {
    getPath(name) {
      assert.equal(name, 'userData');
      return path.resolve('ignored-user-data');
    },
  };

  const paths = getRuntimePaths({ app, isDev: true, workerPath });

  assert.equal(paths.dataRoot, path.join(workerPath, 'data'));
  assert.equal(paths.sqlitePath, path.join(workerPath, 'data', 'storyfactory.db'));
  assert.equal(paths.localStoragePath, path.join(workerPath, 'data', 'storage'));
  assert.equal(paths.logDir, path.join(workerPath, 'logs'));
});

test('getRuntimePaths uses Electron userData in production', () => {
  const userData = path.resolve('tmp/user-data');
  const workerPath = path.resolve('resources/worker');
  const app = {
    getPath(name) {
      assert.equal(name, 'userData');
      return userData;
    },
  };

  const paths = getRuntimePaths({ app, isDev: false, workerPath });

  assert.equal(paths.dataRoot, path.join(userData, 'data'));
  assert.equal(paths.sqlitePath, path.join(userData, 'data', 'storyfactory.db'));
  assert.equal(paths.localStoragePath, path.join(userData, 'data', 'storage'));
  assert.equal(paths.logDir, path.join(userData, 'logs'));
});

test('buildWorkerEnv preserves loaded env and overrides writable paths', () => {
  const runtimePaths = {
    sqlitePath: 'C:/Users/renom/AppData/Roaming/storyfactory-desktop/data/storyfactory.db',
    localStoragePath: 'C:/Users/renom/AppData/Roaming/storyfactory-desktop/data/storage',
    logDir: 'C:/Users/renom/AppData/Roaming/storyfactory-desktop/logs',
  };

  const env = buildWorkerEnv({
    baseEnv: { PATH: 'base-path', SQLITE_PATH: 'old.sqlite' },
    envVars: { OPENAI_API_KEY: 'abc', LOCAL_STORAGE_PATH: 'old-storage' },
    runtimePaths,
  });

  assert.equal(env.PATH, 'base-path');
  assert.equal(env.OPENAI_API_KEY, 'abc');
  assert.equal(env.SQLITE_PATH, runtimePaths.sqlitePath);
  assert.equal(env.LOCAL_STORAGE_PATH, runtimePaths.localStoragePath);
  assert.equal(env.STORYFACTORY_LOG_DIR, runtimePaths.logDir);
  assert.equal(env.PYTHONIOENCODING, 'utf-8');
  assert.equal(env.PYTHONUTF8, '1');
});

test('getSeedDatabaseCandidates includes bundled and adjacent source databases in production', () => {
  const resourcesPath = path.resolve('apps/desktop/dist/win-unpacked/resources');
  const workerPath = path.join(resourcesPath, 'worker');

  const candidates = getSeedDatabaseCandidates({
    isDev: false,
    resourcesPath,
    workerPath,
  });

  assert.deepEqual(candidates, [
    path.join(resourcesPath, 'seed-data', 'storyfactory.db'),
    path.join(workerPath, 'data', 'storyfactory.db'),
    path.resolve(resourcesPath, '..', '..', '..', '..', 'worker', 'data', 'storyfactory.db'),
  ]);
});

test('getSeedDatabaseCandidates does not seed development because dev already uses worker data', () => {
  const candidates = getSeedDatabaseCandidates({
    isDev: true,
    resourcesPath: path.resolve('resources'),
    workerPath: path.resolve('apps/worker'),
  });

  assert.deepEqual(candidates, []);
});

test('bootstrapRuntimeDatabase copies a populated seed over an empty runtime database', () => {
  const root = fs.mkdtempSync(path.join(process.cwd(), 'tmp-runtime-db-'));
  const dataRoot = path.join(root, 'data');
  const seedRoot = path.join(root, 'seed-data');
  fs.mkdirSync(dataRoot, { recursive: true });
  fs.mkdirSync(seedRoot, { recursive: true });

  const runtimeDb = path.join(dataRoot, 'storyfactory.db');
  const seedDb = path.join(seedRoot, 'storyfactory.db');
  fs.writeFileSync(runtimeDb, 'empty-runtime');
  fs.writeFileSync(seedDb, 'populated-seed');

  const result = bootstrapRuntimeDatabase({
    runtimePaths: { sqlitePath: runtimeDb },
    seedPaths: [seedDb],
    inspectDatabase(dbPath) {
      return dbPath === seedDb ? 12 : 0;
    },
    now: () => new Date('2026-05-30T12:00:00.000Z'),
    logger: { log() {} },
  });

  assert.equal(result.status, 'copied');
  assert.equal(result.source, seedDb);
  assert.equal(fs.readFileSync(runtimeDb, 'utf8'), 'populated-seed');
  assert.equal(
    fs.readFileSync(path.join(dataRoot, 'storyfactory.empty-2026-05-30T12-00-00-000Z.db'), 'utf8'),
    'empty-runtime'
  );

  fs.rmSync(root, { recursive: true, force: true });
});

test('bootstrapRuntimeDatabase does not overwrite a runtime database that already has content', () => {
  const root = fs.mkdtempSync(path.join(process.cwd(), 'tmp-runtime-db-'));
  const dataRoot = path.join(root, 'data');
  const seedRoot = path.join(root, 'seed-data');
  fs.mkdirSync(dataRoot, { recursive: true });
  fs.mkdirSync(seedRoot, { recursive: true });

  const runtimeDb = path.join(dataRoot, 'storyfactory.db');
  const seedDb = path.join(seedRoot, 'storyfactory.db');
  fs.writeFileSync(runtimeDb, 'existing-runtime-content');
  fs.writeFileSync(seedDb, 'populated-seed');

  const result = bootstrapRuntimeDatabase({
    runtimePaths: { sqlitePath: runtimeDb },
    seedPaths: [seedDb],
    inspectDatabase(dbPath) {
      return dbPath === runtimeDb ? 7 : 12;
    },
    logger: { log() {} },
  });

  assert.equal(result.status, 'skipped-runtime-has-content');
  assert.equal(fs.readFileSync(runtimeDb, 'utf8'), 'existing-runtime-content');

  fs.rmSync(root, { recursive: true, force: true });
});
