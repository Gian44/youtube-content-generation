# Cast library for fiction Shorts — implementation plan

Date: 2026-09-19. Repo: `youtube-content-generation` (StoryFactory), `main` at `b1b5ea6`, in sync with `origin/main`. Scope: fiction `pipeline_mode` Shorts only; recap_shorts and sleep_facts are untouched. Local desktop only. No paid API is added to the daily run; all visuals come from Google Flow credits, generated once and stored in a local bank.

## What the feature does

Each fiction Short gets a frozen beat sheet (2–6 second beats, each tagged with an emotion and a shot type), and the renderer cuts between clips of one recurring character per voice persona instead of looping stock footage. The character clips are generated once in Google Flow from prompts the worker writes into an outbox, dropped into an inbox, and sorted into a tagged bank. When a tag has no clip, that beat uses the story's stock footage; when the persona has no bank at all, the Short renders exactly as it does today.

## Facts the plan relies on (verified 2026-09-19 against the code)

Composition lives in `apps/worker/storyfactory/services/renderer.py::render_short` (lines 27–115): it concatenates the story's stock clips (`_concat_backgrounds`, copy-concat with a `paths[0]` fallback), loops the result with `-stream_loop -1`, scales and crops to 1080x1920 at 30 fps, burns the `.ass` captions and cuts at the TTS duration with `-t`. It swallows exceptions into `RenderJob.error`. `pipeline/render.py` is the queue runner that regenerates missing TTS, captions and assets before calling the same `render_short`. `render_short` is also called by `pipeline/recap_shorts.py` for `tts_narration` recap Shorts, so every change to it must be a no-op when a story has no beat rows. Word-level timings exist before assets are collected: `services/caption_service.py` persists them as `caption_words` rows (`start_time`, `end_time`, `order_index`) from whisper-1 or an even-spread approximation, and `daily.py` runs captions (step 7) before asset collection (step 8). The recap scene planner (`services/scene_planner.py`) is the frozen-plan pattern to copy: temperature 0, JSON, a tolerant `_loads` parser, rows in `media_scenes` with a unique `(episode_id, scene_index)` index, `IntegrityError` handled by re-reading. Nested config blocks merge as `{**RECAP_DEFAULTS, **(effective_config("recap", {}) or {})}` (`pipeline/recap_shorts.py:49`). Migrations are numbered functions in `db/migrations.py` (latest `0007`); new tables use `Base.metadata.create_all(tables=[...])`. Prompts are seeded insert-if-absent in `db/seed.py` with `{{placeholder}}` string replacement, and the only template resolver is `pipeline/story_generator.py::_resolve_prompt(session, name, channel_id)`. `Settings.local_storage_path` (env `LOCAL_STORAGE_PATH`, default `./data/storage`) is the storage path `apps/desktop/startup-env.js` sets to `<userData>/data/storage` in packaged builds (verified on the Windows PC; the folder does not exist yet in dev), so the bank hangs off it. The Gemini text path (`services/ai_provider.py::_generate_gemini`) uses the legacy `google.generativeai` SDK (`requirements.txt` pins `>=0.7.0`, which supports `response_mime_type`) and drops `response_format` today. `pipeline/upload.py` uploads every `RenderJob` with `status == "completed"` that has no `YouTubeUpload`, one Short per channel per day, so a rendered Short usually waits days before upload.

## New config block: `channel.config["cast"]`

Defaults live in `content_defaults.py` as `CAST_DEFAULTS`; a channel overrides individual keys. The emotion vocabulary is a module constant, not a config key, because it doubles as the bank's folder names.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Turns the cast path on for this channel's fiction Shorts. Off means zero behavior change. |
| `library_path` | `null` | Bank root override. `null` resolves to `<local_storage_path>/cast/<channel-slug>`. |
| `planner_model` | `"gemini-2.5-flash"` | Gemini model for the beat planner and the character bible. |
| `variants_per_tag` | `3` | How many prompt variants `cast prompts` writes per tag (10 tags × 3 ≈ 30 clips per persona). |
| `clip_seconds` | `8` | Target length of each generated clip; written into the Flow prompts. |
| `beat_min_seconds` | `2.0` | Beats shorter than this are merged into the previous beat. |
| `beat_max_seconds` | `6.0` | Beats longer than this are split evenly. |
| `hero_outbox_enabled` | `false` | Phase 3 only: write hook-beat "hero" Flow prompts for today's Shorts. |
| `hero_prompts_per_day` | `2` | Phase 3 only: cap on hero prompts written per daily run. |

