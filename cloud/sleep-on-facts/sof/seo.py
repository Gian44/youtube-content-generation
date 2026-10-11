"""Discovery metadata: titles, descriptions, chapters, tags, hashtags, playlists, publish slot.

Sleep viewers search broad intent phrases ("space facts to fall asleep to", "history for sleep"),
not the subject alone, so titles lead with length + intent phrase and end with the subject:
    "3 Hours of Calm Space Facts to Fall Asleep To | Saturn"
Each topic category maps to one search phrase, one playlist and one hashtag.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Profile:
    phrase: str          # inside the title: "… of Calm {phrase} to Fall Asleep To"
    playlist: str        # playlist title the video is added to
    hashtag: str         # second hashtag (first is always #sleep)


PROFILES: list[tuple[str, Profile]] = [
    ("space", Profile("Space Facts", "Space Facts to Fall Asleep To", "spacefacts")),
    ("astronom", Profile("Space Facts", "Space Facts to Fall Asleep To", "spacefacts")),
    ("history", Profile("History", "History for Sleep", "historyforsleep")),
    ("nature", Profile("Nature Facts", "Nature Facts to Fall Asleep To", "naturefacts")),
    ("ocean", Profile("Ocean Facts", "Ocean Facts to Fall Asleep To", "oceanfacts")),
    ("how things work", Profile("Science Facts", "Science Facts to Fall Asleep To", "sciencefacts")),
    ("cities", Profile("Geography Facts", "Geography Facts to Fall Asleep To", "geography")),
    ("food", Profile("Facts", "Food, Farming & Plants for Sleep", "sleepfacts")),
    ("invention", Profile("History", "Inventions & Ideas for Sleep", "historyforsleep")),
]
DEFAULT = Profile("Facts", "Calm Facts to Fall Asleep To", "sleepfacts")
SPACE_CATEGORY = "space & astronomy"


def profile(category: str) -> Profile:
    c = (category or "").lower()
    for key, prof in PROFILES:
        if c.startswith(key) or key in c:
            return prof
    return DEFAULT


def _clean(s: str) -> str:
    """YouTube rejects '<' and '>' in titles and descriptions."""
    return re.sub(r"[<>]", "", str(s or "")).strip()


def hours_label(hours: int) -> str:
    h = max(1, int(hours))
    return f"{h} Hour{'s' if h != 1 else ''}"


def build_title(subject: str, category: str, hours: int) -> str:
    p = profile(category)
    t = f"{hours_label(hours)} of Calm {p.phrase} to Fall Asleep To | {_clean(subject)}"
    if len(t) > 100:
        t = f"{hours_label(hours)} of {p.phrase} to Fall Asleep To | {_clean(subject)}"
    return t[:100]


def hashtags(subject: str, category: str) -> list[str]:
    camel = "".join(w[:1].upper() + w[1:] for w in re.findall(r"[A-Za-z0-9]+", subject or ""))
    tags = ["#sleep", f"#{profile(category).hashtag}"]
    if camel and f"#{camel}".lower() not in {t.lower() for t in tags}:
        tags.append(f"#{camel}")
    return tags


def fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def chapter_lines(chapters: list[dict], *, min_gap: float = 10.0) -> list[str]:
    """YouTube chapter rules: first at 0:00, ascending, ≥ 3 chapters, each ≥ 10 s. Returns [] if unmet."""
    rows = sorted(((float(c.get("start") or 0), _clean(c.get("title") or "")) for c in chapters or []), key=lambda r: r[0])
    out: list[tuple[float, str]] = []
    for start, title in rows:
        title = re.sub(r"\s+", " ", title).rstrip(".:;,")[:70] or f"Part {len(out) + 1}"
        if not out:
            out.append((0.0, title))
        elif start - out[-1][0] >= min_gap:
            out.append((start, title))
    return [f"{fmt_ts(s)} {t}" for s, t in out] if len(out) >= 3 else []


def build_description(*, subject: str, category: str, hours: int, blurb: str = "", chapters: list[dict] | None = None,
                      footer: str = "", credits_url: str = "", extra: str = "", limit_bytes: int = 4900) -> str:
    """Keyword-first opening line → blurb → chapters → AI/sources footer → credits → hashtags.

    Over the byte limit, source lines are dropped from the end first (chapters carry more value).
    """
    p = profile(category)
    h = max(1, int(hours))
    opening = f"Fall asleep to {h} hour{'s' if h != 1 else ''} of calm {p.phrase.lower()}: {_clean(subject)}."
    head = "\n\n".join(x for x in [opening + (" " + _clean(blurb) if blurb else ""),
                                   ("Chapters\n" + "\n".join(chapter_lines(chapters or []))) if chapter_lines(chapters or []) else ""] if x)
    tail_parts = []
    if credits_url:
        tail_parts.append(f"Photos: Wikimedia Commons contributors, NASA, Pexels and Pixabay — credits and licences: {credits_url}")
    if extra:
        tail_parts.append(_clean(extra))
    tail_parts.append(" ".join(hashtags(subject, category)))
    foot_lines = _clean(footer).splitlines()

    def assemble(lines: list[str]) -> str:
        return "\n\n".join(x for x in [head, "\n".join(lines).strip(), *tail_parts] if x)

    text = assemble(foot_lines)
    while len(text.encode("utf-8")) > limit_bytes and any(l.startswith("- ") for l in foot_lines):
        idx = max(i for i, l in enumerate(foot_lines) if l.startswith("- "))
        foot_lines.pop(idx)
        text = assemble(foot_lines)
    return text.encode("utf-8")[:limit_bytes].decode("utf-8", "ignore")


def build_tags(subject: str, category: str, extra: list[str] | None = None, *, limit_chars: int = 450) -> list[str]:
    p = profile(category)
    s = _clean(subject).lower()
    base = ["sleep", "facts to fall asleep to", f"{p.phrase.lower()} to fall asleep to", s, f"{s} facts",
            "sleep documentary", "bedtime", "calm", "relaxing", "fall asleep fast", "sleep on facts"]
    out: list[str] = []
    for t in base + [str(x) for x in (extra or [])]:
        t = _clean(t).replace(",", " ").strip()
        if t and t.lower() not in {o.lower() for o in out} and sum(len(o) + 1 for o in out) + len(t) <= limit_chars:
            out.append(t)
    return out


def playlist_description(category: str) -> str:
    p = profile(category)
    return (f"Calm, slow {p.phrase.lower()} narrated to help you fall asleep — one long, gentle video at a time. "
            "Narration is AI-generated; facts are drawn from Wikipedia and the sources listed on each video.")


def publish_slot(now: datetime, hour: int, minute: int, *, tz: str = "UTC", max_wait_hours: float = 12.0) -> datetime | None:
    """Next HH:MM in ``tz`` at least 5 minutes away, as UTC — or None (publish immediately) if that is
    more than ``max_wait_hours`` off, so a very late or manual run does not sit private for a day."""
    from zoneinfo import ZoneInfo

    local_now = now.astimezone(ZoneInfo(tz))
    slot = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot <= local_now + timedelta(minutes=5):
        slot = (slot + timedelta(days=1)).replace(hour=hour, minute=minute)
    slot = slot.astimezone(timezone.utc)
    return slot if slot - now.astimezone(timezone.utc) <= timedelta(hours=max_wait_hours) else None


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
