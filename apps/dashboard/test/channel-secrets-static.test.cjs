const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appDir = path.resolve(__dirname, '..', 'src', 'app');
const libDir = path.resolve(__dirname, '..', 'src', 'lib');

const callbackSource = fs.readFileSync(
  path.join(appDir, 'api', 'auth', 'youtube', 'callback', 'route.ts'),
  'utf8'
);
const secretsSource = fs.readFileSync(
  path.join(appDir, 'api', 'channels', '[id]', 'secrets', 'route.ts'),
  'utf8'
);

test('OAuth callback stores tokens via the worker, not by writing .env', () => {
  // Must not write the refresh token into an env file anymore.
  assert.doesNotMatch(callbackSource, /writeFileSync/);
  assert.doesNotMatch(callbackSource, /YOUTUBE_REFRESH_TOKEN=/);
  // Must route the token through the worker CLI.
  assert.match(callbackSource, /runWorkerCommand/);
  assert.match(callbackSource, /set-youtube-token/);
});

test('OAuth callback derives the channel from the OAuth state param', () => {
  assert.match(callbackSource, /searchParams\.get\(['"]state['"]\)/);
});

test('secrets route forwards to the worker and never returns secret values', () => {
  assert.match(secretsSource, /runWorkerCommand/);
  assert.match(secretsSource, /set-secret/);
  // The success response must not echo the incoming secrets back.
  assert.doesNotMatch(secretsSource, /json\(\{[^}]*secrets/);
});

test('worker-cli bridge inherits env so DB and master key match', () => {
  const cliSource = fs.readFileSync(path.join(libDir, 'worker-cli.ts'), 'utf8');
  assert.match(cliSource, /\.\.\.process\.env/);
  assert.match(cliSource, /storyfactory/);
});
