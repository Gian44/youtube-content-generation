"""Marathon compilations: three recent long videos joined back to back into one ~9-hour upload.

Gian chose this on 2026-10-25. Sleepers who stay asleep stay watching, and the long documentaries our
videos are suggested beside include 8-hour compilations. A marathon costs no narration, script or
photo spend: each daily run keeps its finished ``final.mp4`` (+ chapters, thumbnail photo) as the
``media-<run id>`` artifact for 45 days, and this module downloads three of them, joins them without
re-encoding (identical encoder settings), and uploads the result with offset chapters.

Grouping: a category with enough unused videos makes a themed marathon ("9 Hours of Space Facts to Fall
Asleep To | Saturn, The Moon & Jupiter"); otherwise the newest unused videos make a "Random Facts" one.
A video goes into one marathon at most, and the workflow runs weekly, so compilations stay a small
share of the channel (reused-content policy).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from sof import seo

log = logging.getLogger("sof.marathon")

MIX = "mix"
MIX_PROFILE = seo.Profile("Random Facts", "Marathons: Hours of Facts to Fall Asleep To", "sleepfacts", "Documentary")
MARATHON_PLAYLIST = "Marathons: Hours of Facts to Fall Asleep To"


def run_id(row: dict) -> str:
    m = re.search(r"/actions/runs/(\d+)", str(row.get("run_url") or ""))
    return m.group(1) if m else ""


def compiled_ids(rows: list[dict]) -> set[str]:
    return {v for r in rows if r.get("kind") == "marathon" for v in (r.get("segments") or [])}


def candidates(rows: list[dict], available_runs: set[str]) -> list[dict]:
    """Daily videos (newest first) whose media artifact still exists and that no marathon has used."""
    used = compiled_ids(rows)
    out = [r for r in rows if r.get("kind", "video") == "video" and not r.get("abandoned") and r.get("video_id")
           and r["video_id"] not in used and run_id(r) in available_runs]
    return sorted(out, key=lambda r: str(r.get("date") or ""), reverse=True)


def choose_group(cands: list[dict], *, size: int = 3, group: str = "auto") -> tuple[str, list[dict]]:
    """(category or MIX, segments in publish order). ``group`` = auto | mix | a category name."""
    if group not in ("auto", MIX):
        pick = [r for r in cands if r.get("category") == group][:size]
        return (group, sorted(pick, key=lambda r: str(r.get("date")))) if len(pick) == size else (group, [])
    if group == "auto":
        by_cat: dict[str, list[dict]] = {}
        for r in cands:
            by_cat.setdefault(str(r.get("category") or ""), []).append(r)
        full = [(c, rs[:size]) for c, rs in by_cat.items() if c and len(rs) >= size]
        if full:                                   # the category whose newest video is newest
            c, rs = max(full, key=lambda x: str(x[1][0].get("date")))
            return c, sorted(rs, key=lambda r: str(r.get("date")))
    pick = cands[:size]
    return (MIX, sorted(pick, key=lambda r: str(r.get("date")))) if len(pick) == size else (MIX, [])


def profile_for(category: str) -> seo.Profile:
    return MIX_PROFILE if category == MIX else seo.profile(category)


def _names(segments: list[dict]) -> list[str]:
    return [seo._clean(str(s.get("topic") or "")) for s in segments]


def _join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " & " + names[-1]


def build_title(category: str, segments: list[dict], hours: int) -> str:
    p = profile_for(category)
    h = seo.hours_label(hours)
    for t in (f"{h} of {p.phrase} to Fall Asleep To | {_join_names(_names(segments))}",
              f"{h} of {p.phrase} to Fall Asleep To | {len(segments)} Videos Back to Back"):
        if len(t) <= 100:
            return t
    return t[:100]


def offset_chapters(parts: list[tuple[str, float, list[dict]]]) -> list[dict]:
    """[(topic, duration seconds, chapters [{start, title}])] → one chapter list for the joined video.
    Each part's first chapter is named after its subject so the seams are visible."""
    out, offset = [], 0.0
    for topic, duration, chapters in parts:
        rows = sorted(chapters or [{"start": 0, "title": topic}], key=lambda c: float(c.get("start") or 0))
        for k, c in enumerate(rows):
            title = re.sub(r"^\d+[.)]\s*", "", str(c.get("title") or "").strip())
            out.append({"start": offset + float(c.get("start") or 0),
                        "title": f"{topic}: {title}" if k == 0 and title and title.lower() != topic.lower() else (title or topic)})
        offset += float(duration)
    return out


