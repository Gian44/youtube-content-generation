# Narrated movie recaps for CinybeShorts — design

**Date:** 2026-10-05
**Status:** draft for review
**Channel:** `cinybe-shorts` (the only channel in scope)
**Supersedes for this channel:** `recap_shorts` scene-montage mode (left in the code, no longer used by CinybeShorts)

## 1. Goal

Turn CinybeShorts into an unattended narrated movie-recap channel. Each queued film yields one
long-form recap (~10 min) plus a fixed number of Shorts, written from the film's plot text, voiced
with TTS, captioned, and uploaded by the existing uploader. Gian supplies film files when he has
them; the system runs without them. A daily scheduled Claude task keeps the queue fed and
reports; it never renders.

### Why not the existing `recap_shorts` mode

It cuts scenes out of the video first and writes around them. In four months it produced two
Shorts from one 47-minute episode (56 of 58 scenes rejected), needs a video file for every title,
and uploaded original audio. The new mode plans from plot text and treats footage as optional.

### Decisions already made (Gian, 2026-10-05)

- Output per film: Shorts **and** long-form, from one plan.
- Narration is TTS; the film's original audio is never used.
- Plot source: Wikipedia plot section, with Claude (the operator) writing the synopsis when
  Wikipedia is thin. Never the model's memory.
- Title policy: back catalog by theme (e.g. "every Nolan film"), not new releases.
- The app's own scheduler renders and uploads; the Claude task only feeds the queue.
- Footage: the real film when a file is present, stock b-roll + TMDB stills when not.
  Gian accepts Content ID claims on footage-backed uploads as a business cost.
- Cast library (Flow-generated character clips) is parked; it is not used here.

## 2. Scope

**In:** new `pipeline_mode: narrated_recap`; `recap_queue` table and CLI; Wikipedia plot fetcher;
TMDB metadata lookup with Wikidata fallback; Gemini planner (acts → long-form script + Short
scripts); two visual sources (source video via transcript alignment, stock + stills); per-item
footage mode; daily-run branch; upload-health reporting; dashboard/shared-schema updates needed
for the mode to validate; the operator task's contract.

**Out:** changes to `recap_shorts`, `fiction`, `sleep_facts`; new-release selection; subtitle
(`.srt`) input; trailer downloads; Flow/AI-generated visuals; a dashboard UI for the queue
(CLI only in v1); multi-channel support for the mode.

## 3. Architecture

```
operator (Claude, daily)      Gian (optional)
  recap queue add/set-plot      drops Film (Year).mp4 in inbox
          │                             │
          ▼                             ▼
   ┌──────────────── recap_queue (sqlite) ────────────────┐
   │ queued → ready | needs_input → planned → rendered → done | failed │
   └───────────────────────────────────────────────────────┘
          ▲ movie_meta (TMDB/Wikidata) + wiki_plot fill plot_text
          │
   daily.py  ──► recap_planner (Gemini, 2 calls) ──► stories (+ story_segments)
                      │
                      ▼
            visuals: source_video (whisper align → cut) | stock (+ TMDB stills)
                      │
                      ▼
            tts → captions → render_short ×N + render_long_form ──► upload drip
```

Everything below the queue is the existing fiction pipeline with one new planner and one new
visual path. The queue is the only new concept.

## 4. Data

### 4.1 `recap_queue` (migration `0008_recap_queue`)

| column | type | notes |
| --- | --- | --- |
| id | uuid pk | |
| channel_id | fk channels | |
| title | text | canonical title from TMDB once resolved |
| year | int null | |
| tmdb_id | int null | |
| wikipedia_title | text null | resolved article title |
| theme | text null | operator-supplied grouping, free text |
| status | text | `queued`, `ready`, `needs_input`, `planned`, `rendered`, `done`, `failed` |
| plot_source | text null | `wikipedia`, `operator` |
| plot_text | text null | |
| plot_url | text null | |
| file_path | text null | set when a film file exists; drives footage mode |
| footage_mode | text | `source_video`, `stock` — derived at `ready` time, stored for the record |
| metadata_json | text | TMDB payload subset: genres, director, cast top 5, runtime, poster/backdrop paths |
| fail_reason | text null | |
| created_at / updated_at | datetime | |

