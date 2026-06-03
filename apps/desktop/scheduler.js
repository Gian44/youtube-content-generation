/**
 * StoryFactory Desktop — Scheduler
 * 
 * Schedules the full content pipeline (generate → render → upload)
 * to run daily at 9:00 AM Manila Standard Time (Asia/Manila, UTC+8).
 * 
 * Uses node-cron for reliable cron-based scheduling.
 */

const cron = require('node-cron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let scheduledTask = null;
let scheduledAnalyticsTask = null;
let currentProcess = null;
let currentAnalyticsProcess = null;
let pipelineLogs = [];
let isAnalyticsRunning = false;
let schedulerEventSink = null;
let pipelineLogEventSink = null;
let lastPipelineProgressKey = null;

const pipelineProgressMilestones = [
  { key: 'batch-created', phrase: 'batch created', dashboardSlices: ['pipelineStatus', 'recentBatches'] },
  { key: 'topic-selected', phrase: 'topic selected', dashboardSlices: ['pipelineStatus', 'recentBatches'] },
  { key: 'stories-generated', phrase: 'running policy checks', dashboardSlices: ['pipelineStatus', 'recentBatches', 'metrics'] },
  { key: 'policy-checked', phrase: 'stories approved', dashboardSlices: ['pipelineStatus', 'recentBatches'] },
  { key: 'tts-complete', phrase: 'generating captions', dashboardSlices: ['pipelineStatus'] },
  { key: 'captions-complete', phrase: 'caption files', dashboardSlices: ['pipelineStatus'] },
  { key: 'assets-collected', phrase: 'collected story-relevant assets', dashboardSlices: ['pipelineStatus'] },
];

const state = {
  isRunning: false,
  lastRun: null,
  lastRunStatus: null, // 'success' | 'error'
  lastRunError: null,
  nextRun: null,
  logs: [],
};

function getWorkerSpawnEnv(envVars = {}) {
  return {
    ...process.env,
    ...envVars,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    SQLITE_PATH: envVars.SQLITE_PATH,
    LOCAL_STORAGE_PATH: envVars.LOCAL_STORAGE_PATH,
    STORYFACTORY_LOG_DIR: envVars.STORYFACTORY_LOG_DIR,
  };
}

function ensureWorkerDataDir(workerDir, envVars = {}) {
  const dataDir = envVars.SQLITE_PATH
    ? path.dirname(envVars.SQLITE_PATH)
    : path.resolve(workerDir, 'data');
  fs.mkdirSync(dataDir, { recursive: true });
}

function setSchedulerEventSink(handler) {
  schedulerEventSink = typeof handler === 'function' ? handler : null;
}

function setPipelineLogEventSink(handler) {
  pipelineLogEventSink = typeof handler === 'function' ? handler : null;
}

function emitSchedulerEvent(eventType, payload = {}) {
  if (!schedulerEventSink) return;

  schedulerEventSink({
    ...getSchedulerStatus(),
    eventType,
    dashboardSlices: payload.dashboardSlices || [],
    message: payload.message || null,
    timestamp: new Date().toISOString(),
  });
}

function emitPipelineLog(entry) {
  if (pipelineLogEventSink) {
    pipelineLogEventSink(entry);
  }
}

function getDashboardSlicesForPipelineLog(message) {
  const lowerMessage = String(message).toLowerCase();
  const milestone = pipelineProgressMilestones.find((item) => lowerMessage.includes(item.phrase));

  if (!milestone || milestone.key === lastPipelineProgressKey) {
    return [];
  }

  lastPipelineProgressKey = milestone.key;
  return milestone.dashboardSlices;
}

/**
 * Initialize the daily scheduler.
 * Runs at 9:00 AM Manila time (PHT, UTC+8).
 */
function initScheduler(workerPath, envVars) {
  // Cron: minute hour day month weekday
  // 9:00 AM Manila = 01:00 UTC (UTC+8)
  // Use node-cron timezone support
  scheduledTask = cron.schedule('0 9 * * *', () => {
    console.log('[Scheduler] Triggered daily pipeline at 9:00 AM PHT');
    runPipeline(workerPath, envVars);
  }, {
    scheduled: true,
    timezone: 'Asia/Manila',
  });

  // Scheduled analytics update: runs every 3 hours (at minute 0 of every 3rd hour)
  scheduledAnalyticsTask = cron.schedule('0 */3 * * *', () => {
    console.log('[Scheduler] Triggered scheduled analytics update');
    runAnalytics(workerPath, envVars);
  }, {
    scheduled: true,
    timezone: 'Asia/Manila',
  });

  updateNextRun();
  console.log('[Scheduler] Daily pipeline scheduled for 9:00 AM Asia/Manila');
  console.log('[Scheduler] Analytics update scheduled for every 3 hours');
  emitSchedulerEvent('scheduler-initialized');

  // Trigger initial analytics run on startup after a 5 second delay to populate stats
  setTimeout(() => {
    console.log('[Scheduler] Triggering initial startup analytics update...');
    runAnalytics(workerPath, envVars);
  }, 5000);
}

/**
 * Run the full pipeline immediately.
 */
function runPipelineNow(workerPath, envVars) {
  if (state.isRunning) {
    console.log('[Scheduler] Pipeline already running — skipping');
    return { success: false, message: 'Pipeline is already running' };
  }

  console.log('[Scheduler] Manual pipeline trigger');
  runPipeline(workerPath, envVars);
  return { success: true, message: 'Pipeline started' };
}

/**
 * Execute the Python worker pipeline.
 */
function runPipeline(workerPath, envVars) {
  if (state.isRunning) return;

  state.isRunning = true;
  state.logs = [];
  pipelineLogs = [];
  lastPipelineProgressKey = null;
  emitSchedulerEvent('pipeline-started', {
    dashboardSlices: ['pipelineStatus'],
  });

  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  const workerDir = workerPath;

  ensureWorkerDataDir(workerDir, envVars);

  console.log(`[Scheduler] Running: ${pythonCmd} -m storyfactory full --all-active`);
  console.log(`[Scheduler] CWD: ${workerDir}`);

  currentProcess = spawn(pythonCmd, ['-m', 'storyfactory', 'full', '--all-active'], {
    cwd: workerDir,
    env: getWorkerSpawnEnv(envVars),
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  currentProcess.stdout.on('data', (data) => {
    const line = data.toString().trim();
    if (line) {
      console.log(`[Worker] ${line}`);
      addLog('stdout', line);
    }
  });

  currentProcess.stderr.on('data', (data) => {
    const line = data.toString().trim();
    if (line) {
      console.error(`[Worker ERR] ${line}`);
      addLog('stderr', line);
    }
  });

  currentProcess.on('error', (err) => {
    console.error('[Scheduler] Failed to start worker:', err);
    state.isRunning = false;
    state.lastRun = formatDateTime(new Date());
    state.lastRunStatus = 'error';
    state.lastRunError = err.message;
    currentProcess = null;
    emitSchedulerEvent('pipeline-start-error', {
      dashboardSlices: ['pipelineStatus'],
      message: err.message,
    });
  });

  currentProcess.on('close', (code) => {
    console.log(`[Scheduler] Worker exited with code ${code}`);
    state.isRunning = false;
    state.lastRun = formatDateTime(new Date());
    state.lastRunStatus = code === 0 ? 'success' : 'error';
    state.lastRunError = code !== 0 ? `Exit code ${code}` : null;
    currentProcess = null;
    updateNextRun();
    emitSchedulerEvent('pipeline-finished', {
      dashboardSlices: ['pipelineStatus', 'metrics', 'recentBatches'],
      message: code === 0 ? 'Pipeline finished successfully' : `Pipeline exited with code ${code}`,
    });

    // Send notification
    try {
      const { showNotification } = require('./tray');
      if (code === 0) {
        showNotification('Pipeline Complete ✅', 'Daily content generation and upload finished successfully!');
      } else {
        showNotification('Pipeline Failed ❌', `The pipeline exited with code ${code}. Check logs for details.`);
      }
    } catch (e) {
      // Tray may not be loaded yet
    }
  });
}

function addLog(type, message) {
  const entry = {
    time: new Date().toISOString(),
    type,
    message,
  };
  state.logs.push(entry);
  // Keep last 500 log entries
  if (state.logs.length > 500) {
    state.logs = state.logs.slice(-500);
  }
  emitPipelineLog(entry);

  const dashboardSlices = getDashboardSlicesForPipelineLog(message);
  if (dashboardSlices.length > 0) {
    emitSchedulerEvent('pipeline-progress', {
      dashboardSlices,
      message,
    });
  }
}

function updateNextRun() {
  // Calculate next 9 AM PHT
  const now = new Date();
  const manila = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Manila' }));
  
  const next = new Date(manila);
  next.setHours(9, 0, 0, 0);
  
  if (next <= manila) {
    next.setDate(next.getDate() + 1);
  }
  
  // Format for display
  state.nextRun = next.toLocaleString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
    timeZone: 'Asia/Manila',
  }) + ' PHT';
}

function formatDateTime(date) {
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
    timeZone: 'Asia/Manila',
  }) + ' PHT';
}

