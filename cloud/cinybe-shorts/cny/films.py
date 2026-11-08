"""The film queue is a committed file: ``films.yml`` lists themes in order, each with its films in order.

The run takes the first film that has no ledger row (``kind: video`` published it; ``kind: skipped`` means the
run gave up on it — too few dialogue cues, no usable shots — and it is passed over until its ledger row is
removed). A film with ``archive: <identifier>`` is a public-domain print the run downloads itself from the
Internet Archive; the others wait for Gian's file in the store. The ledger, not the YAML, holds the state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import yaml


@dataclass(frozen=True)
class Film:
    title: str
    year: int | None
    theme: str
    archive: str | None = None      # Internet Archive item identifier: the run fetches this public-domain print itself

    @property
    def slug(self) -> str:
        return slug(self.title, self.year)

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title


def slug(title: str, year: int | None = None) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    return f"{s}-{year}" if year else s


def load_films(path: str) -> list[Film]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out: list[Film] = []
    for theme in data.get("themes") or []:
        name = str(theme.get("name") or "").strip()
        for row in theme.get("films") or []:
            archive = None
            if isinstance(row, str):
                title, year = row, None
            else:
                title, year = str(row.get("title") or "").strip(), row.get("year")
                archive = (str(row.get("archive") or "").strip() or None)
            if title:
                out.append(Film(title=title, year=int(year) if year else None, theme=name, archive=archive))
    return out


def _done_slugs(rows: list[dict]) -> set[str]:
    """Films the ledger already settled: published, or skipped (until a plot file is added — see pipeline)."""
    return {slug(str(r.get("film") or ""), r.get("year")) for r in rows if r.get("film")}


def next_films(films: list[Film], rows: list[dict], *, limit: int = 3, retry_skipped: set[str] = frozenset()) -> list[Film]:
    """The next ``limit`` candidates in queue order. A skipped film is offered again only when its slug is in
    ``retry_skipped`` (the pipeline passes the slugs that now have an operator plot file)."""
    published = {slug(str(r.get("film") or ""), r.get("year")) for r in rows
                 if r.get("film") and r.get("kind", "video") == "video"}
    skipped = _done_slugs([r for r in rows if r.get("kind") == "skipped"]) - published
    out: list[Film] = []
    for f in films:
        if f.slug in published:
            continue
        if f.slug in skipped and f.slug not in retry_skipped:
            continue
        out.append(f)
        if len(out) >= limit:
            break
    return out


def find_film(films: list[Film], title: str, year: int | None = None) -> Film:
    """A forced film: the YAML row when it exists (keeps its theme), otherwise an ad-hoc one."""
    want = title.strip().lower()
    for f in films:
        if f.title.lower() == want and (year is None or f.year == year):
            return f
    return Film(title=title.strip(), year=year, theme="")


def ran_recently(rows: list[dict], *, gap_hours: float, now: datetime | None = None) -> str | None:
    """Why the daily cron should do nothing: the last published video is younger than ``gap_hours``."""
    now = now or datetime.now(timezone.utc)
    stamps = []
    for r in rows:
        if r.get("kind", "video") != "video" or not r.get("video_id"):
            continue
        raw = str(r.get("uploaded_at") or r.get("date") or "")
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        stamps.append(dt)
    if not stamps:
        return None
    age = (now - max(stamps)).total_seconds() / 3600
    if age < gap_hours:
        return f"last video was {age:.0f} h ago (gap {gap_hours:g} h)"
    return None
