# Desktop Startup Loading and Build Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the packaged Electron app show startup progress immediately, start the dashboard reliably in installed builds, move writable data out of Program Files, and clean up requested build warnings.

**Architecture:** Add a local Electron startup screen that receives progress events from the main process while migrations and the Next standalone server run in the background. Launch Next with Electron's bundled runtime and package the standalone `node_modules` explicitly so installed builds do not depend on global Node or repo-level dependencies. Centralize production runtime paths under Electron `userData` and pass them to dashboard and worker processes.

**Tech Stack:** Electron 35, Electron Builder 26, Next.js standalone output, Node CommonJS main/preload scripts, Python worker, pytest, Node built-in `node:test`.

---

## Scope Check

This plan covers one connected desktop packaging and startup flow. It touches Electron startup, packaged resources, worker writable paths, dashboard DB path tracing, and build metadata because all of those are part of the same packaged-app launch failure.

The current workspace is not a Git repository. Each task includes a checkpoint step instead of a required commit. If this plan is executed in a Git checkout, run the listed optional commit command at each checkpoint.

## File Structure

- Create `apps/desktop/startup-env.js`: pure helper for runtime paths and worker/dashboard environment variables.
- Create `apps/desktop/startup.html`: self-contained startup UI shown before the dashboard server is ready.
- Modify `apps/desktop/preload.js`: expose startup progress, retry, and quit IPC APIs.
- Modify `apps/desktop/main.js`: create the window early, orchestrate startup progress, launch Next via Electron-as-Node, fail fast on server exit, and navigate only after readiness.
- Modify `apps/desktop/scheduler.js`: use `SQLITE_PATH`, `LOCAL_STORAGE_PATH`, and `STORYFACTORY_LOG_DIR` passed from main instead of writing data under the packaged worker directory.
- Modify `apps/desktop/package.json`: add author, build scripts, startup asset packaging, and standalone `node_modules` resource copy.
- Modify `package.json`: add `packageManager`.
- Modify `apps/worker/storyfactory/logger.py`: honor `STORYFACTORY_LOG_DIR`.
- Modify `apps/dashboard/src/lib/db.ts`: make production SQLite path resolution deterministic and avoid broad file tracing.
- Create `apps/desktop/test/*.test.cjs`: Node smoke tests for desktop helper/config/static startup contracts.
- Create `apps/dashboard/test/db-path-static.test.cjs`: static check for deterministic DB path code.
- Create `apps/worker/tests/test_logger_paths.py`: pytest coverage for worker log directory env override.

## Task 1: Runtime Path Helper

**Files:**
- Create: `apps/desktop/startup-env.js`
- Create: `apps/desktop/test/startup-env.test.cjs`

- [ ] **Step 1: Write the failing helper tests**

Create `apps/desktop/test/startup-env.test.cjs`:

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
  getRuntimePaths,
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
```

- [ ] **Step 2: Run the helper tests and verify they fail**

Run:

```powershell
node --test apps/desktop/test/startup-env.test.cjs
```

Expected: FAIL with `Cannot find module '../startup-env'`.

- [ ] **Step 3: Add the helper implementation**

Create `apps/desktop/startup-env.js`:

```js
const path = require('path');
const fs = require('fs');

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
  buildWorkerEnv,
};
```

- [ ] **Step 4: Run the helper tests and verify they pass**

Run:

```powershell
node --test apps/desktop/test/startup-env.test.cjs
```

Expected: PASS with 3 passing tests.

- [ ] **Step 5: Checkpoint**

Run:

```powershell
Test-Path apps/desktop/startup-env.js
node --test apps/desktop/test/startup-env.test.cjs
```

Expected: first command prints `True`; tests pass. If executing in a Git checkout, commit:

```powershell
git add apps/desktop/startup-env.js apps/desktop/test/startup-env.test.cjs
git commit -m "test: add desktop runtime path helper"
```

## Task 2: Worker Log Directory Env Override

**Files:**
- Create: `apps/worker/tests/test_logger_paths.py`
- Modify: `apps/worker/storyfactory/logger.py`

- [ ] **Step 1: Write the failing worker logger test**

Create `apps/worker/tests/test_logger_paths.py`:

```python
from pathlib import Path

from storyfactory.logger import setup_logging


