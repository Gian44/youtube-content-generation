"""Interactive Roblox Shorts. Gian 2026-11-19: kid-safe, all ages (a general-audience channel, not "made for kids"),
and a daily mix of one story and two interactive Shorts so two weeks of views pick the winner. Three formats, all
over the same Creative Commons gameplay:

- ``would_you_rather``: 4 rounds of two Roblox choices (A red, B blue), a 3-second countdown after each, then
  "comment your picks".
- ``guess_the_game``: 3 rounds of 3 clues about a well-known, kid-safe Roblox game from a fixed list, a countdown,
  then the answer.
- ``pick_one``: 2 rounds of "pick an egg / a chest / a door: one, two or three", a countdown, then what each held.

One writer call (Claude on the subscription) fills the rounds; the code turns them into beats: a spoken line, the
panel on screen, the seconds of countdown after it. Each beat is voiced on its own and the pieces are joined with
the countdown ticks in between, so what the panel shows always matches what is being said. Pure helpers (prompts,
validation, beats, the timeline, the ffmpeg commands, the panel drawing) are unit-tested without a model or ffmpeg.

Felix (Puello Media) 2026-11: "much less on-screen text, never repeat what the narrator is saying", quieter and less
generic sound effects, minimal section indicators, icons instead of text cards. So the panels show the round as a
number ("1/4"), each choice / clue / reward as 1-3 key words plus one emoji icon (the writer gives both; a missing or
invalid one falls back to the spoken line's key words, no icon), and the intro / call to action as an icon plus a
word or two. The countdown ticks are a soft synthesised woodblock (``cc.sfx``, a lower "tock" on the last count),
varied per Short and far under the voice."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from cc import icons, sfx
from sof import render as sof_render
from sof import shorts as sof_shorts
from sof import tts
from sof.llm import LLMError

from bqd import voice as narrator
from bqd.stories import DARK_WORDS, Story, StoryError

log = logging.getLogger("bqd.interactive")

FORMATS: dict[str, dict] = {
    "would_you_rather": {"label": "Roblox Would You Rather", "rounds": 4},
    "guess_the_game": {"label": "Guess the Roblox Game", "rounds": 3},
    "pick_one": {"label": "Roblox Pick One", "rounds": 2},
}

# Guess the Game draws only from long-running, well-known Roblox games that suit every age (no horror, no shooters,
# no name a parent would mind hearing read out: Tower of Hell was dropped 2026-11-19).
GAMES = ["Adopt Me!", "Brookhaven RP", "Natural Disaster Survival", "Work at a Pizza Place", "Jailbreak",
         "Bee Swarm Simulator", "Royale High", "Theme Park Tycoon 2", "Lumber Tycoon 2", "Welcome to Bloxburg",
         "Build A Boat For Treasure", "Epic Minigames", "Blox Fruits", "Dress to Impress", "Grow a Garden"]
GENERIC_NAME_WORDS = {"simulator", "tycoon", "the", "for", "to", "a", "at"}
WYR_THEMES = ["Pets Edition", "Obby Edition", "Robux Edition", "Avatar Edition", "Superpowers Edition", "Roleplay Edition",
              "Tycoon Edition", "Server Edition", "Hard Mode", "Impossible Choices", "Trading Edition", "Lucky Edition"]
PICK_THINGS = ["egg", "chest", "door", "gift box", "portal", "potion"]
GTG_TITLES = ["Guess the Roblox Game in 3 Clues", "Can You Guess These 3 Roblox Games", "Name the Roblox Game From 3 Clues",
              "Only Real Roblox Players Get All 3", "Guess the Roblox Game Before the Timer"]
NUM_WORDS = ["one", "two", "three", "four", "five"]
TICK_GAP = 0.25            # breathing room after each spoken line
# Felix 2026-11: "the pings were too loud". The countdown tick (cc.sfx woodblock) sits 8 dB under the shared effect
# level because the final loudnorm (dynamic) lifts the quiet countdown seconds by 6-10 dB, most on the last count.
# Measured 2026-11-21 with Kokoro af_heart: voice peak -1.9 dBFS, the three counts about -30 / -28 / -24 dBFS, 21-28 dB
# under the voice and still heard over the silent gameplay. At -27 the last count was only 16-17 dB under the voice.
TICK_DB = sfx.PEAK_DB - 8.0
# on screen: 1-3 key words per choice / clue / reward, plus one emoji icon (Felix 2026-11)
LABEL_WORDS, LABEL_CHARS = 3, 20
CTA_ICON, GAME_ICON = "💬", "🎮"
THING_ICONS = {"egg": "🥚", "chest": "📦", "door": "🚪", "gift box": "🎁", "portal": "🌀", "potion": "🧪"}
# kid-safe channel: icons never shown (weapons, explosives, death, blood, drink, smoking, drugs, gambling, rude, gross,
# romantic, scary), nor a creed, a political sign or a flag (no group is singled out, as in cc.textbudget.GROUPS)
ICON_BLOCK = set("🔫🔪🗡⚔🪓🏹💣🧨💀☠⚰⚱🪦🩸🍺🍻🍷🍸🍹🥃🍾🥂🚬💊💉🎰🍆🍑💦💋🫦👅👄👙🩲💏💑🔞🖕💩🤮🤢"
                 "👻👹👺😈👿🤬🧟🧛🧌🕷🕸☢☣✝✞☦☪☬☭☯☸✡🔯🕉🕎🛐⛪🕌🕍🕋🏳🏴⚧")
# Guess the Game: an icon that pictures a word of the game's name gives the answer away (no 🍕 for the pizza game)
NAME_ICONS = {"Work at a Pizza Place": "🍕", "Bee Swarm Simulator": "🐝", "Build A Boat For Treasure": "⛵🚤🛶🚣🛥🚢⛴",
              "Blox Fruits": "🍎🍏🍐🍊🍋🍌🍉🍇🍓🫐🍈🍒🥭🍍🥥🥝", "Dress to Impress": "👗", "Lumber Tycoon 2": "🪵",
              "Grow a Garden": "🌱🪴"}
# words the fallback label skips: articles, pronouns, helpers, generic verbs, time words
_FILLER = {"a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "at", "for", "with", "from", "by", "as", "into",
           "onto", "that", "which", "who", "it", "its", "it's", "this", "these", "those", "you", "your", "you're", "i", "my",
           "me", "we", "our", "they", "their", "he", "she", "his", "her", "is", "are", "was", "were", "be", "been", "can",
           "could", "will", "would", "here", "there", "every", "each", "any", "all", "some", "very", "really", "so", "just",
           "once", "again", "day", "time", "get", "gets", "have", "has", "had", "become", "own", "where", "when", "what",
           "how", "if", "then", "than", "also"}
_NEGATION = {"never", "no", "not", "cannot", "can't", "won't", "don't", "without"}
SYSTEM = ("You write interactive YouTube Shorts for a Roblox channel watched by players of every age, younger ones "
          "included: kid-safe, upbeat, specific to Roblox. Completely original. Output strict JSON.")
SAFETY = ("- Kid-safe for every age: nothing scary, mean, gross, romantic or dangerous; nothing about hacking, scams, "
          "bans, real money (Robux is fine) or anyone's real-life details.\n"
          "- No real person, real username or YouTuber.")


@dataclass
class Beat:
    say: str                       # the spoken line ("" = silent)
    panel: dict                    # what the panel shows while this line is spoken and during its countdown
    hold: int = 0                  # seconds of countdown after the line


@dataclass
class Cue:
    start: float
    end: float
    panel: dict
    caption: str = ""
    count: int = 0                 # countdown number shown (0 = none)

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "panel": self.panel, "caption": self.caption, "count": self.count}

    @classmethod
    def from_dict(cls, d: dict) -> "Cue":
        return cls(**{k: d[k] for k in ("start", "end", "panel", "caption", "count") if k in d})


# ---------------------------------------------------------------- the daily mix (pure)

def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.sha1(seed.encode()).hexdigest(), 16))


def recent_formats(rows: list[dict], *, last: int = 1) -> set[str]:
    """Interactive formats used in the last ``last`` ledger rows."""
    out: set[str] = set()
    for r in rows[-last:] if last > 0 else []:
        for s in r.get("stories") or []:
            if str(s.get("format") or "story") in FORMATS:
                out.add(str(s["format"]))
    return out


def plan_slots(mix: list[str], *, count: int, seed: str, avoid: set[str], force: str | None = None) -> list[str]:
    """The day's Shorts in publish order: "story" or an interactive format key. ``mix`` is cycled to ``count``; each
    "interactive" slot gets a distinct format (the ones used yesterday last); the order is shuffled by the date so
    every format takes its turn at each time slot."""
    if force:
        if force != "story" and force not in FORMATS:
            raise StoryError(f"unknown format {force!r}")
        return [force] * count
    base = [mix[i % len(mix)] for i in range(count)] if mix else ["story"] * count
    rng = _rng("mix|" + seed)
    fresh = sorted(f for f in FORMATS if f not in avoid)
    stale = sorted(f for f in FORMATS if f in avoid)
    rng.shuffle(fresh)
    rng.shuffle(stale)
    order = fresh + stale
    out, n = [], 0
    for slot in base:
        if slot == "interactive":
            out.append(order[n % len(order)])
            n += 1
        else:
            out.append(slot)
    rng.shuffle(out)
    return out


# ---------------------------------------------------------------- the writer (prompts + validation, pure)

def _words(s: str) -> int:
    return len(str(s).split())


def _clean(s) -> str:
    return " ".join(str(s or "").split()).strip()


def _check_safe(texts: list[str], errors: list[str]) -> None:
    dark = sorted({m.group(0).lower() for t in texts for m in DARK_WORDS.finditer(t)})
    if dark:
        errors.append(f"kid-safe channel: nothing creepy, scary, hacked, scammed or banned (found: {', '.join(dark)})")


def _name_words(game: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", game.lower()) if len(w) >= 4 and w not in GENERIC_NAME_WORDS}


def prompt_for(fmt: str, *, seed: str, used: list[str], recent_games: list[str] | None = None) -> tuple[str, dict]:
    """(prompt, params) for one interactive Short; ``params`` are what the validator checks against."""
    rng = _rng(fmt + "|" + seed)
    used_txt = "\n".join(f"- {u}" for u in used) or "- (none yet)"
    if fmt == "would_you_rather":
        theme = rng.choice(WYR_THEMES)
        return (f"""Write a "Roblox Would You Rather" Short: exactly 4 rounds, each two choices a Roblox player would argue about.