/**
 * Get current scheduler status.
 */
function getSchedulerStatus() {
  return {
    isRunning: state.isRunning,
    lastRun: state.lastRun,
    lastRunStatus: state.lastRunStatus,
    lastRunError: state.lastRunError,
    nextRun: state.nextRun,
    isAnalyticsRunning,
    logCount: state.logs.length,
    recentLogs: state.logs.slice(-20),
  };
}

/**
 * Stop the scheduler and any running pipeline.
 */
function stopScheduler() {
  if (scheduledTask) {
    scheduledTask.stop();
    scheduledTask = null;
  }
  if (scheduledAnalyticsTask) {
    scheduledAnalyticsTask.stop();
    scheduledAnalyticsTask = null;
  }
  if (currentProcess) {
    try {
      currentProcess.kill('SIGTERM');
    } catch (e) {
      // Process may have already exited
    }
    currentProcess = null;
  }
  if (currentAnalyticsProcess) {
    try {
      currentAnalyticsProcess.kill('SIGTERM');
    } catch (e) {
      // Process may have already exited
    }
    currentAnalyticsProcess = null;
  }
}

/**
 * Execute the Python worker analytics script.
 */
function runAnalytics(workerPath, envVars) {
  if (isAnalyticsRunning || state.isRunning) {
    console.log('[Scheduler] Worker is busy — skipping analytics update');
    return;
  }

  isAnalyticsRunning = true;
  emitSchedulerEvent('analytics-started');

  const pythonCmd = process.platform === 'win32' ? 'python' : 'python3';
  const workerDir = workerPath;

  console.log(`[Scheduler] Running: ${pythonCmd} -m storyfactory analytics`);
  console.log(`[Scheduler] CWD: ${workerDir}`);

  currentAnalyticsProcess = spawn(pythonCmd, ['-m', 'storyfactory', 'analytics'], {
    cwd: workerDir,
    env: getWorkerSpawnEnv(envVars),
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });

  currentAnalyticsProcess.stdout.on('data', (data) => {
    const line = data.toString().trim();
    if (line) {
      console.log(`[Worker Analytics] ${line}`);
    }
  });

  currentAnalyticsProcess.stderr.on('data', (data) => {
    const line = data.toString().trim();
    if (line) {
      console.error(`[Worker Analytics ERR] ${line}`);
    }
  });

  currentAnalyticsProcess.on('error', (err) => {
    console.error('[Scheduler] Failed to start analytics worker:', err);
    isAnalyticsRunning = false;
    currentAnalyticsProcess = null;
    emitSchedulerEvent('analytics-start-error', {
      message: err.message,
    });
  });

  currentAnalyticsProcess.on('close', (code) => {
    console.log(`[Scheduler] Analytics worker exited with code ${code}`);
    isAnalyticsRunning = false;
    currentAnalyticsProcess = null;
    emitSchedulerEvent('analytics-finished', {
      dashboardSlices: code === 0 ? ['analytics', 'recentBatches'] : [],
      message: code === 0 ? 'Analytics finished successfully' : `Analytics exited with code ${code}`,
    });
  });
}

/**
 * Run the analytics update immediately.
 */
function runAnalyticsNow(workerPath, envVars) {
  if (isAnalyticsRunning) {
    console.log('[Scheduler] Analytics update already running — skipping');
    return { success: false, message: 'Analytics update is already running' };
  }

  console.log('[Scheduler] Manual analytics update trigger');
  runAnalytics(workerPath, envVars);
  return { success: true, message: 'Analytics update started' };
}

module.exports = {
  initScheduler,
  getSchedulerStatus,
  runPipelineNow,
  runAnalyticsNow,
  stopScheduler,
  setSchedulerEventSink,
  setPipelineLogEventSink,
};
