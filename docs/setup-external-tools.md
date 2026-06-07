# Setup: External Tools & APIs

StoryFactory is a local desktop app ([desktop-app.md](desktop-app.md)). The two
new pipelines — **CinybeShorts** (`recap_shorts`) and **Sleep On Facts**
(`sleep_facts`) — deliberately introduce **no new paid services**. Everything
builds on tools the app already uses plus one free, keyless web API.

This page is the step-by-step setup for every external dependency these pipelines
use. Shared provider keys are entered **once** under the dashboard
**Settings → Integrations** (or via `npm run worker:settings`), never per channel.

| Tool / API | Used by | Cost | Key required? |
|------------|---------|------|---------------|
| **FFmpeg + ffprobe** | both (clip cutting, duration probe, rendering) | free | no (local binary) |
| **OpenAI** (text + TTS) | both (recap/sleep narration + voice) | paid usage | yes (shared) |
| **Pexels** | Sleep visuals; CinybeShorts `stock_metaphor` fallback | free | yes (free, shared) |
| **Pixabay** | Sleep visuals; CinybeShorts `stock_metaphor` fallback | free | yes (free, shared) |
| **Wikipedia REST API** | Sleep fact grounding + keywords | free | **no key, no account** |
| **YouTube Data API (OAuth)** | upload/analytics | free quota | per-channel OAuth |

---

## 1. FFmpeg + ffprobe (required for real runs)

Used to probe episode durations, cut recap clips, and render every video.
`ffprobe` ships with FFmpeg. Dry-runs do **not** need FFmpeg (placeholders are
written instead), so you can test the whole flow before installing it.

**Windows**

```powershell
winget install Gyan.FFmpeg
# or: choco install ffmpeg
```

**macOS**

```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu)**

```bash
sudo apt update && sudo apt install -y ffmpeg
```

Verify both are on your `PATH`:

```bash
ffmpeg -version
ffprobe -version
```

If `ffprobe` can't read an episode's duration, register the episode with an
explicit `--duration <seconds>` (see [multi-channel.md](multi-channel.md) → Recap CLI).

---

## 2. OpenAI (text + TTS) — shared

Both pipelines write their narration with OpenAI text models and voice it with
OpenAI TTS.

1. Create an API key at <https://platform.openai.com/api-keys>.
2. Add billing (TTS and text generation are paid usage).
3. Enter the key once under the dashboard **Settings → Integrations → OpenAI**,
   or from the CLI (the key is read from stdin so it never appears in argv):

   ```bash
   echo '{"api_key":"sk-..."}' | npm run worker:settings -- set-secret --provider text.openai --enable --secrets-stdin
   echo '{"api_key":"sk-..."}' | npm run worker:settings -- set-secret --provider tts.openai  --enable --secrets-stdin
   ```

`.env` `OPENAI_API_KEY` also works as a fallback/seed. You can test without a key
using `--dry-run` (no API calls are made).

---

## 3. Pexels + Pixabay (free) — shared

Provide the background footage for **Sleep On Facts** and the
`stock_metaphor` footage mode of **CinybeShorts**. Both have free API keys.

- **Pexels:** sign up at <https://www.pexels.com/api/>, copy your API key.
- **Pixabay:** sign up at <https://pixabay.com/api/docs/>, copy your API key.

Enter them once under **Settings → Integrations**, or via CLI:

```bash
echo '{"api_key":"<pexels-key>"}'  | npm run worker:settings -- set-secret --provider assets.pexels  --enable --secrets-stdin
echo '{"api_key":"<pixabay-key>"}' | npm run worker:settings -- set-secret --provider assets.pixabay --enable --secrets-stdin
```

If neither is configured, CinybeShorts still works with `with_source_video`
(it uses your own clips) but Sleep On Facts will fall back to generic/placeholder
backgrounds.

---

## 4. Wikipedia REST API (free, no key)

Sleep On Facts grounds each topic with a free Wikipedia REST summary
(`https://en.wikipedia.org/api/rest_v1/page/summary/<Topic>`). **No API key or
account is required** — it just needs outbound internet at run time, and the app
sends a descriptive `User-Agent` per Wikimedia policy.

- Nothing to configure. It is on by default.
- Disable per channel with `config.sleep_facts.enable_wikipedia_grounding = false`
  (the video is then produced from the topic title alone).
- If the lookup fails (offline, rate-limited, missing page) the run continues
  gracefully without grounding.

---

## 5. YouTube OAuth (per channel)

Each channel connects its own YouTube account. The shared Google Cloud OAuth app
(`YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REDIRECT_URI` in `.env`)
is configured once; then click **Connect YouTube** on each channel's card.

Full walkthrough: [youtube-oauth-setup.md](youtube-oauth-setup.md) and
[multi-channel.md](multi-channel.md) → *Connecting YouTube per channel*.

---

## 6. CinybeShorts inbox folder

CinybeShorts reads **only local files you place** in an inbox folder (it is not a
stream ripper). Default location: `./data/sources/inbox/cinybe-shorts`
(override with `config.recap.inbox_path`).

Create it and drop episodes/movies in, named by convention:

```
data/sources/inbox/cinybe-shorts/
├── Breaking Bad S01E01.mkv          # series: S01E01
├── Breaking Bad S01E02.mkv
├── Inception (2010).mp4             # movie
└── The Office/                      # subfolder = series title
    ├── The Office S02E01.mp4
    └── The Office S02E02.mp4
```

Accepted extensions: `.mp4 .mkv .mov .webm .avi .m4v .ts`. New files are picked
up on each daily run, or immediately via:

```bash
npm run worker -- recap inbox scan --channel cinybe-shorts
```

Cut clips are written to `./data/sources/clips/` during real runs.

See [multi-channel.md](multi-channel.md) for the full filename convention,
segmentation rules, the dedupe ledger, and the recap CLI.

---

## Quick verification (no keys needed)

```bash
npm run db:migrate                                      # create tables incl. recap media
npm run worker:daily -- --channel cinybe-shorts --dry-run
npm run worker:daily -- --channel sleep-on-facts --dry-run
```

Dry-runs make no API calls, cut no real clips, and upload nothing — they prove
the wiring end-to-end before you add keys or footage.
