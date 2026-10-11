# Sleep On Facts — cloud pipeline

Unattended daily ~3-hour "facts to fall asleep to" videos, rendered on GitHub Actions and
uploaded to the Sleep On Facts YouTube channel. No desktop app, no database, no PC.

```
topic (generated daily from topics.yml categories, verified on Wikipedia) → research (full Wikipedia article
  + ~15 linked articles, chunked; per-movement retrieval) → script (gpt-5.6-terra writes from the notes,
  gpt-5.6-luna plans; repetition guard; sources in description) → narration (gpt-4o-mini-tts, echo)
  → faint synthesised ambient bed mixed under the voice (numpy + ffmpeg, licence-free)
  → photos of the subject itself: Wikimedia Commons (the article's own photos, then its Commons
    category), NASA for space topics, then Pexels/Pixabay only if the caption NAMES the subject;
    a vision pass labels each subject / setting / reject → reel of subject shots (+ ≤30 % setting),
    dimmed to 75 %, portraits shown whole on a blurred fill → smooth 4× zoompan reel, looped under the audio
  → thumbnail from a vision-picked subject photo
  → YouTube upload, private + scheduled for 8:30 PM US Eastern; search-style title, chapters, Education
    category, hashtags; added to its category playlist → credits page (docs/credits/) + ledger.json committed
```

Design: `../../docs/superpowers/specs/2026-10-06-sleep-on-facts-cloud-pipeline-design.md`.

## Running

Everything runs from the `sleep-on-facts daily` workflow (`.github/workflows/sleep-daily.yml`).

```bash
# smoke run: real APIs, ~3 minutes of video, uploaded UNLISTED, ledger untouched (≈ $0.05)
gh workflow run sleep-daily.yml -f minutes=3

# first full run (public by default)
gh workflow run sleep-daily.yml -f minutes=180

# render only, no YouTube
gh workflow run sleep-daily.yml -f minutes=180 -f skip_upload=true

# watch
gh run list --workflow sleep-daily.yml --limit 3 && gh run watch
```

The daily cron (`17 15 * * *` UTC = 23:17 Asia/Manila; GitHub starts it 3–5 h late) uploads the
video private and YouTube publishes it at 20:30 America/New_York. A run more than 12 h from that
slot (e.g. a manual daytime run) publishes immediately. Comment out the `schedule:` block to pause;
`workflow_dispatch` still works for manual runs.

Locally (needs ffmpeg + the env vars below): `python run.py --minutes 3 --skip-upload`.

## Secrets (repo → Settings → Secrets → Actions)

`OPENAI_API_KEY`, `GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `YT_CLIENT_ID`,
`YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`. The YouTube token is the Sleep On Facts channel's
OAuth refresh token (re-authorize with the StoryFactory CLI
`channel connect-youtube --channel sleep-on-facts`, then copy it over).

## Cost per video (≈ $3.70)

OpenAI `gpt-4o-mini-tts` ≈ $17 per 1M characters → ~170k chars ≈ $2.90; `gpt-5.6-terra` script ≈ $0.75 + `gpt-5.6-luna` planning ≈ $0.03 + `gpt-4o-mini` photo labels + thumbnail pick ≈ $0.02;
Wikimedia Commons/NASA/Pexels/Pixabay/YouTube/Actions free (public repo). The run aborts before TTS if the script
exceeds 1.3× the word target, and before everything if the YouTube token is dead.

## Files

| file | purpose |
| --- | --- |
| `topics.yml` | categories that rotate daily (fewest past videos first); optional seed topics |
| `ledger.json` | one row per uploaded video, appended and committed by the workflow |
| `run.py`, `sof/` | the pipeline (`config, llm, topics, topicgen, research, script, prompts, tts, music, images, render, thumbnail, seo, credits, upload, ledger, pipeline`) |
| `../../docs/credits/` | one photo-credits page per video (GitHub Pages), linked from its description — required for CC BY/BY-SA photos |
| `tests/` | unit tests (mocked HTTP) + one real-ffmpeg render test |

Each run works in `work/<date>-<topic>/` and resumes from existing stage outputs if re-run
on the same checkout. Failures: the job goes red and GitHub emails the owner; the ledger is
only written after a successful upload, so the same topic is retried next day.

## Not here (by decision)

Shorts, captions upload (needs the youtube.force-ssl scope), analytics. The StoryFactory desktop app's `sleep_facts` mode is
legacy; its `sleep-on-facts` channel is paused so two systems never upload to one channel.
