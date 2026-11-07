"""Titles, descriptions, tags and publish times for the story Shorts. Category: Entertainment (24)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sof import seo as sof_seo
from sof import upload as sof_upload

CATEGORY_ENTERTAINMENT = "24"
_BAD = re.compile(r"[!#@\"“”]|\b(shocking|insane|unbelievable|you won't believe)\b", re.I)
CATEGORY_LABELS = {"aita": "Am I Wrong?", "relationships": "Relationship Stories", "cheating": "Betrayal Stories",
                   "revenge": "Revenge Stories", "workplace_drama": "Workplace Stories", "entitled_parents": "Entitled Parents",
                   "family_drama": "Family Drama", "confessions": "Confessions", "scary_stories": "Scary Stories",
                   "creepy_encounters": "Creepy Encounters", "wholesome": "Wholesome Stories", "mysteries": "Mysteries",
                   "customer_service_drama": "Customer Service Stories"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _BAD.sub("", str(s or ""))).strip(" -–—:|.")


def short_title(title: str, category: str) -> str:
    t = _clean(title)
    t = t[:1].upper() + t[1:]
    out = f"{t} #Shorts"
    if len(out) > 100:
        out = f"{t[:85].rstrip()} #Shorts"
    return out


def short_description(*, hook: str, category: str, tags: list[str]) -> str:
    label = CATEGORY_LABELS.get(category, category.replace("_", " ").title())
    hashtags = " ".join("#" + re.sub(r"[^a-z0-9]", "", t.lower()) for t in (["shorts", "storytime", category] + tags[:4]) if t)
    return (f"{hook.strip()}\n\n{label} — a new story every day on Block Quest Daily.\n\n"
            "Original fiction written for this channel; any resemblance to real people or events is coincidental. "
            "Footage: Pexels and Pixabay.\n\n" + hashtags)[:4900]


def short_tags(category: str, tags: list[str]) -> list[str]:
    base = ["story", "storytime", "short story", category.replace("_", " "), CATEGORY_LABELS.get(category, ""), "block quest daily"] + list(tags)
    out, seen, size = [], set(), 0
    for t in base:
        t = re.sub(r"[<>\"]", "", str(t)).strip()[:60]
        if not t or t.lower() in seen:
            continue
        if size + len(t) + 2 > 480 or len(out) >= 25:
            break
        seen.add(t.lower()); out.append(t); size += len(t) + 2
    return out


def video_body(*, title: str, description: str, tags: list[str], privacy: str, publish_at: str | None = None) -> dict:
    body = sof_upload.video_body(title=title, description=description, tags=tags, privacy=privacy, publish_at=publish_at)
    body["snippet"]["categoryId"] = CATEGORY_ENTERTAINMENT
    return body


def publish_at(index: int, *, spacing_hours: float, now: datetime | None = None) -> str | None:
    """Short 0 goes live at once; Short k at k × ``spacing_hours`` from now."""
    if index <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    return sof_seo.rfc3339(now + timedelta(hours=spacing_hours * index))
