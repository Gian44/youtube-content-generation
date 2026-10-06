# Sleep On Facts — cloud pipeline design

**Date:** 2026-10-06 · **Status:** approved by Gian in conversation, written for review
**Channel:** Sleep On Facts (YouTube) · **Runs on:** GitHub Actions (public repo), no PC involved

## 1. Goal

Publish one ~3-hour "facts to fall asleep to" video per day, fully unattended, from a
cloud runner: one calm narrator reading a long, flowing stream of low-stakes facts about
a single topic over a slideshow of ~150 topic-matched stock images. No captions, no
music, no title card. Reference: Sleepy Science Channel.

This replaces the `sleep_facts` mode of the StoryFactory desktop app, which has never
produced a video (one failed batch, June 2026) and depends on Gian's PC being awake.

### Decisions (Gian, 2026-10-06)

- Compute: GitHub Actions on a **public** repo (unlimited free minutes). Code is public;
  all keys live in Actions secrets. Cloudflare Workers were considered and rejected (no
  ffmpeg, CPU-seconds budget); Oracle VM rejected (a server to babysit).
- Script: **GPT-4o-mini** with Gemini free tier as automatic fallback on 429/503.
- Voice: OpenAI **`tts-1`, voice `onyx`, speed 0.9** (≈ $2.25 per 3-hour video).
- Cadence: **3 hours, one video per day.**
- Visibility: **public from day one.** A 3-minute real smoke run is therefore mandatory
  before the cron is enabled.
- Cost accepted: ≈ $2.50 per video (TTS + script) ≈ $75/month at daily cadence.
  Compute, images and upload are free.

## 2. Scope

**In:** a standalone Python pipeline (`topic → script → TTS → images → render → upload`),
a committed ledger for state, a GitHub Actions workflow (daily cron + manual dispatch
with a `minutes` override), unit tests with mocked HTTP, a smoke mode.

**Out:** StoryFactory changes beyond pausing its `sleep-on-facts` channel; Shorts;
background music; captions; analytics; thumbnails beyond a generated still; multi-channel.

## 3. Architecture

```
.github/workflows/daily.yml  (cron 18:00 UTC = 02:00 Asia/Singapore; workflow_dispatch)
        │
        ▼
run.py ──► pipeline/topics.py   next topic from topics.yml not in ledger (round-robin)
       ──► pipeline/script.py   outline → N movement expansions → ~27k words (gpt-4o-mini; Gemini fallback)
       ──► pipeline/tts.py      chunk ≤ 4,000 chars → tts-1 onyx 0.9 → concat → loudnorm → narration.mp3
       ──► pipeline/images.py   Pexels + Pixabay landscape photos, ≥100 target 150, dedupe by URL/hash
       ──► pipeline/render.py   Ken Burns clips → xfade reel (batched) → loop under audio -c:v copy → final.mp4
       ──► pipeline/upload.py   YouTube Data API v3 resumable upload, public, category 24, thumbnail
       ──► pipeline/ledger.py   append {date, topic, video_id, duration, words, cost_estimate}; git commit + push
```

Every stage is a pure function over `work/<run-id>/` paths plus a small typed config.
Stages skip themselves when their output file already exists, so a re-dispatched run on
the same runner resumes; a fresh runner starts over (nothing is cached across days on
purpose).

## 4. Components

### 4.1 `topics.yml` + `pipeline/topics.py`

```yaml
topics:
  - name: Ancient Egypt
    queries: [egypt pyramids desert, nile river sunset, hieroglyphs temple]
  - name: The Deep Sea
    queries: [deep ocean dark water, bioluminescent jellyfish, underwater cave]
  …
```

`pick_next(topics, ledger) -> Topic`: the first topic whose name has the fewest ledger
entries, ties broken by list order (round-robin that tolerates additions). The initial
list is StoryFactory's rotation: Ancient Egypt, The Deep Sea, Outer Space, The Roman
Empire, Volcanoes, The Human Brain, Whales, Antarctica, The Solar System, Dinosaurs —
each with 3 image queries written by hand in the YAML.

