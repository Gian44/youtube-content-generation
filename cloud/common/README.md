# cloud/common — shared helpers (package `cc`)

Pieces more than one channel pipeline uses. Channels put this folder on `sys.path` (see `cny/__init__.py`,
`bqd/__init__.py`, `clipper/__init__.py`, `sof/__init__.py`); `cc` in turn puts `cloud/sleep-on-facts` on the path.

| module | what | used by |
|---|---|---|
| `cc/segments.py` | segment composition (cards, clips, photos) and the vertical Short builders | Block Quest, CinybeShorts |
| `cc/broll.py` | stock b-roll selection with a vision check | Block Quest (stock path) |
| `cc/textbudget.py` | which few words a Short may show (key number with its unit, then a first-mentioned name) | all four channels |
| `cc/sfx.py` | quiet synthesised effects (tick, tock, pluck, thump), which cuts get one, level measurements | Block Quest, sylusnix |
| `cc/icons.py` | a colour emoji drawn as an image (clean simple icons instead of words) | Block Quest, sylusnix |

Tests: `python -m pytest -q` here (the sleep, CinybeShorts and Block Quest workflows run them too).

## Visual style rules (Felix, 2026-11)

The text budget, effects and icons above exist to keep every channel inside Felix's rules (Puello Media, 2026-11;
lessons in `docs/notes/felix-feedback.md`): footage, not stills; few words on screen and never the narration repeated;
quiet, varied effects on a few cuts only, never a whoosh, static or sine ping; minimal section markers; slow motion on
graphics; every frame filled; icons instead of text cards. Change a helper only in a way that keeps those true for every
channel that imports it.
