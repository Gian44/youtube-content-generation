"""Slow moving footage between the photos (Gian, 2026-10-25, after studying The Sleepy Scientist).

The channels that lead this niche run moving, vivid footage under the narration; ours was a slideshow of
dimmed stills. This module finds free stock clips for the subject (Pexels Videos and Pixabay Videos, both
free to use without attribution), checks each clip's preview frame with the same vision labels as the
photos (subject / setting / reject), downloads the keepers and hands their paths to the timeline, which
gives some filler slots (slots where no named thing is on screen) to a clip instead of a photo.

The timeline renderer (sof/timeline.py) treats a ``.mp4`` slot as moving footage: the clip is pre-cut once
to the output size and frame rate, slowed (``slow``), and looped if it runs short.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger("sof.footage")

VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".m4v")
MIN_SECONDS = 8            # shorter clips loop too visibly once slowed
MAX_CLIP_BYTES = 120_000_000
PREVIEW_MIN_BYTES = 2_000


def is_video(path: str) -> bool:
    return str(path).lower().endswith(VIDEO_SUFFIXES)


# ------------------------------------------------------------------ providers

def _best_pexels_file(files: list[dict], *, min_width: int = 1280, max_width: int = 1920) -> dict | None:
    mp4 = [f for f in files or [] if str(f.get("file_type")) == "video/mp4" and f.get("link")
           and int(f.get("width") or 0) >= int(f.get("height") or 1)]
    good = [f for f in mp4 if min_width <= int(f.get("width") or 0) <= max_width]
    pool = good or [f for f in mp4 if int(f.get("width") or 0) >= min_width]
    return min(pool, key=lambda f: abs(int(f.get("width") or 0) - max_width)) if pool else None


def pexels_videos(c: httpx.Client, key: str, query: str, *, per_page: int = 20) -> list[dict]:
    if not key:
        return []
    r = c.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
              params={"query": query, "orientation": "landscape", "size": "medium", "per_page": per_page})
    if r.status_code != 200:
        log.warning("pexels videos %r: HTTP %s", query, r.status_code)
        return []
    out = []
    for v in r.json().get("videos") or []:
        f = _best_pexels_file(v.get("video_files") or [])
        pics = [p.get("picture") for p in v.get("video_pictures") or [] if p.get("picture")]
        if not f or not pics or float(v.get("duration") or 0) < MIN_SECONDS:
            continue
        out.append({"id": f"pexels-{v.get('id')}", "url": f["link"], "preview": pics[len(pics) // 2],
                    "seconds": float(v.get("duration") or 0), "source": "pexels", "license": "Pexels License",
                    "page": str(v.get("url") or ""), "artist": str((v.get("user") or {}).get("name") or ""),
                    "desc": str(v.get("url") or "").rstrip("/").rsplit("/", 1)[-1].replace("-", " "), "query": query})
    return out


def pixabay_videos(c: httpx.Client, key: str, query: str, *, per_page: int = 20) -> list[dict]:
    if not key:
        return []
    r = c.get("https://pixabay.com/api/videos/", params={"key": key, "q": query, "per_page": per_page,
                                                        "safesearch": "true", "video_type": "all"})
    if r.status_code != 200:
        log.warning("pixabay videos %r: HTTP %s", query, r.status_code)
        return []
    out = []
    for h in r.json().get("hits") or []:
        vids = h.get("videos") or {}
        f = next((vids[k] for k in ("large", "medium") if (vids.get(k) or {}).get("url")
                  and int(vids[k].get("width") or 0) >= 1280), None)
        if not f or float(h.get("duration") or 0) < MIN_SECONDS or int(f.get("width") or 0) < int(f.get("height") or 1):
            continue
        out.append({"id": f"pixabay-{h.get('id')}", "url": f["url"], "preview": f.get("thumbnail") or "",
                    "seconds": float(h.get("duration") or 0), "source": "pixabay", "license": "Pixabay Content License",
                    "page": str(h.get("pageURL") or ""), "artist": str(h.get("user") or ""),
                    "desc": str(h.get("tags") or ""), "query": query})
    return [o for o in out if o["preview"]]


def search(queries: list[str], *, pexels_key: str, pixabay_key: str, limit: int = 80, transport=None) -> list[dict]:
    """Candidate clips across both providers, interleaved query by query, de-duplicated."""
    out: list[dict] = []
    seen: set[str] = set()
    with httpx.Client(timeout=30.0, transport=transport, follow_redirects=True) as c:
        for q in [q for q in dict.fromkeys(q.strip() for q in queries) if q]:
            for fetch, key in ((pexels_videos, pexels_key), (pixabay_videos, pixabay_key)):
                try:
                    found = fetch(c, key, q)
                except httpx.HTTPError as exc:
                    log.warning("footage search %r failed: %s", q, exc)
                    found = []
                for it in found:
                    if it["id"] not in seen:
                        seen.add(it["id"])
                        out.append(it)
            if len(out) >= limit:
                break
    return out[:limit]


# ------------------------------------------------------------------ download + vision check

def _get(c: httpx.Client, url: str, dest: Path, *, max_bytes: int, deadline: float = 120.0) -> bool:
    try:
        with c.stream("GET", url) as r:
            if r.status_code != 200 or int(r.headers.get("content-length") or 0) > max_bytes:
                return False
            t0, size = time.monotonic(), 0
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes or time.monotonic() - t0 > deadline:
                        f.close()
                        dest.unlink(missing_ok=True)
                        return False
                    f.write(chunk)
        return dest.stat().st_size > PREVIEW_MIN_BYTES
    except (httpx.HTTPError, OSError) as exc:
        log.warning("footage download %s failed: %s", url, exc)
        dest.unlink(missing_ok=True)
        return False


def gather(candidates: list[dict], out_dir: str, *, classify_fn, want: int = 30, transport=None,
           budget_seconds: float = 600.0) -> list[str]:
    """Vision-check each candidate's preview frame, download the keepers' clips; returns clip paths
    (subject clips first). ``classify_fn(paths) -> {path: label}``. Writes ``footage.json`` beside them."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    rows: list[dict] = []
    with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True) as c:
        previews: dict[str, dict] = {}
        for it in candidates:
            if time.monotonic() - t0 > budget_seconds / 3:
                break
            p = out / f"{hashlib.sha1(it['id'].encode()).hexdigest()[:12]}.jpg"
            if p.exists() or _get(c, it["preview"], p, max_bytes=5_000_000, deadline=20):
                previews[str(p)] = it
        labels = classify_fn(list(previews)) if previews else {}
        keep = [(lab, path) for path, lab in labels.items() if lab in ("subject", "setting", "unknown")]
        keep.sort(key=lambda x: {"subject": 0, "unknown": 1, "setting": 2}[x[0]])
        for lab, preview in keep:
            if len(rows) >= want or time.monotonic() - t0 > budget_seconds:
                break
            it = previews[preview]
            clip = Path(preview).with_suffix(".mp4")
            if clip.exists() or _get(c, it["url"], clip, max_bytes=MAX_CLIP_BYTES):
                rows.append({"file": clip.name, "label": lab, **{k: it[k] for k in ("id", "source", "license", "page",
                                                                                   "artist", "seconds", "query")}})
    (out / "footage.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    log.info("footage: %d clips kept of %d candidates (%d previews checked) in %.0f s", len(rows), len(candidates),
             len(previews), time.monotonic() - t0)
    return [str((out / r["file"]).resolve()) for r in rows]


