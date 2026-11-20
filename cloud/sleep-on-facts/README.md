# Sleep On Facts — cloud pipeline

Unattended daily ~3-hour "facts to fall asleep to" videos, rendered on GitHub Actions and
uploaded to the Sleep On Facts YouTube channel. No desktop app, no database, no PC.

```
topic (generated daily from topics.yml categories, verified on Wikipedia) → research (full Wikipedia article
  + ~15 linked articles, chunked; per-movement retrieval) → script (gpt-5.6-terra writes from the notes,
  gpt-5.6-luna plans; repetition guard; sources in description)
  → no-AI-slop pass per movement (sof/noslop.py: the narrator voice + style rules sit in the writer prompt;
    a regex lint then scores the prose for the written tics of AI text — "not X but Y", colon reveals,
    fake-profound kickers, recap paragraphs, puffery, mood words — and only the flagged paragraphs go
    back to gpt-5.6-luna for a targeted rewrite, at most `slop_rounds`=2 times; score → ledger "slop")
  → narration (gpt-4o-mini-tts, echo)
  → faint synthesised ambient bed mixed under the voice (numpy + ffmpeg, licence-free)
  → photos of the subject itself: Wikimedia Commons (the article's own photos, then its Commons
    category), NASA for space topics, then Pexels/Pixabay only if the caption NAMES the subject;
    a vision pass labels each subject / setting / reject; the named things the narrator talks about get
    their own photos in the 20-second slots where they are named (sof/visuals.py, sof/cues.py)
  → calm real footage (sof/footage.py): NASA video (space topics), Wikimedia Commons video, then
    Pexels/Pixabay video only if its own metadata NAMES the subject; for the subject and for the named
    things holding the most slots; each clip's longest calm window (no cuts, flashes or busy motion) is
    vision-checked on two of its frames, then alternates with the photos in its thing's / the subject's slots
  → one narration-synced timeline (sof/timeline.py): photos with a slow push-in, clips slowed 1.5-2×,
    dimmed to 50 %, crossfaded, exactly as long as the narration
  → thumbnail from a vision-picked subject photo (the subject large and recognisable at tile size), the
    topic in big caps and the video's 2-4 word hook (`thumb_text`) in gold under it
  → YouTube upload, private + scheduled for 8:30 PM US Eastern; curiosity-first title ("The Moon Is Slowly
    Drifting Away From Earth | 3 Hours of Space Facts to Fall Asleep To"), plain-English chapters,
    Education category, hashtags; added to its category playlist
  → 3 Shorts teasers (sof/shorts.py): three different facts from three different chapters, ~50 s each,
    same narrator a touch brighter, 1080×1920 over the run's own footage clips and photos (frame always
    filled: clips cover-cropped, photos panned), a few emphasis words on screen; the first goes live one
    hour after the long video, the others one and two days later, all linking to it
    (Shorts are the one surface a new channel gets reach on)
  → ledger.json committed (Shorts under "shorts"; the first also under "short")
```

Design: `../../docs/superpowers/specs/2026-10-06-sleep-on-facts-cloud-pipeline-design.md`.

### Growth changes (2026-10-25, from Studio analytics)

76 % of long-form watch time came from *Suggested* (beside other long documentaries at bedtime), where
1.4 % of 8,167 thumbnail impressions were clicked; search gave 0.1 h; Shorts gave 88 % of views but 9 % of
watch time. So: hook-first titles (`seo.build_title`), hook thumbnails (`thumbnail.compose(hook=)`),
plain chapter titles, categories weighted by demand (`topics.yml`), sharper Shorts titles, one video a day,
and two new workflows:

- `sleep-repackage.yml` → `repackage.py`: new title, thumbnail and chapter names for videos already live.
  `apply=false` only builds them (the B arm for a Studio "Test & compare"); `apply=true` updates YouTube.
- `sleep-marathon.yml` → `marathon.py` (Sundays): three recent videos joined into one ~9-hour upload from
  the `media-<run id>` artifacts the daily runs keep for 45 days — no new narration cost.

## Running

