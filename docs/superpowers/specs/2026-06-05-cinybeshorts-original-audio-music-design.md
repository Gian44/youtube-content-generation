# CinybeShorts: Original Audio + Background Music

**Date:** 2026-06-05
**Status:** Design — pending implementation plan
**Pipeline:** `recap_shorts` (CinybeShorts channel)

## Problem

CinybeShorts Shorts currently feel "AI dubbed." The recap pipeline cuts the
source clip with the source audio **removed** (`-an`), writes an LLM recap
script, voices it with TTS, and uses that AI narration as the soundtrack and the
video's master clock. The owner wants the opposite:

1. **Keep the clip's original audio** — no AI narration / dubbing.
2. **Add uncopyrighted background music**, mixed quietly so it never competes
   with the original audio.

Reference style the owner is targeting: a TV/movie clip with its real audio,
on-screen subtitles of the spoken dialogue, and a subtle music bed
(`https://www.youtube.com/shorts/ChdXjc1nouk`).

## Decisions (confirmed with owner)

| Question | Decision |
|---|---|
| Captions | **Burned-in subtitles transcribed from the clip's real dialogue** via OpenAI **`whisper-1`** (the only OpenAI model that returns word-level timestamps; `gpt-4o-transcribe` does not). |
| Music source | **Jamendo API** (free `client_id`, instrumental Creative Commons tracks). Pixabay was considered but has **no music API** — images/videos only. |
| Music mood | **Derived from the scene** — inferred from the transcribed dialogue, then mapped to Jamendo search tags. Falls back to a config default mood. |
| Music level | **Subtle (~10%)** under the original audio. |
| Scope | **CinybeShorts only**, via a new `audio_mode` config flag. Other recap channels keep the existing AI-narration behavior. |
| Subtitle fallback | If transcription is unavailable/fails, **render the Short without subtitles** — never block it. |
| Music fallback | If Jamendo is unavailable/fails, **render with original audio only** — never block it. |

## Goals

- A config-driven `original_audio` mode for the `recap_shorts` pipeline.
- Original clip audio preserved end-to-end.
- Subtle, properly-licensed background music auto-fetched from Jamendo, with
  attribution carried into the upload description.
- Dialogue subtitles auto-generated from the clip's real audio.
- Zero behavior change for any channel still on `tts_narration` mode.

## Non-Goals

