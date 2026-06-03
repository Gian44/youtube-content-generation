/**
 * Single source of truth (dashboard side) for provider scope, mirroring the
 * worker registry's `scope` field:
 *  - app-scoped  → shared across channels, configured under Settings → Integrations
 *  - channel-scoped → per-channel (YouTube only, for now)
 *
 * The dashboard does not import the worker/shared package at runtime, so these
 * sets are defined once here and reused by every route that gates on scope.
 */

export const APP_SCOPED_PROVIDERS: ReadonlySet<string> = new Set([
  'text.openai',
  'text.gemini',
  'tts.openai',
  'tts.gemini',
  'assets.pexels',
  'assets.pixabay',
  'storage.local',
  'storage.r2',
]);

export const CHANNEL_SCOPED_PROVIDERS: ReadonlySet<string> = new Set(['youtube']);
