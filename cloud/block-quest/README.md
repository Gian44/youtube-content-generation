# Block Quest Daily — cloud pipeline (Roblox Shorts)

**Since 2026-11-19** (Gian: kid-safe, all ages, no creepy stories; a general-audience channel, not "made for
kids"): every day one Roblox story and two interactive Shorts, all over Creative Commons Roblox gameplay
(`gameplay.yml`), the order shuffled by the date. Stories come from the 10 funny/wholesome/adventure categories in
`topics.yml` (the writer rejects anything creepy, scammed, hacked or banned). The interactive formats live in
`bqd/interactive.py`: **would_you_rather** (4 rounds, A red / B blue, 3-second countdown), **guess_the_game** (3
games from a fixed kid-safe list, 3 clues each, the name never in a clue) and **pick_one** (pick an egg / chest /
door: one, two or three, then all three open). Each spoken line is voiced on its own and joined with countdown
ticks, so the panel always matches the voice. Ledger rows carry `format` for the two-week comparison.

**Voice (2026-11-19, `bqd/voice.py`)**: Kokoro **af_heart**, US English, speed 1.05 (about 180 words a minute), on
the runner, free, no quota; fixed for this channel whatever `TTS_BACKEND` says. "Roblox" and "Robux" are respelled
for the voice only (Kokoro says RAH-blocks otherwise). Voice F (Pocket) is no longer used here.

```bash
gh workflow run block-quest-daily.yml -f count=3 -f smoke=true                         # today's mix, unlisted
gh workflow run block-quest-daily.yml -f count=1 -f smoke=true -f format=pick_one      # one format, unlisted
```

The text below describes the original story pipeline; the stock-footage path still exists behind
`background: "stock"`.


Three original first-person story Shorts a day (the first live at once, the next 8 h and 16 h later), written,
voiced, cut over stock b-roll and uploaded from GitHub Actions. No PC, no database. The channel's 45 earlier
Shorts (StoryFactory, to 2026-06-06) stay as they are.

```
topics.yml (13 categories with weights, hints and a narrator persona)
  → draw 3 distinct categories for the day (weighted, seeded by the date, skipping the last 2 days' picks)
  → one writer call per story (Claude on the subscription via sof/llm.py; OpenAI/Gemini fallback):
    hook → setup → turn → payoff → one closing question; 100–150 words; no "Reddit"; recent hooks avoided;
    validated, retried once with the errors; no-AI-slop pass (sof/noslop.py, mode "fiction")
  → narration: voice F (Pocket TTS, free, on the runner); TTS_BACKEND=gemini + GEMINI_API_KEY_BLOCKQUEST
    switches to Charon on this channel's own free tier
  → b-roll: Pexels/Pixabay video on the story's setting queries, previews checked by Codex/Claude; stock
    photos when nothing fits; a category card when the photos fail too
  → 1080×1920, clips 6 s each crossfaded, burned captions, title band "Block Quest Daily", −16 LUFS
  → upload (Entertainment): Short 1 public now, Shorts 2–3 private + publishAt; ledger.json committed
```

Everything that is not story-specific is imported from `../sleep-on-facts/sof` and `../common/cc`
(see `bqd/__init__.py`); nothing in `sof` is modified.

## Running

```bash
gh workflow run block-quest-daily.yml -f count=1 -f skip_upload=true   # tests + one render, no YouTube
gh workflow run block-quest-daily.yml -f count=1 -f smoke=true         # one unlisted Short, ledger untouched
gh workflow run block-quest-daily.yml -f category=revenge              # today's 3 Shorts, all revenge
gh workflow run block-quest-daily.yml                                   # today's 3 Shorts
```

Locally: `python run.py --count 1 --skip-upload` (ffmpeg, the Python deps and the env below; Pocket TTS needs
`HF_TOKEN`).

## Secrets

Shared with the other channels: `CLAUDE_CODE_OAUTH_TOKEN`, `CODEX_AUTH_JSON`, `HF_TOKEN`, `GEMINI_API_KEY`,
`PEXELS_API_KEY`, `PIXABAY_API_KEY`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `OPENAI_API_KEY` (optional). This
channel's own: `YT_CLIENT_ID_BLOCKQUEST`, `YT_CLIENT_SECRET_BLOCKQUEST`, `YT_REFRESH_TOKEN_BLOCKQUEST` (the
channel's own OAuth client and token — copied from the StoryFactory DB while alive, else `channel connect-youtube
--channel default` on the PC) and optional `GEMINI_API_KEY_BLOCKQUEST`.

## Ledger

One row per day: `{date, kind: "shorts", stories: [{video_id, title, category, persona, hook, words, seconds,
publish_at}], voice, run}`. The cron does nothing twice in one UTC day; `smoke` runs never write it.

## Files

| file | purpose |
|---|---|
| `bqd/config.py` | tunables; wraps `sof.config.Config` for keys and voice |
| `bqd/stories.py` | topics.yml, the category draw, the writer, validation, no-slop |
| `bqd/seo.py` | titles, descriptions, tags, publish times |
| `bqd/pipeline.py` | orchestration, resumable `work/<date>/`, ledger |

## Roblox stories over gameplay (2026-11-18)

The channel is a Roblox channel: `topics.yml` holds ten Roblox story categories, the writer prompt in `bqd/stories.py`
keeps every story clean for young listeners, and each Short plays over one continuous clip of Creative Commons Roblox
gameplay (`gameplay.yml`, `bqd/gameplay.py`, `cc.segments.build_background_short`). The clip's maker is credited by
name and link in the description; uploads go to the Gaming category with `#roblox`. `background: "stock"` in
`bqd/config.py` brings back the Pexels/Pixabay path.
