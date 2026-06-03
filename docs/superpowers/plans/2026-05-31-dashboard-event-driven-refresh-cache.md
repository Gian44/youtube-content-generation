# Dashboard Event-Driven Refresh Cache Plan

> **For Gian Myrl Renomeron:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan step-by-step.

**Goal:** Restore dashboard auto-refresh without one-second polling or SQLite hammering. The dashboard should refresh only when the Electron scheduler/worker emits a real change event, and each refresh should fetch only the dashboard slices affected by that event.

**Architecture**

- Move dashboard SQL into a shared server module with per-slice in-memory cache.
- Add partial `/api/dashboard-data?slices=...` support so the UI can request only changed data.
- Make Electron scheduler emit status/change events for pipeline and analytics lifecycle points.
- Send those events through `main.js` -> `preload.js` -> dashboard.
- Dashboard listens for events and refreshes requested slices with in-flight deduplication. No `setInterval` or dashboard timer loop.

**Slice mapping**

- `metrics`: total counts and today counts.
- `recentBatches`: recent batch list and current views.
- `pipelineStatus`: latest batch status.
- `analytics`: top performers from analytics snapshots.

**Implementation Tasks**

- [ ] Add focused tests for no dashboard timers, event subscription, partial refresh endpoint, and scheduler event wiring.
- [ ] Add `apps/dashboard/src/lib/dashboard-data.ts` with slice loaders, cache, and helpers.
- [ ] Refactor overview page and dashboard-data route to use the shared helper.
- [ ] Update `DashboardContent` to subscribe to `window.storyfactory.onSchedulerUpdate`, merge partial payloads, and dedupe concurrent refreshes.
- [ ] Update desktop scheduler to emit `dashboardSlices` events when pipeline/analytics starts or finishes.
- [ ] Wire scheduler events through Electron main and make preload listeners return cleanup callbacks.
- [ ] Run focused Node tests, dashboard build, and desktop build.
