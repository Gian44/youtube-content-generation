# Sleep On Facts — cloud pipeline

Unattended daily ~3-hour "facts to fall asleep to" videos, rendered on GitHub Actions and
uploaded to the Sleep On Facts YouTube channel. No desktop app, no database, no PC.

```
topic (topics.yml) → script (gpt-4o-mini, Gemini fallback) → narration (OpenAI gpt-4o-mini-tts, echo, bedtime direction)
  → ~150 landscape photos (Pexels/Pixabay) → Ken Burns reel, looped under the audio (ffmpeg)
  → YouTube upload → ledger.json (committed)
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

The daily cron is commented out in the workflow until the smoke run has been reviewed —
uncomment the `schedule:` block to go live (02:00 Asia/Singapore).

Locally (needs ffmpeg + the env vars below): `python run.py --minutes 3 --skip-upload`.

## Secrets (repo → Settings → Secrets → Actions)

`OPENAI_API_KEY`, `GEMINI_API_KEY`, `PEXELS_API_KEY`, `PIXABAY_API_KEY`, `YT_CLIENT_ID`,
`YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`. The YouTube token is the Sleep On Facts channel's
OAuth refresh token (re-authorize with the StoryFactory CLI
`channel connect-youtube --channel sleep-on-facts`, then copy it over).

## Cost per video (≈ $2.80)

OpenAI `gpt-4o-mini-tts` ≈ $17 per 1M characters → ~150k chars ≈ $2.60; `gpt-4o-mini` script ≈ $0.15;
Pexels/Pixabay/YouTube/Actions free (public repo). The run aborts before TTS if the script
exceeds 1.3× the word target, and before everything if the YouTube token is dead.

## Files

| file | purpose |
| --- | --- |
| `topics.yml` | topics + stock-photo queries; the picker takes the topic with the fewest ledger entries |
| `ledger.json` | one row per uploaded video, appended and committed by the workflow |
| `run.py`, `sof/` | the pipeline (`config, llm, topics, research, script, prompts, tts, images, render, upload, ledger, pipeline`) |
| `tests/` | unit tests (mocked HTTP) + one real-ffmpeg render test |

Each run works in `work/<date>-<topic>/` and resumes from existing stage outputs if re-run
on the same checkout. Failures: the job goes red and GitHub emails the owner; the ledger is
only written after a successful upload, so the same topic is retried next day.

## Not here (by decision)

Shorts, music, captions, analytics. The StoryFactory desktop app's `sleep_facts` mode is
legacy; its `sleep-on-facts` channel is paused so two systems never upload to one channel.
