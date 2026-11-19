"""Category draw and the story writer: one writer call per Short (Claude on the subscription via ``sof.llm``),
validated, then the no-AI-slop pass (mode ``fiction``) on the flagged paragraphs. Pure helpers are unit-tested
without a model."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import yaml

from sof import noslop
from sof.llm import LLM, LLMError

log = logging.getLogger("bqd.stories")
BANNED_WORDS = re.compile(r"\b(reddit|subreddit|r/|op\b|upvote|karma)\b", re.I)
# Gian 2026-11-19: kid-safe, all ages, nothing creepy. Stories that lean on these are rewritten, not published.
DARK_WORDS = re.compile(r"\b(creep(y|ier|iest|ed)?|horror|haunt(ed|ing)?|stalk(er|ed|ing)?|kidnap\w*|blood\w*|gore|"
                        r"hack(ed|er|ers|ing)?|scam(s|med|mer|mers)?|banned|password|home address|phone number|"
                        r"hell|damn|crap|idiot|stupid)\b", re.I)    # mild swears too: the narrator reads every word


class StoryError(RuntimeError):
    pass


@dataclass
class Category:
    key: str
    weight: int
    hints: str
    persona: str


@dataclass
class Story:
    category: str
    persona: str
    title: str
    hook: str
    body: str                    # the full narration including the hook, ending on the comment bait
    broll_queries: list[str]
    tags: list[str] = field(default_factory=list)
    slop: dict = field(default_factory=dict)
    format: str = "story"        # story | would_you_rather | guess_the_game | pick_one (bqd/interactive.py)
    script: dict = field(default_factory=dict)   # the interactive formats' rounds; empty for a story

    @property
    def words(self) -> int:
        return len(self.body.split())

    def to_dict(self) -> dict:
        return {"category": self.category, "persona": self.persona, "title": self.title, "hook": self.hook, "body": self.body,
                "broll_queries": self.broll_queries, "tags": self.tags, "slop": self.slop, "format": self.format,
                "script": self.script}

    @classmethod
    def from_dict(cls, d: dict) -> "Story":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------- topics.yml + the draw

def load_topics(path: str) -> tuple[list[Category], dict[str, str]]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cats = [Category(key=k, weight=int(v.get("weight") or 1), hints=str(v.get("hints") or ""), persona=str(v.get("persona") or "calm"))
            for k, v in (data.get("categories") or {}).items()]
    return cats, {k: str(v) for k, v in (data.get("personas") or {}).items()}


def recent_categories(rows: list[dict], *, days: int, now: datetime | None = None) -> set[str]:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    out: set[str] = set()
    for r in rows:
        if str(r.get("date") or "") >= cutoff:
            for s in r.get("stories") or []:
                if s.get("category"):
                    out.add(str(s["category"]))
    return out


def recent_hooks(rows: list[dict], *, limit: int, fmt: str = "story") -> list[str]:
    """The last ``limit`` hooks of one format (rows written before 2026-11-19 carry no format: they are stories)."""
    hooks = [str(s.get("hook") or s.get("title") or "") for r in rows for s in (r.get("stories") or [])
             if str(s.get("format") or "story") == fmt]
    return [h for h in hooks if h][-limit:]


def draw_categories(cats: list[Category], *, count: int, avoid: set[str], seed: str, force: str | None = None) -> list[Category]:
    """``count`` distinct categories, weighted, deterministic for ``seed`` (the date), skipping ``avoid`` when
    enough remain. ``force`` repeats one category for every story."""
    if force:
        c = next((c for c in cats if c.key == force), None)
        if c is None:
            raise StoryError(f"unknown category {force!r}")
        return [c] * count
    pool = [c for c in cats if c.key not in avoid] or list(cats)
    if len(pool) < count:
        pool = list(cats)
    rng = random.Random(int(hashlib.sha1(seed.encode()).hexdigest(), 16))
    out: list[Category] = []
    while pool and len(out) < count:
        pick = rng.choices(pool, weights=[max(1, c.weight) for c in pool], k=1)[0]
        out.append(pick)
        pool = [c for c in pool if c.key != pick.key]
    return out


# ---------------------------------------------------------------- the writer

SYSTEM = ("You write original short fiction read aloud as YouTube Shorts over Roblox gameplay: first-person stories "
          "told by a Roblox player about something that happened in a game, the way players tell them to each other. "
          "Completely original; never claim it is real; never mention any forum or social app. The listeners are "
          "Roblox players of every age, younger ones included, so every story is kid-safe: funny, wholesome or "
          "exciting, never scary or mean. Output strict JSON.")

PROMPT = """Write an original first-person Roblox story for the category "{category}" ({hints}).
Narrator style: {persona} — {persona_note}.
The narrator is a Roblox player; the story happens in or around a Roblox game (a server, a trade, an obby, a roleplay,
a friend list, Robux, an account). Use the words players use (server, obby, trade, Robux, noob, AFK, lobby), and say
what kind of game it is. A real, well-known Roblox game may be named when it fits; invent the player usernames.

