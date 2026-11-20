"""On-screen text budget (Felix, Puello Media, 2026-11): much less text, never the narration repeated word for word.

A Short used to burn in every sentence the narrator says. Now only a few emphasis words reach the frame: the key
number with its unit ("$14", "99.86%", "10 million", "400 BC") and, while the budget allows, the first mention of a
name ("Cassini", "Brookhaven"). At most ``max_words`` words at a time, at least ``min_gap`` seconds apart, at most
``per_minute`` a minute. Pure functions; every channel draws the picked words its own way.

Input is timed words ``(start, end, word)`` — from a speech-to-text pass, or spread over caption cues by
:func:`words_from_cues` (TTS pace is close to uniform per character).
"""

from __future__ import annotations

import re

Word = tuple[float, float, str]

# unit words that belong with the number before them; value = how it is shown ("" = the word itself)
UNITS = {
    "percent": "%", "per cent": "%", "%": "%",
    "million": "", "billion": "", "trillion": "", "thousand": "", "hundred": "",
    "bc": "BC", "bce": "BCE", "ad": "AD", "ce": "CE",
    "km": "", "kilometres": "km", "kilometers": "km", "kilometre": "km", "kilometer": "km",
    "miles": "", "mile": "", "metres": "m", "meters": "m", "metre": "m", "meter": "m", "cm": "", "mm": "",
    "feet": "ft", "foot": "ft", "kg": "", "kilograms": "kg", "kilogram": "kg", "tons": "", "tonnes": "",
    "pounds": "lb", "degrees": "°", "°c": "°C", "°f": "°F", "light-years": "light-years", "light": "",
    "years": "years", "year": "", "days": "days", "hours": "hours", "minutes": "min", "seconds": "s",
    "mph": "", "km/h": "", "robux": "Robux", "dollars": "", "views": "", "times": "x",
}
_NUM = re.compile(r"^[$£€]?\d[\d,]*(?:\.\d+)?(?:%|st|nd|rd|th|s|k|m|bn)?$", re.I)
_YEAR = re.compile(r"^(1[0-9]{3}|20[0-9]{2})s?$")
_EDGE = re.compile(r"^[\"'“”‘’(\[]+|[\"'“”‘’)\],.;:!?…]+$")
STOP = {
    "i", "i'm", "i've", "i'd", "i'll", "the", "a", "an", "and", "but", "or", "so", "then", "now", "when", "while",
    "this", "that", "these", "those", "it", "it's", "its", "he", "she", "they", "we", "you", "his", "her", "their",
    "our", "your", "my", "in", "on", "at", "of", "for", "to", "from", "with", "by", "as", "if", "not", "no", "yes",
    "oh", "okay", "ok", "what", "who", "why", "how", "where", "there", "here", "one", "two", "three", "monday",
    "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mr", "mrs", "ms", "dr", "god", "lol",
    "chat", "bro", "yo", "hey", "wait", "look", "comment", "pick", "would", "rather", "guess", "it'll", "let's",
    "don't", "they're", "we're", "you're", "can't", "won't", "didn't", "that's", "there's", "he's", "she's",
}
# Nationalities, creeds and groups are never the "most necessary word" of a sentence and must not be singled out on
# screen ("Marx was not a German, he was a Jew" picked "German" and "Jew" in a 2026-11 review).
GROUPS = {
    "american", "americans", "british", "english", "french", "german", "germans", "italian", "spanish", "russian",
    "chinese", "japanese", "korean", "indian", "african", "asian", "european", "arab", "arabs", "mexican", "irish",
    "jew", "jews", "jewish", "muslim", "muslims", "islamic", "christian", "christians", "catholic", "protestant",
    "hindu", "buddhist", "nazi", "nazis", "communist", "communists", "black", "white", "latino", "gay", "lesbian",
}


def _bare(w: str) -> str:
    return _EDGE.sub("", w.strip())


def words_from_cues(cues: list[tuple[float, float, str]]) -> list[Word]:
    """Spread each cue's time over its words by character share → ``[(start, end, word)]`` (punctuation kept on the
    word, so sentence ends stay visible to :func:`pick`)."""
    out: list[Word] = []
    for s, e, text in cues:
        ws = str(text or "").split()
        total = sum(len(w) + 1 for w in ws) or 1
        t = float(s)
        for w in ws:
            d = (float(e) - float(s)) * (len(w) + 1) / total
            out.append((round(t, 3), round(t + d, 3), w))
            t += d
    return out


