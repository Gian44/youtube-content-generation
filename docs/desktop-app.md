# StoryFactory Desktop (local application)

StoryFactory is built as a **local desktop application**, not a hosted web service. The shipped product bundles:

- **Electron shell** (`apps/desktop`) — window, tray, startup flow, daily scheduler
- **Dashboard** (`apps/dashboard`) — Next.js UI served locally inside the app
- **Worker** (`apps/worker`) — Python pipeline (generate → render → upload → analytics)

Writable data (SQLite, renders, logs, encrypted channel secrets) lives under the user’s app data directory in packaged builds, not in the install folder.

There is **no planned cloud deployment** (Vercel, Docker production stacks, or CI schedulers) for this project.

## Development

Run components separately while building features:

```bash
# From repo root — dashboard only
npm run dev

# Worker (CLI)
npm run worker:daily -- --dry-run

# Full desktop shell (Electron + embedded dashboard + worker paths)
cd apps/desktop
npm install
npm run dev
```

Configure `.env` at the repo root (see `.env.example`). The desktop app reads it when packaging and in dev via `startup-env.js`.

## Building the Windows installer

From `apps/desktop`:

```bash
# 1. Build the dashboard standalone bundle
npm run build:dashboard

# 2. Package Electron (NSIS installer under apps/desktop/dist/)
npm run package
```

Requirements: Node 18+, Python 3.11+ and worker deps installed for the bundled worker, FFmpeg on PATH for rendering, and a populated root `.env` (copied into the installer as a template — users should configure secrets on first run).

Use `npm run package:dir` for an unpacked `win-unpacked` folder (faster iteration without running the installer).

## Scheduling

The desktop app runs the daily pipeline via `scheduler.js`:

```text
python -m storyfactory full --all-active
```

That processes every **active** channel sequentially. Per-channel runs are available from the dashboard or CLI; see [multi-channel.md](multi-channel.md).

## Related docs

- [Worker setup](worker-setup.md) — FFmpeg, CLI commands, troubleshooting
- [Multi-channel](multi-channel.md) — channels, integrations, OAuth per channel
- [YouTube OAuth](youtube-oauth-setup.md) — Google Cloud app setup
