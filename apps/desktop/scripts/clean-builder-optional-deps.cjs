const fs = require('node:fs');
const path = require('node:path');

const workspaceRoot = path.resolve(__dirname, '..', '..', '..');
const nodeModulesRoot = path.join(workspaceRoot, 'node_modules');

const optionalWasmDeps = [
  ['@emnapi', 'core'],
  ['@emnapi', 'runtime'],
  ['@emnapi', 'wasi-threads'],
  ['@napi-rs', 'wasm-runtime'],
  ['@tybys', 'wasm-util'],
];

for (const segments of optionalWasmDeps) {
  const target = path.join(nodeModulesRoot, ...segments);
  if (!fs.existsSync(target)) continue;

  const resolved = fs.realpathSync(target);
  const allowedPrefix = `${fs.realpathSync(nodeModulesRoot)}${path.sep}`;
  if (!resolved.startsWith(allowedPrefix)) {
    throw new Error(`Refusing to remove path outside node_modules: ${resolved}`);
  }

  fs.rmSync(resolved, { recursive: true, force: true });
  console.log(`[prepackage] Removed extraneous optional dependency ${segments.join('/')}`);
}
