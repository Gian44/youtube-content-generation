const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dashboardContentPath = path.resolve(
  __dirname,
  '..',
  'src',
  'components',
  'dashboard-content.tsx'
);

test('overview dashboard does not auto-poll dashboard data', () => {
  const source = fs.readFileSync(dashboardContentPath, 'utf8');

  assert.doesNotMatch(source, /setInterval\s*\(/);
  assert.doesNotMatch(source, /setTimeout\s*\(/);
  assert.match(source, /handleRefreshDashboard/);
});

test('overview dashboard refreshes from scheduler events instead of timers', () => {
  const source = fs.readFileSync(dashboardContentPath, 'utf8');

  assert.match(source, /onSchedulerUpdate/);
  assert.match(source, /dashboardSlices/);
  assert.match(source, /refreshDashboardSlices/);
  assert.match(source, /queuedSlicesRef/);
  assert.match(source, /URLSearchParams/);
});
