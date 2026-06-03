# Plan: Per-Channel Content Profiles + Global Integrations Split

**Status:** Draft for review (Phase 1). Do not implement until approved.
**Scope:** Local desktop app only (see [project-scope.md](../project-scope.md)). No cloud/CI/Docker work.
**Date:** 2026-06-03

This plan covers two related changes to the existing app — it **extends** current code, no rewrite:

- **Goal A — Per-channel content freedom.** Each channel composes *what it generates* via explicit flags (shorts only / long-form only / both, counts, length) plus niche/style/prompt overrides for the *kind* of content. Not a fixed list of presets.
- **Goal B — Global integrations vs per-channel YouTube.** Shared provider credentials (OpenAI, Gemini, Pexels, Pixabay, shared TTS, storage) move to one app-level store. YouTube stays per-channel.

## Decisions locked in (from brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Content model | **Explicit per-channel flags** in `channel.config` (composable knobs, no fixed preset list). |
| 2 | Shared-cred storage | **New `app_integrations` table** (Fernet, mirrors `channel_integrations`); YouTube per-channel. |
| 3 | Migration conflict policy | **Default channel wins**; differing values from other channels are logged for manual reconciliation. |
| 4 | Profile breadth | Fully customizable per channel (e.g. "1 short/day", "long-form sleep facts") via flags + niche + prompt overrides — **not** limited to N named profiles. |

Optional UI "quick-start" buttons (e.g. *Drama shorts*, *Sleep facts*, *Reddit mix*) may pre-fill the flags as a convenience, but they are **client-side sugar only** — nothing is stored as a "profile name". Marked optional in the task breakdown.

---

## 1. Gap analysis — what assumes a fixed shape or per-channel keys