### 4.2 `pipeline/script.py`

Ported from StoryFactory `sleep_script_generator.py` and its three seeded prompts
(`sleep_facts_outline`, `sleep_facts_segment`), simplified:

1. `outline(topic, minutes) -> list[Movement]`: one call, 16 movements with a title and
   3–5 fact seeds each. Wikipedia summary (`REST /page/summary` + the first section of
   the article, same contact User-Agent as the recap work) is passed as grounding text.
2. `expand(movement, previous_tail) -> str`: one call per movement, ~1,500–2,000 words,
   calm present tense, no jokes, no second person, no "in this video", ends mid-flow (no
   sign-off), continuity from the previous 600 characters.
3. Loop: after all movements, if total words < 0.9 × target (target = minutes × 150),
   request 4 more movements "continuing the stream" and expand them, up to a hard cap of
   40 expansions. Abort the run if words > 1.3 × target before TTS (cost guard).
4. `write_metadata(topic, script) -> {title, description, tags}`: one small call; title
   ≤ 100 chars, pattern "Calm Facts About <Topic> to Fall Asleep To (3 Hours)", description
   with a one-line disclosure ("Narration is AI-generated; facts drawn from public
   sources") and 8–12 tags.

LLM client: OpenAI chat completions (`gpt-4o-mini`, temperature 0.7, max_tokens 2,800 per
expansion) behind a tiny interface; on HTTP 429/503/5xx after 4 backoff retries the same
prompt goes to Gemini (`gemini-3.6-flash` → `gemini-3.5-flash` → `gemini-3.5-flash-lite`,
free tier, reusing the REST client from the recap work). Any other error fails the run.

### 4.3 `pipeline/tts.py`

Split the script at sentence boundaries into chunks ≤ 4,000 characters (OpenAI limit
4,096). For each chunk: `audio.speech.create(model="tts-1", voice="onyx", speed=0.9,
response_format="mp3")` with 4 retries. Chunks are written as `tts/0001.mp3`…, skipped if
present, concatenated with ffmpeg concat demuxer, then `loudnorm=I=-18:TP=-2:LRA=11`
(quieter than the -14/-16 used for Shorts; this is bedtime audio) → `narration.mp3`.
Duration measured with ffprobe and recorded. Estimated cost logged
(`chars / 1e6 × $15`).

### 4.4 `pipeline/images.py`

For each of the topic's 3 queries, page Pexels (`/v1/search`, orientation=landscape,
size=large, 80/page) then Pixabay (`image_type=photo`, orientation=horizontal,
min_width=1920) until the pool has ≥ `images_target` (150) unique URLs, minimum 100.
Shortfall → pad with calm generic queries (`night sky stars`, `calm ocean`, `forest fog`,
`desert dunes`). Download to `images/NNN.jpg`, drop files < 50 KB or that fail to open
with Pillow, record attribution (photographer, source URL) to `images/credits.json`.
Never aborts for images unless fewer than 30 survive.

### 4.5 `pipeline/render.py`

Ported from StoryFactory `slideshow_renderer.py` (the reel-loop design), parameters:
1920×1080, 24 fps, `dwell_seconds` 20, `xfade` 1.5 s, Ken Burns zoom 1.0→1.08 with a slow
pan, libx264 `-preset veryfast -crf 24`, batches of 20 clips per partial reel.
`build_reel(images) -> reel.mp4` (~50 min for 150 images, encoded once) then
`assemble(reel, narration) -> final.mp4` with `-stream_loop -1 -i reel -i narration
-c:v copy -c:a aac -b:a 128k -shortest -movflags +faststart`. Thumbnail: the 10th image
with the topic name drawn by Pillow (large serif, dark overlay) → `thumb.jpg`.

Expected runner time: reel ≈ 25–40 min, assembly ≈ 3–6 min (copy), TTS ≈ 10–15 min,
script ≈ 10 min, upload ≈ 5–10 min for a 1.5–3 GB file. Budget 2 h; job timeout 5 h.