- No change to the `fiction` or `sleep_facts` pipelines.
- No change to the source-footage copyright posture (the existing recap
  disclosure line stays; sourcing/fair-use remains the owner's responsibility).
- No stream ripping — still only reads user-supplied inbox files.
- No local-music-folder feature in v1 (Jamendo + original-audio fallback only).
- No automatic music ducking in v1 (fixed low volume; ducking is a possible
  later enhancement).

## Current Flow (for reference)

`run_recap_shorts_for_channel` → per planned window → `_produce_short`:
1. `_make_recap_story` — LLM writes recap narration; persists a `Story`.
2. `generate_tts(story)` — AI voice (**the dubbing**).
3. `generate_captions(story, tts_job)` — word-highlight ASS from the narration.
4. `_segment_assets` → `cut_clip(..., -an)` — clip video, **audio dropped**.
5. `render_short(story, tts_job, caption_job, assets, batch_id)` — composites a
   silent clip background under the TTS audio (TTS = master clock).

Key files: `pipeline/recap_shorts.py`, `services/recap_service.py`,
`services/renderer.py`, `services/caption_service.py`,
`services/asset_collector.py`, `content_defaults.py`, `config.py`,
`services/youtube_uploader.py`.

## Proposed Design (Approach A)

A new `audio_mode` selects between the existing narration path and a new
clip-native path. The new path is additive; the narration path is unchanged.

### 1. Configuration

Add to `RECAP_DEFAULTS` (`content_defaults.py`):

```python
"audio_mode": "tts_narration",        # "tts_narration" | "original_audio"
"music_enabled": True,
"music_volume": 0.10,                 # ~10% — subtle bed under original audio
"music_mood_fallback": "cinematic ambient",  # used when scene mood can't be inferred
"music_provider": "jamendo",
"music_allow_noncommercial": False,   # exclude CC-NC/ND tracks (safe to monetize)
"subtitles_from_dialogue": True,      # transcribe real audio → burned subtitles
```

- The **CinybeShorts** channel config sets `recap.audio_mode = "original_audio"`.
  (Set via the existing channel config seam — `apps/dashboard` channel config /
  `db/seed.py` — confirmed during planning.)
- New credential: `JAMENDO_CLIENT_ID` env var, added to `config.py` `Settings`
  and `.env.example`, resolved per-channel via
  `resolve_api_key("music.jamendo", "jamendo_client_id")` (same pattern as
  Pexels/Pixabay).
- Mode is read with `cfg.get("audio_mode", "tts_narration")` so existing
  channels with no key default to today's behavior.

### 2. Clip cutting preserves audio

`recap_service.cut_clip()` gains `keep_audio: bool = False`:

- `keep_audio=False` (default): unchanged — `-an`, narration is master clock.
- `keep_audio=True`: drop `-an`; add `-c:a aac -b:a 192k`. The cut window length
  (`end - start`) is the Short's length, so the clip is the master clock.

`_segment_assets` passes `keep_audio=True` when `audio_mode == "original_audio"`.
The clip `Asset` is recorded as today (provider `local`, `User-provided`).

### 3. Pipeline branch (`_produce_short`)

When `audio_mode == "original_audio"`:

1. **Story (metadata only):** still call `_make_recap_story` + policy check so we
   get a title / hook / description / comment-bait for the upload. It is **not**
   voiced. (LLM cost stays; this is intentional — metadata quality matters for
   reach. A later optimization could swap to a lighter metadata prompt.)
2. **Skip `generate_tts`** entirely.
3. **Cut clip** with `keep_audio=True`.
4. **Subtitles:** if `subtitles_from_dialogue` and a transcription key exists,
   transcribe the cut clip's audio (`whisper-1`, word timestamps) and build the
   word-highlight ASS (see §5). On any failure → no captions, continue. Keep the
   transcript text in memory for the next step.
5. **Scene mood:** infer a short music-search mood from the transcript text via a
   lightweight LLM call (`infer_music_mood`). No transcript / no key / error →
   fall back to `music_mood_fallback`. Not persisted (no schema change).
6. **Music:** fetch one track for that mood (see §4). On any failure → original
   audio only.
7. **Render** via `render_recap_short` (see §6).
8. **Ledger** the segment `rendered` exactly as today.

A small helper (e.g. `_produce_short_original_audio`) keeps the branch readable;
shared steps (story, policy, ledger) are reused, not duplicated.

### 4. Music service (Jamendo) — `services/music_service.py` (new)

`fetch_music_track(mood: str, min_duration: float) -> Asset | None`
(`mood` is the scene-derived search phrase from §3.5):

- `GET https://api.jamendo.com/v3.0/tracks/` with:
  `client_id`, `format=json`, `limit` (small pool), `vocalinstrumental=instrumental`,
  `audioformat=mp32`, `include=licenses+musicinfo`, `fuzzytags=<mood terms>`,
  `order=popularity_total`, plus a randomized pick from the returned pool for
  variety across Shorts.
- `infer_music_mood(transcript: str, fallback: str) -> str` lives alongside it:
  a small LLM call returning 2–4 mood/genre words (e.g. "tense suspenseful",
  "melancholic piano", "triumphant orchestral"); returns `fallback` on no
  text / no key / error.
- `resolve_api_key("assets.jamendo", "jamendo_client_id")` for the key.
- **License handling (important):** Jamendo tracks carry *different* CC licenses
  (CC-BY, CC-BY-SA, CC-BY-NC, CC-BY-ND). NC ("non-commercial") and ND
  ("no-derivatives") are unsafe for a channel that may monetize. Use
  `include=licenses` and **prefer commercial-use-friendly licenses** (CC-BY /
  CC-BY-SA / CC0); record the track's **actual** license, never a hardcoded one.
  Whether to *hard-filter out* NC/ND or just prefer-then-allow is a config knob
  (`music_allow_noncommercial`, default `False`) finalized in planning.
- Persist as `Asset(type="audio", provider="jamendo", original_url=<audiodownload>,
  creator=<artist>, license=<actual cc license short name + url>,
  attribution="Music: \"<title>\" by <artist> (<actual license, e.g. CC BY 4.0>) via Jamendo",
  duration_seconds=<n>, policy_status="verified")`, then download via the
  existing `download_asset` (extend it to write `.mp3` for `type="audio"`).
- **Caching/reuse:** prefer an already-downloaded, long-enough Jamendo audio
  `Asset` for the channel before hitting the API, to limit calls/downloads and
  give a consistent feel. (Reuse strategy finalized in planning.)
- `track_api_call(provider="jamendo", ...)` for cost/usage tracking, matching
  the Pexels/Pixabay pattern.
- Returns `None` on missing key / no results / download failure — caller falls
  back to original audio only.
- `validate_asset_license`: treat `jamendo` as a verified free/CC provider.

### 5. Subtitles from dialogue — `caption_service`

Add a transcription-from-audio entry point that reuses the existing machinery:

- New `generate_captions_from_audio(story, audio_path, style="word_highlight")`
  (or refactor `generate_captions` to accept an audio path + optional TTS job).
- Reuse `_openai_transcription(audio_path)` — it already calls **`whisper-1`**
  with `verbose_json` + `timestamp_granularities=["word"]` and returns word-level
  timings — pointed at the **clip audio** instead of TTS output. (`whisper-1` is
  required here: `gpt-4o-transcribe`/`-mini` do not return word timestamps.)
- Reuse `_generate_word_highlight_ass` unchanged.
- No transcription key (`resolve_api_key("text.openai", ...)` empty) or any
  error → return no caption job; the render proceeds without burned subtitles.
- Dialogue can be long/dense; grouping stays at the current
  `max_words_per_line=3`, positioned in the existing Shorts safe area.

### 6. New render path — `renderer.render_recap_short` (new)

Clip is the master clock; original audio is preserved and music is mixed under
it. Keeps `render_short` (TTS path) untouched.

Inputs: clip path (video **+** audio), optional caption ASS path, optional music
path, `music_volume`, output paths, 1080×1920/30fps.

FFmpeg shape:
- Video: scale to cover + crop to 1080×1920, `fps=30`, `setsar=1`; burn ASS if
  present.
- Audio: `[clip:a]` at full volume mixed with `[music:a]volume=<music_volume>`,
  music `-stream_loop -1` and trimmed to clip length; combine with
  `amix=inputs=2:duration=first:normalize=0` (so the dialogue is **not**
  auto-attenuated). If the clip has no audio track → music-only bed; if no music
  → clip audio only.
- Output duration = clip duration (no `-shortest` surprises).
- Extract thumbnail as today.

Returns a `RenderJob` exactly like `render_short`, so downstream
ledger/upload code is unchanged.

### 7. Metadata & attribution

`youtube_uploader` recap metadata builder (`~line 491`) appends the music
track's `attribution` (when a track was used) beneath the existing recap
disclosure line. Example tail:

```
This video contains transformative recap/commentary for entertainment.
All footage belongs to its respective owners.
Music: "Track Title" by Artist (CC BY 4.0) via Jamendo.
```

(The license shown is the track's **actual** license string, not hardcoded.)

### 8. Edge cases

| Case | Behavior |
|---|---|
| No `JAMENDO_CLIENT_ID` | No music; original audio only. No failure. |
| Jamendo returns nothing / download fails | Original audio only. No failure. |
| No transcription key / Whisper fails | No subtitles; render proceeds. |
| Clip has no audio stream | Music-only bed (or silent if no music). |
| Music shorter than clip | Loop (`-stream_loop -1`). |
| Music longer than clip | Trimmed via `duration=first`. |
| `dry_run` | Placeholders; no cut/transcribe/network/render, as today. |
| Channel still `tts_narration` | Fully unchanged path. |

## Data / Schema

No migrations. Reuses the existing `Asset` model with `type="audio"` and
`provider="jamendo"`. The `RecapSegment` ledger and `Story`/`RenderJob` flows are
unchanged.

## File-by-file changes

| File | Change |
|---|---|
| `content_defaults.py` | New `RECAP_DEFAULTS` keys (§1). |
| `config.py` | `jamendo_client_id` setting; include in `validate_api_keys`. |
| `integrations/registry.py` | Register `assets.jamendo` (app-scoped, `api_key`←`JAMENDO_CLIENT_ID`). |
| `.env.example` | `JAMENDO_CLIENT_ID=` + comment. |
| `services/recap_service.py` | `cut_clip(keep_audio=...)`. |
| `services/music_service.py` | **New** — `infer_music_mood` + Jamendo fetch/download/cache. |
| `services/asset_collector.py` | `download_asset` audio extension; `validate_asset_license` accepts `jamendo`. |
| `services/caption_service.py` | Transcription-from-audio caption entry point. |
| `services/renderer.py` | **New** `render_recap_short` (clip-native A/V + music). |
| `pipeline/recap_shorts.py` | `audio_mode` branch + `_produce_short_original_audio`. |
| `services/youtube_uploader.py` | Append music attribution to recap metadata. |
| channel config / `db/seed.py` | Set CinybeShorts `recap.audio_mode = "original_audio"`. |
| `docs/multi-channel.md` | Document `audio_mode` and the music keys. |

## Testing

Mock all network + FFmpeg; follow the existing `apps/worker/tests/test_recap_*`
style.

- **Config:** `audio_mode` resolution; CinybeShorts resolves to `original_audio`;
  unknown channels default to `tts_narration`.
- **cut_clip:** `keep_audio=True` omits `-an` and adds AAC; `False` keeps `-an`.
- **Pipeline branch:** `original_audio` path skips `generate_tts`, calls clip-cut
  with `keep_audio=True`, and still ledgers `rendered`.
- **music_service:** parse a sample Jamendo JSON → `Asset` with correct
  attribution/license; missing key → `None`; cache reuse path.
- **captions:** transcription-from-audio produces word timings → ASS; no key →
  no caption job (no raise).
- **renderer:** `render_recap_short` builds the expected filter graph
  (cover-crop, ASS burn when present, `amix … normalize=0`, music volume,
  loop/trim); music-absent and audio-absent fallbacks.
- **metadata:** music attribution appears only when a track was used.

## Rollout

1. Land behind `audio_mode` (default `tts_narration`) — no channel changes
   behavior until flipped.
2. Set `JAMENDO_CLIENT_ID`; flip CinybeShorts to `original_audio`.
3. Validate one episode in `dry_run`, then a real run; eyeball music level and
   subtitle timing; tune `music_volume` / `music_query` via config (no code
   change).

## Open Questions

None blocking. To finalize during planning: exact Jamendo track-reuse policy
(per-channel pool size, rotation), and whether the metadata prompt should be
slimmed for the no-narration case.
