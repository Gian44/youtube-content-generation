"""The channel's narrator. Gian 2026-11-19: "The voice seems inappropriate to the theme now. Use a more suitable voice.
You decide." Voice F (Pocket TTS, a mellow Australian man at about 130 words a minute) suited bedtime facts, not
kid-safe Roblox quizzes.

Chosen: Kokoro **af_heart** (open weights, Apache-2.0, free, runs on the runner, no daily quota) at speed 1.05, US
English. Why this one:
- Gemini TTS sounds best, but the only key is shared with Sleep On Facts, whose 3-hour video uses 18 of the 20
  free requests a day; Block Quest would starve it. No spending (manager mandate).
- Of Kokoro's American voices, af_heart and af_bella are the two graded A/A- by Kokoro's makers (the male voices
  top out at C+), and Gian dislikes voices that sound machine-made. On the same test lines af_heart had the most
  pitch movement of the two (about 2.9 semitones s.d. against 2.6) and the brisker pace (about 180 words a minute
  at 1.05, against voice F's ~130): upbeat without being shouty.
- Roblox Shorts audiences are mostly American; a bright young-adult American narrator is the genre's norm.

Kokoro's phonemizer says "Roblox" as RAH-blocks and "Robux" as RAH-bucks; ``for_tts`` respells them for the voice
only (captions and panels keep the real spelling)."""

from __future__ import annotations

import re

BACKEND = "kokoro"
VOICE = "af_heart"
LANG = "en-us"
SPEED = 1.05
SENTENCE_PAUSE = 0.25
CLAUSE_PAUSE = 0.12

# word → what the voice is given (checked against Kokoro's phonemes, 2026-11-19: "Roeblox" → ɹˈoʊblɑːks,
# "Roebucks" → ɹˈoʊbʌks)
RESPELL = {"roblox": "Roeblox", "robux": "Roebucks"}
_RESPELL_RE = re.compile(r"\b(" + "|".join(RESPELL) + r")\b", re.I)


def for_tts(text: str) -> str:
    """``text`` as the narrator should be given it."""
    return _RESPELL_RE.sub(lambda m: RESPELL[m.group(1).lower()], text)


def apply(sof_cfg) -> None:
    """Point a ``sof.config.Config`` at the channel voice (Kokoro af_heart, US English, Shorts pace)."""
    sof_cfg.tts_backend = BACKEND
    sof_cfg.kokoro_voice = VOICE
    sof_cfg.kokoro_lang = LANG
    sof_cfg.kokoro_short_speed = SPEED
    sof_cfg.kokoro_short_sentence_pause = SENTENCE_PAUSE
    sof_cfg.kokoro_clause_pause = CLAUSE_PAUSE