Theme for today: {theme} (you may name the edition differently if a better one fits the choices).
Rules:
- Each choice is 3 to 12 words and starts with a verb, e.g. "get a pet dragon that cannot fly". No question marks, no "or".
- Both choices in a round are equally tempting or equally funny, so the pick is hard. Make them about Roblox: obbies,
  pets, eggs, Robux, avatars, servers, roleplay, tycoons, trading, badges, a well-known Roblox game.
- For each choice also give what the screen shows while it is read out: "a_short" / "b_short", the 1-3 key words
  (e.g. "Dragon pet", "1,000 Robux", "Never lag"), never the whole choice, and "a_icon" / "b_icon", exactly one emoji
  that pictures it (e.g. "🐉").
{SAFETY}
- Different from these recent Shorts:
{used_txt}

Return JSON:
{{"title": "<the edition, 2-4 words, e.g. Pets Edition>",
  "rounds": [{{"a": "<choice A>", "a_short": "<1-3 words>", "a_icon": "<one emoji>", "b": "<choice B>", "b_short": "...", "b_icon": "..."}},
             {{...}}, {{...}}, {{...}}],
  "tags": ["<5-8 lowercase tags>"]}}""", {"rounds": 4})
    if fmt == "guess_the_game":
        avoid = set(recent_games or [])
        pool = [g for g in GAMES if g not in avoid] or list(GAMES)
        return (f"""Write a "Guess the Roblox Game" Short: exactly 3 rounds, three different games, chosen ONLY from this list
