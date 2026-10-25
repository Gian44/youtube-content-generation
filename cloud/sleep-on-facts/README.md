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
    a vision pass labels each subject / setting / reject → reel of subject shots (+ ≤30 % setting),
    dimmed to 75 %, portraits shown whole on a blurred fill → smooth 4× zoompan reel, looped under the audio
  → thumbnail from a vision-picked subject photo (the subject large and recognisable at tile size), the
    topic in big caps and the video's 2-4 word hook (`thumb_text`) in gold under it
  → YouTube upload, private + scheduled for 8:30 PM US Eastern; curiosity-first title ("The Moon Is Slowly
    Drifting Away From Earth | 3 Hours of Space Facts to Fall Asleep To"), plain-English chapters,
    Education category, hashtags; added to its category playlist
  → 3 Shorts teasers (sof/shorts.py): three different facts from three different chapters, ~50 s each,
    same narrator a touch brighter, 1080×1920 over the run's own photos with burned-in captions; the first
    goes live one hour after the long video, the others one and two days later, all linking to it
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

- **Voice (since the evening of 2026-10-25): our own designed narrator** — `brand/voice/narrator.wav`, an original
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
`YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`. The YouTube token is the Sleep On Facts channel's
OAuth refresh token (re-authorize with the StoryFactory CLI
`channel connect-youtube --channel sleep-on-facts`, then copy it over).

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
| `run.py`, `sof/` | the pipeline (`config, llm, topics, topicgen, research, script, prompts, noslop, tts, music, images, render, thumbnail, seo, credits, upload, ledger, pipeline`) |
| `sof/noslop.py` | the no-AI-slop lint + targeted rewrite + the prompt text of the rules; rules ported from [petergyang/no-ai-slop](https://github.com/petergyang/no-ai-slop) (MIT) and adapted for spoken narration. Vendored verbatim into `apps/worker/storyfactory/services/noslop.py` (a worker test keeps the two identical) |
| `../../docs/credits/` | one photo-credits page per video (GitHub Pages), linked from its description — required for CC BY/BY-SA photos |
| `tests/` | unit tests (mocked HTTP) + one real-ffmpeg render test |

Each run works in `work/<date>-<topic>/` and resumes from existing stage outputs if re-run
on the same checkout. Failures: the job goes red and GitHub emails the owner; the ledger is
only written after a successful upload, so the same topic is retried next day.

## Not here (by decision)

Shorts, captions upload (needs the youtube.force-ssl scope), analytics. The StoryFactory desktop app's `sleep_facts` mode is
legacy; its `sleep-on-facts` channel is paused so two systems never upload to one channel.