def _number_at(words: list[Word], i: int) -> tuple[str, int] | None:
    """The number phrase starting at word ``i`` ("$14", "99.86%", "10 million", "400 BC") and how many words it
    spans, or None."""
    w = _bare(words[i][2])
    if not _NUM.match(w):
        return None
    shown, n = w, 1
    if i + 1 < len(words):
        nxt = _bare(words[i + 1][2]).lower()
        two = (nxt + " " + _bare(words[i + 2][2]).lower()) if i + 2 < len(words) else ""
        if two in UNITS:
            shown, n = shown + UNITS[two], 3
        elif nxt in UNITS:
            unit = UNITS[nxt] or _bare(words[i + 1][2])
            if nxt == "dollars" and not shown.startswith("$"):
                shown = "$" + shown                        # Felix's own example: "$14"
            else:
                shown = shown + unit if unit in ("%", "°", "°C", "°F") else f"{shown} {unit}"
            n = 2
    return shown, n


def _sentence_start(words: list[Word], i: int) -> bool:
    return i == 0 or bool(re.search(r"[.!?…:]$", words[i - 1][2].strip("\"'”’)")))


def _name_at(words: list[Word], i: int, max_words: int) -> tuple[str, int] | None:
    """A run of capitalised words not at a sentence start ("Cassini", "Grand Canyon"), at most ``max_words`` long."""
    if _sentence_start(words, i):
        return None
    parts: list[str] = []
    j = i
    while j < len(words) and len(parts) < max_words:
        w = _bare(words[j][2])
        if len(w) < 2 or not w[:1].isupper() or w.lower() in STOP or w.lower() in GROUPS or _NUM.match(w):
            break
        parts.append(w)
        if words[j][2].rstrip("\"'”’)").endswith((".", ",", "!", "?", ";", ":")):
            j += 1
            break
        j += 1
    if not parts or (len(parts) == 1 and len(parts[0]) < 3):
        return None
    return " ".join(parts), len(parts)


def _in_quotes(words: list[Word]) -> list[bool]:
    """Per word: inside quoted dialogue? A quoted line's capitals ("They're", "Don't") are not names."""
    out, inside = [], False
    for _, _, w in words:
        core = w.strip(".,!?;:…()[]")
        opens = core[:1] in "\"“"
        closes = core[-1:] in "\"”" and (len(core) > 1 or not opens)
        out.append(inside or opens)
        if opens and not closes:
            inside = True
        elif closes:
            inside = False
    return out


def pick(words: list[Word], *, per_minute: float = 6.0, min_gap: float = 3.0, max_words: int = 3, hold: float = 1.6,
         names: bool = True, skip: set[str] | frozenset = frozenset(), duration: float | None = None) -> list[tuple[float, float, str]]:
    """The emphasis overlays ``(start, end, text)`` for timed ``words``: numbers first, then the first mention of a
    name while the budget allows. Each starts when its word is spoken and stays ``hold`` seconds (never past the next
    overlay, never past ``duration``). ``skip`` holds lower-case texts never to show (e.g. the channel name)."""
    if not words:
        return []
    end_t = float(duration if duration is not None else words[-1][1])
    budget = max(1, int(per_minute * max(end_t, 1.0) / 60.0 + 0.5))
    cands: list[tuple[int, float, str]] = []          # (priority, time, text); 0 = number, 1 = name
    seen: set[str] = set(s.lower() for s in skip)
    skip_digits = {re.sub(r"\D", "", s) for s in skip if re.search(r"\d", s)}   # "#3" on screen already → never "3"
    quoted = _in_quotes(words)
    i = 0
    while i < len(words):
        num = _number_at(words, i)
        if num:
            text, n = num
            if len(text.split()) <= max_words and text.lower() not in seen and re.sub(r"\D", "", text) not in skip_digits:
                cands.append((0, words[i][0], text))
                seen.add(text.lower())
            i += n
            continue
        name = _name_at(words, i, max_words) if names and not quoted[i] else None
        if name:
            text, n = name
            if text.lower() not in seen:
                cands.append((1, words[i][0], text))
                seen.add(text.lower())
            i += n
            continue
        i += 1
    chosen: list[tuple[float, str]] = []
    for pri in (0, 1):
        for p, t, text in cands:
            if p != pri or len(chosen) >= budget:
                continue
            if all(abs(t - c) >= min_gap for c, _ in chosen):
                chosen.append((t, text))
    chosen.sort()
    out: list[tuple[float, float, str]] = []
    for k, (t, text) in enumerate(chosen):
        stop = min(t + hold, end_t)
        if k + 1 < len(chosen):
            stop = min(stop, chosen[k + 1][0] - 0.1)
        if stop - t >= 0.4:
            out.append((round(t, 3), round(stop, 3), text))
    return out


def shown_words(overlays: list[tuple[float, float, str]]) -> int:
    """Total words the overlays put on screen (for logs and the budget checks in tests)."""
    return sum(len(t.split()) for _, _, t in overlays)