Unique index on `(channel_id, lower(title), year)`.

Status transitions:

- `queued` → `ready` when `plot_text` has ≥ `min_plot_words` and metadata resolved.
- `queued` → `needs_input` when Wikipedia has no plot or it is below `min_plot_words`.
- `needs_input` → `ready` on `set-plot`.
- `ready` → `planned` after the planner writes stories; `planned` → `rendered` when all renders
  exist; `rendered` → `done` when all uploads for the item are `completed` (checked by the daily
  run, not the uploader).
- Any step → `failed` with `fail_reason`; `retry` resets to `queued` (or `ready` if plot exists).

### 4.2 Reused tables

- `stories`: one row per Short and one per long-form, with a new nullable `recap_queue_id`
  column (added in the same migration) so renders and uploads trace back to the film.
- `story_segments`: long-form sections, as the fiction long-form already uses them.
- `caption_words`, `render_jobs`, `youtube_uploads`: unchanged.

### 4.3 Channel config (`channel.config["narrated_recap"]`, defaults in `content_defaults.py`)

```
inbox_path: ./data/sources/inbox/<slug>      # film files
shorts_per_film: 5
min_plot_words: 250
planner_model: gemini-3.6-flash
tmdb_enabled: true
stills_enabled: true                          # TMDB poster/backdrops as Ken Burns cards in stock mode
theme_plan: []                                # ordered list of theme strings the operator consumes
min_ready_items: 3                            # operator tops up below this
```

Long-form length uses the channel's existing top-level `long_form_target_minutes` (set to 10 for
CinybeShorts), not a nested copy.

Shared Zod schema: add `narrated_recap` to `PIPELINE_MODES`, a `NarratedRecapConfigSchema`, and a
guard row: `narrated_recap` ⇒ `enable_shorts` true **and** `enable_long_form` true. The worker's
`config.py` Literal and the dashboard API mirror it.

## 5. Components

### 5.1 `services/movie_meta.py`

`resolve(title, year=None) -> MovieMeta | None`. TMDB `search/movie` → best match (year, then
popularity) → `movie/{id}` with `append_to_response=external_ids,images`. Returns canonical title,
year, tmdb_id, wikidata_id, genres, director, top-5 cast, runtime, poster/backdrop URLs. Wikidata
fallback (`wbsearchentities` + `wbgetentities`) when no TMDB key or TMDB fails; it yields the
Wikipedia article title and basic facts, no images. API key via `resolve_api_key("meta.tmdb",
"tmdb_api_key")`; missing key ⇒ Wikidata only, logged once.

### 5.2 `services/wiki_plot.py`

`fetch_plot(wikipedia_title) -> PlotText | None`. MediaWiki API `action=parse&prop=sections` to
locate a section titled Plot / Synopsis / Plot summary, then `action=parse&section=N&prop=wikitext`
→ plain text (strip templates, refs, links). Returns text, word count, URL. Resolves the article via
the Wikidata sitelink when `movie_meta` supplied one, else by title search with disambiguation
rejection (`"(film)"` preferred). No plot section or `< min_plot_words` ⇒ `None`.

### 5.3 `services/recap_queue.py`

CRUD + state machine over `recap_queue`; `prepare(item)` runs movie_meta then wiki_plot and sets
`ready` / `needs_input`; `ingest_inbox(channel)` lists the inbox, uses `recap_service.parse_filename`
for title/year, upserts items with `file_path` (existing items gain the file; status unchanged
unless `done`). Cooldown: `add` refuses a title present in the table at any status unless
`--force`; `done` items older than `topic_cooldown_days` may be re-added with `--force` only.

### 5.4 `services/recap_planner.py`