Everything runs from the `sleep-on-facts daily` workflow (`.github/workflows/sleep-daily.yml`).

```bash
# smoke run: real APIs, ~3 minutes of video, uploaded UNLISTED, ledger untouched (≈ $0.05)
gh workflow run sleep-daily.yml -f minutes=3

# first full run (public by default)
gh workflow run sleep-daily.yml -f minutes=180

# render only, no YouTube
gh workflow run sleep-daily.yml -f minutes=180 -f skip_upload=true

# no Shorts teasers for this run (locally: --no-shorts)
gh workflow run sleep-daily.yml -f minutes=180 -f shorts=false

# watch
gh run list --workflow sleep-daily.yml --limit 3 && gh run watch
```

The cron (`17 15 * * *` UTC — daily, 23:17 Asia/Manila; GitHub starts it 3–5 h late) uploads the
video private and YouTube publishes it at 20:30 America/New_York. A run more than 12 h from that
slot (e.g. a manual daytime run) publishes immediately. Comment out the `schedule:` block to pause;
`workflow_dispatch` still works for manual runs.

Locally (needs ffmpeg + the env vars below): `python run.py --minutes 3 --skip-upload`.

## Who writes the scripts (2026-10-25)

Claude, on Gian's Claude subscription: the workflow installs the Claude Code CLI and the pipeline calls
`claude -p` (Sonnet for the narration, Haiku for planning/metadata/rewrites), authenticated by the
`CLAUDE_CODE_OAUTH_TOKEN` secret. Create it once on a computer logged in to the Claude account:
`claude setup-token`, then `gh secret set CLAUDE_CODE_OAUTH_TOKEN` and paste the token. A preflight call
checks it each run; if Claude is capped or logged out, OpenAI then Gemini write instead (ledger `llm_calls`).

## The voice and the photo checks (2026-10-25, after the OpenAI keys ran out of credits)

- **Voice (since 2026-10-26): Gemini TTS "Charon"** (`tts_backend="gemini"`, `sof/tts.py` GeminiSpeaker) on
  gemini-3.8-flash-tts, then gemini-3.8-flash-lite-tts when the first model's day is used up. Free tier on the
  GEMINI_API_KEY project: 3 requests/min and **10 requests/day per model** (measured 2026-10-26); one request
  per ~1,250 words (≈ 9 min), so a 2.5 h video + 3 Shorts needs ~17 of the 20. Delivery is tuned toward the
  gemini-3.1 Charon Gian prefers: pauses × 1.9 (`stretch_pauses`), tempo 0.92 via ffmpeg rubberband
  (`gemini_tempo`, `gemini_pitch`). A refused or cut-off chunk is split and retried; when every model is out
  of quota the whole narration falls back to the designed voice below (a Short falls back on its own).