def build_description(category: str, segments: list[dict], hours: int, chapters: list[dict], *,
                      thumb_credits: list[str] | None = None) -> str:
    p = profile_for(category)
    names = _names(segments)
    head = (f"Fall asleep to {hours} hours of {p.phrase.lower()}: {_join_names(names)}, back to back, "
            f"with no breaks between them. Each part is a full Sleep On Facts video, narrated slowly from "
            f"Wikipedia research over real photographs.")
    lines = seo.chapter_lines(chapters)
    originals = "The original videos:\n" + "\n".join(f"- {n}: https://youtu.be/{s['video_id']}" for n, s in zip(names, segments))
    from sof import captions

    parts = [head, ("Chapters\n" + "\n".join(lines)) if lines else "", originals,
             "Facts in these videos are drawn from Wikipedia (CC BY-SA 4.0); each original video lists its sources.",
             captions.DESCRIPTION_LINE, *[c for c in (thumb_credits or []) if c],
             " ".join(["#sleep", f"#{p.hashtag}", "#sleepdocumentary"])]
    text = "\n\n".join(x for x in parts if x)
    while len(text.encode("utf-8")) > 4900 and lines:
        lines = lines[: max(3, len(lines) * 3 // 4)] if len(lines) > 3 else []
        parts[1] = ("Chapters\n" + "\n".join(lines)) if lines else ""
        text = "\n\n".join(x for x in parts if x)
    return text.encode("utf-8")[:4900].decode("utf-8", "ignore")


def build_tags(category: str, segments: list[dict]) -> list[str]:
    p = profile_for(category)
    extra = [f"{seo._clean(str(s.get('topic'))).lower()} facts" for s in segments]
    return seo.build_tags(" & ".join(_names(segments)), category if category != MIX else "", [
        "sleep marathon", f"{p.phrase.lower()} for sleep", "long sleep documentary", "hours of facts", *extra])


def concat_cmd(list_file: str, out: str) -> list[str]:
    """Join MP4s with identical encoder settings (the daily renders) without re-encoding."""
    return ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy",
            "-movflags", "+faststart", "-map_metadata", "-1", out]


def write_concat_list(paths: list[str], list_file: str) -> str:
    Path(list_file).write_text("".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in paths), encoding="utf-8")
    return list_file


def probe_seconds(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True, timeout=120, check=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def thumb_credit_from(description: str) -> str:
    return next((l.strip() for l in str(description or "").splitlines() if l.startswith("Thumbnail photo:")), "")


# ------------------------------------------------------------------ GitHub artifacts (gh CLI)

def available_runs(repo: str, rows: list[dict], *, gh=None) -> set[str]:
    """Run ids (of these rows) whose ``media-<run id>`` artifact exists and has not expired."""
    gh = gh or _gh
    out = set()
    for r in rows:
        rid = run_id(r)
        if not rid:
            continue
        try:
            data = json.loads(gh("api", f"repos/{repo}/actions/artifacts?name=media-{rid}&per_page=5"))
        except Exception as exc:  # noqa: BLE001
            log.warning("artifact lookup failed for run %s: %s", rid, exc)
            continue
        if any(not a.get("expired") for a in data.get("artifacts") or []):
            out.add(rid)
    return out


def download_media(rid: str, dest: Path, *, gh=None) -> Path:
    """Download + extract ``media-<rid>``; returns the folder holding final.mp4."""
    gh = gh or _gh
    dest.mkdir(parents=True, exist_ok=True)
    gh("run", "download", rid, "-n", f"media-{rid}", "-D", str(dest))
    hits = list(dest.rglob("final.mp4"))
    if not hits:
        raise RuntimeError(f"media-{rid} has no final.mp4")
    return hits[0].parent


def _gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {r.stderr.strip()[:300]}")
    return r.stdout