def test_setup_logging_uses_storyfactory_log_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "desktop-logs"
    monkeypatch.setenv("STORYFACTORY_LOG_DIR", str(log_dir))

    setup_logging()

    assert log_dir.exists()
    assert log_dir.is_dir()
    assert not Path("logs").resolve().samefile(log_dir) if Path("logs").exists() else True
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
cd apps/worker
python -m pytest tests/test_logger_paths.py -v
```

Expected: FAIL because `setup_logging()` creates `./logs` and ignores `STORYFACTORY_LOG_DIR`.

- [ ] **Step 3: Update the logger implementation**

Modify `apps/worker/storyfactory/logger.py` imports:

```python
import os
import structlog
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone
```

Replace the log directory block in `setup_logging()`:

```python
    # Create logs directory
    log_dir = Path(os.environ.get("STORYFACTORY_LOG_DIR", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run the worker logger test and verify it passes**

Run:

```powershell
cd apps/worker
python -m pytest tests/test_logger_paths.py -v
```

Expected: PASS.

- [ ] **Step 5: Checkpoint**

Run from the repository root:

```powershell
python -m pytest apps/worker/tests/test_logger_paths.py -v
```

Expected: PASS. If executing in a Git checkout, commit:

```powershell
git add apps/worker/storyfactory/logger.py apps/worker/tests/test_logger_paths.py
git commit -m "fix: route worker logs to configured directory"
```

## Task 3: Startup UI and Preload IPC Contract

**Files:**
- Create: `apps/desktop/startup.html`
- Create: `apps/desktop/test/startup-ui.test.cjs`
- Modify: `apps/desktop/preload.js`

- [ ] **Step 1: Write the failing startup UI contract test**

Create `apps/desktop/test/startup-ui.test.cjs`:

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const desktopRoot = path.resolve(__dirname, '..');

test('startup.html contains required startup screen elements', () => {
  const html = fs.readFileSync(path.join(desktopRoot, 'startup.html'), 'utf8');

  assert.match(html, /id="statusText"/);
  assert.match(html, /id="detailText"/);
  assert.match(html, /id="errorPanel"/);
  assert.match(html, /id="retryButton"/);
  assert.match(html, /id="quitButton"/);
  assert.match(html, /onStartupProgress/);
  assert.match(html, /startupRetry/);
  assert.match(html, /startupQuit/);
});

test('preload exposes startup progress, retry, and quit APIs', () => {
  const preload = fs.readFileSync(path.join(desktopRoot, 'preload.js'), 'utf8');

  assert.match(preload, /onStartupProgress/);
  assert.match(preload, /startup-progress/);
  assert.match(preload, /startupRetry/);
  assert.match(preload, /startup-retry/);
  assert.match(preload, /startupQuit/);
  assert.match(preload, /startup-quit/);
});
```

- [ ] **Step 2: Run the startup UI contract test and verify it fails**

Run:

```powershell
node --test apps/desktop/test/startup-ui.test.cjs
```

Expected: FAIL because `startup.html` does not exist and preload does not expose startup APIs.

- [ ] **Step 3: Create the startup screen**

Create `apps/desktop/startup.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>StoryFactory</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: Inter, Segoe UI, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: #101114;
      color: #f5f7fb;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at top left, rgba(36, 115, 203, 0.28), transparent 32rem),
        #101114;
    }

    main {
      width: min(520px, calc(100vw - 48px));
    }

    h1 {
      margin: 0 0 12px;
      font-size: 32px;
      line-height: 1.1;
      font-weight: 700;
      letter-spacing: 0;
    }

    #statusText {
      margin: 0;
      font-size: 17px;
      color: #d8deea;
    }

    #detailText {
      margin: 10px 0 0;
      min-height: 22px;
      font-size: 14px;
      color: #9aa6bb;
    }

    .bar {
      width: 100%;
      height: 6px;
      margin-top: 28px;
      overflow: hidden;
      background: #2a2f3a;
      border-radius: 999px;
    }

    .bar span {
      display: block;
      width: 42%;
      height: 100%;
      background: #58a6ff;
      border-radius: inherit;
      animation: slide 1.15s ease-in-out infinite;
    }

    #errorPanel {
      display: none;
      margin-top: 24px;
      padding: 16px;
      border: 1px solid #7f2f3b;
      background: #291317;
      border-radius: 8px;
      color: #ffd8de;
      white-space: pre-wrap;
      font-size: 13px;
      line-height: 1.45;
    }

    .actions {
      display: none;
      gap: 10px;
      margin-top: 18px;
    }

    button {
      height: 36px;
      border: 0;
      border-radius: 7px;
      padding: 0 14px;
      color: #ffffff;
      background: #2563eb;
      font: inherit;
      cursor: pointer;
    }

    button.secondary {
      background: #303745;
    }

    @keyframes slide {
      0% { transform: translateX(-110%); }
      50% { transform: translateX(80%); }
      100% { transform: translateX(250%); }
    }
  </style>
