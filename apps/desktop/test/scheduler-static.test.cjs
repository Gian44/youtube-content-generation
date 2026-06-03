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

test('scheduler emits dashboard refresh events for worker lifecycle changes', () => {
  assert.match(scheduler, /setSchedulerEventSink/);
  assert.match(scheduler, /emitSchedulerEvent/);
  assert.match(scheduler, /dashboardSlices/);
  assert.match(scheduler, /pipeline-started/);
  assert.match(scheduler, /pipeline-finished/);
  assert.match(scheduler, /analytics-finished/);
});
