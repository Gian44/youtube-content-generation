/**
 * StoryFactory Desktop - Electron Main Process
 *
 * Manages the application lifecycle:
 * - Shows a startup screen immediately
 * - Starts the Next.js dashboard server
 * - Creates the system tray icon
 * - Manages the BrowserWindow dashboard UI
 * - Runs the background scheduler for automated content generation
 */

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process');
const fs = require('fs');

// Setup logging to userData before the rest of the app starts.
const logFile = path.join(app.getPath('userData'), 'StoryFactory.log');
fs.mkdirSync(path.dirname(logFile), { recursive: true });
const logStream = fs.createWriteStream(logFile, { flags: 'a' });

const originalLog = console.log;
const originalError = console.error;

function logToFile(level, ...args) {
  const msg = args.map((arg) => {
    if (arg instanceof Error) return arg.stack;
    return typeof arg === 'object' ? JSON.stringify(arg) : arg;
  }).join(' ');
  const timestamp = new Date().toISOString();
  logStream.write(`[${timestamp}] [${level}] ${msg}\n`);

  if (level === 'ERROR') {
    originalError.apply(console, args);
  } else {
    originalLog.apply(console, args);
  }
}

console.log = (...args) => logToFile('INFO', ...args);
console.error = (...args) => logToFile('ERROR', ...args);
console.warn = (...args) => logToFile('WARN', ...args);

const { createTray } = require('./tray');
const {
  initScheduler,
  getSchedulerStatus,
  runPipelineNow,
  runAnalyticsNow,
  stopScheduler,
  setSchedulerEventSink,
  setPipelineLogEventSink,
} = require('./scheduler');
const {
  getRuntimePaths,
  ensureRuntimeDirs,
  getSeedDatabaseCandidates,
  bootstrapRuntimeDatabase,
  buildWorkerEnv,
} = require('./startup-env');

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
}

let mainWindow = null;
let tray = null;
let nextServerProcess = null;
let isQuitting = false;
let startupInProgress = false;
let latestStartupEvent = {
  status: 'Starting StoryFactory',
  detail: 'Preparing the desktop app.',
};

const DASHBOARD_PORT = 3847;
const isDev = !app.isPackaged;

function getDashboardUrl() {
  return `http://localhost:${DASHBOARD_PORT}`;
}

function getDashboardPath() {
  if (isDev) {
    return path.resolve(__dirname, '..', 'dashboard');
  }
  return path.join(process.resourcesPath, 'dashboard');
}

function getWorkerPath() {
  if (isDev) {
    return path.resolve(__dirname, '..', 'worker');
  }
  return path.join(process.resourcesPath, 'worker');
}

function getEnvPath() {
  if (isDev) {
    return path.resolve(__dirname, '..', '..', '.env');
  }

  const locations = [
    path.join(process.resourcesPath, '.env'),
    path.join(path.dirname(app.getPath('exe')), '.env'),
    path.join(app.getPath('userData'), '.env'),
  ];
  for (const loc of locations) {
    if (fs.existsSync(loc)) return loc;
  }
  return locations[0];
}

function loadEnvVars() {
  const envPath = getEnvPath();
  if (!fs.existsSync(envPath)) {
    console.warn(`[Main] .env not found at ${envPath}`);
    return {};
  }

  const content = fs.readFileSync(envPath, 'utf-8');
  const vars = {};
  for (const line of content.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIdx = trimmed.indexOf('=');
    if (eqIdx === -1) continue;
    const key = trimmed.slice(0, eqIdx).trim();
    const value = trimmed.slice(eqIdx + 1).trim();
    vars[key] = value;
  }
  return vars;
}

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

function sendSchedulerUpdate(event) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('scheduler-update', event);
  }
}

function sendPipelineLog(event) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('pipeline-log', event);
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

function startNextServer(config = getRuntimeConfig()) {
  return new Promise((resolve, reject) => {
    const { dashboardPath, workerEnv } = config;

    if (nextServerProcess) {
      resolve();
      return;
    }

    if (isDev) {
      console.log(`[Main] Starting Next.js dev server from: ${dashboardPath}`);
      nextServerProcess = spawn('npm', ['run', 'dev', '--', '-p', String(DASHBOARD_PORT)], {
        cwd: dashboardPath,
        env: {
          ...workerEnv,
          PORT: String(DASHBOARD_PORT),
        },
        stdio: ['ignore', 'pipe', 'pipe'],
        shell: true,
        windowsHide: true,
      });
    } else {
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
    }

    let started = false;

    nextServerProcess.stdout.on('data', (data) => {
      const output = data.toString();
      console.log(`[Next.js] ${output.trim()}`);
      if (!started && (output.includes('Ready') || output.includes('started') || output.includes(String(DASHBOARD_PORT)))) {
        started = true;
        resolve();
      }
    });

    nextServerProcess.stderr.on('data', (data) => {
      const output = data.toString().trim();
      if (output) console.error(`[Next.js ERR] ${output}`);
      if (!started && (output.includes('Ready') || output.includes('started') || output.includes(String(DASHBOARD_PORT)))) {
        started = true;
        resolve();
      }
    });

    nextServerProcess.on('error', (err) => {
      console.error('[Main] Failed to start Next.js:', err);
      if (!started) {
        started = true;
        reject(err);
      }
    });

    nextServerProcess.on('close', (code) => {
      console.log(`[Main] Next.js server exited with code ${code}`);
      nextServerProcess = null;
      if (!started) {
        started = true;
        reject(new Error(`Next.js server exited before startup completed. Exit code: ${code}`));
      }
    });

    setTimeout(() => {
      if (!started) {
        started = true;
        reject(new Error('Next.js startup timed out before the dashboard became ready.'));
      }
    }, 30000);
  });
}