### 4.6 `pipeline/upload.py`

google-api-python-client, OAuth refresh token from secrets, resumable upload in 8 MB
chunks with retry, `snippet.categoryId=24`, `status.privacyStatus` from config (default
`public`), `selfDeclaredMadeForKids=false`, `containsSyntheticMedia=true`. Then
`thumbnails().set`. Returns `video_id`. A failed upload fails the run; the ledger is not
written, so the next day re-attempts the same topic. Quota: ≈ 1,650 units of 10,000/day.

### 4.7 `pipeline/ledger.py` + `ledger.json`

```json
[{"date": "2026-10-07", "topic": "Ancient Egypt", "video_id": "…", "duration_seconds": 10812,
  "words": 26950, "images": 150, "cost_estimate_usd": 2.41, "run_url": "…"}]
```

Appended only after a successful upload, committed by the workflow with
`git commit -m "ledger: <date> <topic>"` and pushed with the default `GITHUB_TOKEN`
(workflow permission `contents: write`).

### 4.8 `run.py` and config

`python run.py --minutes 180 [--topic "Whales"] [--privacy public|unlisted] [--skip-upload]`.
Config dataclass with the defaults above; env overrides for keys only. `--minutes 3` is
the smoke mode: identical path, real APIs, ≈ $0.04, uploaded unlisted regardless of
`--privacy` so a smoke never reaches subscribers.

### 4.9 Workflow `daily.yml`

- `on: schedule: cron "0 18 * * *"` and `workflow_dispatch` with inputs `minutes`
  (default 180), `topic`, `privacy`.
- `ubuntu-latest`, `timeout-minutes: 300`, Python 3.12, `apt-get install ffmpeg`,
  `pip install -r requirements.txt`, `python run.py …`, then commit/push the ledger.
- On failure GitHub emails the repo owner (default notification). No other alerting.
- Concurrency group `sleep-daily` with `cancel-in-progress: false` so a manual run and the
  cron never overlap.

## 5. Error handling

| Failure | Behaviour |
| --- | --- |
| OpenAI 429/5xx (script) | 4 retries with backoff, then Gemini fallback chain, then fail |
| OpenAI 429/5xx (TTS) | 4 retries per chunk, then fail (no free TTS fallback by decision) |
| Script too long/short | > 1.3× target → fail before TTS; < 0.9× → extend loop, cap 40 expansions |
| Images < 100 | pad with generic calm queries; < 30 → fail |
| ffmpeg error | fail with the last 500 chars of stderr in the log |
| Upload error | fail; ledger untouched; same topic retried next day |
| Runner disk | work dir on the runner's 14 GB disk; images ≤ 1 GB, reel ≤ 2 GB, final ≤ 3 GB |

## 6. Testing

- Unit (no network): topic picker (round-robin, ledger counts), script chunking and
  word-target loop (fake LLM), TTS chunker (sentence boundaries, ≤ 4,000 chars), image
  pool dedupe/pad logic (MockTransport), ffmpeg command builders (string assertions),
  ledger append/commit message, config/CLI parsing.
- Smoke: `workflow_dispatch` with `minutes=3` on the real repo → a real 3-minute unlisted
  video; Gian watches it. Only then is the cron enabled (it ships disabled: the schedule
  block is commented until the smoke passes).
- First full run: `minutes=180` via dispatch, public per decision; checked the next
  morning.

## 7. Rollout

1. Gian creates the public GitHub repo `sleep-on-facts` (empty) and adds the six secrets.
2. Pause StoryFactory's `sleep-on-facts` channel so the desktop app never uploads too.
3. Push the pipeline; run unit tests in Actions on push.
4. Smoke run (3 min) → review → enable cron.
5. First 3-hour run → review → leave it alone for a week; check the ledger and the
   channel.

## 8. Open items

- Exact `dwell_seconds`/zoom values are taste; start with the June spec's 20 s and tune
  after the first video.
- Whether to add a quiet music bed later (out of scope now; the reference channel has none).
