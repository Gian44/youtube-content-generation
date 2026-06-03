# Desktop Startup Loading and Build Cleanup Design

Date: 2026-05-30

## Context

The packaged StoryFactory desktop app can appear to take a very long time to open. The app currently waits for database migrations and the Next.js standalone server before creating the main window. In installed builds, the Next server can fail immediately, then Electron waits for the existing 30 second startup timeout before opening a window that points at an unavailable dashboard.

Evidence from `StoryFactory.log` showed these startup failures in installed builds:

- `Cannot find module 'next'` from `C:\Program Files\StoryFactory\resources\dashboard\apps\dashboard\server.js`.
- `ERR_CONNECTION_REFUSED` when Electron loads `http://localhost:3847/`.
- Permission errors writing `data` and `logs` under `C:\Program Files\StoryFactory\resources\worker`.

The build also reports non-blocking warnings:

- Missing desktop package author.
- Electron Builder package manager detection fallback.
- Node `[DEP0190]` warning from Electron Builder internals on Node 24.
- Duplicate dependency reference warning for optional WASM-related packages.
- Turbopack file tracing warning around dashboard database path probing.

## Goals

- Show a visible startup screen immediately when the desktop app opens.
- Report startup progress while migrations and the dashboard server start.
- Fail fast and show a useful error if the dashboard server exits before it is ready.
- Ensure installed builds can start the Next standalone server without depending on a system Node installation or repo-level `node_modules`.
- Move production writable data out of `C:\Program Files`.
- Clean up requested build metadata and warnings where it is safe to do so.

## Non-Goals

- Do not redesign the dashboard UI.
- Do not replace the Next.js server architecture with a static export.
- Do not package Python or remove the app's current dependency on a local Python runtime.
- Do not make broad worker pipeline refactors beyond writable path handling needed for packaged desktop runs.

## Approach

Use an immediate Electron startup shell with background initialization.

Electron will create the main window as soon as `app.whenReady()` runs and load a local `startup.html` file. The main process will then run startup tasks in sequence and send progress events to the renderer:

1. Preparing app data.
2. Running database migrations.
3. Starting dashboard.
4. Opening dashboard.

When the dashboard server is confirmed ready, Electron will navigate the same window to `http://localhost:3847`. If the dashboard process exits before readiness, the startup screen will switch to an error state instead of waiting for the old 30 second timeout.

## Runtime Architecture

### Startup Window

Add a local startup page packaged with the desktop app. It should be simple, self-contained, and not depend on the Next server. It will listen for IPC messages from the main process and render:

- current status text,
- lightweight progress indicator,
- error details when startup fails,
- retry and quit actions if startup fails.

### Main Process Startup

Refactor startup orchestration in `apps/desktop/main.js`:

- Create the window before migrations and server startup.
- Register IPC handlers before loading the startup page.
- Send progress updates through `webContents.send`.
- Run migrations and Next startup after the window exists.
- Navigate to the dashboard only after the server is actually reachable.
- Treat an early Next process exit as a startup failure.

The old timeout can remain as a final guard, but it must not report success when the server has already exited.

### Next Server Launch

In production, launch the standalone Next server with Electron's bundled runtime instead of system `node`:

- command: `process.execPath`
- env: `ELECTRON_RUN_AS_NODE=1`
- args: dashboard `server.js`

This makes the packaged app independent from whichever Node version is installed globally.

### Packaged Dashboard Dependencies

Electron Builder must copy the standalone build's root `node_modules` into:

`resources/dashboard/node_modules`

The Next standalone `server.js` calls `require('next')` from `resources/dashboard/apps/dashboard/server.js`, and Node resolution will find `resources/dashboard/node_modules/next`.

The existing fixed asset destinations remain:

- `resources/dashboard/apps/dashboard/.next/static`
- `resources/dashboard/apps/dashboard/public`

### Writable App Data

Production writes should use Electron `userData`, not `process.resourcesPath` or `C:\Program Files`.

Main process will compute:

- app data root: `app.getPath('userData')`
- SQLite DB: `<userData>/data/storyfactory.db`
- local storage: `<userData>/data/storage`
- worker logs: `<userData>/logs`

These paths will be passed through environment variables to dashboard, migrations, scheduler, and analytics:

- `SQLITE_PATH`
- `LOCAL_STORAGE_PATH`
- `STORYFACTORY_LOG_DIR`

The worker logger should honor `STORYFACTORY_LOG_DIR`, falling back to `./logs` for existing dev behavior.

## Build Warning Cleanup

### Author

Add `author: "Gian Myrl Renomeron"` to `apps/desktop/package.json`.

### Package Manager

Add a root `packageManager` field matching the local npm major version. Current local tool output showed npm `11.7.0`, so use:

`"packageManager": "npm@11.7.0"`

### DEP0190

The `[DEP0190]` warning is emitted by Electron Builder's dependency collector using `shell: true` on Windows. Electron Builder 26.8.1 is currently the latest observed version, so there is no newer builder release to upgrade to for this warning.

Use a build script wrapper that invokes Electron Builder with Node's warning disable flag:

`node --disable-warning=DEP0190 ./node_modules/electron-builder/cli.js --win`

This suppresses only this known tooling warning during packaging.

### Duplicate Dependency References

The duplicate reference warning is generated by Electron Builder while collecting optional WASM-related packages. It is not a runtime failure. The safe cleanup path is:

- inspect whether any listed packages are extraneous,
- run a non-destructive npm cleanup if available,
- avoid manually deleting packages that are still referenced by optional dependencies.

If the warning persists after safe cleanup, document it as an upstream Electron Builder collector warning rather than risking dependency breakage.

### Turbopack NFT Warning

The Turbopack warning comes from broad `process.cwd()` and filesystem probing in dashboard DB path resolution. The preferred fix is to make dashboard database path resolution deterministic:

- in production, require or default to explicit `SQLITE_PATH`;
- in development, keep a small set of scoped fallback paths;
- avoid broad root-relative probing that makes file tracing think the whole project may be relevant.

This warning is separate from the immediate startup UX fix but should be addressed in the same cleanup pass if it stays low-risk.

## Testing and Verification

Run from the monorepo root:

- `npm run desktop:build`
- verify `apps/desktop/dist/win-unpacked/resources/dashboard/node_modules/next` exists
- verify `apps/desktop/dist/win-unpacked/resources/dashboard/apps/dashboard/.next/static` exists and contains files
- verify `apps/desktop/dist/win-unpacked/resources/dashboard/apps/dashboard/public` exists and contains files
- launch `apps/desktop/dist/win-unpacked/StoryFactory.exe`
- confirm startup window appears quickly
- confirm dashboard replaces startup window when ready
- confirm dashboard CSS, JS, and public assets return HTTP 200
- confirm logs and SQLite data are written under Electron `userData`, not under `C:\Program Files`
- confirm no requested build metadata warnings remain, except any documented upstream duplicate dependency collector warning if it cannot be safely removed

## Risks

- Launching Next with Electron as Node may expose environment differences from system Node. This should be verified by starting the packaged app, not only the standalone server from the terminal.
- Moving data to `userData` changes where new packaged installs store SQLite and local files. Existing data under `resources/worker/data` may need manual migration if users already have real production data there.
- Suppressing `[DEP0190]` hides a known builder warning, not an app warning. The suppression must stay scoped to build scripts.
