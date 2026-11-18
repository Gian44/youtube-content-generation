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
validation, beats, the timeline, the ffmpeg commands, the panel drawing) are unit-tested without a model or ffmpeg."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from sof import render as sof_render
from sof import shorts as sof_shorts
from sof import tts
from sof.llm import LLMError

from bqd.stories import DARK_WORDS, Story, StoryError

log = logging.getLogger("bqd.interactive")

FORMATS: dict[str, dict] = {
    "would_you_rather": {"label": "Roblox Would You Rather", "header": "WOULD YOU RATHER", "rounds": 4},
    "guess_the_game": {"label": "Guess the Roblox Game", "header": "GUESS THE GAME", "rounds": 3},
    "pick_one": {"label": "Roblox Pick One", "header": "PICK ONE", "rounds": 2},
}

# Guess the Game draws only from long-running, well-known Roblox games that suit every age (no horror, no shooters).
GAMES = ["Adopt Me!", "Brookhaven RP", "Tower of Hell", "Natural Disaster Survival", "Work at a Pizza Place", "Jailbreak",
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
{SAFETY}
- Different from these recent Shorts:
{used_txt}

Return JSON:
{{"title": "<the edition, 2-4 words, e.g. Pets Edition>",
  "rounds": [{{"a": "<choice A>", "b": "<choice B>"}}, {{"a": "...", "b": "..."}}, {{"a": "...", "b": "..."}}, {{"a": "...", "b": "..."}}],
  "tags": ["<5-8 lowercase tags>"]}}""", {"rounds": 4})
    if fmt == "guess_the_game":
        avoid = set(recent_games or [])
        pool = [g for g in GAMES if g not in avoid] or list(GAMES)
        return (f"""Write a "Guess the Roblox Game" Short: exactly 3 rounds, three different games, chosen ONLY from this list
(copy the name exactly): {json.dumps(pool)}.
For each game write three spoken clues, vaguest first; the third makes it easy.
Rules:
- Each clue is 5 to 14 words, a plain statement ("You can adopt a baby or a pet.").
- Never use the game's name or any word of it, not even in another form.
- Use only well-known features you are completely sure of and that have been in the game for years: what players
  do there, the setting, the famous item. If you are unsure about a detail, leave it out.
{SAFETY}

Return JSON:
{{"rounds": [{{"game": "<exactly as in the list>", "clues": ["<clue 1>", "<clue 2>", "<clue 3>"]}}, {{...}}, {{...}}],
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
{SAFETY}
- Different from these recent Shorts:
{used_txt}

Return JSON:
{{"title": "<2-4 words, e.g. Mystery Egg Edition>",
  "rounds": [{{"thing": "{things[0]}", "rewards": ["...", "...", "..."]}}, {{"thing": "{things[1]}", "rewards": ["...", "...", "..."]}}],
  "tags": ["<5-8 lowercase tags>"]}}""", {"rounds": 2, "things": things})
    raise StoryError(f"unknown format {fmt!r}")


def validate(fmt: str, data, params: dict) -> dict:
    """Clean and check the writer's JSON for ``fmt``; raises StoryError listing every problem."""
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
            a, b = _clean((r or {}).get("a")).rstrip(".?!"), _clean((r or {}).get("b")).rstrip(".?!")
            for name, v in (("a", a), ("b", b)):
                if not 3 <= _words(v) <= 12:
                    errors.append(f"round {i} choice {name} must be 3-12 words")
                if "?" in v or re.search(r"\bor\b", v, re.I):
                    errors.append(f"round {i} choice {name}: no question mark and no 'or'")
            if a and a.lower() == b.lower():
                errors.append(f"round {i}: the two choices are the same")
            out_rounds.append({"a": a[:1].lower() + a[1:], "b": b[:1].lower() + b[1:]})
            texts += [a, b]
    elif fmt == "guess_the_game":
        seen: set[str] = set()
        for i, r in enumerate(rounds, 1):
            game = _clean((r or {}).get("game"))
            clues = [_clean(c) for c in ((r or {}).get("clues") or []) if _clean(c)]
            if game not in params["games"]:
                errors.append(f"round {i}: game must be copied exactly from the list (got {game!r})")
            if game in seen:
                errors.append(f"round {i}: {game} is used twice")
            seen.add(game)
            if len(clues) != 3:
                errors.append(f"round {i}: exactly 3 clues")
            for k, c in enumerate(clues, 1):
                if not 5 <= _words(c) <= 14:
                    errors.append(f"round {i} clue {k} must be 5-14 words")
                leak = _name_words(game) & {w.rstrip("s") for w in re.findall(r"[a-z]+", c.lower())}
                if leak:
                    errors.append(f"round {i} clue {k} gives the name away ({', '.join(sorted(leak))})")
            clues = [c if c[-1:] in ".!" else c + "." for c in clues]
            out_rounds.append({"game": game, "clues": clues})
            texts += clues
    elif fmt == "pick_one":
        for i, r in enumerate(rounds, 1):
            thing = _clean((r or {}).get("thing")).lower()
            rewards = [_clean(x).rstrip(".!") for x in ((r or {}).get("rewards") or []) if _clean(x)]
            if i <= len(params["things"]) and thing != params["things"][i - 1]:
                errors.append(f"round {i} must be a {params['things'][i - 1]}")
            if len(rewards) != 3:
                errors.append(f"round {i}: exactly 3 rewards")
            for k, x in enumerate(rewards, 1):
                if not 2 <= _words(x) <= 8:
                    errors.append(f"round {i} reward {k} must be 2-8 words")
            out_rounds.append({"thing": thing, "rewards": [x[:1].lower() + x[1:] if not x[:1].isdigit() else x for x in rewards]})
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
    """The spoken lines, the panel for each and the countdowns, built from a validated script."""
    rounds = script["rounds"]
    n = len(rounds)
    beats: list[Beat] = []
    if fmt == "would_you_rather":
        for i, r in enumerate(rounds, 1):
            panel = {"kind": "wyr", "round": i, "of": n, "a": r["a"], "b": r["b"]}
            beats.append(Beat(f"Would you rather {r['a']}, or {r['b']}?", panel, hold))
        beats.append(Beat(f"Comment your {NUM_WORDS[n - 1] if n <= 5 else n} picks, like A, B, B, A.",
                          {"kind": "cta", "text": "COMMENT YOUR PICKS"}))
    elif fmt == "guess_the_game":
        beats.append(Beat("Guess the Roblox game from three clues.", {"kind": "intro", "text": "3 CLUES. 1 GAME."}))
        for i, r in enumerate(rounds, 1):
            for k, c in enumerate(r["clues"], 1):
                panel = {"kind": "clues", "round": i, "of": n, "clues": r["clues"][:k]}
                beats.append(Beat(f"Clue {NUM_WORDS[k - 1]}: {c}", panel, hold if k == len(r["clues"]) else 0))
            beats.append(Beat(f"It's {r['game'].rstrip('!')}!", {"kind": "clues", "round": i, "of": n, "clues": r["clues"], "answer": r["game"]}))
        beats.append(Beat(f"How many did you get? Comment your score out of {NUM_WORDS[n - 1] if n <= 5 else n}.",
                          {"kind": "cta", "text": "COMMENT YOUR SCORE"}))
    elif fmt == "pick_one":
        for i, r in enumerate(rounds, 1):
            thing = r["thing"]
            closed = {"kind": "pick", "round": i, "of": n, "thing": thing, "open": [None, None, None]}
            beats.append(Beat(f"Pick {_article(thing)}: one, two, or three.", closed, hold))
            opened: list = [None, None, None]
            for k, x in enumerate(r["rewards"]):
                opened[k] = x
                beats.append(Beat(f"{thing.capitalize()} {NUM_WORDS[k]}: {x}.", {**closed, "open": list(opened), "now": k}))
        beats.append(Beat("Comment what you got!", {"kind": "cta", "text": "COMMENT WHAT YOU GOT"}))
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


def tick_cmd(out: str, *, last: bool = False) -> list[str]:
    """One countdown second: a short soft tick, then silence to exactly 1 s (a higher tick on the last one)."""
    freq = 1320 if last else 880
    return ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=0.09",
            "-af", "volume=0.22,afade=t=out:st=0.05:d=0.04,apad=whole_dur=1", "-t", "1", "-ar", "44100", "-ac", "1",
            "-c:a", "pcm_s16le", out]


def _concat_cmd(list_file: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_file, "-c:a", "pcm_s16le", out]


def voice(story: Story, work: Path, cfg, *, index: int, hold: int = 3) -> str:
    """Voice every beat, join them with the breaths and countdown ticks, loudness-normalise → ``work/short<n>.mp3``;
    the cues go to ``work/s<k>/timeline.json`` for the render. Kept when both files already exist (resume)."""
    out = work / f"short{index + 1}.mp3"
    d = work / f"s{index}"
    tl_path = d / "timeline.json"
    if out.exists() and tl_path.exists():
        return str(out)
    beats = make_beats(story.format, story.script, hold=hold)
    scfg = cfg.sof
    if scfg.tts_backend == "gemini":         # dozens of tiny lines would spend the free daily Gemini requests
        scfg = copy.copy(scfg)
        scfg.tts_backend = "pocket"
    raw = tts.speak([b.say for b in beats], str(d / "tts"), scfg, short=True)
    pieces_dir = d / "pieces"
    pieces_dir.mkdir(parents=True, exist_ok=True)
    gap = str(pieces_dir / "gap.wav")
    tick, tock = str(pieces_dir / "tick.wav"), str(pieces_dir / "tock.wav")
    sof_render.run(silence_cmd(gap, TICK_GAP), 120)
    sof_render.run(tick_cmd(tick), 120)
    sof_render.run(tick_cmd(tock, last=True), 120)
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


def _text_center(d, cx: float, y: float, text: str, font, fill, stroke: int = 0) -> None:
    d.text((cx - d.textlength(text, font=font) / 2, y), text, font=font, fill=fill,
           stroke_width=stroke, stroke_fill=INK if stroke else None)


def panel_png(panel: dict, out: str, *, header: str, width: int, height: int, caption: str = "", count: int = 0) -> str:
    """One full-frame RGBA overlay: the header chip, the format's boxes, the countdown number and the caption.
    Solid boxes with a dark outline (no see-through bands); the gameplay stays visible above and below."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    H, W = height, width
    ow = max(4, round(H * 0.003))
    f_head, f_body = sof_shorts._font(round(H * 0.027)), sof_shorts._font(round(H * 0.029))
    f_small, f_big = sof_shorts._font(round(H * 0.022)), sof_shorts._font(round(H * 0.06))
    x0, x1 = round(W * 0.07), round(W * 0.93)
    pad = round(H * 0.014)

    kind = panel.get("kind")
    head = header + (f"  {panel['round']}/{panel['of']}" if panel.get("round") else "")
    if kind == "pick":
        head = f"PICK {_article(panel['thing']).upper()}" + f"  {panel['round']}/{panel['of']}"
    hw = d.textlength(head, font=f_head) + 2 * pad * 1.6
    hy = round(H * 0.115)
    d.rounded_rectangle([(W - hw) / 2, hy, (W + hw) / 2, hy + round(H * 0.027) + 2 * pad], radius=pad * 2, fill=GOLD,
                        outline=INK, width=ow)
    _text_center(d, W / 2, hy + pad * 0.9, head, f_head, INK)

    y = round(H * 0.2)
    if kind == "wyr":
        for letter, text, color in (("A", panel["a"], RED), ("B", panel["b"], BLUE)):
            lines = sof_shorts._wrap(d, text.upper(), f_body, int((x1 - x0) * 0.78))[:3]
            lh = round(H * 0.029 * 1.2)
            bh = max(round(H * 0.075), len(lines) * lh + 2 * pad)
            d.rounded_rectangle([x0, y, x1, y + bh], radius=pad * 2, fill=color, outline=INK, width=ow)
            r = round(H * 0.026)
            cx, cy = x0 + pad + r, y + bh / 2
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE, outline=INK, width=ow)
            _text_center(d, cx, cy - H * 0.017, letter, f_body, INK)
            ty = y + (bh - len(lines) * lh) / 2
            tx = cx + r + pad
            for ln in lines:
                d.text((tx, ty), ln, font=f_body, fill=WHITE, stroke_width=max(2, ow // 2), stroke_fill=INK)
                ty += lh
            y += bh
            if letter == "A":
                y += round(H * 0.008)
                ow_txt = "OR"
                cw = d.textlength(ow_txt, font=f_head) + 2 * pad
                d.rounded_rectangle([(W - cw) / 2, y, (W + cw) / 2, y + round(H * 0.04)], radius=pad, fill=INK)
                _text_center(d, W / 2, y + H * 0.005, ow_txt, f_head, WHITE)
                y += round(H * 0.048)
    elif kind == "clues":
        lh = round(H * 0.026 * 1.25)
        lines: list[str] = []
        for k, c in enumerate(panel.get("clues") or [], 1):
            wrapped = sof_shorts._wrap(d, c, f_small, int((x1 - x0) - 2 * pad - H * 0.03))
            lines += [f"{k}. {wrapped[0]}"] + ["   " + w for w in wrapped[1:]]
        bh = max(round(H * 0.08), len(lines) * lh + 2 * pad)
        d.rounded_rectangle([x0, y, x1, y + bh], radius=pad * 2, fill=WHITE, outline=INK, width=ow)
        ty = y + pad
        for ln in lines:
            d.text((x0 + pad * 1.5, ty), ln, font=f_small, fill=INK)
            ty += lh
        y += bh + round(H * 0.012)
        if panel.get("answer"):
            name = panel["answer"].upper()
            ans_lines = sof_shorts._wrap(d, name, f_body, int((x1 - x0) * 0.9))[:2]
            ah = len(ans_lines) * round(H * 0.035) + 2 * pad
            d.rounded_rectangle([x0, y, x1, y + ah], radius=pad * 2, fill=GREEN, outline=INK, width=ow)
            ty = y + pad
            for ln in ans_lines:
                _text_center(d, W / 2, ty, ln, f_body, WHITE, stroke=max(2, ow // 2))
                ty += round(H * 0.035)
    elif kind == "pick":
        gap = round(W * 0.03)
        bw = ((x1 - x0) - 2 * gap) / 3
        bh = round(H * 0.2)
        for k in range(3):
            bx = x0 + k * (bw + gap)
            item = (panel.get("open") or [None, None, None])[k]
            fill = PICK_COLORS[k]
            outline_w = ow * 2 if panel.get("now") == k else ow
            d.rounded_rectangle([bx, y, bx + bw, y + bh], radius=pad * 2, fill=fill, outline=INK if panel.get("now") != k else WHITE,
                                width=outline_w)
            _text_center(d, bx + bw / 2, y + pad, str(k + 1), f_body, WHITE, stroke=max(2, ow // 2))
            if item is None:
                _text_center(d, bx + bw / 2, y + bh * 0.38, "?", f_big, WHITE, stroke=max(2, ow // 2))
            else:
                lines = sof_shorts._wrap(d, item.upper(), f_small, int(bw - 2 * pad))[:5]
                lh = round(H * 0.022 * 1.2)
                ty = y + bh * 0.3 + max(0, (bh * 0.65 - len(lines) * lh) / 2)
                for ln in lines:
                    _text_center(d, bx + bw / 2, ty, ln, f_small, WHITE, stroke=max(2, ow // 2))
                    ty += lh
        y += bh
    elif kind in ("cta", "intro"):
        text = str(panel.get("text") or "")
        lines = sof_shorts._wrap(d, text, f_big, int((x1 - x0) * 0.9))[:3]
        lh = round(H * 0.06 * 1.15)
        bh = len(lines) * lh + 2 * pad
        by = round(H * 0.3)
        d.rounded_rectangle([x0, by, x1, by + bh], radius=pad * 2, fill=GOLD if kind == "cta" else BLUE, outline=INK, width=ow)
        ty = by + pad
        for ln in lines:
            _text_center(d, W / 2, ty, ln, f_big, INK if kind == "cta" else WHITE, stroke=0 if kind == "cta" else max(2, ow // 2))
            ty += lh

    if count:
        r = round(H * 0.05)
        cx, cy = W / 2, round(H * 0.66)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE, outline=INK, width=ow * 2)
        _text_center(d, cx, cy - H * 0.036, str(count), f_big, INK)

    if caption:
        cap = Image.open(sof_shorts.caption_png(caption, out + ".cap.png", width=W, height=H)).convert("RGBA")
        im.alpha_composite(cap)
        Path(out + ".cap.png").unlink(missing_ok=True)
    im.save(out, "PNG")
    return out


def render(*, work: Path, name: str, index: int, header: str, background: str, audio: str, width: int = 1080,
           height: int = 1920, fps: int = 30, captions: bool = False) -> str:
    """The gameplay clip for the whole length, the panel PNG of each cue on top, the voiced beats → ``work/<name>.mp4``.
    No captions by default: the panel already shows every choice, clue and reward being read out."""
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
        pngs.append(panel_png(c.panel, str(tmp / f"panel_{i:03d}.png"), header=header, width=width, height=height,
                              caption=c.caption if captions else "", count=c.count))
        spans.append((c.start, min(c.end, duration), c.caption))
    out = str(work / f"{name}.mp4")
    sof_render.run(sof_shorts.overlay_cmd(reel, audio, out, cues=spans, cue_pngs=pngs, title_png_path=None, credit_png_path=None,
                                          duration=duration, fps=fps), 1800)
    return out