(copy the name exactly): {json.dumps(pool)}.
For each game write three spoken clues, vaguest first; the third makes it easy.
Rules:
- Each clue is 5 to 9 words, a plain statement ("You can adopt a baby or a pet.").
- Never use the game's name or any word of it, not even in another form.
- Use only well-known features you are completely sure of and that have been in the game for years: what players
  do there, the setting, the famous item. No claims about menus, voting, settings, updates, prices or numbers.
  If you are unsure about a detail, leave it out.
- For each clue also give what the screen shows while it is read out: "short", its 1-3 key words (e.g. "Baby or pet"),
  never the whole clue and never a word of the game's name, and "icon", exactly one emoji that pictures the clue
  (e.g. "🍼") but not the name (no 🍕 for a pizza game).
{SAFETY}

Return JSON:
{{"rounds": [{{"game": "<exactly as in the list>", "clues": [{{"say": "<clue 1>", "short": "<1-3 words>", "icon": "<one emoji>"}},
                                                         {{"say": "<clue 2>", "short": "...", "icon": "..."}},
                                                         {{"say": "<clue 3>", "short": "...", "icon": "..."}}]}},
             {{...}}, {{...}}],
  "tags": ["<5-8 lowercase tags>"]}}""", {"rounds": 3, "games": pool})
    if fmt == "pick_one":
        things = rng.sample(PICK_THINGS, 2)
        return (f"""Write a "Roblox Pick One" Short: exactly 2 rounds. In round 1 the viewer picks a {things[0]} (one, two or three),
in round 2 a {things[1]}; then all three are opened.
Rules:
- Each round has exactly 3 rewards, 2 to 8 words each: invented Roblox-style rewards such as pets, auras, hats, wings,
  trails, Robux amounts up to 1,000, or something silly. Start each with "a", "an" or a number.
- In each round one reward is amazing ("a rainbow dragon pet that flies"), one is funny-bad ("a single wet sock"),
  one is in between; put them in a random order.