</head>
<body>
  <main>
    <h1>StoryFactory</h1>
    <p id="statusText">Starting desktop app</p>
    <p id="detailText">Preparing the dashboard.</p>
    <div class="bar" aria-hidden="true"><span></span></div>
    <pre id="errorPanel"></pre>
    <div class="actions" id="errorActions">
      <button id="retryButton" type="button">Retry</button>
      <button id="quitButton" class="secondary" type="button">Quit</button>
    </div>
  </main>

  <script>
    const statusText = document.getElementById('statusText');
    const detailText = document.getElementById('detailText');
    const errorPanel = document.getElementById('errorPanel');
    const errorActions = document.getElementById('errorActions');
    const retryButton = document.getElementById('retryButton');
    const quitButton = document.getElementById('quitButton');

    retryButton.addEventListener('click', () => window.storyfactory.startupRetry());
    quitButton.addEventListener('click', () => window.storyfactory.startupQuit());

    window.storyfactory.onStartupProgress((event) => {
      statusText.textContent = event.status || 'Starting StoryFactory';
      detailText.textContent = event.detail || '';

      if (event.error) {
        errorPanel.style.display = 'block';
        errorActions.style.display = 'flex';
        errorPanel.textContent = event.error;
      } else {
        errorPanel.style.display = 'none';
        errorActions.style.display = 'none';
        errorPanel.textContent = '';
      }
    });
  </script>
</body>
</html>
```

- [ ] **Step 4: Expose startup APIs in preload**

Modify `apps/desktop/preload.js` inside the `storyfactory` object:

```js
  onStartupProgress: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('startup-progress', listener);
    return () => ipcRenderer.removeListener('startup-progress', listener);
  },
  startupRetry: () => ipcRenderer.invoke('startup-retry'),
  startupQuit: () => ipcRenderer.invoke('startup-quit'),
```

Keep the existing scheduler and app info APIs unchanged.

- [ ] **Step 5: Run the startup UI contract test and verify it passes**

Run:

```powershell
node --test apps/desktop/test/startup-ui.test.cjs
```

Expected: PASS with 2 passing tests.

- [ ] **Step 6: Checkpoint**

Run:

```powershell
node --test apps/desktop/test/startup-ui.test.cjs
```

Expected: PASS. If executing in a Git checkout, commit:

```powershell
git add apps/desktop/startup.html apps/desktop/preload.js apps/desktop/test/startup-ui.test.cjs
git commit -m "feat: add desktop startup loading screen"
```

## Task 4: Main Process Startup Orchestration

**Files:**
- Create: `apps/desktop/test/main-startup-static.test.cjs`
- Modify: `apps/desktop/main.js`

- [ ] **Step 1: Write the failing main-process static contract test**

Create `apps/desktop/test/main-startup-static.test.cjs`:

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const mainSource = fs.readFileSync(path.resolve(__dirname, '..', 'main.js'), 'utf8');

test('main process includes early startup orchestration functions', () => {
  assert.match(mainSource, /function sendStartupProgress/);
  assert.match(mainSource, /function loadStartupScreen/);
  assert.match(mainSource, /async function runStartupSequence/);
  assert.match(mainSource, /function showStartupError/);
  assert.match(mainSource, /function waitForDashboard/);
});

test('production Next server runs through Electron as Node, not global node', () => {
  assert.match(mainSource, /ELECTRON_RUN_AS_NODE/);
  assert.match(mainSource, /process\.execPath/);
  assert.doesNotMatch(mainSource, /spawn\('node'/);
});

test('startup retry and quit IPC handlers exist', () => {
  assert.match(mainSource, /startup-retry/);
  assert.match(mainSource, /startup-quit/);
});
```

- [ ] **Step 2: Run the main-process static test and verify it fails**

Run:

```powershell
node --test apps/desktop/test/main-startup-static.test.cjs
```

Expected: FAIL because the current main process waits before creating the window and uses `spawn('node'`.

- [ ] **Step 3: Import startup helpers and remove unused shell import**

In `apps/desktop/main.js`, change:

```js
const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
```

to:

```js
const { app, BrowserWindow, ipcMain, dialog } = require('electron');
```

Add after scheduler imports:

```js
const {
  getRuntimePaths,
  ensureRuntimeDirs,
  buildWorkerEnv,
} = require('./startup-env');
```

- [ ] **Step 4: Add startup state helpers**

Add below globals in `apps/desktop/main.js`:

