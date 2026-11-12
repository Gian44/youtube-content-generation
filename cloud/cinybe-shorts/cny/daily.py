"""The daily cadence (Gian, 2026-11-12: "it should have a daily output of this"): one long recap per UTC day.

GitHub starts scheduled runs hours late (6-10 h seen) and now and then not at all, so the workflow is triggered
several times a day and every trigger asks :func:`hold` first: a recap is already up today → nothing to do; a
scheduled trigger before ``post_from_hour`` UTC → nothing to do either (a late trigger from the evening before
lands after midnight; posting then would drift the posting time by half a day). The first trigger that gets
through after ``post_from_hour`` makes the day's recap; the rest are no-ops that end in seconds.

Standard library only: ``gate.py`` runs this before any dependency is installed.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path


def _day(row: dict) -> date | None:
    """The day a recap counts for: its run's start date (``date``), so a run that starts at 23:00 and uploads after
    midnight still fills the day it was made for; else the upload time's UTC date."""
    for raw, n in ((row.get("date"), 10), (row.get("uploaded_at"), None)):
        raw = str(raw or "").strip()
        if not raw:
            continue
        try:
            if n:
                return date.fromisoformat(raw[:n])
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date()
        except ValueError:
            continue
    return None


def posted_today(rows: list[dict], *, now: datetime | None = None) -> str | None:
    """A published recap (``kind: video`` with a video id, not taken down) made for today's UTC date → why."""
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    for r in rows:
        if r.get("kind", "video") != "video" or not r.get("video_id") or r.get("taken_down"):
            continue
        if _day(r) == today:
            return f"today's recap is already up ({r.get('film')}, {r.get('video_id')})"
    return None


def hold(rows: list[dict], *, now: datetime | None = None, scheduled: bool = True, post_from_hour: int = 9) -> str | None:
    """Why this trigger makes nothing, or None when it should make today's recap."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if scheduled and now.hour < post_from_hour:
        return f"before {post_from_hour:02d}:00 UTC (today's recap is made by a later trigger)"
    return posted_today(rows, now=now)


def load_rows(path: str | Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
