const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const preloadSource = fs.readFileSync(path.resolve(__dirname, '..', 'preload.js'), 'utf8');

test('preload event listeners return cleanup callbacks', () => {
  assert.match(preloadSource, /onSchedulerUpdate/);
  assert.match(preloadSource, /removeListener\(['"]scheduler-update['"]/);
  assert.match(preloadSource, /onPipelineLog/);
  assert.match(preloadSource, /removeListener\(['"]pipeline-log['"]/);
});