Requirements:
- {words_min}-{words_max} words in total, target {words_target}; about 55 seconds read aloud.
- Structure: HOOK (the first sentence, a concrete event or line of dialogue that makes the listener stay; no
  "What if I told you", no questions), SETUP (who, where, one or two specific details), ESCALATION (the turn),
  PAYOFF (a twist, a consequence or a quiet landing), then ONE closing question to the audience as the last
  sentence (e.g. "Would you have told her?").
- Specific, physical details: names, places, objects, amounts. Plain words. Contractions are fine.
- Kid-safe for every age: funny, wholesome or exciting. Nothing scary at all (no creepy players, no empty or
  haunted servers, no one who knows things about the narrator's real life, no strangers asking for anything).
  No scams, hacks, exploits, bans or stolen accounts. No bullying, revenge or betrayal; a rivalry ends friendly.
  No romance or online dating, no swearing (not even "hell" or "stupid"), no slurs, no violence beyond an avatar
  falling off an obby. Don't name games with a swear in the title (no Tower of Hell).
- No real person, no real username, no real YouTuber. Nobody shares a password, address or real name.
- The trouble in the story is light: a fail, a race, a mix-up, a silly rule, bad luck that turns good.
- Avoid these recent hooks (different situations, different openings):
{used_hooks}

Return JSON:
{{"title": "<4-9 words, the situation, no clickbait punctuation>",
  "hook": "<the first sentence, exactly as in body>",
  "body": "<the full story, hook first, closing question last>",
  "broll_queries": ["<3-6 word stock-footage search for the setting: place, time of day, object>", "<another>", "<another>"],
  "tags": ["<5-8 lowercase tags: category, theme, key nouns>"]}}"""

EDIT_SYSTEM = ("You are a line editor for spoken first-person fiction. You change wording only; every event, name and "
               "outcome stays exactly as written. Output strict JSON.")
STYLE_BLOCK = noslop.style_block("fiction")


def validate(data, *, words_min: int, words_max: int, need_queries: bool = True) -> dict:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise StoryError("story must be a JSON object")
    body = " ".join(str(data.get("body") or "").split())
    hook = " ".join(str(data.get("hook") or "").split())
    title = " ".join(str(data.get("title") or "").split())
    n = len(body.split())
    if not title:
        errors.append("title is empty")
    if len(title) > 70:
        errors.append("title is longer than 70 characters")
    if not body:
        errors.append("body is empty")
    elif not (words_min <= n <= words_max):
        errors.append(f"body is {n} words; must be {words_min}-{words_max}")
    if hook and body and not body.lower().startswith(hook.lower()[:40]):
        errors.append("body must start with the hook sentence")
    if body and not body.rstrip().endswith("?"):
        errors.append("the last sentence must be one closing question to the audience")
    if BANNED_WORDS.search(body) or BANNED_WORDS.search(title):
        errors.append("no mention of Reddit, subreddits, OP, upvotes or karma")
    if hook.count("?"):
        errors.append("the hook must not be a question")
    dark = sorted({m.group(0).lower() for m in DARK_WORDS.finditer(f"{title} {body}")})
    if dark:
        errors.append("kid-safe channel: rewrite without anything creepy, scary, hacked, scammed or banned "
                      f"(found: {', '.join(dark)})")
    q = [str(x).strip() for x in (data.get("broll_queries") or []) if str(x).strip()]
    if need_queries and len(q) < 2:
        errors.append("broll_queries needs at least 2 searches")
    if errors:
        raise StoryError("; ".join(errors))
    data["title"], data["hook"], data["body"], data["broll_queries"] = title, hook or body.split(".")[0] + ".", body, q[:3]
    data["tags"] = [str(t).strip().lower() for t in (data.get("tags") or []) if str(t).strip()][:8]
    return data


def _call(llm: LLM, prompt: str, validate_fn, *, temperature: float) -> dict:
    try:
        return validate_fn(llm.generate_json(prompt, system=SYSTEM, temperature=temperature, max_tokens=1500))
    except StoryError as first:
        log.warning("story rejected, retrying once: %s", str(first)[:300])
        retry = f"{prompt}\n\nYour previous answer was rejected. Validation errors:\n{first}\nReturn corrected JSON that satisfies every rule."
        return validate_fn(llm.generate_json(retry, system=SYSTEM, temperature=temperature, max_tokens=1500))


def write_story(llm: LLM, cat: Category, *, persona_note: str, used_hooks: list[str], words_min: int, words_max: int,
                words_target: int, noslop_rounds: int = 1, fast_model: str | None = None, need_queries: bool = True) -> Story:
    prompt = PROMPT.format(category=cat.key.replace("_", " "), hints=cat.hints, persona=cat.persona, persona_note=persona_note,
                           words_min=words_min, words_max=words_max, words_target=words_target,
                           used_hooks="\n".join(f"- {h}" for h in used_hooks) or "- (none yet)")
    prompt = noslop.inject(prompt, STYLE_BLOCK, before="Return JSON")
    try:
        data = _call(llm, prompt, lambda d: validate(d, words_min=words_min, words_max=words_max, need_queries=need_queries),
                     temperature=0.9)
    except LLMError as exc:
        raise StoryError(f"writer call failed: {exc}") from exc
    story = Story(category=cat.key, persona=cat.persona, title=data["title"], hook=data["hook"], body=data["body"],
                  broll_queries=data["broll_queries"], tags=data["tags"])
    story.slop = despop(llm, story, rounds=noslop_rounds, words_max=words_max, fast_model=fast_model)
    return story


def despop(llm: LLM, story: Story, *, rounds: int, words_max: int, fast_model: str | None) -> dict:
    """No-AI-slop pass on the body (mode fiction); the closing question is exempt. Never raises."""
    if rounds <= 0:
        return {}

    def rewrite(prompt: str) -> dict:
        return llm.generate_json(prompt, system=EDIT_SYSTEM, temperature=0.0, model=fast_model, max_tokens=2000)

    try:
        sents = noslop.sentences(story.body)
        closing = sents[-1] if sents and sents[-1].endswith("?") else ""
        core = story.body[: len(story.body) - len(closing)].strip() if closing else story.body
        res = noslop.polish(core, rewrite=rewrite, mode="fiction", rounds=rounds, length_band=(0.8, 1.2),
                            extra="This is a spoken first-person story; keep the voice, every event and the ending.")
        new = (res.text.strip() + (" " + closing if closing else "")).strip()
        if story.words * 0.8 <= len(new.split()) <= words_max and new.lower().startswith(story.hook.lower()[:30]):
            story.body = new
        return {"before": res.before.to_dict()["findings"], "after": res.after.to_dict()["findings"], "rewritten": res.rewritten}
    except Exception as exc:  # noqa: BLE001
        log.warning("noslop pass failed: %s", exc)
        return {}


def dump(stories: list[Story]) -> str:
    return json.dumps([s.to_dict() for s in stories], indent=2, ensure_ascii=False)