### Goal A (pipeline hardcoded to one shape)
- [`pipeline/daily.py:154`](../../apps/worker/storyfactory/pipeline/daily.py#L154) — `long_form_count = random.randint(2, 5)` is a naked literal; `long_form_per_day` is defined in config ([`config.py:57`](../../apps/worker/storyfactory/config.py#L57)) but **never read** by the pipeline.
- [`pipeline/daily.py:143-146`](../../apps/worker/storyfactory/pipeline/daily.py#L143-L146) — shorts count honors `effective_config` bounds (good) but always runs; there is no "shorts off" gate.
- `_run_for_channel` ([`daily.py:94-342`](../../apps/worker/storyfactory/pipeline/daily.py#L94-L342)) always runs story-gen steps 3 & 4 and render steps 9 & 10 — no conditional gates.
- [`pipeline/upload.py`](../../apps/worker/storyfactory/pipeline/upload.py) `_upload_for_channel` uploads **all** completed render jobs for the channel with **no `type` filter** → skipping must happen at generation/render time, not at upload.
- Asset search queries are hardcoded per category in `asset_collector.py` (`CATEGORY_SEARCH_QUERIES`) — out of scope to make profile-driven now (noted as future).
- `effective_config(key, default)` ([`channel_context.py:150`](../../apps/worker/storyfactory/channel_context.py#L150)) + `content_config_defaults()` ([`config.py:133`](../../apps/worker/storyfactory/config.py#L133)) are the established extension points. Per-channel prompts already work via `_resolve_prompt` (channel-scoped row → global fallback) ([`story_generator.py:33-47`](../../apps/worker/storyfactory/pipeline/story_generator.py#L33-L47)).

### Goal B (credentials per-channel for everything)
- Provider registry `PROVIDERS` dict in `integrations/registry.py` is the single source of truth (required/optional secrets, config fields, env-var maps). **No `scope` concept yet** — every provider is treated as per-channel.
- Shared-secret resolution funnels through `resolve_api_key()` / `youtube_credentials()` in [`channel_context.py:166-211`](../../apps/worker/storyfactory/channel_context.py#L166-L211). Call sites (all must keep working): `ai_provider.py:58,123`, `asset_collector.py:226,344,345`, `caption_service.py:149`, `tts_service.py:118,148`, `youtube_uploader.py:150,197`, `analytics.py:96`.
- `SettingsModel` is plain key/value (no encryption, no service wrapper) — not suitable as-is for secrets.
- Migrations are a **custom runner** (`db/migrations.py`, append-only `MIGRATIONS` list + `schema_migrations` tracking table) — **not Alembic**. New migration = define `_mNNNN_name(session)` and append a tuple.
- `ensure_default_channel` → `import_env_into_channel` ([`services/channel_service.py`](../../apps/worker/storyfactory/services/channel_service.py)) writes a `channel_integrations` row for **every** provider from env.
- Dashboard `PROVIDER_META` ([`channels-manager.tsx:43-53`](../../apps/dashboard/src/components/channels-manager.tsx#L43-L53)) renders secret inputs for all 9 providers per channel card. Settings page ([`settings/page.tsx`](../../apps/dashboard/src/app/settings/page.tsx)) is **read-only**. Dashboard redefines types locally (does not import `packages/shared`).
- `contentStyle` column exists, is selected ([`lib/channels.ts:21`](../../apps/dashboard/src/lib/channels.ts#L21)) but is **never rendered** — a ready slot for the Content section.
- **Security note:** secrets are passed to the worker via **CLI argv** (`--secret k=v`, `--token`), visible in process listings. Flag for security review; add a stdin-JSON path for the new settings-secrets route (see §7).

---

## 2. Content model — explicit flags + state machine

### 2.1 New `channel.config` keys (snake_case JSON; defaults in `content_config_defaults()`)
All optional; absent → app-level default → current behavior preserved.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enable_shorts` | bool | `true` | Produce & render Shorts for this channel. |
| `enable_long_form` | bool | `true` | Produce & render the long-form compilation video. |
| `shorts_per_day_min` | int 0–10 | `3` | (existing) lower bound of Shorts/day when enabled. |
| `shorts_per_day_max` | int 0–10 | `5` | (existing) upper bound of Shorts/day when enabled. |
| `long_form_per_day` | int 0–5 | `1` | (existing, **now wired**) number of long-form videos/day when enabled. |
| `long_form_segments_min` | int 1–20 | `2` | replaces hardcoded `randint(2,5)` lower bound (stories composed into one long video). |
| `long_form_segments_max` | int 1–20 | `5` | upper bound (replaces the `5`). |
| `long_form_target_minutes` | num 1–60 | `10` | (existing, **now read**) used as guidance for segment count targeting. |
| `long_form_includes_shorts` | bool | `true` | Whether approved Shorts are folded into the long-form compilation (current behavior = true). |

`niche`, `content_style`, and per-channel `PromptTemplate` overrides remain the mechanism for the *kind* of content (drama vs facts vs calm narration). No new prompt names required; a channel overrides `short_story_generation` / `long_form_story_generation` by name.

### 2.2 Validation (worker + Zod + UI)
- **Reject** `enable_shorts == false && enable_long_form == false` ("a channel must produce at least one output").
- `shorts_per_day_min <= shorts_per_day_max`; `long_form_segments_min <= long_form_segments_max`.
- When `enable_shorts == false`, shorts count keys are ignored (UI hides them). When `enable_long_form == false`, long-form keys ignored (UI hides them).
- Existing `long_form_per_day` upper bound stays ≤ 5.

### 2.3 Pipeline state machine (`_run_for_channel`)
```
                +-- enable_shorts? --no--+          +-- enable_long_form? --no--+
                |                         |          |                           |
 batch -> topic +-- yes --> gen shorts ---+--policy--+-- yes --> gen long stories+--policy
                                                                                  |
 policy(approved) -> TTS -> captions -> assets (only for stories that exist)
                                                                                  |
   render:  if enable_shorts  -> render each approved Short  (RenderJob type=short)
            if enable_long_form && (approved_long or long_form_includes_shorts)
                               -> render 1..long_form_per_day long videos (type=long_form)
                                                                                  |
   upload:  uploads only the RenderJobs that were created (no new filter needed)
```
Concrete gate edits in `_run_for_channel`:
1. Early guard: validate at least one output enabled; fail batch with clear error otherwise.
2. Step 3: `short_stories = generate_short_stories(...)` only if `enable_shorts`, else `[]`.
3. Step 4: `long_stories = generate_long_form_stories(batch, long_form_count)` only if `enable_long_form`, where `long_form_count = random.randint(long_form_segments_min, long_form_segments_max)` (replaces hardcoded `randint(2,5)`).
4. Step 9 (render Shorts): wrap in `if enable_shorts`.
5. Step 10 (render long-form): wrap in `if enable_long_form`; compose from `(approved_shorts if long_form_includes_shorts else []) + approved_long`; skip if nothing to compose.
6. Batch metadata (`shorts_count`, `long_form_count`) already reflects actuals — keep; ensure they read the gated lists.
7. Because skipped outputs never create RenderJobs, `upload.py` needs **no** change (it only sees jobs that exist). Add a defensive note/test rather than a filter.

`--all-active` and the desktop scheduler are unaffected — each channel's run reads its own `effective_config`. Policy/compliance stages are unchanged.

---

## 3. App-level integrations — data model + resolver

### 3.1 Provider scope
Add `scope: Literal["app", "channel"]` to `ProviderSpec` in `integrations/registry.py`:
- `scope="app"`: `text.openai`, `text.gemini`, `tts.openai`, `tts.gemini`, `assets.pexels`, `assets.pixabay`, `storage.local`, `storage.r2`.
- `scope="channel"`: `youtube`.

Both the resolver and the UI read `scope` from the registry (single source of truth — no duplicated lists).

### 3.2 New model `AppIntegration` (`db/models.py`)
Mirror of `ChannelIntegration` minus `channel_id`:
```
app_integrations(
  id PK, provider_key (unique, indexed), enabled bool,
  config JSON, secrets_encrypted Text (Fernet), status, status_detail,
  last_checked_at, created_at, updated_at
)
```

### 3.3 New service `services/app_integration_service.py`
Mirror the proven `channel_service.set_integration` pattern (decrypt-merge-reencrypt via `crypto`):
- `get_app_integration(session, provider_key)`
- `list_app_integrations(session)`
- `set_app_integration(session, provider_key, *, enabled, secrets, config, commit)`
- `build_app_resolver(session) -> IntegrationResolver` (reuse the existing `IntegrationResolver` class with app-level data dict).
- `import_env_into_app(session, commit)` — for app-scoped providers, seed from `registry` env maps (mirrors `import_env_into_channel`, app-scoped subset).

### 3.4 Resolver changes (`channel_context.py`)
- Load an app-level resolver once (lazy module-level cache keyed by nothing, invalidated on writes; or build in `build_context`). Keep it simple: a `get_app_resolver()` that queries `app_integrations` and caches per-process; CLI writes reset the cache.
- `resolve_api_key(provider_key, fallback_attr)`:
  - If provider `scope == "app"`: return `app_resolver.secret(provider_key, "api_key")` → else env `getattr(settings, fallback_attr)` (env fallback **always allowed** at app level).
  - If `scope == "channel"`: keep current per-channel behavior.
- `youtube_credentials()`: **unchanged** (refresh token per-channel; `client_id`/`client_secret` from per-channel override → app-level → env, as today).
- `resolve_model(provider_key, ...)`: for app-scoped providers, read model/config from `app_integrations.config` instead of the channel integration.

### 3.5 CLI (`__main__.py`)
Add a `settings` command group (parallel to `channel`):
- `settings set-secret --provider text.openai --secret api_key=... [--config model=gpt-4o] [--enable|--disable]`
- `settings status` / `settings list` (no secret values, masked)
- `settings import-env` (seed app providers from `.env`)
Keep `channel set-youtube-token` where it is (per-channel). Update `worker:channel` docs.

---

## 4. UI / UX

### 4.1 Settings → Integrations (new, write-capable)
- Section on [`settings/page.tsx`](../../apps/dashboard/src/app/settings/page.tsx) (currently read-only) listing **app-scoped** providers from the registry.
- Reuse a variant of `IntegrationRow` (`channels-manager.tsx`) for: enable/disable toggle, masked secret inputs (`type=password`), config fields (model, bucket, path), connection-health badge.
- New routes:
  - `GET /api/settings/integrations` → status + config (never secrets; returns `hasSecret: boolean`).
  - `POST /api/settings/integrations` → `{ provider, secrets, config, enabled }` → worker `settings set-secret` (via **stdin JSON**, see §7).
- Top of page keeps the existing env-status badges but reframed as "fallback from `.env`".

### 4.2 Channels → [channel]
- **Remove** shared-provider secret fields from `PROVIDER_META` / the channel card (drive card from registry `scope == "channel"`). Keep only **YouTube** (OAuth connect/disconnect/status) and the channel content config.
- **Add a "Content" section** bound to `channel.config`:
  - Output toggles: `enable_shorts`, `enable_long_form` (the "shorts only / long only / both" choice).
  - Conditional fields: Shorts/day min–max (when shorts on); long-form videos/day, segments min–max, target minutes, "include shorts in long-form" (when long-form on).
  - `niche` + `content_style` text areas (already in DB; `content_style` finally surfaced) with helper copy ("e.g. 100+ random facts, calm narration for sleep").
  - Link to per-channel prompt overrides (advanced).
  - Optional quick-start buttons (client-side prefill only).
- Config still saved through the existing `PATCH /api/channels/[id]` config path; validation mirrored client-side.

### 4.3 packages/shared sync
- `ChannelConfigSchema`: add `enable_shorts`, `enable_long_form`, `long_form_segments_min/max`, `long_form_includes_shorts` (all `.optional()` via `.partial()`), and the cross-field refinements (≥1 output, min≤max).
- `IntegrationProviderSpec`: add `scope: 'app' | 'channel'`.
- Add `AppIntegration` type mirroring `ChannelIntegration` (minus `channelId`).
- Keep the worker `registry.py` and shared constants aligned (same provider keys + scope).

---

## 5. Migration & backward compatibility

New migration `_m0004_app_integrations` appended to `MIGRATIONS` (runner auto-creates the new table via `init_db()`):
1. **Idempotent guard:** skip if `app_integrations` already populated for a provider.
2. For each **app-scoped** provider:
   - Collect that provider's `channel_integrations` rows across all channels.
   - Take the **default channel's** decrypted value as canonical → write to `app_integrations` (re-encrypted).
   - If another channel has a **different** value, **do not overwrite** — emit a structured warning (`log.warning("app_integration_conflict", provider=..., channel=...)`) and print a human line in migration output so the user can reconcile in Settings.
   - If only non-default channels have it (no default value), take the most-complete/most-recent non-default and log.
3. **Delete** the migrated app-scoped rows from `channel_integrations` (keep `youtube`). Leaves the per-channel table holding only channel-scoped providers.
4. Leave `youtube` rows untouched.
5. Update `ensure_default_channel`: new installs call `import_env_into_app` (app-scoped) + `import_env_into_channel` (channel-scoped/youtube only).

Backward compat:
- Existing channels keep working after `npm run db:migrate`: shared resolution now reads `app_integrations` (seeded from the default channel) → env fallback unchanged for the default install.
- `worker:channel set-secret` for an app-scoped provider should print a deprecation hint pointing to `settings set-secret` (or transparently forward) — decide in implementation; default: forward to app-level with a notice.
- A channel that previously had a unique shared key (now logged, not migrated) continues to fall back to the app-level/default key; user re-enters it in Settings if a distinct key was truly intended (acceptable per Decision #3 — these are "one set of tools for the whole app").

---

## 6. Test plan

### Worker (pytest — `npm run test:worker`)
- **Content gating** (`tests/test_content_profile_pipeline.py`, dry-run):
  - shorts-only: long-form story-gen and long-form RenderJob are **absent**; shorts present.
  - long-form-only: short story-gen and short RenderJobs **absent**; one long-form video present.
  - both (default): current mixed behavior preserved.
  - all-off config: batch fails with the validation error; no stories created.
  - `long_form_count` derives from `long_form_segments_min/max` (assert no `randint(2,5)` path) and `long_form_per_day` is respected.
- **App integrations** (`tests/test_app_integrations.py`):
  - `resolve_api_key` returns app-level secret for an app-scoped provider; env fallback when unset.
  - `youtube_credentials` still resolves per-channel refresh token.
  - `resolve_model` reads app-level config for app-scoped providers.
- **Migration** (`tests/test_migration_app_integrations.py`):
  - per-channel → app-level backfill; default channel wins; conflicting non-default value logged and **not** lost from log; `youtube` rows untouched; idempotent re-run.

### Dashboard (`npm run test:dashboard`, `npm run build`)
- Type-check + build pass with new `ChannelConfig` keys and Integrations route.
- Component/static test (if infra present) that the channel card no longer renders shared-provider secret inputs and the Content section renders conditionally on the toggles.

### Manual smoke (evidence required before "done")
- `npm run db:migrate` on a copy of the dev DB → show migration log incl. any conflict warnings.
- Create/configure a **shorts-only** channel → `worker:full --dry-run --channel <id>` → output shows shorts rendered, **no** long-form render/upload.
- Configure a **long-form-only / sleep-facts** channel (long-form on, shorts off, calm prompt override) → dry-run shows long-form only.
- Default channel dry-run → unchanged mixed output.
- `npm run test` + `npm run build` output captured.

---

## 7. Security review focus (before merge)

- App-level secrets stored encrypted (Fernet, existing key mgmt); **never** returned to the client (only `hasSecret`/status).
- New `POST /api/settings/integrations` must pass secrets to the worker via **stdin JSON**, not argv (fixes the existing argv-leak class for the new surface; consider retrofitting `channel set-secret` similarly).
- Migration must not log plaintext secret values (log provider + channel slug only).
- `worker-cli` stderr must not echo secrets back into API error bodies.
- Run `security-reviewer` agent on the credential-storage + migration diff.

---

## 8. Incremental task / PR breakdown

Each PR is independently shippable and leaves the app working.

1. **A-core — pipeline gating.** Add config keys + defaults (`config.py`), wire `long_form_per_day` + replace `randint(2,5)`, add `enable_shorts`/`enable_long_form` gates + validation in `_run_for_channel`. pytest for gating. `python-reviewer`.
2. **A-ui — Content section.** `packages/shared` `ChannelConfigSchema` additions; dashboard Content section + conditional fields; surface `content_style`. `react-reviewer`/`typescript-reviewer`.
3. **B-core — app integrations backend.** `scope` on registry; `AppIntegration` model; `app_integration_service`; resolver tier; `settings` CLI. pytest. `python-reviewer`.
4. **B-migration.** `_m0004_app_integrations` + `import_env_into_app` + `ensure_default_channel` split + conflict logging. pytest migration. `security-reviewer`.
5. **B-ui — Integrations page + strip channel card.** Settings→Integrations (GET/POST, stdin-JSON secrets); remove shared secret fields from `PROVIDER_META`. `react-reviewer`/`security-reviewer`.
6. **Docs + verification.** Update `multi-channel.md`, README config section, `.env.example` (app-level shared keys; YouTube per channel). Full `npm run test` + `npm run build` + dry-run smoke evidence (`verify`).

---

## 9. Acceptance criteria mapping

| Acceptance criterion | Addressed by |
|----------------------|--------------|
| Shorts-only channel (e.g. 1 short/day), no long-form | §2 flags + §2.3 gates (PR 1) |
| Long-form-only channel w/ configurable length/style (facts/sleep) | §2 flags + niche/prompt override (PR 1–2) |
| `reddit_mix`-equivalent mixed behavior with configurable counts | defaults preserve current behavior (PR 1) |
| OpenAI/Gemini/Pexels/Pixabay/shared TTS configured once | §3 app_integrations + §4.1 (PR 3,5) |
| Each channel configures YouTube + content only (no dup keys) | §3.1 scope + §4.2 (PR 3,5) |
| Pipeline/render/upload/`--all-active` respect profiles | §2.3 (PR 1) |
| Migration succeeds without breaking default channel | §5 (PR 4) |
| `npm run test` + `build` pass; new tests for branching + creds | §6 (all PRs) |
| Docs reflect two-section model | §8 PR 6 |

## 10. User stories satisfied
1. **Drama shorts** — `enable_shorts=true`, `enable_long_form=false`, shorts/day=1, niche="relationship drama", shared OpenAI+Pexels (app-level), own YouTube.
2. **Sleep facts** — `enable_shorts=false`, `enable_long_form=true`, `long_form_per_day=1`, niche="100+ random facts, calm narration", per-channel `long_form_story_generation` prompt override, `voice_personas=["calm"]`, shared tools, different YouTube.
3. **Main channel** — defaults unchanged (3–5 shorts + 1 long-form).

---

## Open questions for reviewer
- PR 5: retrofit `channel set-secret` to stdin-JSON too, or only the new app route? (Recommend: new route now, retrofit as follow-up.)
- Should app-scoped providers allow a per-channel override escape hatch later? (Out of scope now; `scope` field leaves room.)
- `long_form_per_day > 1`: ship multi-long-form-video support now or cap at 1 with validation and follow up? (Recommend: honor the count but document that >1 splits segments across videos in a later PR; cap UI at current need.)
