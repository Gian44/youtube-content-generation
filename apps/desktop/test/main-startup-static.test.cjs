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

test('main process bridges scheduler updates to renderer windows', () => {
  assert.match(mainSource, /setSchedulerEventSink/);
  assert.match(mainSource, /function sendSchedulerUpdate/);
  assert.match(mainSource, /webContents\.send\(['"]scheduler-update['"]/);
});
