const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appDir = path.resolve(__dirname, '..', 'src', 'app');

function findPageFiles(dir) {
  const files = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const entryPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...findPageFiles(entryPath));
    } else if (entry.name === 'page.tsx') {
      files.push(entryPath);
    }
  }
  return files;
}

test('DB-backed pages opt out of build-time prerendering', () => {
  const dbBackedPages = findPageFiles(appDir).filter((file) => {
    const source = fs.readFileSync(file, 'utf8');
    return source.includes('@/lib/db');
  });

  assert.ok(dbBackedPages.length > 0);

  for (const page of dbBackedPages) {
    const source = fs.readFileSync(page, 'utf8');
    assert.match(
      source,
      /export const dynamic = ['"]force-dynamic['"]/,
      `${path.relative(appDir, page)} should export force-dynamic`
    );
  }
});