# ------------------------------------------------------------------ slot mixing (pure)

def mix_into_slots(slots: list[str], plan: list[dict] | None, clips: list[str], *, every: int = 3,
                   max_uses: int = 3, gap: int = 8) -> tuple[list[str], int]:
    """Give some filler slots to footage. Returns (new slots, number of footage slots).

    Only slots where no named thing is on screen are eligible (``plan[k]["entity"]`` empty), never slot 0
    (the video opens on its subject photo). Every ``every``-th eligible slot takes the next clip that has
    been used fewer than ``max_uses`` times and not in the last ``gap`` slots; a clip never sits next to
    itself, and a slot whose neighbours are footage stays a photo, so stills and motion alternate.
    """
    if not clips or every < 1:
        return list(slots), 0
    out = list(slots)
    uses = {c: 0 for c in clips}
    last_at: dict[str, int] = {}
    eligible = 0
    placed = 0
    ci = 0
    for k in range(1, len(out)):
        row = plan[k] if plan and k < len(plan) else {}
        if row.get("entity"):
            continue
        eligible += 1
        if eligible % every:
            continue
        if is_video(out[k - 1]):
            continue
        for step in range(len(clips)):
            c = clips[(ci + step) % len(clips)]
            if uses[c] < max_uses and k - last_at.get(c, -10 ** 9) > gap:
                out[k] = c
                uses[c] += 1
                last_at[c] = k
                ci = (ci + step + 1) % len(clips)
                placed += 1
                break
    return out, placed
