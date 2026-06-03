const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appDir = path.resolve(__dirname, '..', 'src', 'app');
const libDir = path.resolve(__dirname, '..', 'src', 'lib');
const componentsDir = path.resolve(__dirname, '..', 'src', 'components');

const SCOPED_PAGES = [
  'batches',
  'stories',
  'renders',
  'uploads',
  'analytics',
  'calendar',
  'assets',
  'api-usage',
  'compliance',
];

test('data pages scope queries to the active channel', () => {
  for (const page of SCOPED_PAGES) {
    const source = fs.readFileSync(path.join(appDir, page, 'page.tsx'), 'utf8');
    assert.match(source, /getActiveChannelId/, `${page} should resolve the active channel`);
    assert.match(source, /channel_id\s*=\s*\$/, `${page} should filter by channel_id`);
  }
});

test('dashboard-data is channel-scoped and channel-keyed', () => {
  const source = fs.readFileSync(path.join(libDir, 'dashboard-data.ts'), 'utf8');
  assert.match(source, /getActiveChannelId/);
  assert.match(source, /channel_id = \$1/);
  assert.match(source, /cacheKey\(/);
});

test('sidebar mounts the channel switcher', () => {
  const source = fs.readFileSync(path.join(componentsDir, 'sidebar.tsx'), 'utf8');
  assert.match(source, /ChannelSwitcher/);
});

test('channels lib exposes only a boolean for secrets, never the ciphertext', () => {
  const source = fs.readFileSync(path.join(libDir, 'channels.ts'), 'utf8');
  // Only a derived boolean is projected...
  assert.match(source, /AS has_secret/);
  // ...and the raw ciphertext is never returned to callers.
  assert.doesNotMatch(source, /secretsEncrypted/);
});

test('run-action targets the active channel', () => {
  const source = fs.readFileSync(path.join(appDir, 'api', 'run-action', 'route.ts'), 'utf8');
  assert.match(source, /getActiveChannelId/);
  assert.match(source, /--channel|--all-active/);
});