- For each reward also give what the screen shows while it is read out: "short", its 1-3 key words (e.g. "Rainbow
  dragon", "Wet sock", "100 Robux"), and "icon", exactly one emoji that pictures it (e.g. "🧦").
{SAFETY}
- Different from these recent Shorts:
{used_txt}

Return JSON:
{{"title": "<2-4 words, e.g. Mystery Egg Edition>",
  "rounds": [{{"thing": "{things[0]}", "rewards": [{{"say": "<reward>", "short": "<1-3 words>", "icon": "<one emoji>"}}, {{...}}, {{...}}]}},
             {{"thing": "{things[1]}", "rewards": [{{...}}, {{...}}, {{...}}]}}],
  "tags": ["<5-8 lowercase tags>"]}}""", {"rounds": 2, "things": things})
    raise StoryError(f"unknown format {fmt!r}")


def short_label(spoken: str) -> str:
    """The fallback on-screen words for a spoken choice / clue / reward: its first run of content words, at most
    ``LABEL_WORDS`` words and ``LABEL_CHARS`` characters, an "and"/"or" kept between two of them, never ending on one
    or on a "never"/"cannot" ("get a dragon pet that cannot fly" → "dragon pet", "never lag again in any game" →
    "never lag", "Tornadoes and floods hit the map" → "Tornadoes and floods")."""
    joins = {"and", "or"}
    out: list[str] = []
    for w in re.findall(r"\$?\d[\d,]*(?:\.\d+)?|[A-Za-z][A-Za-z'-]*", spoken):
        if w.lower() in _FILLER and not (w.lower() in joins and out and out[-1].lower() not in joins):
            if sum(x.lower() not in joins for x in out) >= 2:
                break
            continue
        if len(out) == LABEL_WORDS or len(" ".join(out + [w])) > LABEL_CHARS:
            break
        out.append(w)
    while len(out) > 1 and out[-1].lower() in _NEGATION | joins:
        out.pop()
    return " ".join(out) or " ".join(spoken.split()[:2])


def _icon(s, *, avoid: str = "") -> str:
    """``s`` when it is exactly one kid-safe emoji (a ZWJ sequence or skin tone counts as one) and none of ``avoid``,
    else "". Flags (regional-indicator letters) are never icons."""
    s = "".join(str(s or "").split()) if isinstance(s, str) else ""
    if not s or any(ch not in "\u200d\ufe0f" and (not icons.is_emoji(ch) or 0x2C00 <= ord(ch) < 0x1F000 or ch in ICON_BLOCK
                                                   or 0x1F1E6 <= ord(ch) <= 0x1F1FF or ch in avoid) for ch in s):
        return ""
    bases = [ch for i, ch in enumerate(s) if icons.is_emoji(ch) and not 0x1F3FB <= ord(ch) <= 0x1F3FF
             and (i == 0 or s[i - 1] != "\u200d")]
    return s if len(bases) == 1 else ""


def label(short, icon, spoken: str, *, avoid: set[str] = frozenset(), avoid_icons: str = "") -> dict:
    """What the panel shows for one spoken choice / clue / reward: ``{"text": 1-3 words, "icon": one emoji or ""}``.
    The writer's ``short`` when it is 1-3 words, at most ``LABEL_CHARS`` characters, kid-safe, plain text and holds
    none of ``avoid`` (a clue's game-name words); otherwise ``short_label(spoken)``. The writer's ``icon`` when it is
    one kid-safe emoji and none of ``avoid_icons`` (a picture of the game's name), otherwise none."""
    text = _clean(short).strip(" .,!?;:\"'") if isinstance(short, str) else ""
    ok = (1 <= _words(text) <= LABEL_WORDS and len(text) <= LABEL_CHARS and not DARK_WORDS.search(text)
          and not any(icons.is_emoji(ch) for ch in text)
          and not avoid & {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower())})
    return {"text": text if ok else short_label(spoken), "icon": _icon(icon, avoid=avoid_icons)}


def _items(raw) -> list[dict]:
    """A clue / reward list as ``[{"say", "short", "icon"}]``; plain strings (the older answer shape) are just "say"."""
    out = [x if isinstance(x, dict) else {"say": x} for x in (raw if isinstance(raw, list) else [])]
    return [x for x in out if _clean(x.get("say"))]


def validate(fmt: str, data, params: dict) -> dict:
    """Clean and check the writer's JSON for ``fmt``; raises StoryError listing every problem. The on-screen words and
    icons are never a reason to reject an answer: ``label`` falls back to the spoken line's key words."""
    if not isinstance(data, dict):
        raise StoryError("answer must be a JSON object")
    errors: list[str] = []
    rounds = data.get("rounds") if isinstance(data.get("rounds"), list) else []
    if len(rounds) != params["rounds"]:
        errors.append(f"exactly {params['rounds']} rounds, not {len(rounds)}")
    texts: list[str] = []
    out_rounds: list[dict] = []
    if fmt == "would_you_rather":
        for i, r in enumerate(rounds, 1):
            r = r if isinstance(r, dict) else {}
            a, b = _clean(r.get("a")).rstrip(".?!"), _clean(r.get("b")).rstrip(".?!")
            for name, v in (("a", a), ("b", b)):
                if not 3 <= _words(v) <= 12:
                    errors.append(f"round {i} choice {name} must be 3-12 words")
                if "?" in v or re.search(r"\bor\b", v, re.I):
                    errors.append(f"round {i} choice {name}: no question mark and no 'or'")
            if a and a.lower() == b.lower():
                errors.append(f"round {i}: the two choices are the same")
            out_rounds.append({"a": a[:1].lower() + a[1:], "b": b[:1].lower() + b[1:],
                               "a_label": label(r.get("a_short"), r.get("a_icon"), a),
                               "b_label": label(r.get("b_short"), r.get("b_icon"), b)})
            texts += [a, b]
    elif fmt == "guess_the_game":
        seen: set[str] = set()
        for i, r in enumerate(rounds, 1):
            r = r if isinstance(r, dict) else {}          # a malformed round is a rejection (and the retry), not a crash
            game = _clean(r.get("game"))
            items = _items(r.get("clues"))
            clues = [_clean(c["say"]) for c in items]
            if game not in params["games"]:
                errors.append(f"round {i}: game must be copied exactly from the list (got {game!r})")
            if game in seen:
                errors.append(f"round {i}: {game} is used twice")
            seen.add(game)
            if len(clues) != 3:
                errors.append(f"round {i}: exactly 3 clues")
            for k, c in enumerate(clues, 1):
                if not 5 <= _words(c) <= 9:
                    errors.append(f"round {i} clue {k} must be 5-9 words")
                leak = _name_words(game) & {w.rstrip("s") for w in re.findall(r"[a-z]+", c.lower())}
                if leak:
                    errors.append(f"round {i} clue {k} gives the name away ({', '.join(sorted(leak))})")
            clues = [c if c[-1:] in ".!" else c + "." for c in clues]
            out_rounds.append({"game": game, "clues": clues,
                               "labels": [label(c.get("short"), c.get("icon"), say, avoid=_name_words(game),
                                                avoid_icons=NAME_ICONS.get(game, "")) for c, say in zip(items, clues)]})
            texts += clues
    elif fmt == "pick_one":
        for i, r in enumerate(rounds, 1):
            r = r if isinstance(r, dict) else {}
            thing = _clean(r.get("thing")).lower()
            items = _items(r.get("rewards"))
            rewards = [_clean(x["say"]).rstrip(".!") for x in items]
            if i <= len(params["things"]) and thing != params["things"][i - 1]:
                errors.append(f"round {i} must be a {params['things'][i - 1]}")
            if len(rewards) != 3:
                errors.append(f"round {i}: exactly 3 rewards")
            for k, x in enumerate(rewards, 1):
                if not 2 <= _words(x) <= 8:
                    errors.append(f"round {i} reward {k} must be 2-8 words")
            out_rounds.append({"thing": thing, "rewards": [x[:1].lower() + x[1:] if not x[:1].isdigit() else x for x in rewards],
                               "labels": [label(x.get("short"), x.get("icon"), say) for x, say in zip(items, rewards)]})
            texts += rewards
    else:
        raise StoryError(f"unknown format {fmt!r}")
    title = _clean(data.get("title")).strip("\"'.!")
    if fmt != "guess_the_game":
        if not 1 <= _words(title) <= 5 or len(title) > 32:
            errors.append("title: the edition name, 2-4 words")
        texts.append(title)
    _check_safe(texts, errors)
    if errors:
        raise StoryError("; ".join(errors))
    tags = [str(t).strip().lower() for t in (data.get("tags") or []) if str(t).strip()][:8]
    return {"title": title, "rounds": out_rounds, "tags": tags}


def _article(thing: str) -> str:
    return ("an " if thing[:1] in "aeiou" else "a ") + thing


def make_beats(fmt: str, script: dict, *, hold: int = 3) -> list[Beat]:
    """The spoken lines, the panel for each and the countdowns, built from a validated script. Panels carry only the
    round number, the 1-3 word labels with their icons, and an icon plus a word or two for the intro / call to action
    (Felix 2026-11: never what the narrator is saying)."""
    rounds = script["rounds"]
    n = len(rounds)
    beats: list[Beat] = []
    if fmt == "would_you_rather":
        for i, r in enumerate(rounds, 1):
            panel = {"kind": "wyr", "round": i, "of": n, "a": r["a_label"], "b": r["b_label"]}
            beats.append(Beat(f"Would you rather {r['a']}, or {r['b']}?", panel, hold))
        beats.append(Beat(f"Comment your {NUM_WORDS[n - 1] if n <= 5 else n} picks, like A, B, B, A.",
                          {"kind": "cta", "icon": CTA_ICON, "text": "A or B?"}))
    elif fmt == "guess_the_game":
        beats.append(Beat("Guess the Roblox game.", {"kind": "intro", "icon": GAME_ICON, "text": "3 clues"}))
        for i, r in enumerate(rounds, 1):
            for k, c in enumerate(r["clues"], 1):
                panel = {"kind": "clues", "round": i, "of": n, "clues": r["labels"][:k]}
                beats.append(Beat(c, panel, hold if k == len(r["clues"]) else 0))
            beats.append(Beat(f"It's {r['game'].rstrip('!')}!", {"kind": "clues", "round": i, "of": n, "clues": r["labels"], "answer": r["game"]}))
        beats.append(Beat(f"How many did you get? Comment your score out of {NUM_WORDS[n - 1] if n <= 5 else n}.",
                          {"kind": "cta", "icon": CTA_ICON, "text": f"?/{n}"}))
    elif fmt == "pick_one":
        for i, r in enumerate(rounds, 1):
            thing = r["thing"]
            closed = {"kind": "pick", "round": i, "of": n, "icon": THING_ICONS.get(thing, ""), "open": [None, None, None]}
            beats.append(Beat(f"Pick {_article(thing)}: one, two, or three.", closed, hold))
            opened: list = [None, None, None]
            for k, x in enumerate(r["rewards"]):
                opened[k] = r["labels"][k]
                beats.append(Beat(f"{thing.capitalize()} {NUM_WORDS[k]}: {x}.", {**closed, "open": list(opened), "now": k}))
        beats.append(Beat("Comment what you got!", {"kind": "cta", "icon": CTA_ICON, "text": "1, 2 or 3?"}))
    else:
        raise StoryError(f"unknown format {fmt!r}")
    return beats


def write(llm, fmt: str, *, seed: str, used: list[str], recent_games: list[str] | None = None, hold: int = 3) -> Story:
    """One interactive Short as a ``Story`` (format, script, the spoken lines joined as the body)."""
    prompt, params = prompt_for(fmt, seed=seed, used=used, recent_games=recent_games)
    try:
        try:
            data = validate(fmt, llm.generate_json(prompt, system=SYSTEM, temperature=0.9, max_tokens=1500), params)
        except StoryError as first:
            log.warning("%s rejected, retrying once: %s", fmt, str(first)[:300])
            retry = f"{prompt}\n\nYour previous answer was rejected. Validation errors:\n{first}\nReturn corrected JSON that satisfies every rule."
            data = validate(fmt, llm.generate_json(retry, system=SYSTEM, temperature=0.9, max_tokens=1500), params)
    except LLMError as exc:
        raise StoryError(f"writer call failed: {exc}") from exc
    if fmt == "guess_the_game":
        data["title"] = _rng("gtg-title|" + seed).choice(GTG_TITLES)
    beats = make_beats(fmt, data, hold=hold)
    body = " ".join(b.say for b in beats if b.say)
    label = FORMATS[fmt]["label"]
    title = data["title"] if fmt == "guess_the_game" else f"{label}: {data['title']}"
    tags = data["tags"] + ([r["game"].lower() for r in data["rounds"]] if fmt == "guess_the_game" else [])
    return Story(category=fmt, persona="", title=title, hook=beats[0].say, body=body, broll_queries=[], tags=tags[:10],
                 format=fmt, script=data)


def recent_used(rows: list[dict], fmt: str, *, limit: int = 10) -> list[str]:
    """Titles and opening lines of the last ``limit`` Shorts of ``fmt``: the writer is told to make something else."""
    found = [s for r in rows for s in (r.get("stories") or []) if str(s.get("format") or "story") == fmt][-limit:]
    out = []
    for s in found:
        out += [str(s.get("title") or "").replace("#roblox", "").replace("#Shorts", "").strip(), str(s.get("hook") or "")]
    return [x for x in out if x]


def recent_games(rows: list[dict], *, limit: int = 12) -> list[str]:
    games = [g for r in rows for s in (r.get("stories") or []) for g in (s.get("games") or [])]
    return games[-limit:]


# ---------------------------------------------------------------- voice + timeline

def timeline(beats: list[Beat], speech: list[float], *, gap: float = TICK_GAP) -> tuple[list[Cue], float]:
    """Cues for the panel PNGs and the total length: each line's captions spread over its speech (by characters),
    then ``gap``, then one cue per countdown second."""
    cues: list[Cue] = []
    t = 0.0
    for b, d in zip(beats, speech):
        if b.say and d > 0:
            for s, e, text in sof_shorts.caption_cues(b.say, d):
                cues.append(Cue(round(t + s, 3), round(t + e, 3), b.panel, text))
            t += d
        end_gap = t + gap
        if cues:
            cues[-1].end = round(end_gap, 3)             # the last caption stays up through the breath
        t = end_gap
        for k in range(b.hold):
            cues.append(Cue(round(t, 3), round(t + 1.0, 3), b.panel, "", b.hold - k))
            t += 1.0
    return cues, round(t, 3)


def to_wav_cmd(src: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-i", src, "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", out]


def silence_cmd(out: str, seconds: float) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{seconds:.3f}",
            "-c:a", "pcm_s16le", out]


def tick_cmds(tick: str, tock: str, *, seed: str) -> list[list[str]]:
    """The two countdown seconds (Felix 2026-11: the sine beeps were "too loud" and generic): a soft woodblock tick and
    a lower "tock" for the last count (``cc.sfx``), each padded with silence to exactly 1 s, ``TICK_DB`` peak, their
    pitch and tone drawn from ``seed`` (the date and the Short) so the sound changes from video to video."""
    return [sfx.synth_cmd("tick", tick, seed=seed, peak_db=TICK_DB, pad_to=1.0),
            sfx.synth_cmd("tock", tock, seed=seed, peak_db=TICK_DB, pad_to=1.0)]


def _concat_cmd(list_file: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_file, "-c:a", "pcm_s16le", out]


def voice(story: Story, work: Path, cfg, *, index: int, hold: int = 3, seed: str = "") -> str:
    """Voice every beat, join them with the breaths and countdown ticks, loudness-normalise → ``work/short<n>.mp3``;
    the cues go to ``work/s<k>/timeline.json`` for the render. Kept when both files already exist (resume).
    ``seed`` (the date) and ``index`` pick this Short's tick sound."""
    out = work / f"short{index + 1}.mp3"
    d = work / f"s{index}"
    tl_path = d / "timeline.json"
    if out.exists() and tl_path.exists():
        return str(out)
    beats = make_beats(story.format, story.script, hold=hold)
    scfg = cfg.sof                           # the channel voice (bqd/voice.py), set in Config.from_env
    raw = tts.speak([narrator.for_tts(b.say) for b in beats], str(d / "tts"), scfg, short=True)
    pieces_dir = d / "pieces"
    pieces_dir.mkdir(parents=True, exist_ok=True)
    gap = str(pieces_dir / "gap.wav")
    tick, tock = str(pieces_dir / "tick.wav"), str(pieces_dir / "tock.wav")
    sof_render.run(silence_cmd(gap, TICK_GAP), 120)
    for cmd in tick_cmds(tick, tock, seed=f"{seed}|{index}"):
        sof_render.run(cmd, 120)
    order: list[str] = []
    speech: list[float] = []
    for i, (b, src) in enumerate(zip(beats, raw)):
        w = str(pieces_dir / f"beat_{i:03d}.wav")
        sof_render.run(to_wav_cmd(src, w), 300)
        speech.append(tts.duration_seconds(w))
        order += [w, gap] + [tock if k == b.hold - 1 else tick for k in range(b.hold)]
    list_file = pieces_dir / "list.txt"
    list_file.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in order), encoding="utf-8")
    joined = str(pieces_dir / "joined.wav")
    sof_render.run(_concat_cmd(str(list_file), joined), 600)
    sof_render.run(sof_shorts.loudnorm_cmd(joined, str(out), tts.post_filter(scfg)), 600)
    cues, total = timeline(beats, speech)
    tl_path.write_text(json.dumps({"total": total, "cues": [c.to_dict() for c in cues]}, indent=1), encoding="utf-8")
    return str(out)


