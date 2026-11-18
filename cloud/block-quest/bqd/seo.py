"""Titles, descriptions, tags and publish times for the story Shorts. Category: Gaming (20) for the Roblox
stories over gameplay, Entertainment (24) for the older stories over stock footage."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sof import seo as sof_seo
from sof import upload as sof_upload

CATEGORY_ENTERTAINMENT = "24"
CATEGORY_GAMING = "20"
_BAD = re.compile(r"[!#@\"“”]|\b(shocking|insane|unbelievable|you won't believe)\b", re.I)
CATEGORY_LABELS = {"aita": "Am I Wrong?", "relationships": "Relationship Stories", "cheating": "Betrayal Stories",
                   "revenge": "Revenge Stories", "workplace_drama": "Workplace Stories", "entitled_parents": "Entitled Parents",
                   "family_drama": "Family Drama", "confessions": "Confessions", "scary_stories": "Scary Stories",
                   "creepy_encounters": "Creepy Encounters", "wholesome": "Wholesome Stories", "mysteries": "Mysteries",
                   "customer_service_drama": "Customer Service Stories",
                   "trade_scams": "Roblox Trade Stories", "obby_rage": "Roblox Obby Stories",
                   "friends_and_betrayal": "Roblox Friend Stories", "banned_or_hacked": "Roblox Account Stories",
                   "robux_and_parents": "Robux Stories", "wholesome": "Wholesome Roblox Stories",
                   "revenge_on_trolls": "Roblox Troll Stories", "roleplay_drama": "Roblox Roleplay Stories",
                   "creepy_servers": "Creepy Roblox Stories", "sibling_rivalry": "Roblox Sibling Stories",
                   # 2026-11-19: kid-safe, all-ages story categories + the interactive formats
                   "obby_fails": "Roblox Obby Stories", "lucky_hatch": "Lucky Roblox Stories",
                   "helping_a_noob": "Wholesome Roblox Stories", "roleplay_comedy": "Funny Roblox Roleplay Stories",
                   "sibling_shenanigans": "Roblox Sibling Stories", "build_battle": "Roblox Build Stories",
                   "epic_comeback": "Roblox Comeback Stories", "robux_and_chores": "Robux Stories",
                   "best_friend_duo": "Roblox Friend Stories", "fair_trades": "Roblox Trading Stories",
                   "would_you_rather": "Roblox Would You Rather", "guess_the_game": "Guess the Roblox Game",
                   "pick_one": "Roblox Pick One"}
INTERACTIVE_CALLS = {"would_you_rather": "Pick fast and comment your answers",
                     "guess_the_game": "Guess before the timer runs out and comment your score",
                     "pick_one": "Pick one, then comment what you got"}
INTERACTIVE_TAGS = {"would_you_rather": ["roblox would you rather", "would you rather", "wyr", "this or that"],
                    "guess_the_game": ["guess the roblox game", "roblox quiz", "guess the game", "roblox trivia"],
                    "pick_one": ["roblox pick one", "pick one", "choose one", "mystery egg"]}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _BAD.sub("", str(s or ""))).strip(" -–—:|.")


def short_title(title: str, category: str, *, roblox: bool = False) -> str:
    t = _clean(title)
    t = t[:1].upper() + t[1:]
    tail = "#roblox #Shorts" if roblox else "#Shorts"
    out = f"{t} {tail}"
    if len(out) > 100:
        out = f"{t[:99 - len(tail)].rstrip()} {tail}"
    return out


def short_description(*, hook: str, category: str, tags: list[str], credit: str = "") -> str:
    label = CATEGORY_LABELS.get(category, category.replace("_", " ").title())
    lead = ["shorts", "roblox", "robloxstory", "storytime"] if credit else ["shorts", "storytime", category]
    hashtags = " ".join(dict.fromkeys("#" + re.sub(r"[^a-z0-9]", "", t.lower()) for t in (lead + tags[:4]) if t))
    if credit:      # Roblox stories over Creative Commons gameplay: the maker of the gameplay is credited by name and link
        return (f"{hook.strip()}\n\n{label} — a new Roblox story every day on Block Quest Daily.\n\n"
                "Original fiction written for this channel; any resemblance to real players or events is coincidental. "
                "Not affiliated with or endorsed by Roblox Corporation.\n"
                f"{credit}\n\n" + hashtags)[:4900]
    return (f"{hook.strip()}\n\n{label} — a new story every day on Block Quest Daily.\n\n"
            "Original fiction written for this channel; any resemblance to real people or events is coincidental. "
            "Footage: Pexels and Pixabay.\n\n" + hashtags)[:4900]


def interactive_description(*, fmt: str, hook: str, tags: list[str], credit: str) -> str:
    label = CATEGORY_LABELS.get(fmt, fmt.replace("_", " ").title())
    lead = ["shorts", "roblox"] + [t.replace(" ", "") for t in INTERACTIVE_TAGS.get(fmt, [])[:2]]
    hashtags = " ".join(dict.fromkeys("#" + re.sub(r"[^a-z0-9]", "", t.lower()) for t in (lead + tags[:3]) if t))
    return (f"{hook.strip()}\n\n{label}: {INTERACTIVE_CALLS.get(fmt, 'Comment your answers')}. A new Roblox Short every "
            "day on Block Quest Daily.\n\nNot affiliated with or endorsed by Roblox Corporation.\n"
            + (f"{credit}\n" if credit else "") + "\n" + hashtags)[:4900]


def interactive_tags(fmt: str, tags: list[str]) -> list[str]:
    return short_tags(fmt, INTERACTIVE_TAGS.get(fmt, []) + list(tags), roblox=True)


def short_tags(category: str, tags: list[str], *, roblox: bool = False) -> list[str]:
    base = ((["roblox", "roblox story", "roblox storytime", "roblox shorts"] if roblox else [])
            + ["story", "storytime", "short story", category.replace("_", " "), CATEGORY_LABELS.get(category, ""), "block quest daily"] + list(tags))
    out, seen, size = [], set(), 0
    for t in base:
        t = re.sub(r"[<>\"]", "", str(t)).strip()[:60]
        if not t or t.lower() in seen:
            continue
        if size + len(t) + 2 > 480 or len(out) >= 25:
            break
        seen.add(t.lower()); out.append(t); size += len(t) + 2
    return out


def video_body(*, title: str, description: str, tags: list[str], privacy: str, publish_at: str | None = None,
               gaming: bool = False) -> dict:
    body = sof_upload.video_body(title=title, description=description, tags=tags, privacy=privacy, publish_at=publish_at)
    body["snippet"]["categoryId"] = CATEGORY_GAMING if gaming else CATEGORY_ENTERTAINMENT
    return body


def publish_at(index: int, *, spacing_hours: float, now: datetime | None = None) -> str | None:
    """Short 0 goes live at once; Short k at k × ``spacing_hours`` from now."""
    if index <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    return sof_seo.rfc3339(now + timedelta(hours=spacing_hours * index))