function stopNextServer() {
  if (nextServerProcess) {
    console.log('[Main] Stopping Next.js server...');
    if (process.platform === 'win32') {
      try {
        execSync(`taskkill /pid ${nextServerProcess.pid} /T /F`, { windowsHide: true });
      } catch (e) {
        nextServerProcess.kill('SIGTERM');
      }
    } else {
      nextServerProcess.kill('SIGTERM');
    }
    nextServerProcess = null;
  }
}

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

function showMainWindow() {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  } else {
    createMainWindow();
    runStartupSequence();
  }
}

function setupIPC() {
  setSchedulerEventSink(sendSchedulerUpdate);
  setPipelineLogEventSink(sendPipelineLog);

  ipcMain.handle('get-scheduler-status', () => {
    return getSchedulerStatus();
  });

  ipcMain.handle('run-pipeline-now', async () => {
    return runPipelineNow(getWorkerPath(), getRuntimeConfig().workerEnv);
  });

  ipcMain.handle('run-analytics-now', async () => {
    return runAnalyticsNow(getWorkerPath(), getRuntimeConfig().workerEnv);
  });

  ipcMain.handle('get-app-version', () => {
    return app.getVersion();
  });

  ipcMain.handle('get-is-dev', () => {
    return isDev;
  });

  ipcMain.handle('startup-retry', async () => {
    stopNextServer();
    loadStartupScreen();
    await runStartupSequence();
    return { success: true };
  });

  ipcMain.handle('startup-quit', () => {
    isQuitting = true;
    app.quit();
    return { success: true };
  });
}

function runMigrations(config = getRuntimeConfig()) {
  return new Promise((resolve) => {
    const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
    const workerPath = config.workerPath;
    const workerEnv = config.workerEnv;

    console.log('[Main] Running database migrations...');

    const child = spawn(pythonCmd, ['-m', 'storyfactory.db.migrate'], {
      cwd: workerPath,
      env: workerEnv,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    child.stdout.on('data', (data) => console.log(`[Migration] ${data.toString().trim()}`));
    child.stderr.on('data', (data) => console.error(`[Migration ERR] ${data.toString().trim()}`));

    child.on('close', (code) => {
      console.log(`[Main] Database migrations finished with code ${code}`);
      resolve(code === 0);
    });

    child.on('error', (err) => {
      console.error('[Main] Failed to start migrations:', err);
      resolve(false);
    });
  });
}

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

    if (!isDev) {
      sendStartupProgress({
        status: 'Syncing local database',
        detail: config.runtimePaths.sqlitePath,
        error: null,
      });
      const seedResult = bootstrapRuntimeDatabase({
        runtimePaths: config.runtimePaths,
        seedPaths: getSeedDatabaseCandidates({
          isDev,
          resourcesPath: process.resourcesPath,
          workerPath: config.workerPath,
        }),
      });
      console.log(`[Main] Runtime database bootstrap: ${seedResult.status}`);
    }

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

app.on('second-instance', () => {
  showMainWindow();
});

app.on('before-quit', () => {
  isQuitting = true;
});

app.whenReady().then(async () => {
  console.log('[Main] StoryFactory Desktop starting...');
  console.log(`[Main] Dev mode: ${isDev}`);
  console.log(`[Main] Dashboard path: ${getDashboardPath()}`);
  console.log(`[Main] Worker path: ${getWorkerPath()}`);

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

  if (!isDev) {
    try {
      app.setLoginItemSettings({
        openAtLogin: true,
        path: process.execPath,
        args: ['--hidden'],
      });
      console.log('[Main] Auto-launch registered successfully');
    } catch (e) {
      console.error('[Main] Failed to set auto-launch settings:', e);
    }
  }
});

app.on('window-all-closed', () => {
  // Keep running in system tray.
});

app.on('activate', () => {
  showMainWindow();
});

app.on('will-quit', () => {
  stopScheduler();
  stopNextServer();
});

process.on('uncaughtException', (err) => {
  console.error('[Main] Uncaught exception:', err);
});

process.on('unhandledRejection', (reason) => {
  console.error('[Main] Unhandled rejection:', reason);
});