`CAST_EMOTION_TAGS` (constant): `neutral_talking`, `shocked`, `angry`, `sad`, `smug`, `laughing`, `thinking`, `whisper_secret`, `relieved`, `disgusted`. Unknown tags returned by the planner map to `neutral_talking`.

Bank layout (all under the resolved `library_path`):

```
<persona>/character.json          # bible written by `cast init`: name, look, wardrobe, setting, style_line, negatives, reference_image_prompt
<persona>/reference.png           # the reference image you generate in Flow/Whisk from that prompt and save here
<persona>/outbox/<tag>_<n>.txt    # one Flow prompt per file, written by `cast prompts`
<persona>/inbox/                  # drop downloaded Flow clips here, named after the prompt file (shocked_1.mp4)
<persona>/<tag>/<n>.mp4           # the bank; `cast scan` moves inbox clips here by filename prefix
<persona>/manifest.json           # written by `cast scan`: {relpath: {duration, width, height, mtime}}
hero/outbox/<story8>_hook.txt     # phase 3 only
hero/<story8>_hook.mp4            # phase 3 only
```

## Ordered code changes

Phase 0 — groundwork with no behavior change

1. `apps/worker/storyfactory/services/ai_provider.py`. Add a `response_format` parameter to `_generate_gemini`, pass it from the dispatch in `generate_text`, and when it is `"json"` set `response_mime_type="application/json"` on `genai.types.GenerationConfig`. Keep the legacy SDK; three lines.

2. `apps/worker/storyfactory/content_defaults.py`. Add `CAST_EMOTION_TAGS` and `CAST_DEFAULTS` (the table above), in the same comment style as `RECAP_DEFAULTS`.

3. `apps/worker/storyfactory/db/models.py`. Add `StoryBeat` (`__tablename__ = "story_beats"`) after `Story`: `id`, `story_id` FK `stories.id` indexed, `channel_id` FK nullable indexed, `beat_index` Integer, `start_seconds` Float, `end_seconds` Float, `word_start` Integer, `word_end` Integer, `text` Text, `emotion` String(40), `shot` String(10) (`cast` or `broll`), `broll_query` String(120) nullable, `asset_id` String(36) FK `assets.id` nullable, `source` String(10) nullable (`cast`, `stock`, `hero`), `created_at`. Add `Index("ix_story_beats_story_idx", "story_id", "beat_index", unique=True)`.

4. `apps/worker/storyfactory/db/migrations.py`. Append `("0008_story_beats", _m0008_story_beats)` to `MIGRATIONS`, body copied from `_m0006_media_scenes` with `StoryBeat.__table__`. Never reorder earlier entries.

5. `apps/worker/storyfactory/db/seed.py`. Add three global prompts to `DEFAULT_PROMPTS` (category `"cast"`):
   - `cast_character_bible`: inputs `{{persona}}`, `{{niche}}`, `{{content_style}}`; output JSON with `name`, `age_range`, `look`, `wardrobe`, `setting`, `style_line`, `negatives`, `reference_image_prompt`. The style line must be one sentence that every clip prompt repeats verbatim, which is what keeps the character consistent across Flow generations.
   - `cast_clip_prompt`: inputs `{{style_line}}`, `{{look}}`, `{{setting}}`, `{{emotion}}`, `{{clip_seconds}}`; output plain text, one Flow prompt for a 9:16 talking-to-camera clip showing the given emotion, natural mouth movement, no on-screen text, no speech audio needed.
   - `cast_beat_plan`: inputs `{{numbered_words}}`, `{{emotion_tags}}`, `{{beat_min}}`, `{{beat_max}}`; output strict JSON `{"beats":[{"word_start":int,"word_end":int,"emotion":str,"shot":"cast"|"broll","broll_query":str}]}`. Rules in the prompt: cover every word once in order, the first beat is always `cast` and covers the opening words, roughly one beat in three is `broll` with a 2–4 word stock query, put `cast` on the emotional turns.
   Seeding is insert-if-absent, so `npm run seed` adds them without touching edited prompts.

