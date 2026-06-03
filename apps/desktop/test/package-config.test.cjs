const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..', '..', '..');
const rootPackage = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const desktopPackage = JSON.parse(fs.readFileSync(path.join(root, 'apps/desktop/package.json'), 'utf8'));

function extraResourceTo(to) {
  return desktopPackage.build.extraResources.find((entry) => entry.to === to);
}

test('root package declares npm package manager', () => {
  assert.equal(rootPackage.packageManager, 'npm@11.7.0');
});

test('desktop package declares author', () => {
  assert.equal(desktopPackage.author, 'Gian Myrl Renomeron');
});

test('desktop package declares npm package manager for electron-builder', () => {
  assert.equal(desktopPackage.packageManager, 'npm@11.7.0');
});

test('desktop package suppresses known DEP0190 builder warning in package scripts', () => {
  assert.match(desktopPackage.scripts.package, /--disable-warning=DEP0190/);
  assert.match(desktopPackage.scripts.package, /electron-builder\/cli\.js --win/);
  assert.match(desktopPackage.scripts['package:dir'], /--disable-warning=DEP0190/);
  assert.match(desktopPackage.scripts['package:dir'], /electron-builder\/cli\.js --win --dir/);
});

test('desktop package cleans duplicate optional wasm dependencies before packaging', () => {
  assert.equal(desktopPackage.scripts.prepackage, 'node scripts/clean-builder-optional-deps.cjs');
  assert.equal(desktopPackage.scripts['prepackage:dir'], 'node scripts/clean-builder-optional-deps.cjs');
});

test('desktop package includes startup and runtime helper files', () => {
  assert.ok(desktopPackage.build.files.includes('startup.html'));
  assert.ok(desktopPackage.build.files.includes('startup-env.js'));
});

test('desktop package explicitly copies standalone node_modules', () => {
  const nodeModules = extraResourceTo('dashboard/node_modules');
  assert.equal(nodeModules.from, '../dashboard/.next/standalone/node_modules');
  assert.deepEqual(nodeModules.filter, ['**/*']);
});

test('desktop package includes current worker database as a runtime seed', () => {
  const seedDb = extraResourceTo('seed-data');
  assert.equal(seedDb.from, '../worker/data');
  assert.deepEqual(seedDb.filter, ['storyfactory.db']);
});

test('desktop package keeps fixed dashboard static asset destinations', () => {
  assert.equal(extraResourceTo('dashboard/apps/dashboard/.next/static').from, '../dashboard/.next/static');
  assert.equal(extraResourceTo('dashboard/apps/dashboard/public').from, '../dashboard/public');
});

test('desktop package uses buildResources-relative icon paths', () => {
  assert.equal(desktopPackage.build.win.icon, 'icon.ico');
  assert.equal(desktopPackage.build.nsis.installerIcon, 'icon.ico');
  assert.equal(desktopPackage.build.nsis.uninstallerIcon, 'icon.ico');
  assert.equal(desktopPackage.build.nsis.installerHeaderIcon, 'icon.ico');
});