Two Gemini calls through the existing `gemini_rest` client (JSON mode, temperature 0, rules as
system instruction, prompts seeded in `db/seed.py` as `narrated_recap_acts` and
`narrated_recap_scripts`, category `narrated_recap`):

1. **Acts.** Input: plot_text, metadata. Output: 10–14 ordered acts
   `{index, title, summary (≤60 words), broll_query, stakes}`; the last act is the ending. Validated:
   count in range, indices contiguous, summaries non-empty.
2. **Scripts.** Input: acts, metadata, `shorts_per_film`, `long_form_target_minutes`. Output:
   `long_form: {title, description, sections: [{act_index, text}]}` at 140–170 words/min of target,
   and `shorts: [{act_index, hook, text, title, tags}]` each ≤ 55 words (≤ 20 s), opening with the
   hook in the first sentence, ending on a cliffhanger or payoff, per the repo's
   `youtube-shorts-optimizer` rules (no hashtags in titles, 3-tier tags). Validated: word caps,
   distinct `act_index` per Short, long-form word total within ±15 % of target.

Failure of either call after `gemini_rest`'s retries ⇒ item `failed` with the error; no paid
fallback. A validation failure retries the call once with the validation errors appended.

Planner output is persisted as `stories` rows (`story_type` short/long_form, `recap_queue_id`,
`title`, `script`, `description`, `tags`) and `story_segments` for the long-form, then the item is
`planned`.

### 5.5 Visuals

**Stock mode** (`file_path` null): per Short, `collect_story_relevant_assets` with the act's
`broll_query` (existing path); for long-form, one query per section. When `stills_enabled` and
TMDB images exist: a new `services/stills.py` downloads poster + up to 3 backdrops once per item
into `<local_storage_path>/recap/<queue_id>/` and makes 4-second Ken Burns clips (ffmpeg
`zoompan`, 1080×1920 and 1920×1080) used as the first clip of every Short and as section
title-cards in the long-form. Stock clips fill the rest.

**Source-video mode** (`file_path` set):

1. `services/film_align.py`: extract audio, transcribe with the existing whisper-1 path in
   chunks (`transcribe_dialogue` extended to accept an offset), persist words to
   `<local_storage_path>/recap/<queue_id>/transcript.json` (once per item).
2. One Gemini call (JSON, temp 0): acts + the transcript as `[mm:ss] text` lines → for each act a
   `{act_index, start_seconds, end_seconds}` window of 20–90 s. Validated: inside the film's
   duration, non-overlapping, ordered. On failure fall back to proportional windows (act i covers
   the i-th slice of the runtime) — deterministic, never paid.
3. Per Short: cut the act's window with `recap_service.cut_clip`, drop audio, normalize to
   1080×1920@30 (crop), feed it to `render_short` as the background in place of stock. Per
   long-form section: cut the window, loop/trim to the section's TTS duration, 1920×1080.

The visual source is chosen per item at `ready` time and recorded in `footage_mode`; a file that
appears after planning is picked up only on `retry`.

### 5.6 Daily run (`pipeline/narrated_recap.py`, branched from `daily._run_for_channel`)

1. `ingest_inbox`; `prepare` every `queued` item (network calls, cheap).
2. Take the oldest `ready` item (none ⇒ log `queue_empty`, exit).
3. Plan (5.4) → `planned`.
4. For each Short story: TTS → captions → visuals → `render_short`. Long-form: TTS per section →
   captions → visuals → `render_long_form`. Existing policy check runs on every script first.
5. All renders present ⇒ `rendered`. Any render failure ⇒ item `failed`, completed renders kept.
6. Sweep: `rendered` items whose uploads are all `completed` ⇒ `done`.

Uploads: unchanged `run_upload_pipeline`. `max_short_uploads_per_day` governs the drip; with the
default 1 a film's five Shorts spread over five days, which is intended. Long-form uploads are not
throttled today and stay that way.

### 5.7 CLI (`recap queue` group in `__main__.py`)

