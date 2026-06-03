const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dashboardDataPath = path.resolve(__dirname, '..', 'src', 'lib', 'dashboard-data.ts');
const routePath = path.resolve(__dirname, '..', 'src', 'app', 'api', 'dashboard-data', 'route.ts');
const overviewPagePath = path.resolve(__dirname, '..', 'src', 'app', 'page.tsx');

test('dashboard data is loaded through a shared slice cache', () => {
  const source = fs.readFileSync(dashboardDataPath, 'utf8');

  assert.match(source, /ALL_DASHBOARD_SLICES/);
  assert.match(source, /sliceCache/);
  assert.match(source, /getDashboardData/);
  assert.match(source, /getFullDashboardData/);
  assert.match(source, /loadDashboardSlice/);
  assert.match(source, /metrics/);
  assert.match(source, /recentBatches/);
  assert.match(source, /pipelineStatus/);
  assert.match(source, /analytics/);
});

test('dashboard data API supports partial slice refreshes', () => {
  const source = fs.readFileSync(routePath, 'utf8');

  assert.match(source, /request:\s*Request/);
  assert.match(source, /searchParams\.get\(['"]slices['"]\)/);
  assert.match(source, /getDashboardData/);
  assert.doesNotMatch(source, /from ['"]@\/lib\/db['"]/);
});

test('overview page reuses full dashboard data helper instead of duplicating SQL', () => {
  const source = fs.readFileSync(overviewPagePath, 'utf8');

  assert.match(source, /getFullDashboardData/);
  assert.doesNotMatch(source, /from ['"]@\/lib\/db['"]/);
});