6. `packages/shared/src/index.ts`. Add `CAST_EMOTION_TAGS`, a `CastConfigSchema` (`z.object({...}).partial()` mirroring the `CAST_DEFAULTS` types) and `cast: CastConfigSchema` inside `ChannelConfigSchema`. Zod objects strip unknown keys, so without this a `cast` block saved through the dashboard would be dropped.

7. `apps/worker/storyfactory/services/channel_service.py`. Add `set_cast_options(session, channel, *, commit=True, **options)` copied from `set_recap_options`, writing into `config["cast"]`.

Phase 1 — bank tooling (CLI), usable before any render change

8. New `apps/worker/storyfactory/services/cast_library.py`. Filesystem and manifest logic only, no LLM:
   - `merged_cast_config()` returns `{**CAST_DEFAULTS, **(effective_config("cast", {}) or {})}`.
   - `library_root(channel, cfg)` resolves `library_path` or `Path(settings.local_storage_path) / "cast" / channel.slug`.
   - `ingest_inbox(root, persona)` moves `inbox/<tag>_<n>.mp4` into `<tag>/` for filenames whose prefix is in `CAST_EMOTION_TAGS`, and reports the rest.
   - `build_manifest(root, persona)` runs ffprobe once per new or changed clip (reuse `renderer._get_video_duration`) and writes `manifest.json`; the daily run never probes or hashes clip files.
   - `coverage(root, persona)` returns `{tag: clip_count}`.
   - `pick_clip(root, persona, tag, story_id, occurrence)` returns a path or `None`: sorted variants of the tag, index `(int(sha256(f"{story_id}:{tag}").hexdigest()[:8], 16) + occurrence) % n`, where `occurrence` is how many earlier beats of this story already used this tag. Same story, same clips every time; two same-tag beats in one story use different variants while variants last.
   - `asset_for_clip(session, channel_id, persona, tag, path, manifest_entry)` finds an `Asset` by `original_url == f"file://{path}"` or creates one with `provider="cast"`, `type="video"`, `license="Owner-generated (Google Flow)"`, `source_query=f"{persona}/{tag}"`, `checksum=""`, `duration_seconds/width/height` from the manifest, `local_path`, `policy_status="verified"`, and bumps `usage_count`.

9. `apps/worker/storyfactory/__main__.py`. Add a `cast` click group next to `recap`, using `_with_channel` and `use_channel` the same way; prompt templates come from `story_generator._resolve_prompt` (import it):
   - `cast init --channel <ref> --persona <p> [--all-personas] [--force]`: renders `cast_character_bible`, calls `generate_text(provider=AIProvider.GEMINI, model=cfg["planner_model"], temperature=0.7, max_tokens=1200, response_format="json")`, writes `<persona>/character.json` and `outbox/00_reference_image.txt`. Skips personas that already have a `character.json` unless `--force`.
   - `cast prompts --channel <ref> --persona <p>`: for every `(tag, n)` up to `variants_per_tag` with no clip in the bank, renders `cast_clip_prompt` by string substitution (no LLM call) into `outbox/<tag>_<n>.txt`, and writes `outbox/README.md` with the Flow steps: upload `reference.png` as the ingredient, 9:16, `clip_seconds`, download, rename to the prompt filename, drop in `inbox/`, run `cast scan`.
   - `cast scan --channel <ref> [--persona <p>]`: `ingest_inbox` then `build_manifest`, printing coverage.
   - `cast status --channel <ref>`: coverage per persona and tag, plus which entries of the channel's `voice_personas` have no bank yet.
   - `cast config --channel <ref> [--enabled/--disabled] [--planner-model] [--beat-min] [--beat-max] [--library-path]`: calls `set_cast_options`.