```js
let startupInProgress = false;
let latestStartupEvent = {
  status: 'Starting StoryFactory',
  detail: 'Preparing the desktop app.',
};

function getRuntimeConfig() {
  const workerPath = getWorkerPath();
  const runtimePaths = getRuntimePaths({ app, isDev, workerPath });
  const envVars = loadEnvVars();
  const workerEnv = buildWorkerEnv({
    baseEnv: process.env,
    envVars,
    runtimePaths,
  });

  return {
    workerPath,
    dashboardPath: getDashboardPath(),
    runtimePaths,
    envVars,
    workerEnv,
  };
}

function sendStartupProgress(event) {
  latestStartupEvent = {
    ...latestStartupEvent,
    ...event,
  };

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('startup-progress', latestStartupEvent);
  }
}

function showStartupError(error) {
  const message = error instanceof Error ? error.stack || error.message : String(error);
  sendStartupProgress({
    status: 'Dashboard failed to start',
    detail: 'StoryFactory could not open the local dashboard.',
    error: message,
  });
}

function getDashboardUrl() {
  return `http://localhost:${DASHBOARD_PORT}`;
}
```

- [ ] **Step 5: Add startup screen loading and dashboard readiness check**

Add near the window management section:

```js
function loadStartupScreen() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.loadFile(path.join(__dirname, 'startup.html'));
}

function waitForDashboard(timeoutMs = 15000) {
  const startedAt = Date.now();
  const dashboardUrl = getDashboardUrl();

  return new Promise((resolve, reject) => {
    const check = async () => {
      try {
        const response = await fetch(dashboardUrl);
        if (response.ok || response.status < 500) {
          resolve();
          return;
        }
      } catch (error) {
        if (Date.now() - startedAt >= timeoutMs) {
          reject(new Error(`Dashboard did not respond at ${dashboardUrl} within ${timeoutMs}ms`));
          return;
        }
      }

      setTimeout(check, 250);
    };

    check();
  });
}

async function openDashboard() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  sendStartupProgress({
    status: 'Opening dashboard',
    detail: getDashboardUrl(),
    error: null,
  });
  await mainWindow.loadURL(getDashboardUrl());
}
```

- [ ] **Step 6: Replace production Next launch**

In `startNextServer()`, production branch should use `process.execPath` and `ELECTRON_RUN_AS_NODE`:

```js
      const serverPath = path.join(dashboardPath, 'apps', 'dashboard', 'server.js');
      console.log(`[Main] Starting Next.js standalone from: ${serverPath}`);

      nextServerProcess = spawn(process.execPath, [serverPath], {
        cwd: dashboardPath,
        env: {
          ...workerEnv,
          PORT: String(DASHBOARD_PORT),
          HOSTNAME: 'localhost',
          ELECTRON_RUN_AS_NODE: '1',
        },
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
      });
