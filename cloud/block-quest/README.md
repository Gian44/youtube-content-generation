# Block Quest Daily — cloud pipeline (Roblox Shorts)

**Since 2026-11-19** (Gian: kid-safe, all ages, no creepy stories; a general-audience channel, not "made for
kids"): every day one Roblox story and two interactive Shorts, all over Creative Commons Roblox gameplay
(`gameplay.yml`), the order shuffled by the date. Stories come from the 10 funny/wholesome/adventure categories in
`topics.yml` (the writer rejects anything creepy, scammed, hacked or banned). The interactive formats live in
`bqd/interactive.py`: **would_you_rather** (4 rounds, A red / B blue, 3-second countdown), **guess_the_game** (3
games from a fixed kid-safe list, 3 clues each, the name never in a clue) and **pick_one** (pick an egg / chest /
door: one, two or three, then all three open). Each spoken line is voiced on its own and joined with the countdown
seconds (a soft woodblock tick, one per second, far under the voice), so the panel always matches the voice. Panels
show the round number and each choice / clue / reward as 1-3 words with an emoji icon, never the spoken sentence
(see "Visual style rules" below). Ledger rows carry `format` for the two-week comparison.

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
    photos when nothing fits; no Short when the photos fail too (no text card instead of footage)
  → 1080×1920, clips 6 s each crossfaded, a few emphasis words (no captions, no title band), −16 LUFS
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

## Visual style rules (Felix, 2026-11)

A client editor (Felix, Puello Media) said the programmatic videos looked "made by Claude". What his notes mean for
this channel, for every future change (Gian: the channel stays kid-safe, all ages, on the Creative Commons gameplay):

1. **Footage, not stills.** Every Short plays continuous gameplay from `gameplay.yml`. The stock path
   (`background: "stock"`) makes no Short when it finds neither footage nor photos, rather than a text card.
2. **Little text, never the narration.** Story Shorts show only a few emphasis words (`cc.textbudget` via
   `cc.segments`: the key number with its unit, a first-mentioned name; never "Roblox"/"Robux" alone,
   `pipeline.SKIP_WORDS`). Interactive panels show each choice / clue / reward as 1-3 key words plus one emoji icon:
   the writer's `a_short`/`a_icon`, `b_short`/`b_icon` and per-clue / per-reward `short`/`icon`, checked (1-3
   words, at most 20 characters, kid-safe, one kid-safe emoji — no weapon, grave, romance, monster, creed,
   political sign or flag, `interactive.ICON_BLOCK` — no word of the game's name in a clue and no picture of it,
   `interactive.NAME_ICONS`, e.g. no 🍕 for Work at a Pizza Place); when one is missing or invalid the spoken line's
   key words are shown with no icon. The spoken lines never change for this. A label too long for its box shrinks;
   no word is ever cut off. Guess the Game's answer (the game's name) is the one longer text: it is the needed word.
3. **Quiet, varied sound.** No whoosh, static or sine ping. The countdown is a soft synthesised woodblock with a
   lower "tock" on the last count (`cc.sfx`), a different variant per Short (seeded by the date and the Short), one
   per countdown second so the number keeps time with the voice. `interactive.TICK_DB` (-32 dBFS) keeps every tick
   21-28 dB under the voice's peak after the final loudnorm, which lifts the quiet countdown by 6-10 dB (measured
   2026-11-21 with Kokoro af_heart; `test_countdown_ticks_keep_time_and_stay_far_under_the_voice` holds it at
   18 dB or more). No other effects.
4. **Minimal section indicators.** The round as a number ("1/4"); the format's name is in the title and the voice,
   not on the panels.
5. **Graphics.** None on this channel today. Any future one: slow camera motion, no label that repeats what is
   already on screen.
6. **Every frame filled.** Gameplay fills the 1080×1920 frame under everything (all 87 clips are 1080×1920 with no
   bars, checked 2026-11-21). Panels are compact solid boxes in the upper half, the countdown circle under them,
   clear of the Shorts UI (bottom fifth, right edge).
7. **Icons, not text cards.** The intro and the call to action are an icon with a word or two (🎮 "3 clues",
   💬 "A or B?", 💬 "?/3", 💬 "1, 2 or 3?"); Pick One's closed boxes show the egg / chest / door as an icon.
   Icons are Noto Color Emoji (`fonts-noto-color-emoji` in the workflow); without the font the words are drawn alone.
