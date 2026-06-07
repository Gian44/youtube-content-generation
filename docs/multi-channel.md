# Multi-Channel Guide

StoryFactory is a **local desktop application** ([desktop-app.md](desktop-app.md)); channels and integrations are stored in the on-machine database, not on a hosted service.

StoryFactory runs an arbitrary number of **channels**. Each channel is an
independent content operation — its own niche, content rules, prompt/voice
customization, selected integrations, and YouTube account — all sharing the same
pipeline (story → TTS → captions → assets → render → upload → analytics).

## Concepts

- **Channel** — the primary unit of configuration and operation. Has an
  identity (name, slug, status), a niche/content style, a per-channel `config`
  (quotas, category weights, TTS ratios, privacy, disclosure, caption style…),
  and a set of enabled **integrations**.
- **Integration** — a provider used by the pipeline. Integrations have a **scope**:
  - **Shared (app-level):** `text.openai`, `text.gemini`, `tts.openai`,
    `tts.gemini`, `assets.pexels`, `assets.pixabay`, `storage.local`, `storage.r2`.
    Configured **once** for the whole app (Settings → Integrations); every channel
    uses the same credentials.
  - **Per-channel:** `youtube` — each channel connects its own account and stores
    its own encrypted refresh token.
- **Content profile** — each channel composes which outputs it produces (Shorts,
  long-form, or both) and how many, plus niche/style and prompt overrides. See
  [Content profiles](#content-profiles) below.
- **Inheritance** — anything a channel does not override falls back to the
  app-level defaults from `.env`. Prompts are global by default; a channel may
  override any prompt by name.

## App-level vs per-channel

| App-level | Per-channel (database) |
|---|---|
| `DATABASE_URL` / `SQLITE_PATH`, storage infra | YouTube account (encrypted refresh token) |
| `STORYFACTORY_SECRET_KEY` (master key) | Content profile (Shorts / long-form / counts) |
| **Shared provider keys** (OpenAI/Gemini/Pexels/Pixabay/TTS/storage) — Settings → Integrations, encrypted | Overrides for quotas, category weights, prompts, niche, content style |
| Shared Google Cloud OAuth `YOUTUBE_CLIENT_ID` / `_SECRET` | — |
| Default values for quotas/ratios/privacy/etc. | — |

## Content profiles

Every channel decides what it generates — there is no fixed template:

| Goal | Configuration |
|------|---------------|
| **Shorts-only** drama (e.g. 1/day) | Shorts ✓, long-form ✗, shorts/day min=max=1 |
| **Long-form only** (e.g. "100+ facts to sleep to") | Shorts ✗, long-form ✓, niche + a per-channel long-form prompt override, calm voice |
| **Classic mix** (default) | Shorts ✓ (3–5/day) + one long-form compilation ✓ |

Set these under the dashboard **Channels → Content** section, or via the
`config` JSON on the channel. The relevant keys (all optional; absent inherits
the app-level default):

- `enable_shorts` / `enable_long_form` (booleans; at least one must be true)
- `shorts_per_day_min` / `shorts_per_day_max`
- `long_form_per_day` (also acts as an off-switch when `0`; currently capped at one long-form video/day)
- `long_form_segments_min` / `long_form_segments_max` (stories composed into the long-form video)
- `long_form_target_minutes`, `long_form_includes_shorts`

The pipeline skips story generation, TTS, rendering, and upload for any disabled
output, so a shorts-only channel never renders or uploads a long-form video (and
vice-versa). The *kind* of content (drama vs facts vs calm narration) comes from
`niche` / `content_style` and per-channel prompt overrides.

## Pipeline modes

A channel's **content engine** is chosen by `config.pipeline_mode`. The default
path is unchanged; two additional modes ship pre-named presets:

| `pipeline_mode` | Channel | Outputs | Source of content |
|-----------------|---------|---------|-------------------|
| `fiction` (default) | any | Shorts and/or long-form | Original Reddit-style drama (LLM) |
| `recap_shorts` | **CinybeShorts** | Shorts only | Many Shorts cut from **user-supplied** local video files |
| `sleep_facts` | **Sleep On Facts** | long-form only | One calm, single-topic "facts to fall asleep to" video |

`daily.py` branches on `pipeline_mode` at the start of a channel's run. The mode
must match the enabled outputs (enforced in the worker, the shared Zod schema,
and the dashboard API): `recap_shorts` ⇒ Shorts on / long-form off;
`sleep_facts` ⇒ long-form on / Shorts off.

### One-click presets

On the **Channels** page, *Quick start* offers two one-click presets that
pre-fill the name, slug, niche, `pipeline_mode`, and content profile — you only
connect YouTube afterward. Presets are client-side sugar: the stored config is
explicit flags (e.g. `pipeline_mode: "recap_shorts"`), never a "preset name".
The same channels can be created from the CLI:

```bash
# CinybeShorts (recap shorts) — config passed as JSON on stdin
echo '{"pipeline_mode":"recap_shorts","enable_shorts":true,"enable_long_form":false,"recap":{"inbox_path":"./data/sources/inbox/cinybe-shorts","max_shorts_per_run":5,"footage_mode":"with_source_video"}}' \
  | npm run worker:channel -- create --name "CinybeShorts" --slug cinybe-shorts --niche "TV & movie recaps" --config-stdin

# Sleep On Facts (sleep facts) — ~3h image-slideshow videos
echo '{"pipeline_mode":"sleep_facts","enable_shorts":false,"enable_long_form":true,"long_form_per_day":1,"long_form_target_minutes":180,"sleep_facts":{"topic_rotation":["Ancient Egypt","The Deep Sea","Outer Space"],"asset_type":"image","images_target":150}}' \
  | npm run worker:channel -- create --name "Sleep On Facts" --slug sleep-on-facts --niche "Calm facts to fall asleep to" --config-stdin
```

---

## CinybeShorts (`pipeline_mode: recap_shorts`)

CinybeShorts turns **one episode/movie file you drop into a local inbox** into
many Shorts proportional to its runtime.

### Inbox & filename convention

Drop video files into the channel's inbox folder (default
`./data/sources/inbox/cinybe-shorts`, set via `config.recap.inbox_path`). Two
layouts are supported:

- **Flat**, named by convention — the parser reads the show/episode from the
  filename:
  - `Breaking Bad S01E03.mkv` → series *Breaking Bad*, season 1, episode 3
  - `The Office 2x05.mp4` → series *The Office*, season 2, episode 5
  - `Inception (2010).mp4` → movie *Inception*
  - `Dune Part 2.mkv` → movie *Dune*, part 2
  - anything else → treated as a single movie named after the file
- **Per-series subfolders** — the subfolder name is the series title and the
  filename still supplies the season/episode:
  `inbox/Breaking Bad/Breaking Bad S01E03.mkv`.

Supported extensions: `.mp4 .mkv .mov .webm .avi .m4v .ts`. New files are
registered on each daily run (and via `recap inbox scan`); files already marked
`completed` are skipped. You can correct or pre-register episodes from the CLI
(see below) — useful when a filename can't be parsed.

### Segmentation rules (the dedupe ledger)

For the active series, the pipeline processes **one episode at a time**.
`config.recap.segmentation_mode` chooses how windows are planned:

- **`time`** (default) — content-blind, deterministic `[start, end]` windows:
  windows of `target_short_seconds` (default 52s), stepping by
  `target_short_seconds − overlap_seconds`; a trailing window shorter than
  `min_short_seconds` (default 40s) is dropped; capped at `max_shorts_per_episode`.