```

Adjust `startNextServer()` signature to accept config:

```js
function startNextServer(config = getRuntimeConfig()) {
```

Inside the function, use:

```js
    const { dashboardPath, workerEnv } = config;
```

For the development branch, use `workerEnv` instead of rebuilding env inline.

- [ ] **Step 7: Make early Next exit reject instead of waiting 30 seconds**

Inside `startNextServer()`, replace the `close` handler with:

```js
    nextServerProcess.on('close', (code) => {
      console.log(`[Main] Next.js server exited with code ${code}`);
      nextServerProcess = null;
      if (!started) {
        started = true;
        reject(new Error(`Next.js server exited before startup completed. Exit code: ${code}`));
      }
    });
```

Replace the timeout block with:

```js
    setTimeout(() => {
      if (!started) {
        started = true;
        reject(new Error('Next.js startup timed out before the dashboard became ready.'));
      }
    }, 30000);
```

- [ ] **Step 8: Update main window creation**

Change `createMainWindow()` so it loads the startup screen first:

```js
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'StoryFactory',
    icon: path.join(__dirname, 'resources', 'icon.ico'),
    backgroundColor: '#101114',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.setMenuBarVisibility(false);
  loadStartupScreen();

  if (isDev) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    sendStartupProgress(latestStartupEvent);
  });

  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      mainWindow.hide();
      return false;
    }
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}
```

- [ ] **Step 9: Add startup sequence**

Add before app lifecycle:

```js
async function runStartupSequence() {
  if (startupInProgress) return;
  startupInProgress = true;

  try {
    const config = getRuntimeConfig();

    sendStartupProgress({
      status: 'Preparing app data',
      detail: config.runtimePaths.dataRoot,
      error: null,
    });
    ensureRuntimeDirs(config.runtimePaths);

    sendStartupProgress({
      status: 'Running database migrations',
      detail: config.runtimePaths.sqlitePath,
      error: null,
    });
    const migrationsOk = await runMigrations(config);
    if (!migrationsOk) {
      throw new Error('Database migrations failed. Check StoryFactory.log for migration output.');
    }

    sendStartupProgress({
      status: 'Starting dashboard',
      detail: getDashboardUrl(),
      error: null,
    });
    await startNextServer(config);
    await waitForDashboard();

    console.log(`[Main] Dashboard ready at ${getDashboardUrl()}`);
    await openDashboard();
  } catch (err) {
    console.error('[Main] Failed to start dashboard:', err);
    showStartupError(err);
  } finally {
    startupInProgress = false;
  }
}
```

- [ ] **Step 10: Update migrations to use runtime env**

Change `runMigrations()` signature:

```js
function runMigrations(config = getRuntimeConfig()) {
```

Inside it, use:

```js
    const workerPath = config.workerPath;
    const workerEnv = config.workerEnv;
```

Replace the `env` object in the `spawn()` call with:

```js
      env: workerEnv,
```

- [ ] **Step 11: Add startup IPC handlers**

Inside `setupIPC()` add:

```js
  ipcMain.handle('startup-retry', async () => {
    loadStartupScreen();
    await runStartupSequence();
    return { success: true };
  });

  ipcMain.handle('startup-quit', () => {
    isQuitting = true;
    app.quit();
    return { success: true };
  });
```

- [ ] **Step 12: Reorder `app.whenReady()`**

Replace the startup body from worker data directory creation through delayed window creation with:

```js
  setupIPC();

  const startMinimized = process.argv.includes('--hidden');
  if (!startMinimized) {
    createMainWindow();
  } else {
    console.log('[Main] Started minimized to tray');
  }

  tray = createTray({
    iconPath: path.join(__dirname, 'resources', 'icon.ico'),
    onShow: showMainWindow,
    onRunNow: () => runPipelineNow(getWorkerPath(), getRuntimeConfig().workerEnv),
    onQuit: () => {
      isQuitting = true;
      app.quit();
    },
    getSchedulerStatus,
  });

  await runStartupSequence();

  initScheduler(getWorkerPath(), getRuntimeConfig().workerEnv);
  console.log('[Main] Scheduler initialized - full pipeline at 9:00 AM PHT daily');
```

Keep the auto-launch block after scheduler initialization.

- [ ] **Step 13: Run the main-process static test**

Run:

```powershell
node --test apps/desktop/test/main-startup-static.test.cjs
```

Expected: PASS.

- [ ] **Step 14: Checkpoint**

Run:

```powershell
node --test apps/desktop/test/startup-env.test.cjs apps/desktop/test/startup-ui.test.cjs apps/desktop/test/main-startup-static.test.cjs
```

Expected: PASS. If executing in a Git checkout, commit:

```powershell
git add apps/desktop/main.js apps/desktop/test/main-startup-static.test.cjs
git commit -m "fix: show startup progress before dashboard is ready"
```

## Task 5: Scheduler Writable Paths

**Files:**
- Create: `apps/desktop/test/scheduler-static.test.cjs`
- Modify: `apps/desktop/scheduler.js`

- [ ] **Step 1: Write the failing scheduler static test**

Create `apps/desktop/test/scheduler-static.test.cjs`:

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const scheduler = fs.readFileSync(path.resolve(__dirname, '..', 'scheduler.js'), 'utf8');

test('scheduler derives data directory from SQLITE_PATH when provided', () => {
  assert.match(scheduler, /envVars\.SQLITE_PATH/);
  assert.match(scheduler, /path\.dirname\(envVars\.SQLITE_PATH\)/);
  assert.doesNotMatch(scheduler, /path\.join\(workerDir, 'data'\)/);
});

test('scheduler forwards desktop writable path environment variables', () => {
  assert.match(scheduler, /LOCAL_STORAGE_PATH/);
  assert.match(scheduler, /STORYFACTORY_LOG_DIR/);
});
```

- [ ] **Step 2: Run the scheduler test and verify it fails**

Run:

```powershell
node --test apps/desktop/test/scheduler-static.test.cjs
```

Expected: FAIL because `scheduler.js` creates `path.join(workerDir, 'data')`.

- [ ] **Step 3: Add scheduler env helper**

Near the top of `apps/desktop/scheduler.js`, after `state`, add:

```js
function getWorkerSpawnEnv(envVars) {
  return {
    ...process.env,
    ...envVars,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
  };
}

function ensureWorkerDataDir(workerDir, envVars) {
  const dataDir = envVars.SQLITE_PATH
    ? path.dirname(envVars.SQLITE_PATH)
    : path.join(workerDir, 'data');
  fs.mkdirSync(dataDir, { recursive: true });
}
```

- [ ] **Step 4: Update `runPipeline()`**

Replace the data directory block:

```js
  // Ensure data directory exists
  const dataDir = path.join(workerDir, 'data');
  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true });
  }
```

with:

```js
  ensureWorkerDataDir(workerDir, envVars);
```

Replace the `env` object in `spawn()`:

```js
    env: getWorkerSpawnEnv(envVars),
```

- [ ] **Step 5: Update `runAnalytics()`**

Replace the `env` object in analytics `spawn()`:

```js
    env: getWorkerSpawnEnv(envVars),
```

- [ ] **Step 6: Run the scheduler test and verify it passes**

Run:

```powershell
node --test apps/desktop/test/scheduler-static.test.cjs
```

Expected: PASS.

- [ ] **Step 7: Checkpoint**

Run:

```powershell
node --test apps/desktop/test/scheduler-static.test.cjs
```

Expected: PASS. If executing in a Git checkout, commit:

```powershell
git add apps/desktop/scheduler.js apps/desktop/test/scheduler-static.test.cjs
git commit -m "fix: use configured desktop worker data paths"
```

## Task 6: Build Metadata and Packaged Resources

**Files:**
- Create: `apps/desktop/test/package-config.test.cjs`
- Modify: `package.json`
- Modify: `apps/desktop/package.json`

- [ ] **Step 1: Write the failing package config test**

Create `apps/desktop/test/package-config.test.cjs`:

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..', '..', '..');
const rootPackage = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const desktopPackage = JSON.parse(fs.readFileSync(path.join(root, 'apps/desktop/package.json'), 'utf8'));

function extraResourceTo(to) {
  return desktopPackage.build.extraResources.find((entry) => entry.to === to);
}

test('root package declares npm package manager', () => {
  assert.equal(rootPackage.packageManager, 'npm@11.7.0');
});

test('desktop package declares author', () => {
  assert.equal(desktopPackage.author, 'Gian Myrl Renomeron');
});

test('desktop package suppresses known DEP0190 builder warning in package scripts', () => {
  assert.match(desktopPackage.scripts.package, /--disable-warning=DEP0190/);
  assert.match(desktopPackage.scripts.package, /electron-builder\/cli\.js --win/);
  assert.match(desktopPackage.scripts['package:dir'], /--disable-warning=DEP0190/);
  assert.match(desktopPackage.scripts['package:dir'], /electron-builder\/cli\.js --win --dir/);
});

test('desktop package includes startup and runtime helper files', () => {
  assert.ok(desktopPackage.build.files.includes('startup.html'));
  assert.ok(desktopPackage.build.files.includes('startup-env.js'));
});

test('desktop package explicitly copies standalone node_modules', () => {
  const nodeModules = extraResourceTo('dashboard/node_modules');
  assert.equal(nodeModules.from, '../dashboard/.next/standalone/node_modules');
  assert.deepEqual(nodeModules.filter, ['**/*']);
});

test('desktop package keeps fixed dashboard static asset destinations', () => {
  assert.equal(extraResourceTo('dashboard/apps/dashboard/.next/static').from, '../dashboard/.next/static');
  assert.equal(extraResourceTo('dashboard/apps/dashboard/public').from, '../dashboard/public');
});
```

- [ ] **Step 2: Run the config test and verify it fails**

Run:

```powershell
node --test apps/desktop/test/package-config.test.cjs
```

Expected: FAIL because author, packageManager, script wrappers, file entries, and standalone `node_modules` copy are not all present.

- [ ] **Step 3: Update root `package.json`**

Add after `"private": true`:

```json
  "packageManager": "npm@11.7.0",
```

- [ ] **Step 4: Update desktop package metadata and scripts**

In `apps/desktop/package.json`, add:

```json
  "author": "Gian Myrl Renomeron",
```

Update scripts:

```json
    "package": "node --disable-warning=DEP0190 ../../node_modules/electron-builder/cli.js --win",
    "package:dir": "node --disable-warning=DEP0190 ../../node_modules/electron-builder/cli.js --win --dir"
```

- [ ] **Step 5: Update packaged file list**

In `apps/desktop/package.json`, add to `build.files`:

```json
      "startup.html",
      "startup-env.js",
```

- [ ] **Step 6: Add explicit standalone `node_modules` resource copy**

In `build.extraResources`, add immediately after the standalone copy entry:

```json
      {
        "from": "../dashboard/.next/standalone/node_modules",
        "to": "dashboard/node_modules",
        "filter": ["**/*"]
      },
```

- [ ] **Step 7: Run the package config test and verify it passes**

Run:

```powershell
node --test apps/desktop/test/package-config.test.cjs
```

Expected: PASS.

- [ ] **Step 8: Checkpoint**

Run:

```powershell
node --test apps/desktop/test/package-config.test.cjs
```

Expected: PASS. If executing in a Git checkout, commit:

```powershell
git add package.json apps/desktop/package.json apps/desktop/test/package-config.test.cjs
git commit -m "chore: clean desktop build metadata and resources"
```

## Task 7: Dashboard SQLite Path Tracing Cleanup

**Files:**
- Create: `apps/dashboard/test/db-path-static.test.cjs`
- Modify: `apps/dashboard/src/lib/db.ts`

- [ ] **Step 1: Write the failing dashboard DB path static test**

Create `apps/dashboard/test/db-path-static.test.cjs`:

```js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dbSource = fs.readFileSync(path.resolve(__dirname, '..', 'src/lib/db.ts'), 'utf8');

test('production sqlite path resolution uses explicit SQLITE_PATH without filesystem probing', () => {
  assert.match(dbSource, /process\.env\.SQLITE_PATH/);
  assert.match(dbSource, /process\.env\.NODE_ENV === 'production'/);
  assert.doesNotMatch(dbSource, /const possiblePaths = \[/);
  assert.doesNotMatch(dbSource, /fs\.existsSync\(p\)/);
});
```

- [ ] **Step 2: Run the DB path test and verify it fails**

Run:

```powershell
node --test apps/dashboard/test/db-path-static.test.cjs
```

Expected: FAIL because `db.ts` still uses `possiblePaths` and `fs.existsSync(p)`.

- [ ] **Step 3: Replace `getSqlitePath()`**

In `apps/dashboard/src/lib/db.ts`, replace the entire `getSqlitePath()` function with:

```ts
function getSqlitePath(): string {
  if (sqliteDbPath) return sqliteDbPath;

  const rawPath = process.env.SQLITE_PATH || './data/storyfactory.db';
  if (path.isAbsolute(rawPath)) {
    sqliteDbPath = rawPath;
    return sqliteDbPath;
  }

  if (process.env.NODE_ENV === 'production') {
    sqliteDbPath = path.resolve(rawPath);
    return sqliteDbPath;
  }

  const workerFolder = ['worker'].join('');
  const appsWorkerFolder = ['apps', workerFolder].join('/');
  const devPaths = [
    path.resolve(process.cwd(), appsWorkerFolder, rawPath),
    path.resolve(process.cwd(), `../${workerFolder}`, rawPath),
    path.resolve(process.cwd(), rawPath),
  ];

  const existingPath = devPaths.find((candidate) => fs.existsSync(candidate));
  sqliteDbPath = existingPath || devPaths[0];
  return sqliteDbPath;
}
```

- [ ] **Step 4: Run the DB path test and verify it passes**

Run:

```powershell
node --test apps/dashboard/test/db-path-static.test.cjs
```

Expected: PASS.

- [ ] **Step 5: Run the dashboard build**

Run:

```powershell
npm run build --workspace=apps/dashboard
```

Expected: exit code 0. Expected warning state: the Turbopack NFT warning should be gone. If it remains, update only the development branch in `getSqlitePath()` to use `path.resolve(/* turbopackIgnore: true */ process.cwd(), ...)` for each `process.cwd()` call, then rerun this command.

- [ ] **Step 6: Checkpoint**

Run:

```powershell
node --test apps/dashboard/test/db-path-static.test.cjs
npm run build --workspace=apps/dashboard
```

Expected: both commands exit 0. If executing in a Git checkout, commit:

```powershell
git add apps/dashboard/src/lib/db.ts apps/dashboard/test/db-path-static.test.cjs
git commit -m "fix: scope dashboard sqlite path resolution"
```

## Task 8: Full Verification

**Files:**
- No new files.
- Validate all modified files from Tasks 1-7.

- [ ] **Step 1: Run all focused tests**

Run from the repository root:

```powershell
node --test apps/desktop/test/*.test.cjs apps/dashboard/test/*.test.cjs
python -m pytest apps/worker/tests/test_logger_paths.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run the full desktop build**

Run:

```powershell
npm run desktop:build
```

Expected: exit code 0.

Expected build output changes:

- No `author is missed` warning.
- No `packageManager not detected` warning.
- No `[DEP0190]` warning.
- No Turbopack NFT warning.
- If `duplicate dependency references` remains, capture the exact line and confirm `npm ls @emnapi/core @emnapi/runtime @emnapi/wasi-threads @tybys/wasm-util --all` still exits 0 or only reports optional dependency metadata. Do not manually delete optional dependency folders.

- [ ] **Step 3: Verify packaged resource layout**

Run:

```powershell
$base = 'apps\desktop\dist\win-unpacked\resources\dashboard'
@(
  'node_modules\next',
  'apps\dashboard\server.js',
  'apps\dashboard\.next\static',
  'apps\dashboard\public'
) | ForEach-Object {
  $path = Join-Path $base $_
  [PSCustomObject]@{
    Path = $path
    Exists = Test-Path -LiteralPath $path
    FileCount = if (Test-Path -LiteralPath $path) {
      $item = Get-Item -LiteralPath $path
      if ($item.PSIsContainer) { (Get-ChildItem -LiteralPath $path -Recurse -File | Measure-Object).Count } else { 1 }
    } else {
      0
    }
  }
}
```

Expected:

- `node_modules\next` exists.
- `apps\dashboard\server.js` exists.
- `.next\static` exists and contains files.
- `public` exists and contains files.

- [ ] **Step 4: Launch unpacked executable and verify startup screen appears**

Run:

```powershell
$exe = (Resolve-Path -LiteralPath 'apps\desktop\dist\win-unpacked\StoryFactory.exe').Path
Start-Process -FilePath $exe
Start-Sleep -Seconds 2
Get-Process StoryFactory -ErrorAction SilentlyContinue | Select-Object ProcessName,Id,Path
```

Expected: `StoryFactory` process exists within 2 seconds. Visually confirm the startup screen appears quickly and then navigates to the dashboard.

- [ ] **Step 5: Verify dashboard HTTP assets from packaged app**

Run while the unpacked app is running:

```powershell
$port = 3847
$staticRoot = Resolve-Path -LiteralPath 'apps\desktop\dist\win-unpacked\resources\dashboard\apps\dashboard\.next\static'
$cssFile = Get-ChildItem -LiteralPath $staticRoot -Recurse -File -Filter '*.css' | Select-Object -First 1
$jsFile = Get-ChildItem -LiteralPath $staticRoot -Recurse -File -Filter '*.js' | Select-Object -First 1
function StaticUrl($file) {
  $rel = $file.FullName.Substring($staticRoot.Path.Length + 1).Replace('\', '/')
  "http://localhost:$port/_next/static/$rel"
}
@(
  @{ Name = 'css'; Url = StaticUrl $cssFile },
  @{ Name = 'js'; Url = StaticUrl $jsFile },
  @{ Name = 'public'; Url = "http://localhost:$port/file.svg" }
) | ForEach-Object {
  $response = Invoke-WebRequest -Uri $_.Url -UseBasicParsing -TimeoutSec 5
  [PSCustomObject]@{
    Name = $_.Name
    StatusCode = $response.StatusCode
    ContentLength = $response.RawContentLength
    ContentType = ($response.Headers['Content-Type'] -join '; ')
  }
}
```

Expected: CSS, JS, and public asset all return status code `200`.

- [ ] **Step 6: Verify writable paths**

Run:

```powershell
$log = Join-Path $env:APPDATA 'storyfactory-desktop\StoryFactory.log'
Get-Content -LiteralPath $log -Tail 120
```

Expected log evidence:

- Dashboard path is under `dist\win-unpacked\resources\dashboard` for unpacked test.
- Worker path is under `dist\win-unpacked\resources\worker`.
- SQLite path or migration output references `%APPDATA%\storyfactory-desktop\data\storyfactory.db` for packaged runtime.
- No error says `Access is denied` for `C:\Program Files\StoryFactory\resources\worker\data`.
- No worker analytics error says `Access is denied: 'logs'`.

- [ ] **Step 7: Stop the unpacked app cleanly**

Run:

```powershell
Get-Process StoryFactory -ErrorAction SilentlyContinue | Stop-Process -Force
Get-NetTCPConnection -LocalPort 3847 -State Listen -ErrorAction SilentlyContinue
```

Expected: no listener remains on port 3847.

- [ ] **Step 8: Final checkpoint**

Run:

```powershell
node --test apps/desktop/test/*.test.cjs apps/dashboard/test/*.test.cjs
python -m pytest apps/worker/tests/test_logger_paths.py -v
npm run desktop:build
```

Expected: all commands exit 0. If executing in a Git checkout, commit:

```powershell
git add .
git commit -m "fix: improve packaged desktop startup"
```

## Self-Review Results

- Spec coverage: startup loading, fail-fast server startup, Electron-as-Node, standalone dependencies, userData writable paths, author/packageManager/DEP0190 cleanup, and Turbopack warning cleanup each map to a task.
- Placeholder scan: no placeholders are intentionally left in implementation steps.
- Type and name consistency: `getRuntimePaths`, `ensureRuntimeDirs`, `buildWorkerEnv`, `runStartupSequence`, `sendStartupProgress`, `loadStartupScreen`, `showStartupError`, and `waitForDashboard` are named consistently across tests and implementation snippets.