```
recap queue add "Title" [--year 2010] [--theme "Nolan"] [--force]
recap queue status [--json]       # items by status, upload health, last run, next ready item
recap queue show <id>
recap queue set-plot <id> --file plot.txt [--source operator]
recap queue retry <id>
recap queue remove <id>
recap queue scan                  # ingest_inbox + prepare, without rendering
```

`status --json` is the operator contract. It includes: counts per status, every `needs_input` and
`failed` item with title/year/fail_reason, `ready_count` vs `min_ready_items`, `theme_plan`
remaining, the YouTube integration's `status` and `last_checked_at`, and the last 24 h of
`youtube_uploads` for the channel. The worker has no YouTube health check today
(`youtube_uploader.py` only uploads), so `status` adds one: a `channels.list(mine=true)` call
with the stored credentials when `last_checked_at` is older than 24 h, writing `ok` /
`auth_failed` / `unreachable` and `last_checked_at` back to `channel_integrations`.

### 5.8 Operator task (scheduled Claude run, daily, requires Gian's PC)

Runs via Desktop Commander (native Windows shell), **not** the Cowork VM shell (its egress proxy
blocks Google APIs). Procedure, in order:

1. `recap queue status --json`.
2. For each `needs_input`: web-research the plot (Wikipedia, reviews, fan wikis; reconcile), write
   a 300–600-word neutral synopsis including the ending, `set-plot --source operator`.
3. For each `failed`: if `fail_reason` is transient (network, 429, 5xx) → `retry`; otherwise report.
4. If `ready_count < min_ready_items`: pick titles from the current `theme_plan` entry (skipping
   anything in the queue), `add` them, `scan`.
5. If upload health is not `ok` or no uploads completed in 48 h while `rendered` items exist:
   flag it in the report.
6. Report: one short message — items added, plots written, failures, uploads in the last 24 h.

The task never runs `daily`, never edits config, never deletes.

## 6. Error handling

- Network/API failures in `prepare` leave the item `queued` (retried next run); after 3
  consecutive failures the item goes `needs_input` with the reason, so the operator sees it.
- Planner validation failures retry once, then `failed`.
- Any ffmpeg/whisper failure ⇒ `failed`; partial renders are kept and reused on `retry`
  (idempotent by `story_id`).
- The policy check rejecting a script ⇒ that story is dropped; the item proceeds if ≥ 1 Short and
  the long-form survive, otherwise `failed`.
- The daily run never blocks on the queue being empty or on the operator; it logs and exits.

## 7. Testing

Unit (pytest, no network): `wiki_plot` on fixtures (full plot, stub, no section, disambiguation
page); `movie_meta` on recorded TMDB/Wikidata JSON; `recap_planner` validators (counts, word
caps, retry-with-errors); `recap_queue` state machine and cooldown; `film_align` window validation
and proportional fallback; `narrated_recap` daily branch with mocked services; shared Zod guard
for the new mode; CLI `status --json` shape.

Integration (manual, once, on the PC): one film in stock mode and the same film with its file
present, `--dry-run` upload, inspect renders and the `status --json` output. Then enable real
uploads and let the scheduler run for a week before the operator task is created.

## 8. Rollout

1. Land the code; `cinybe-shorts` keeps `recap_shorts` until the integration test passes.
2. Switch the channel: `pipeline_mode: narrated_recap`, `enable_long_form: true`,
   `audio_mode`/`footage_mode` keys from the old `recap` block become inert.
3. Seed the queue with 3 titles (one with a file) and a `theme_plan`.
4. Verify the YouTube integration health once by hand.
5. Create the scheduled operator task (requires computer, Desktop Commander), observe one week.

## 9. Open questions (settle before implementation)

- TMDB terms for a monetized channel: use with attribution in descriptions, or Wikidata-only.
  Default in this spec: TMDB with attribution; flip `tmdb_enabled` if Gian decides otherwise.
- `max_short_uploads_per_day`: keep 1 (five-day drip per film) or raise to 2.