- **Fallback voice: our own designed narrator** — `brand/voice/narrator.wav`, an original
  voice designed from a text description with Qwen3-TTS VoiceDesign on Gian's PC (he picked "F · Mellow
  Australian"; see `brand/voice/README.md`), spoken nightly by **Kyutai Pocket TTS** on the runner CPU (2 worker
  processes × 2 threads; ~2.3× real time per process measured on Gian's laptop). The model is gated on Hugging
  Face: secret `HF_TOKEN` (Gian's read token, account granted access); weights cached. If Pocket TTS cannot load,
  the whole narration falls back to Kokoro below. Never clone a real person's or a product's voice here
  (Gemini's terms and Pocket TTS's conditions forbid it) — design a new one instead.
- **Fallback voice: Kokoro-82M**, an open-weights TTS model (Apache-2.0, fine for a monetised channel), run on the
  runner's CPU with `kokoro-onnx` — no API, no credits. The narrator is our own blend of Kokoro's British male
  voices, `bm_george:0.6,bm_lewis:0.4` (`kokoro_voice`; Gian picked it from samples), at speed 0.88 with
  0.55 s between sentences, plus a little low-end warmth before loudnorm. The workflow downloads the model once
  (GitHub release `model-files-v1.0`, SHA-256 checked) and caches it. It costs ~0.3–0.6 s of CPU per second
  of audio, so a 2.5-hour narration adds about an hour to the run (job timeout raised to 360 min).
  `TTS_BACKEND=openai` brings back gpt-4o-mini-tts echo when there are credits.
- **Photo checks: `sof/vision.py`** — Codex (Gian's ChatGPT plan via `codex exec`) first, Claude (`claude -p`
  with the Read tool, Haiku) as fallback, OpenAI last. Photos go in as numbered contact sheets (12 per question).
  A provider with a dead login or a usage limit is switched off for the run; if nobody answers, photos stay
  "unknown" and are used. Ledger rows record `vision_calls` and `voice`.
- **Codex login in CI**: secret `CODEX_AUTH_JSON` = the contents of `%USERPROFILE%\.codex\auth.json` from
  Gian's PC. The workflow refuses a login with < 6 h left and stamps `last_refresh` so Codex never refreshes
  it on the runner (a runner-side refresh would rotate the refresh token and could sign the PC out). The PC
  keeps the login fresh; re-copy the file into the secret when it renews (about every 10 days). OpenAI's docs
  only support API keys for CI, so this is a workaround that Claude backs up.

## Secrets (repo → Settings → Secrets → Actions)

`CLAUDE_CODE_OAUTH_TOKEN`, `CODEX_AUTH_JSON`, `HF_TOKEN`, `OPENAI_API_KEY` (optional now), `GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `YT_CLIENT_ID`,
`YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`. The YouTube token is the Sleep On Facts channel's
OAuth refresh token (re-authorize with the StoryFactory CLI
`channel connect-youtube --channel sleep-on-facts`, then copy it over).

## Visual style rules (Felix, 2026-11)

Felix (Puello Media, a client editor) reviewed one programmatic video and said it looked "made by Claude": too
many stills, too much on-screen text, stock-sounding effects. His standard, as this channel applies it — keep to
it in future changes:

1. **Footage, not stills** (YouTube treats image-heavy videos as slideshows). Calm real footage of the named
   subject fills a real share of the slots (`sof/footage.py`; log line `footage: … · M of K slots (P %)`, ledger
   `timeline.footage_slots`). Sources in order: NASA video (space topics), Wikimedia Commons video, then
   Pexels/Pixabay only when the clip's own metadata names the subject. A named thing's clips go in its own
   slots, the subject's clips in the others (filler, passage shots, chapter photos), alternating with the photos
   (`footage_every`); a clip at most `footage_max_uses` times, `footage_gap` slots apart; slot 0 stays the
   subject photo; photos keep every slot without a good clip. Every clip passes a calm check (the longest window
   with no cut, no flash, no busy motion, no black and no frozen picture, slowed 1.5-2× to fill a slot without
   looping) and the photos' vision check on two frames of that window ("subject" only; title cards, talking
   heads, text rejected). Author and licence of every clip are credited like the photos. Commons video files
   (upload.wikimedia.org) are rate-limited — 429 after ~10 quick requests (measured 2026-11-21) — so every
   download and window cut there is paced 2 s a request across threads (`footage.WIKIMEDIA_GAP`); keep it.
2. **Almost no text on screen.** The long video shows none (no chapter cards, no captions); credits go on the
   credits page, else in the description, and only those that do not fit there appear small on screen. The
   Shorts show only a few emphasis words (`shorts.emphasis_overlays` / `cc.textbudget`), never the narration
   repeated, and no credit block.
3. **No sound effects** — the narration and the faint synthesised bed only.
4. **Chapters in the description only** (YouTube chapters), nothing on screen.
5. **Maps and graphics** get the same slow push-in as every photo; the vision check rejects anything mostly text
   or crowded with labels (posters, document scans, title cards, infographics, labelled diagrams and maps);
   clean photos and clean maps stay.
6. **Every frame filled.** Clips are cover-cropped; in the Shorts photos are cover-cropped with a slow pan across
   them and clips cropped to 9:16 — no small photo on a blurred letterbox. (In the long video a portrait photo is
   shown whole on a blurred copy of itself, which fills the frame.)
7. **No pop-up text cards** — footage instead.

## Photo credits page (2026-11-07)

CC BY / CC BY-SA photos must name their author. Until 2026-11-07 that credit was burned into the frame
(the old GitHub Pages credits page gave the repo away); now each video gets its own page on Cloudflare —
`https://sleep-on-facts-credits.<account subdomain>.workers.dev/<run id>` — listing every credited photo
with author, licence link and Commons file page, plus the sources that need no credit and what was changed.
The description carries one line with the link (the CC licences accept a link to a page that holds the
attribution); the Shorts link the same page; a marathon links each part's page. The frame carries nothing.

Setup, once: a Cloudflare API token from the **Edit Cloudflare Workers** template (Workers Scripts + Workers
KV Storage edit) → secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` → run the
**sleep-on-facts credits site** workflow (or `python -m sof.credits_site deploy` with both in the
environment). It creates the `sof-credits` KV namespace, deploys the `sleep-on-facts-credits` Worker and
writes a `test` page. The daily run checks the site's root before every upload and re-deploys the Worker
itself if it stopped answering. Without the two secrets (the case today) the credits go in the description
first and only those that do not fit there stay small on screen (2026-11, Felix: less text on screen); with
them but Cloudflare down for the run, the authors are listed in the description instead (`sof/captions.py`).
Footage clips are credited the same way (author, licence, Commons/NASA page).

## Cost per video

Since 2026-10-25: ≈ $0 — Kokoro voice on the runner, scripts on the Claude plan, photo checks on the
ChatGPT/Claude plans. Before (all OpenAI, ≈ $3.75): `gpt-4o-mini-tts` ≈ $17 per 1M characters → ~170k chars ≈ $2.90; `gpt-5.6-terra` script ≈ $0.75 + `gpt-5.6-luna` planning + no-slop rewrites ≈ $0.03–0.3 + `gpt-4o-mini` photo labels + thumbnail pick ≈ $0.02;
Wikimedia Commons/NASA/Pexels/Pixabay/YouTube/Actions free (public repo). The run aborts before TTS if the script
exceeds 1.3× the word target, and before everything if the YouTube token is dead.

## Files

| file | purpose |
| --- | --- |
| `topics.yml` | weighted categories (fewest past videos per unit of weight first); optional seed topics |
| `repackage.py`, `sof/repackage.py` | re-title / re-thumbnail / rename chapters of live videos |
| `marathon.py`, `sof/marathon.py` | weekly ~9-hour compilation from kept media artifacts |
| `ledger.json` | one row per uploaded video, appended and committed by the workflow |
| `run.py`, `sof/` | the pipeline (`config, llm, topics, topicgen, research, script, prompts, noslop, tts, music, images, visuals, cues, footage, timeline, render, shorts, thumbnail, seo, captions, credits_site, upload, ledger, pipeline`) |
| `sof/footage.py` | calm real footage: NASA / Commons / name-gated stock video search, the calm-window check, the clip vision check, cutting the window, placing clips in the slots |
| `sof/credits_site.py` | the per-video photo-credits page on Cloudflare (Workers KV + Worker), published before each upload |
| `sof/noslop.py` | the no-AI-slop lint + targeted rewrite + the prompt text of the rules; rules ported from [petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop) (MIT) and adapted for spoken narration. Vendored verbatim into `apps/worker/storyfactory/services/noslop.py` (a worker test keeps the two identical) |
| `tests/` | unit tests (mocked HTTP) + one real-ffmpeg render test |

Each run works in `work/<date>-<topic>/` and resumes from existing stage outputs if re-run
on the same checkout. Failures: the job goes red and GitHub emails the owner; the ledger is
only written after a successful upload, so the same topic is retried next day.

## Not here (by decision)

Shorts, captions upload (needs the youtube.force-ssl scope), analytics. The StoryFactory desktop app's `sleep_facts` mode is
legacy; its `sleep-on-facts` channel is paused so two systems never upload to one channel.
