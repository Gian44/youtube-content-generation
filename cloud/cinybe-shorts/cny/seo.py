"""Titles, descriptions and tags for the long recap and its Shorts. Category: Film & Animation (1)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sof import seo as sof_seo
from sof import upload as sof_upload

CATEGORY_FILM = "1"
TITLE_VARIANTS = 4
_BAD = re.compile(r"[!#@\"“”]|\b(shocking|insane|unbelievable|you won't believe)\b", re.I)
FOOTAGE_LINES = {
    "film": ("Footage: short excerpts from the film, used for the purpose of commentary, criticism and review. "
             "Narration and editing are our own."),
}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _BAD.sub("", str(s or ""))).strip(" -–—:|")


def fmt_minutes(seconds: float) -> int:
    return max(1, int(round(seconds / 60)))


def long_title(hook: str, *, fallback: str = "", suffix: str = " | Movie Recap") -> str:
    """The writer's hook sentence (never the film's name) + the genre suffix, within YouTube's 100 characters."""
    t = _clean(hook) or _clean(fallback) or "Movie Recap"
    t = t[:1].upper() + t[1:]
    out = f"{t}{suffix}"
    if len(out) > 100:
        out = f"{t[:100 - len(suffix)].rstrip(' ,.')}{suffix}"
    return out


def long_description(*, film: str, year: int | None, blurb: str, chapters: list[dict], director: str | None, theme: str,
                     plot_url: str = "", footage: str = "film") -> str:
    fy = f"{film} ({year})" if year else film
    parts = [f"Film: {fy}" + (f" · directed by {director}" if director else ""),
             blurb.strip() or f"The whole story of {fy}, start to finish, in one narrated recap.",
             "Spoilers: this recap tells the entire plot, including the ending."]
    lines = sof_seo.chapter_lines(chapters) if chapters else []
    if lines:
        parts.append("Chapters:\n" + "\n".join(lines))
    if theme:
        parts.append(f"Part of our series: {theme}.")
    parts.append(FOOTAGE_LINES.get(footage, FOOTAGE_LINES["film"])
                 + (f" Plot reference: Wikipedia ({plot_url}), CC BY-SA 4.0." if plot_url else ""))
    parts.append("#movierecap #filmrecap #recap")
    return "\n\n".join(parts)[:4900]


def long_tags(film: str, *, director: str | None, genres: list[str], cast: list[str], extra: list[str], theme: str) -> list[str]:
    base = [film, f"{film} recap", f"{film} explained", f"{film} plot", "movie recap", "film recap", "plot explained",
            "movie summary", "ending explained"]
    if director:
        base += [director, f"{director} movies"]
    base += [g.lower() for g in genres[:3]] + cast[:3] + [t for t in extra if t] + ([theme] if theme else [])
    out, seen, size = [], set(), 0
    for t in base:
        t = re.sub(r"[<>\"]", "", str(t)).strip()[:60]
        if not t or t.lower() in seen:
            continue
        if size + len(t) + 2 > 480 or len(out) >= 30:
            break
        seen.add(t.lower()); out.append(t); size += len(t) + 2
    return out


def short_title(title: str, film: str) -> str:
    """The Short's hook (no film name, like the samples) + #Shorts."""
    t = _clean(title)
    t = (t[:1].upper() + t[1:]) if t else "Movie Recap"
    out = f"{t} #Shorts"
    return out if len(out) <= 100 else f"{t[:90].rstrip(' ,.')} #Shorts"


def short_description(*, film: str, year: int | None, long_video_id: str, hook: str, tags: list[str], footage: str = "film") -> str:
    fy = f"{film} ({year})" if year else film
    return (f"{hook.strip()}\n\nFilm: {fy}. The full recap, start to finish: https://youtu.be/{long_video_id}\n\n"
            + FOOTAGE_LINES.get(footage, FOOTAGE_LINES["film"]) + "\n\n"
            + " ".join("#" + re.sub(r"[^a-z0-9]", "", t.lower()) for t in (["shorts", "movierecap", film] + tags[:3]) if t))[:4900]


def short_tags(film: str, tags: list[str]) -> list[str]:
    return long_tags(film, director=None, genres=[], cast=[], extra=tags, theme="")[:20]


def video_body(*, title: str, description: str, tags: list[str], privacy: str, publish_at: str | None = None) -> dict:
    body = sof_upload.video_body(title=title, description=description, tags=tags, privacy=privacy, publish_at=publish_at)
    body["snippet"]["categoryId"] = CATEGORY_FILM
    return body


def short_publish_at(long_publish_at: str | None, *, delay_minutes: int, index: int, spacing_hours: float,
                     now: datetime | None = None) -> str:
    """Short k goes live ``delay_minutes`` after the long video, plus k × ``spacing_hours``."""
    now = now or datetime.now(timezone.utc)
    base = datetime.fromisoformat(long_publish_at.replace("Z", "+00:00")) if long_publish_at else now
    at = max(base + timedelta(minutes=delay_minutes), now + timedelta(minutes=2)) + timedelta(hours=spacing_hours * index)
    return sof_seo.rfc3339(at)
