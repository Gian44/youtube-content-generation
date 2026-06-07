# Sleep On Facts → 3‑Hour Image‑Slideshow Sleep Content

**Date:** 2026-06-07
**Pipeline:** `pipeline_mode="sleep_facts"`
**Status:** Approved design — ready for implementation

## 1. Goal

Transform every Sleep On Facts video from the current ~4‑minute clip into a
**~3‑hour, themed fact‑compilation sleep video**: one calm narrator (`onyx`)
reading a long, flowing stream of low‑stakes facts over a **slideshow of 100+
topic‑matched stock images**, opening directly on imagery (no blue title card),
with **no captions** and **no background music**.

Reference benchmark: the Sleepy Science Channel (avg. ~3‑hour videos).

## 2. Why it is ~4 minutes today (root cause)

The duration is parameter-driven, but the real hard limiter is the single
LLM call ceiling, not the 20‑minute config:

- `SLEEP_FACTS_DEFAULTS["long_form_target_minutes"] = 20`
  ([content_defaults.py:117](../../../apps/worker/storyfactory/content_defaults.py#L117)).
- `target_words = target_minutes × 130`
  ([sleep_facts.py:36,210](../../../apps/worker/storyfactory/pipeline/sleep_facts.py#L210)).
- The script is produced in **one** `generate_text(... max_tokens=4000 ...)` call
  ([sleep_facts.py:269](../../../apps/worker/storyfactory/pipeline/sleep_facts.py#L269)),
  capping real output at ~3,000 words regardless of the minute target. The prompt
  even says "one continuous script"
  ([seed.py:179](../../../apps/worker/storyfactory/db/seed.py#L179)).
- Video duration follows TTS audio (audio is the master clock), so a short
  script ⇒ short video.

The blue intro is a hardcoded FFmpeg title card: `color=c=0x0f3460 …` +
`drawtext "Story 1 of 1"`
([renderer.py:821-874](../../../apps/worker/storyfactory/services/renderer.py#L821)).

## 3. Product decisions (locked)

| Decision | Choice |
|---|---|
| Content shape | **Themed fact compilation** — one calm theme per video, flowing prose |
| Length strategy | **Fully unique ~3h script** via multi-segment generation |
| Captions | **None** |
| Background music | **None** (voice only) |
| Narrator voice | **`onyx`** (single voice), `tts-1-hd` @ `speed=0.9` default |
| Visuals | **Stock images** (≥100, target ~150), slideshow with subtle Ken Burns + crossfade |
| Intro | **No blue card** — opens on the first image |

### TTS voice research summary
`onyx` is the strongest OpenAI voice for fall-asleep narration (deep ~98 Hz,
low energy). Default to the **deterministic** path for an unattended daily
pipeline: `onyx` on `tts-1-hd` with `speed=0.9`. The steerable
`gpt-4o-mini-tts` + sleep `instructions` path is supported via config
(`tts_model`, `tts_instructions`) but not the default, because the
`instructions` parameter is documented as unreliable on the rolling alias.

## 4. Architecture overview

```
sleep_facts.run_sleep_facts_for_channel
  └─ _produce_sleep_video
       1. sleep_script_generator.generate_sleep_script(topic, hints, target_words)
            ├─ outline pass  (prompt: sleep_facts_outline)   → N movements
            └─ expansion pass (prompt: sleep_facts_segment)  → N × ~1.5k words
                 (running summary + previous tail threaded for continuity)
       2. tts_service.generate_tts(story, voice="onyx", model, speed, ...)
            └─ chunk body ≤ TTS limit → per-chunk OpenAI calls
               → ffmpeg concat → ffmpeg loudnorm (memory-safe, no pydub on 3h)
       3. asset_collector: collect ~150 IMAGES (paginated) → download
       4. slideshow_renderer.render_sleep_video(story, tts_job, images, cfg)
            ├─ build_slideshow_reel(images)  → reel.mp4 (Ken Burns + xfade, batched)
            └─ loop reel under narration with -c:v copy (NO 3h re-encode) → final.mp4
       5. existing upload pipeline (unchanged)
```

Two **new modules** keep large files from growing
(`renderer.py` is already ~920 lines, over the 800-line guideline):

- `services/sleep_script_generator.py` — segmented script generation.
- `services/slideshow_renderer.py` — image reel + final sleep render. Imports the
  shared low-level ffmpeg helpers from `renderer.py`
  (`_get_audio_duration_ffprobe`, `_extract_thumbnail`, `_get_video_duration`,
  `_create_dry_run_render`).

## 5. Component designs

### 5.1 Segmented script generation (`services/sleep_script_generator.py`)
- **`generate_outline(topic, hints, num_movements) -> list[Movement]`** — one LLM
  call (`sleep_facts_outline` prompt) returns an ordered list of "movements"
  (sub-areas of the theme), each with a heading and 6–12 fact beats. Keeps the
  whole 3h coherent and on-theme.
- **`expand_movement(topic, movement, running_summary, prev_tail, target_words) -> str`**
  — one LLM call per movement (`sleep_facts_segment` prompt, `max_tokens` sized
  for ~1.5–2k words) producing flowing bedtime prose. The previous movement's
  closing lines + a running theme summary are passed in so transitions are smooth
  and facts do not repeat. **No list/bullet formatting** (lists create rhythm
  breaks that wake people).
- **`generate_sleep_script(topic, hints, target_words, settings) -> dict`** —
  orchestrates outline → per-movement expansion → concatenation; returns
  `{title, hook, body, asset_keywords, image_search_queries, word_count}` (same
  shape `sleep_facts.py` already consumes). Dry-run returns a synthesized body
  with **no** API calls.
- **Word target** = `target_minutes × narration_wpm`. Both configurable; defaults
  `long_form_target_minutes=180`, `narration_wpm=150`. The **actual** duration is
  measured from the rendered audio (ffprobe), so the word target is approximate
  and tunable after the first render (2.5–3.5h is acceptable for sleep).

### 5.2 New prompts (`db/seed.py`, require `npm run seed`)
- `sleep_facts_outline` — variables `topic`, `topic_hints`, `num_movements`;
  returns JSON `{title, hook, movements:[{heading, beats:[...]}], asset_keywords, image_search_queries}`.
- `sleep_facts_segment` — variables `topic`, `movement_heading`, `movement_beats`,
  `running_summary`, `previous_tail`, `target_words`; returns JSON
  `{body}` (flowing prose for this movement only).
- The existing `sleep_facts_long_form` prompt is retained for backward
  compatibility but no longer used by the sleep pipeline.
- Pre-flight prompt existence check in `sleep_facts.py` updated to require the new
  prompts.

### 5.3 TTS long-form chunking + single voice (`services/tts_service.py`)
- `generate_tts(story, *, provider=None, voice=None, model=None, speed=None, instructions=None)`
  — new keyword overrides; when `None` they fall back to today's behavior
  (persona map + random provider), so other pipelines are unaffected. The sleep
  pipeline passes `provider="openai", voice="onyx", model="tts-1-hd", speed=0.9`.
- **Chunking:** `_split_text_for_tts(text, max_chars≈3800)` splits on
  paragraph/sentence boundaries (never mid-sentence). `_generate_openai_tts`
  generates each chunk to a temp file, then assembles with **ffmpeg concat**
  (stream copy / re-encode) — **not** pydub — to keep memory bounded on a 3h file.
  A single chunk behaves exactly as today.
- **Voice/model passthrough:** OpenAI call uses the resolved `model`, `voice`,
  `speed`, and (only for `gpt-4o-mini-tts`) `instructions`.
- **Normalization:** for the chunked/long path, normalize with **ffmpeg
  `loudnorm`** (streaming, low memory) to a slightly quieter sleep target
  (~‑16 LUFS). Short single-chunk audio keeps the existing pydub path.
- **Duration:** measured via ffprobe (not pydub) for the long path.
- **Cache key** extended to include `model` + `speed` so changing them busts the
  cache.
- Cost tracking updated to use the chosen model's per-char rate.

### 5.4 Image collection at scale (`services/asset_collector.py`)
- Sleep pipeline collects `asset_type="image"` with a target of ~150
  (`images_target`, configurable; floor enforced ≥100 where providers allow).
- **Pagination:** add a `page` loop to `_collect_from_pexels` / `_collect_from_pixabay`
  (today capped at `per_page≤15`, no paging) so a single query can yield many
  images; spread the target across multiple theme/movement-derived queries for
  visual variety. Add light **429/503 backoff** and continue on partial failure.
- Landscape orientation; prefer adequately-sized images. De-duplicate by URL.
- Reuses existing `download_assets` (already supports `.jpg`).
- If fewer than the target are found, proceed with what is available (the reel
  cycles through them); `log()` the shortfall — never silently claim 150.

### 5.5 Slideshow renderer (`services/slideshow_renderer.py`)
- **`build_slideshow_reel(image_paths, *, dwell, crossfade, width, height, fps) -> reel_path`**
  - Each image → a Ken Burns clip of `dwell + crossfade` seconds: scale-to-cover
    `width×height`, gentle `zoompan` (~8% zoom over the clip; Ken Burns toggle).
  - Clips combined with **`xfade`** crossfades. To keep the FFmpeg filter graph
    manageable at 150 images, assemble in **bounded batches** (e.g. ≤10 clips per
    xfade chain) producing partial reels, then xfade the partials. A pure
    command-builder (mirroring `_build_recap_short_cmd`) is unit-tested; the
    subprocess orchestration is dry-run/integration tested.
  - Fade-in/out fallback if xfade assembly fails.
  - Reel length = `len(images) × dwell` (≈30–50 min for 100–150 images). The reel
    is encoded **once**.
- **`render_sleep_video(story, tts_job, image_assets, batch_id, cfg, output_dir) -> RenderJob`**
  - Builds the reel, then produces the final 3h video by **looping the reel under
    the narration audio with `-stream_loop -1 … -c:v copy -t {audio_duration}`** —
    i.e. **no 3‑hour video re-encode** (the only heavy encode is the reel). Audio
    re-encoded to AAC and muxed; `-movflags +faststart`, `-map_metadata -1`.
  - **No title card, no captions.** Opens on the first image.
  - Returns a `RenderJob` (same contract the upload pipeline expects).
  - Dry-run writes a placeholder (no ffmpeg, no 3h encode).
- Performance rationale: encoding 3h of 1080p is prohibitively slow; stream-copy
  looping a once-encoded reel makes the final assembly near-instant.

### 5.6 Pipeline wiring (`pipeline/sleep_facts.py`)
- Use `generate_sleep_script(...)` instead of the single-call generator.
- TTS: `generate_tts(story, provider="openai", voice="onyx", model=cfg["tts_model"], speed=cfg["tts_speed"], instructions=cfg.get("tts_instructions"))`.
- Captions: skipped when `captions_enabled` is false (default).
- Assets: `collect_topic_assets(keywords, count=cfg["images_target"], asset_type="image")` → `download_assets`.
- Render: `render_sleep_video(...)` instead of `render_long_form(...)`.
- Cursor/one-per-day guard, policy check, batch lifecycle: **unchanged**.

### 5.7 Config knobs (`SLEEP_FACTS_DEFAULTS`)
```python
SLEEP_FACTS_DEFAULTS = {
    "topic_rotation": [],
    "long_form_target_minutes": 180,
    "narration_wpm": 150,             # word target = minutes × wpm (approx)
    "gen_num_movements": 16,          # outline movements
    "gen_segment_max_tokens": 2800,   # per-movement expansion ceiling
    "voice_persona": "calm",
    "tts_voice": "onyx",
    "tts_model": "tts-1-hd",
    "tts_speed": 0.9,
    "tts_instructions": None,         # only used by gpt-4o-mini-tts
    "enable_wikipedia_grounding": True,
    "asset_type": "image",
    "images_target": 150,
    "slideshow_dwell_seconds": 20,
    "crossfade_seconds": 2,
    "ken_burns": True,
    "captions_enabled": False,
    "music_enabled": False,
}
```
All overridable per channel via the existing three-tier `effective_config`
resolution. New keys surfaced through `channel_service` validation; dashboard
plumbing only needs the new keys accepted (no UI redesign required for v1).

## 6. Cost & performance
- **~$5–12 per video**: TTS (~$5–9 on `tts-1-hd` for ~150–270k chars) + a few $
  of LLM across ~17 generation calls; stock image APIs are free. ~$150–360/mo at
  one/day. `tts-1` (not hd) or `gpt-4o-mini-tts` roughly halves TTS cost.
- **Render time** dominated by the one-time reel encode (~30–50 min of 1080p);
  the 3h final assembly is stream-copy (fast). Tunable via `fps` (24 acceptable
  for stills), preset, reel length.

## 7. Out of scope (unchanged)
Topic rotation & one-per-day guard, Wikipedia grounding, policy/rewrite, upload &
SEO metadata pipeline, other pipelines (`recap_shorts`, fiction shorts,
`render_long_form` / `render_short`). Music service untouched.

## 8. Testing plan
- **sleep_script_generator:** outline parsing, word-target math, movement
  stitching/continuity inputs, dry-run no-API path.
- **tts_service:** `_split_text_for_tts` boundary correctness + reassembly order;
  voice/model/speed passthrough; cache-key includes model+speed; single-chunk ==
  legacy behavior.
- **asset_collector:** pagination reaches ≥100; image-type field mapping
  (Pexels `src.original`, Pixabay `largeImageURL`); backoff/partial-failure;
  dedupe.
- **slideshow_renderer:** pure reel command-builder (zoompan + xfade + batching);
  final loop-copy assembly command uses `-c:v copy` + `-t audio_duration` and no
  title card; dry-run placeholder path.
- **sleep_facts:** wiring uses image asset_type, segmented generator, sleep
  render; captions skipped; cursor advances only on success.
- Target ≥80% coverage on new/changed code; run a dry-run end-to-end.
```