# ---------------------------------------------------------------- the panel (PIL)

RED, BLUE, GREEN, GOLD, PURPLE = (229, 72, 77), (62, 123, 250), (46, 184, 92), (255, 197, 49), (155, 89, 222)
INK, WHITE = (18, 18, 22), (255, 255, 255)
PICK_COLORS = [GOLD, GREEN, PURPLE]


def _text_mid(d, cx: float, cy: float, text: str, font, fill, stroke: int = 0) -> None:
    """``text`` centred on (``cx``, ``cy``); with ``stroke`` a dark outline so it reads over any colour."""
    d.text((cx, cy), text, font=font, fill=fill, anchor="mm", stroke_width=stroke, stroke_fill=INK if stroke else None)


def _fit(d, text: str, max_w: float, size: int, *, lines: int, min_size: int) -> tuple:
    """(font, wrapped lines, size): the largest size from ``size`` down at which ``text`` fits ``max_w`` in at most
    ``lines`` lines. Under ``min_size`` only when it must (one long word), and never by dropping a word: "HATCH
    LEGENDARY EGG" used to lose "EGG" at the floor."""
    floor = max(12, min(size, min_size) // 2)
    while True:
        font = sof_shorts._font(size)
        rows = sof_shorts._wrap(d, text, font, int(max_w))
        if size <= floor or (len(rows) <= lines and all(d.textlength(r, font=font) <= max_w for r in rows)):
            return font, rows, size
        size = max(floor, int(size * 0.9))


def _emoji(icon: str, h: int):
    return icons.emoji_image(icon, h) if icon else None


def _icon_and_words(im, d, lab: dict, cx: float, top: float, bottom: float, *, max_w: float, icon_h: int, size: int,
                    lines: int, stroke: int) -> None:
    """One label centred in [``top``, ``bottom``]: its icon, the 1-3 words in outlined capitals under it. Without an
    icon (none given, or no emoji font) the words are drawn a little bigger on their own, one more line allowed."""
    pic = _emoji(lab.get("icon") or "", icon_h)
    if pic is None:
        size, lines = round(size * 1.3), lines + 1
    font, rows, s = _fit(d, str(lab.get("text") or "").upper(), max_w, size, lines=lines, min_size=round(size * 0.55))
    lh = round(s * 1.18)
    gap = round(icon_h * 0.1) if pic else 0
    y = top + (bottom - top - ((pic.height + gap) if pic else 0) - lh * len(rows)) / 2
    if pic:
        im.alpha_composite(pic, (round(cx - pic.width / 2), round(y)))
        y += pic.height + gap
    for row in rows:
        _text_mid(d, cx, y + lh / 2, row, font, WHITE, stroke)
        y += lh


def panel_png(panel: dict, out: str, *, width: int, height: int, caption: str = "", count: int = 0) -> str:
    """One full-frame RGBA overlay over the gameplay: the round chip ("1/4"), the format's boxes with 1-3 words and an
    icon each, the countdown number, the caption (off by default). Solid boxes with a dark outline and outlined
    capitals, so it reads over any footage; the gameplay stays visible around them.

    Felix (Puello Media) 2026-11: minimal section indicator (the round number, no format name on every panel), never
    the narrator's sentence on screen (key words + a clean icon), no text cards (the intro / call to action is an icon
    with a word or two)."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    H, W = height, width
    ow = max(3, round(H * 0.003))
    stroke = max(2, round(H * 0.0028))
    pad = round(H * 0.014)
    x0, x1 = round(W * 0.07), round(W * 0.93)
    top = round(H * 0.19)
    kind = panel.get("kind")

    if panel.get("round"):
        chip = f"{panel['round']}/{panel['of']}"
        f_chip = sof_shorts._font(round(H * 0.028))
        cw, ch = d.textlength(chip, font=f_chip) + 3 * pad, round(H * 0.028) + 1.6 * pad
        cy = round(H * 0.13)
        d.rounded_rectangle([(W - cw) / 2, cy - ch / 2, (W + cw) / 2, cy + ch / 2], radius=ch / 2, fill=GOLD, outline=INK, width=ow)
        _text_mid(d, W / 2, cy, chip, f_chip, INK)

    if kind == "wyr":                                    # two cards side by side: letter, icon, 1-3 words
        gap = round(W * 0.05)
        cw = (x1 - x0 - gap) / 2
        ch = round(H * 0.25)
        r = round(H * 0.023)
        f_letter = sof_shorts._font(round(H * 0.03))
        for k, (letter, lab, color) in enumerate((("A", panel["a"], RED), ("B", panel["b"], BLUE))):
            bx = x0 + k * (cw + gap)
            d.rounded_rectangle([bx, top, bx + cw, top + ch], radius=pad * 2, fill=color, outline=INK, width=ow)
            lx, ly = bx + cw / 2, top + pad + r
            d.ellipse([lx - r, ly - r, lx + r, ly + r], fill=WHITE, outline=INK, width=ow)
            _text_mid(d, lx, ly, letter, f_letter, INK)
            _icon_and_words(im, d, lab, lx, ly + r + pad * 0.5, top + ch - pad, max_w=cw - 2 * pad, icon_h=round(H * 0.085),
                            size=round(H * 0.032), lines=2, stroke=stroke)
        r = round(H * 0.027)                             # the OR badge where the two cards meet
        d.ellipse([W / 2 - r, top + ch / 2 - r, W / 2 + r, top + ch / 2 + r], fill=INK, outline=WHITE, width=ow)
        _text_mid(d, W / 2, top + ch / 2, "OR", sof_shorts._font(round(H * 0.022)), WHITE)
    elif kind == "clues":                                # one pill per clue heard so far, then the answer
        y = top
        icon_h = round(H * 0.05)
        for lab in panel.get("clues") or []:
            pic = _emoji(lab.get("icon") or "", icon_h)
            room = (x1 - x0) - 3 * pad - ((pic.width + pad) if pic else 0)
            font, rows, s = _fit(d, str(lab.get("text") or "").upper(), room, round(H * 0.034), lines=1, min_size=round(H * 0.022))
            pw = d.textlength(rows[0], font=font) + 3 * pad + ((pic.width + pad) if pic else 0)
            ph = max(icon_h, s) + round(1.2 * pad)
            px = (W - pw) / 2
            d.rounded_rectangle([px, y, px + pw, y + ph], radius=ph / 2, fill=WHITE, outline=INK, width=ow)
            x = px + 1.5 * pad
            if pic:
                im.alpha_composite(pic, (round(x), round(y + (ph - pic.height) / 2)))
                x += pic.width + pad
            d.text((x, y + ph / 2), rows[0], font=font, fill=INK, anchor="lm")
            y += ph + round(H * 0.014)
        if panel.get("answer"):
            y += round(H * 0.006)
            font, rows, s = _fit(d, panel["answer"].upper(), (x1 - x0) - 4 * pad, round(H * 0.045), lines=2,
                                 min_size=round(H * 0.03))
            lh = round(s * 1.15)
            aw = max(d.textlength(r, font=font) for r in rows) + 4 * pad
            ah = lh * len(rows) + 2 * pad
            d.rounded_rectangle([(W - aw) / 2, y, (W + aw) / 2, y + ah], radius=pad * 2, fill=GREEN, outline=INK, width=ow)
            for i, row in enumerate(rows):
                _text_mid(d, W / 2, y + pad + lh * (i + 0.5), row, font, WHITE, stroke)
    elif kind == "pick":                                 # three boxes: number, then the thing's icon or what it held
        gap = round(W * 0.03)
        bw = ((x1 - x0) - 2 * gap) / 3
        bh = round(H * 0.22)
        f_num = sof_shorts._font(round(H * 0.034))
        for k in range(3):
            bx = x0 + k * (bw + gap)
            item = (panel.get("open") or [None, None, None])[k]
            now = panel.get("now") == k
            d.rounded_rectangle([bx, top, bx + bw, top + bh], radius=pad * 2, fill=PICK_COLORS[k],
                                outline=WHITE if now else INK, width=ow * 2 if now else ow)
            _text_mid(d, bx + bw / 2, top + pad + H * 0.019, str(k + 1), f_num, WHITE, stroke)
            inner = top + pad * 1.5 + H * 0.038
            if item is None:
                pic = _emoji(panel.get("icon") or "", round(H * 0.08))
                if pic:
                    im.alpha_composite(pic, (round(bx + (bw - pic.width) / 2), round(inner + (top + bh - pad - inner - pic.height) / 2)))
                else:
                    _text_mid(d, bx + bw / 2, (inner + top + bh - pad) / 2, "?", sof_shorts._font(round(H * 0.06)), WHITE, stroke)
            else:
                _icon_and_words(im, d, item, bx + bw / 2, inner, top + bh - pad, max_w=bw - 1.2 * pad, icon_h=round(H * 0.06),
                                size=round(H * 0.024), lines=3, stroke=stroke)
    elif kind in ("cta", "intro"):                       # an icon with a word or two, not a text card
        pic = _emoji(panel.get("icon") or "", round(H * 0.09))
        font, rows, s = _fit(d, str(panel.get("text") or "").upper(), (x1 - x0) - 4 * pad, round(H * 0.05), lines=1,
                             min_size=round(H * 0.03))
        bw = max(d.textlength(rows[0], font=font), pic.width if pic else 0) + 5 * pad
        bh = ((pic.height + pad) if pic else 0) + round(s * 1.2) + 2.5 * pad
        by = round(H * 0.24)
        d.rounded_rectangle([(W - bw) / 2, by, (W + bw) / 2, by + bh], radius=pad * 2.5, fill=GOLD if kind == "cta" else BLUE,
                            outline=INK, width=ow)
        y = by + 1.25 * pad
        if pic:
            im.alpha_composite(pic, (round((W - pic.width) / 2), round(y)))
            y += pic.height + pad
        _text_mid(d, W / 2, y + s * 0.6, rows[0], font, WHITE, stroke)

    if count:
        r = round(H * 0.05)
        cx, cy = W / 2, round(H * 0.56)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE, outline=INK, width=ow * 2)
        _text_mid(d, cx, cy, str(count), sof_shorts._font(round(H * 0.06)), INK)

    if caption:
        cap = Image.open(sof_shorts.caption_png(caption, out + ".cap.png", width=W, height=H)).convert("RGBA")
        im.alpha_composite(cap)
        Path(out + ".cap.png").unlink(missing_ok=True)
    im.save(out, "PNG")
    return out


def render(*, work: Path, name: str, index: int, background: str, audio: str, width: int = 1080, height: int = 1920,
           fps: int = 30, captions: bool = False) -> str:
    """The gameplay clip for the whole length, the panel PNG of each cue on top, the voiced beats → ``work/<name>.mp4``.
    No captions by default (Felix 2026-11: never repeat what the narrator says): the panel shows each choice, clue and
    reward as its key words and icon."""
    from cc import segments

    tl = json.loads((work / f"s{index}" / "timeline.json").read_text(encoding="utf-8"))
    cues = [Cue.from_dict(c) for c in tl["cues"]]
    duration = tts.duration_seconds(audio) or float(tl["total"])
    tmp = work / f"{name}_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    reel = str(tmp / "reel.mp4")
    sof_render.run(segments.clip_window_cmd(background, reel, offset=0.0, seconds=duration, fps=fps), 900)
    pngs, spans = [], []
    for i, c in enumerate(cues):
        pngs.append(panel_png(c.panel, str(tmp / f"panel_{i:03d}.png"), width=width, height=height,
                              caption=c.caption if captions else "", count=c.count))
        spans.append((c.start, min(c.end, duration), c.caption))
    out = str(work / f"{name}.mp4")
    sof_render.run(sof_shorts.overlay_cmd(reel, audio, out, cues=spans, cue_pngs=pngs, title_png_path=None, credit_png_path=None,
                                          duration=duration, fps=fps), 1800)
    return out
