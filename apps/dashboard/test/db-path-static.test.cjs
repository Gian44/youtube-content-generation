const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dbSource = fs.readFileSync(path.resolve(__dirname, '..', 'src/lib/db.ts'), 'utf8');

test('production sqlite path resolution uses explicit SQLITE_PATH without filesystem probing', () => {
  assert.match(dbSource, /process\.env\.SQLITE_PATH/);
  assert.match(dbSource, /process\.env\.NODE_ENV === 'production'/);
  assert.doesNotMatch(dbSource, /const possiblePaths = \[/);
  assert.doesNotMatch(dbSource, /fs\.existsSync\(p\)/);
});

test('sqlite query conversion duplicates repeated postgres-style bind params', () => {
  assert.match(dbSource, /sqliteSql = sql\.replace\(/);
  assert.match(dbSource, /paramIndex: string/);
  assert.match(dbSource, /sqliteParams\.push\(params\[Number\(paramIndex\) - 1\]\)/);
  assert.doesNotMatch(dbSource, /const sqliteParams = \[\.\.\.params\];\s*if \(sql\.includes\('\$'\)\) {\s*sqliteSql = sql\.replace\(\/\\\$\\d\+\/g, '\?'\);/s);
});