Phase 2 — beat planning and rendering

10. New `apps/worker/storyfactory/services/beat_planner.py`, modeled on `scene_planner.py`:
    - `existing_beats(session, story_id)` returns frozen rows ordered by `beat_index`.
    - `shape_beats(raw_beats, words, audio_duration, *, min_seconds, max_seconds) -> list[dict]` is a pure, unit-tested helper. It clamps word ranges to the word list, fills gaps so every word is covered once in order, sets `start_seconds[i] = words[word_start].start_time`, sets `end_seconds[i] = start_seconds[i+1]` for interior beats (so whisper's inter-word silences never leave holes), forces beat 0 to start at 0 and the last beat to end at `audio_duration`, splits beats longer than `max_seconds` evenly, merges beats shorter than `min_seconds` into the previous beat, maps unknown emotions to `neutral_talking`, and forces beat 0 to `shot="cast"`. With an empty word list it returns `[]`.
    - `heuristic_beats(words, ...)` builds a plan with no LLM: split at sentence-ending punctuation, then shape; emotion `neutral_talking`, every third beat `broll` with the story's category as the query.
    - `plan_story_beats(session, story, caption_job, tts_job, cfg, *, dry_run=False) -> list[StoryBeat]`: returns frozen rows if present; otherwise loads `CaptionWord` rows for `caption_job.id` ordered by `order_index`, renders `cast_beat_plan` with the words numbered `#0 word #1 word …`, calls `generate_text(provider=AIProvider.GEMINI, model=cfg["planner_model"], temperature=0.0, max_tokens=1500, response_format="json")`, parses with a copy of `scene_planner._loads`, falls back to `heuristic_beats` on any failure or in dry-run (never a paid provider), shapes with `audio_duration = tts_job.duration_seconds`, writes `StoryBeat` rows, and handles `IntegrityError` by re-reading. Logs `beat_plan_frozen` with the beat count and which planner produced it.
    - `resolve_beat_assets(session, story, beats, stock_pool, cfg) -> list[StoryBeat]`: `cast` beats call `pick_clip(root, story.voice_persona, beat.emotion, story.id, occurrence)` and set `asset_id`/`source="cast"`; `broll` beats and cast misses take `stock_pool[(int(sha256(f"{story.id}:{beat.beat_index}").hexdigest()[:8], 16)) % len(stock_pool)]` with `source="stock"`, where `stock_pool` is the list of assets `collect_story_relevant_assets` already downloaded for the story (no extra API calls). If the persona folder has no clips at all, leave every `asset_id` empty so the renderer uses today's path. Commit and return.

11. `apps/worker/storyfactory/services/renderer.py`.
    - Add `_prepare_beat_background(segments: list[tuple[str, float]], width, height, fps) -> str | None`: for each `(path, seconds)` run `ffmpeg -y -stream_loop -1 -i path -t seconds -an -vf scale=W:H:force_original_aspect_ratio=increase,crop=W:H,fps=FPS,setsar=1 -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p ./data/tmp/beat_<i>_<hex>.mp4`, then concat-demux the segments with `-c copy` into `./data/tmp/beats_<hex>.mp4`. Uniform re-encoding is what makes the copy-concat safe (that is the step `_concat_backgrounds` skips). Return `None` on any ffmpeg failure.
    - In `render_short`, before the `RenderJob` is constructed: query `StoryBeat` rows for `story.id` ordered by `beat_index`; `beats_ready` is true when rows exist and every row's `asset_id` points at an `Asset` whose `local_path` exists on disk. Add `"visual_source": "cast" if beats_ready else "stock"` and `"beat_count": len(rows)` to `render_config`, and append the beat assets' ids to `asset_ids`. In the non-dry-run branch, when `beats_ready`, build `segments = [(asset.local_path, end_seconds - start_seconds) ...]` and set `bg_paths = [timeline]` if `_prepare_beat_background` returns a path; otherwise `bg_paths` stays `_get_valid_background_paths(assets_list)`. The existing `-stream_loop -1` and `-t audio_duration` stay; the timeline already equals the narration length. Stories with no beat rows (recap narration Shorts, cast-disabled channels) take exactly today's path.

12. `apps/worker/storyfactory/pipeline/daily.py` and `apps/worker/storyfactory/pipeline/render.py`. In both drivers, right after the per-story asset collection loop (inside the active `use_channel` context, which `render.py` already has), add a step 8b guarded by `cfg = cast_library.merged_cast_config(); cfg["enabled"] and story.type == "short" and caption_jobs.get(story.id) is not None and caption_jobs[story.id].status == "completed"`: `beats = plan_story_beats(...)` then `resolve_beat_assets(session, story, beats, story_assets[story.id], cfg)`. Print one line per story: `✓ Beats: 7 (cast 5 / stock 2)`. A story with a failed caption job has no words, gets no rows, and renders as today. Long-form rendering is untouched.

13. Tests in `apps/worker/tests/`, following `test_scene_planner.py` and the `isolated_env` fixture in `conftest.py`: `test_beat_planner.py` (`shape_beats` coverage, interior ends equal next starts, split, merge, first-beat rules, unknown-tag mapping, empty words → `[]`; `heuristic_beats` fallback when the LLM returns garbage), `test_cast_library.py` (deterministic `pick_clip`, occurrence offset, inbox ingest by prefix, coverage), `test_cast_render_fallback.py` (in dry-run an empty bank leaves `render_config["visual_source"] == "stock"` with `beat_count > 0`; with `_ffmpeg_render_short` and `_prepare_beat_background` monkeypatched and a temp bank, `render_config["visual_source"] == "cast"`).

14. Docs: add a "Cast library (fiction Shorts)" section to `docs/multi-channel.md` (layout, CLI, config keys, the Flow steps) and one row to the README configuration section. `.env.example` is unchanged.

Phase 3 — hero clips for the hook (optional; only after phases 1–2 have run for real)

15. `beat_planner.py`: when `hero_outbox_enabled`, after freezing, render `cast_clip_prompt` for beat 0 with that beat's text as the action line and write `hero/outbox/<story8>_hook.txt`, capped by `hero_prompts_per_day`. This is what spends the daily Flow credits on the hook moment.

16. `__main__.py`: `cast rerender --channel <ref> --story <id8>`: if `hero/<story8>_hook.mp4` exists, point beat 0's `asset_id` at a new `provider="cast"` Asset with `source="hero"`, set the story's previous completed `RenderJob.status = "superseded"` (so `upload.py`'s `status == "completed"` filter cannot upload both), and call `render_short` again with the same TTS and caption jobs; it overwrites `short_<id8>.mp4`. Because Shorts wait in the one-per-day upload queue, there is a multi-day window to do this.

## Rollout order and first-run checklist

Ship phases 0 and 1 together; they change nothing for existing channels. Run `cast init` and `cast prompts` for one persona on the fiction channel, generate the ~30 clips in Flow, `cast scan`, and set `voice_personas` on that channel to just that persona so every Short shows the character. Then ship phase 2 and run `cast config --enabled`. First run `daily --dry-run`: dry-run uses `heuristic_beats` and placeholder assets, so it should log `visual_source=stock` with a non-zero `beat_count`. Then a real run; check the `render_jobs` rows for `visual_source=cast` and any `error`, since `render_short` still swallows exceptions.

## Out of scope

No dashboard UI beyond the Zod schema; the CLI is the surface. No paid text or media API is added; the planner's only fallback is the heuristic. The UGC pipeline's own character-bible and clip prompts are still on the other computer; when they arrive they replace the wording of the two seed templates in step 5 and nothing else changes.
