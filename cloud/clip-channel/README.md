# sylusnix — clip channel pipeline

Shorts for the sylusnix YouTube channel, cut from other creators' footage (with credit and source links). Internal
notes: nothing posted on YouTube links to this repo.

- **Twitch lane (the channel's lane):** `.github/workflows/sylusnix-twitch.yml` runs `run.py twitch` on GitHub
  Actions three times a day: one "top 5" ranking Short (Style C) from a streamer's most-viewed Twitch clips.
- **PC lane:** `run.py daily` / `run.py auto` (Windows Task Scheduler via `auto_task.pyw`; the task is disabled for
  now): single clips from the watchlist creators' YouTube videos (Style A meme panel, Style B cinematic) and
  YouTube rankings (`run.py rank`, Style C).

Smoke test of the Twitch lane: `gh workflow run sylusnix-twitch.yml -f smoke=true` (unlisted upload, ledger
untouched). Its artifact holds `info.json` and the frame sheet `sheet.jpg` (never a video: the repo is public);
the "Effect levels" step prints each part's loudness and its effect's level in the log. Tests: `python -m pytest -q`.

## Visual style rules (Felix, 2026-11)

Felix (Puello Media) reviewed one of Gian's programmatic videos and said it looked "made by Claude". As applied to
every style here:

1. **Footage, not stills.** The clips are the footage.
2. **Much less text, never the speech repeated.** No running captions. Only emphasis words from
   `cc.textbudget.pick` (`render.emphasis`): the key number with its unit, else a first-mentioned name, 1-3 words,
   about 6 a minute (one at most per ranking entry). Never a word already on screen (title, labels, names; a
   number counts as shown when its digits are), never a masked swear. Streams with live captions
   (`captions: false` in `watchlist.yml`) get none. Ranking labels are 1-2 words plus one emoji icon ("Rage 😡",
   "$250,000 💰"), the ranking title at most 5 words: the planner prompts ask for it, `find.short_label` and
   `find.short_title` enforce it (a longer title falls back to "<Name>'s" + "top clips" / "top <game>"). The
   upload title is not touched.
3. **Sound effects quiet, varied, not on every cut.** No whoosh, static or ping. `cc.sfx` synthesises a soft pluck
   for an entry landing and a low thump for the #1 reveal, only on the cuts `cc.sfx.on_cuts` picks (a few, never
   two in a row, always the #1 reveal), seeded by the output folder (date + streamer) so each ranking sounds
   different. Each effect peaks 18 dB under the clip's own peak and never above `cc.sfx.PEAK_DB` (-24 dBFS).
   A part without an effect has only its own sound (short fades at the cuts).
4. **Section indicators minimal.** The ranking list is the numbers 1-5 and the short label; no "Number 3" cards.
5. **Graphics:** no label that repeats what is already on screen (see 2). No maps here.
6. **No awkward empty space.** A blurred, darkened copy of the clip fills the whole 1080x1920 frame behind the
   sharp clip box (Style C's bands above and below, Style B's bars), with a soft dark gradient behind the ranking
   title. Style A's white headline panel is the meme look Gian approved and stays as it is.

Checks for a change to the look: `tests/test_felix_style.py` (emphasis words, label/title trimming, effects on a
few cuts and under the clip, no flat black band), then a smoke run's log and frame sheet.
