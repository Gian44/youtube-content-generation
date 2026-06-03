# Project scope

Use this when planning or implementing features for StoryFactory.

## Product target

- **Local desktop application** (`apps/desktop`) — Electron shell packaging dashboard + worker
- **Multi-channel** content factory — see [multi-channel.md](multi-channel.md)
- **Not in scope:** cloud deployment (Vercel, hosted dashboard, production Docker stacks, GitHub Actions schedulers, remote databases as a requirement)

## Development vs shipped

| Layer | Dev | Shipped (desktop) |
|-------|-----|-------------------|
| UI | `npm run dev` or Electron `apps/desktop` | Next standalone inside installer |
| Worker | `npm run worker:*` from repo | Bundled Python under `resources/worker` |
| Data | `./data/` under worker (or env paths) | Electron `userData` paths via `startup-env.js` |
| Schedule | CLI / manual | Desktop `scheduler.js` → `full --all-active` |

## Planning implications

- Prefer changes that work in **packaged** Electron paths (userData, bundled resources), not only repo-relative paths.
- Dashboard API routes call the worker via `worker-cli`; secrets stay in the worker/DB layer.
- Do not add deployment steps, hosting guides, or CI pipeline requirements to feature plans unless the user explicitly expands scope.