- **`scene`** — content-aware **smart montage** recaps (CinybeShorts). The
  episode is transcribed and split into candidate scenes, an LLM scores each for
  plot-importance, and **one edited montage Short is produced per integral
  scene**. The Short count is therefore *dynamic* — it equals the number of
  scenes scoring at/above `scene_importance_threshold`, not a fixed slice count.
  See [Smart montage recaps](#smart-montage-recaps-segmentation_mode-scene).

Each rendered Short writes a `recap_segment` row (`episode_id, start_seconds,
end_seconds, story_id, render_job_id`). **This row is the dedupe ledger** — a
window that already has a row is never cut again, so re-runs never produce a
duplicate Short from the same span. Each daily run renders at most
`max_shorts_per_run` of the still-pending windows. When every planned window has
a row, the episode is marked `completed` and the next pending episode advances on
the following run.

### Recap content & footage

Each Short gets a **transformative** recap narration written by the LLM
(`recap_short_script` prompt — hook, 1–2 sentence recap, CTA), voiced, captioned,
and composited over background footage. Footage is chosen by
`config.recap.footage_mode`:

- `with_source_video` (default) — the actual clip cut from your local file (the
  recap narration is the master clock; source audio is dropped),
- `stock_metaphor` — no copyrighted footage; on-topic stock b-roll
  (Pexels/Pixabay) under the narration instead.

`config.recap` keys: `source_mode` (`user_supplied` only in v1), `inbox_path`,
`active_series_slug`, `segmentation_mode`, `target_short_seconds`,
`min_short_seconds`, `overlap_seconds`, `min_shorts_per_episode`,
`max_shorts_per_episode`, `max_shorts_per_run`, `footage_mode`, `voice_persona`,
plus the `audio_mode` keys and the `scene`-mode keys below.

### Recap audio: AI narration vs. original audio

`config.recap.audio_mode` chooses the soundtrack of each Short:

- `tts_narration` (default) — the LLM recap script is voiced by TTS and the
  source audio is dropped (the narration is the master clock).
- `original_audio` — **keep the clip's own audio**, mix a quiet, uncopyrighted
  music bed under it, and burn in subtitles transcribed from the real dialogue.
  No AI dubbing. The LLM recap is still generated, but only for the
  title/description metadata.

`original_audio` keys (ignored in `tts_narration` mode):

- `music_enabled` (default `true`) — mix a background music bed.
- `music_provider` (`jamendo`) — the only provider in v1. Pixabay has **no music
  API**; Jamendo serves free Creative Commons tracks (set `JAMENDO_CLIENT_ID`).
- `music_volume` (default `0.10`) — music level under the dialogue (~10%).
- `music_mood_fallback` (default `"cinematic ambient"`) — used when the scene
  mood can't be inferred from the dialogue. The mood is normally derived
  per-clip from the transcribed dialogue so the bed matches the scene.
- `music_allow_noncommercial` (default `false`) — when `false`, CC-NC/ND tracks
  are excluded so the result is safe to monetize. The chosen track's actual CC
  license and attribution are added to the video description.
- `subtitles_from_dialogue` (default `true`) — transcribe the clip's audio
  (OpenAI `whisper-1`, word timestamps) and burn in word-highlight subtitles.
  Requires an OpenAI key; if absent or transcription fails, the Short still
  renders without captions. (Music likewise degrades to original-audio-only if
  Jamendo is unavailable — a Short is never blocked.)

Enable original audio on an existing channel:

```bash
npm run worker -- recap config --channel cinybe-shorts --audio-mode original_audio
# optional tuning:
npm run worker -- recap config --channel cinybe-shorts --music-volume 0.08 \
  --music-mood-fallback "lofi chill"
```

### Smart montage recaps (`segmentation_mode: scene`)

In `scene` mode the recap pipeline stops slicing the episode on a clock and
instead builds a recap of the **plot-integral** moments:

1. **Transcribe once** — the whole episode is transcribed with `whisper-1` (in
   chunks, word timestamps), cached to `data/cache/transcripts/{episode_id}.json`
   so it is never re-transcribed.
2. **Detect candidate scenes** — FFmpeg scene-change detection (shot boundaries)
   combined with dialogue grouping on silence gaps.
3. **Score + plan** — an LLM rates each scene 0–1 for plot-importance against a
   fixed rubric (`temperature 0`) and, for kept scenes, returns a **hook-first
   montage cut-list** (the key sub-spans, filler dropped) plus a curiosity title,
   2-second hook, comment-bait, post-specific tags, and a music mood.
4. **Freeze the plan** — the result is persisted as `media_scenes` rows, so the
   Short count and windows are **deterministic** across re-runs and the dedupe
   ledger keeps working.
5. **Render** — each kept scene becomes one montage Short that **keeps the
   original audio**, mixes a quiet scene-mood music bed, and burns in subtitles
   sliced from the cached transcript (no second transcription).

The Short **count is dynamic** = number of scenes scoring ≥
`scene_importance_threshold`, bounded by `min_shorts_per_episode` /
`max_shorts_per_episode`. If no transcript is available (no OpenAI key / dry-run)
the pipeline falls back to `time` windows.

`scene`-mode keys (ignored in `time` mode):

- `scene_importance_threshold` (default `0.6`) — keep scenes scoring ≥ this.
- `scene_detect_threshold` (default `0.4`) — FFmpeg scene-change sensitivity.
- `scene_min_gap_seconds` (default `1.2`) — silence gap that separates scenes.
- `montage_target_seconds` (default `18`) — target montage length per Short.
- `montage_max_seconds` (default `20`) — hard ceiling (optimizer: Shorts ≤20s).
- `montage_min_seconds` (default `8`) — minimum Short length.
- `montage_max_cuts` (default `6`) — max sub-clips stitched per montage.

```bash
npm run worker -- recap config --channel cinybe-shorts \
  --segmentation-mode scene --audio-mode original_audio
```

### YouTube Shorts optimizer (applies to all Shorts)

Upload metadata and cadence follow a proven Shorts playbook
(`services/shorts_seo.py`):

- **Titles** are curiosity-driven with a natural keyword and **never contain
  hashtags**.
- **Descriptions** carry **exactly ~3 hashtags** (plus the channel disclosure /
  music attribution).
- **Tags** use the 3-tier formula — *post-specific* + *niche* + *broad* (viral,
  viral shorts, shorts, for you), 9–12 total.
- **Length** — montage Shorts target ≤20s (completions drive the view-to-swipe
  ratio that the algorithm rewards).
- **Cadence** — `max_short_uploads_per_day` (default **1**) caps Short uploads
  per channel per day; surplus Shorts queue and drain oldest-first on later days.
  Long-form uploads are never throttled. Set to `0` for unlimited.

### Synthetic-media disclosure (the "Made with AI" label)

`declare_synthetic_media` (default **`false`**, per channel / app-level) controls
whether the upload sets YouTube's `status.containsSyntheticMedia` flag — the
self-declaration that produces the "altered or synthetic content" label.
Rendered files are also written with `-map_metadata -1` so no provenance/C2PA
tags ride along. You remain responsible for actual YouTube disclosure-policy
compliance; this only stops the app from volunteering the flag.

### Recap CLI

```bash
npm run worker -- recap series create --channel cinybe-shorts --title "Breaking Bad" --active
npm run worker -- recap series list --channel cinybe-shorts
npm run worker -- recap series set-active --channel cinybe-shorts --slug breaking-bad
npm run worker -- recap episode list --channel cinybe-shorts
npm run worker -- recap episode register --channel cinybe-shorts --series breaking-bad \
  --file "/path/Breaking Bad S01E01.mkv" --season 1 --episode 1   # --duration optional (ffprobe)
npm run worker -- recap episode mark-complete --channel cinybe-shorts --episode-id <id>
npm run worker -- recap inbox scan --channel cinybe-shorts
```

### Legal / scope

CinybeShorts only ever reads **local files you placed** in the inbox. The app is
**not** a Netflix/stream ripper and never fetches from streaming services —
sourcing the footage is your responsibility. Recaps of copyrighted shows/movies
carry YouTube copyright and Content-ID risk; you are the uploader and are
responsible for fair-use/transformative-use compliance. Use `stock_metaphor`
footage mode if you want to avoid uploading copyrighted frames.

---

## Sleep On Facts (`pipeline_mode: sleep_facts`)

Sleep On Facts produces **one calm, single-topic ~3-hour long-form video** per run
(~1/day, gated by `long_form_per_day`) — a themed fact compilation narrated by a
single calm voice over a slideshow of 100+ topic-matched stock images. Every video
is about exactly **one subject** — unrelated domains are never mixed (enforced by
the prompts and a post-generation on-topic check).

### Reaching 3 hours (segmented generation)

A single LLM call tops out around ~3k words, far short of a 3-hour script. The
pipeline therefore generates in two passes
(`services/sleep_script_generator.py`): an **outline** call plans ~16 "movements"
(sub-areas of the topic), then **one expansion call per movement** writes flowing
bedtime prose, threaded with a running summary + the previous movement's tail for
smooth, non-repeating continuity. The word target is `long_form_target_minutes ×
narration_wpm`; the actual duration is measured from the rendered audio, so the
target is approximate and tunable. TTS chunks the long script across requests and
assembles/normalizes with ffmpeg (`services/tts_service.py`).

### Topic rotation

`config.sleep_facts.topic_rotation` is a per-channel list of subjects (any
domain: history, animals, space, science, geography…). A per-channel cursor
(stored in `settings` as `sleep_facts_cursor:<channel_id>`) walks the list one
topic per video and wraps around. Edit the list in the dashboard (Sleep pipeline
panel) or in the channel config. If the list is empty, a broad default rotation
is used.

### Fact grounding (free, keyless)

When `config.sleep_facts.enable_wikipedia_grounding` is true (default), the
pipeline fetches a free Wikipedia REST summary for the topic (no API key, no
account) and feeds it to the LLM as factual grounding, and derives on-topic
visual keywords from it. Grounding is best-effort: if the lookup fails (offline,
rate-limited, missing page) the video is still produced from the topic alone.

### Visuals & voice

Visuals are a **slideshow of topic-matched stock images** (not stock video, not a
blue title card — the video opens directly on the first image). The pipeline
collects ~150 images (paginated across Pexels/Pixabay) from the LLM's
`asset_keywords` / `image_search_queries` plus Wikipedia-derived keywords, then
`services/slideshow_renderer.py` builds one Ken-Burns + crossfade reel and loops
it under the narration with a stream-copy (so the 3-hour final assembly does **not**
re-encode video). There are **no captions** and **no music** by default.

Narration is a single calm voice — **`onyx`** on `tts-1-hd` at `speed=0.9` by
default (the deep, low-energy OpenAI voice best suited to falling asleep). The
steerable `gpt-4o-mini-tts` path is available via `tts_model` + `tts_instructions`.

`config.sleep_facts` keys (all per-channel, with defaults in
`content_defaults.py`): `topic_rotation`, `long_form_target_minutes` (default
**180**), `narration_wpm` (150), `gen_num_movements` (16), `gen_segment_max_tokens`
(2800), `voice_persona` (`calm`), `tts_voice` (`onyx`), `tts_model` (`tts-1-hd`),
`tts_speed` (0.9), `tts_instructions`, `enable_wikipedia_grounding`, `asset_type`
(`image`), `images_target` (150), `slideshow_dwell_seconds` (20),
`crossfade_seconds` (2), `slideshow_fps` (24), `ken_burns` (true),
`captions_enabled` (false), `music_enabled` (false).

> **Upgrading an existing Sleep On Facts channel:** the worker honors per-channel
> overrides, so a channel created before v2 may still carry a low
> `long_form_target_minutes` (e.g. 20) and `assets_per_video: 8`. Set
> `long_form_target_minutes` to ~180 (and optionally `images_target` to ~150) in
> the dashboard or channel config to pick up the 3-hour image-slideshow behavior.
> Re-seed prompts (`npm run seed`) so `sleep_facts_outline` / `sleep_facts_segment`
> exist.

---

For step-by-step setup of every external tool/API these modes use (FFmpeg,
OpenAI, Pexels/Pixabay, Wikipedia, the inbox folder), see
[setup-external-tools.md](setup-external-tools.md).

## Secrets & encryption

Per-channel API keys and OAuth tokens are stored **encrypted** in the database
using a Fernet master key. Resolution order:

1. `STORYFACTORY_SECRET_KEY` environment variable (recommended).
2. A key file at `STORYFACTORY_KEY_FILE` (default `./.storyfactory.key`),
   auto-generated on first use if neither is set.

Generate a key explicitly:

```bash
npm run worker:channel -- generate-key
# put the value in STORYFACTORY_SECRET_KEY (.env)
```

The key is never stored in the database and never committed (`.storyfactory.key`
is git-ignored). All encryption/decryption happens in the Python worker — the
dashboard never handles plaintext secrets.

## Migrating an existing single-channel install

Run the migration. It is idempotent and backfills a **default channel** from
your current `.env`, then scopes all existing batches/stories/renders/uploads to
it — no re-setup required:

```bash
npm run db:migrate
```

This creates a channel (slug `default`, name from `CHANNEL_NAME`), seeds the
**shared app-level integrations** from the keys present in `.env` (encrypted),
imports the default channel's YouTube token, and adds `channel_id` to all
operational tables.

If you are upgrading an install that previously stored shared provider keys
per channel, the `0004_app_integrations` migration consolidates them to the app
level: the **default channel's** value for each shared provider is kept as the
canonical one, and any *differing* value on another channel is logged (not
silently lost) so you can re-enter it under Settings → Integrations if it was
intentional. YouTube tokens stay per channel.

## Managing channels

Shared provider keys (OpenAI, Gemini, Pexels, Pixabay, TTS, storage) are entered
once under the dashboard **Settings → Integrations** page — not per channel.

From the dashboard **Channels** page you can:

- Create / duplicate / pause / delete channels
- Set each channel's **content profile** (Shorts / long-form / counts), niche, and style
- **Connect a YouTube account per channel** (the OAuth `state` carries the
  channel id; the token is stored encrypted for that channel only)

The same operations are available from the CLI:

```bash
# Create a channel (shared keys come from app-level Integrations, not per channel)
npm run worker:channel -- create --name "Horror Nights" --slug horror --niche "scary stories"

# List channels
npm run worker:channel -- list

# Configure SHARED provider keys once at app level (used by every channel).
# Secrets are read from stdin as JSON so they never appear in the process args.
echo '{"api_key":"sk-..."}' | npm run worker:settings -- set-secret --provider text.openai --enable --secrets-stdin
npm run worker:settings -- status            # shared integration status (no secret values)

# Per-channel prompt override (falls back to the global prompt otherwise)
npm run worker:channel -- set-prompt --channel horror --name short_story_generation --template-file ./horror_prompt.txt

# Show per-channel integration status (just YouTube; no secret values)
npm run worker:channel -- status --channel horror

# Pause / activate / duplicate / delete
npm run worker:channel -- set-status --channel horror --status paused
npm run worker:channel -- duplicate --channel horror --name "Horror Nights 2"
npm run worker:channel -- delete --channel horror   # only if it has no history
```

### Connecting YouTube per channel

1. Set the shared Google Cloud OAuth app once in `.env`
   (`YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REDIRECT_URI`).
2. On the **Channels** page, click **Connect YouTube** for the channel.
3. Approve access for the YouTube account you want that channel to publish to.

The refresh token is captured and stored encrypted for that channel. Uploads and
analytics for the channel use its own token. (Advanced: a channel may override
`client_id`/`client_secret` to use a separate Google Cloud project.)

## Running the pipeline per channel

```bash
# A single channel (default channel if --channel is omitted)
npm run worker:daily -- --channel horror
npm run worker:upload -- --channel horror
npm run worker:analytics -- --channel horror

# Every active channel, sequentially (fail-isolated, quota-aware)
npm run worker:daily -- --all-active
npm run worker:full -- --all-active
```

The dashboard's run buttons target the **active channel** (selected in the
sidebar switcher). The desktop scheduler runs `full --all-active` daily.

### Quota note

Because all channels share one Google Cloud project, they share the YouTube API
daily quota. Runs are sequential and quota-aware; a single `videos.insert` costs
~1600 units of the 10,000/day default. Stagger or cap uploads per channel if you
operate many channels.
