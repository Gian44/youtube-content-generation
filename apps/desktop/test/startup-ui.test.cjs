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
