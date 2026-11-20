"""Slow, calm real footage of the subject between the photos.

2026-10-25 (Gian, after studying The Sleepy Scientist): the channels that lead this niche run moving footage
under the narration; ours was a slideshow of dimmed stills, so some filler slots got free stock clips (Pexels
Videos / Pixabay Videos, preview frame vision-checked).

Felix (Puello Media) 2026-11: "use video footage, not still images — YouTube treats image-heavy videos as
slideshows; real footage of the actual subject should change behind the narration". The 2026-11-19 Solar
System video had 30 clips but showed one of them in 394 slots (they only went into the few slots with no
named thing on screen): it was a slideshow of 226 photos. Gian's decisions for this channel:

* footage of the NAMED subject, mixed with the photos (photos keep every slot without a good clip);
* sources in this order: the NASA Image and Video Library (space topics), Wikimedia Commons video, then
  Pexels / Pixabay video only when the clip's own metadata names the subject (``images.mentions``);
* the photos' vision check (subject / setting / reject; only "subject" is kept), on frames from the part of
  the clip that will be shown;
* slow footage only — no fast cuts, no bright flashes — it is a sleep channel;
* every clip's author and licence on the video's credits (``credits_by_path``).

How one clip gets on screen:

1. ``search_nasa`` / ``search_commons`` / ``search(terms=…)`` → candidates, each with a low-resolution
   ``probe`` rendition and the full-resolution ``url`` (+ ``fallback``).
2. ``probe_clip``: the low-res rendition is downloaded and decoded at 64×36 grey, 8 frames a second
   (``frame_stats``); ``calm_window`` finds the longest stretch with no cut, no flash, no busy motion, no black
   and no frozen picture, long enough to fill a slot without looping at a gentle slow-down.
3. Two frames from that window, stacked into one tile, go to the vision check (``CLIP_NOTE``).
4. ``cut_window``: ffmpeg reads only the window from the full-resolution URL → ``<name>.mp4`` with
   ``footage.json`` beside it (credit fields + the window's ``seconds`` and ``slow``; ``window``).
5. ``mix_into_slots`` (pure): a thing's clips in its own slots, the subject's clips in the other slots,
   alternating with the photos.

The timeline (sof/timeline.py) and the Shorts (sof/shorts.py) cut each clip with its own ``window``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import httpx

from sof import images

log = logging.getLogger("sof.footage")

VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".m4v")
MIN_SECONDS = 8            # shorter clips loop too visibly once slowed
MAX_CLIP_BYTES = 120_000_000
PREVIEW_MIN_BYTES = 2_000
PROBE_MAX_BYTES = 80_000_000   # the low-res rendition that is analysed (a 2-hour lecture is not footage anyway)
MIN_HEIGHT = 480           # the full-res rendition: anything smaller looks soft at 1080p
MIN_ASPECT = 1.3           # landscape only: a square or portrait clip cover-cropped to 16:9 loses its subject
SUBJECT = "subject"        # the folder (and group key) of the video's own subject's clips

NASA_SEARCH = images.NASA_SEARCH
NASA_MANIFEST = "https://images-api.nasa.gov/asset/{id}"

# calm check (measured 2026-11-21 on synthetic pans: a pan of p % of the frame width per second gives a
# gradient-normalised motion of about p × 0.08 here, on bright texture and on a dark planet alike)
PROBE_FPS = 8
PROBE_W, PROBE_H = 64, 36
ANALYSE_SECONDS = 180      # the first 3 minutes are plenty to find one calm stretch
CUT, CUT_RATIO = 1.5, 4.0  # a cut: a jump far above both this and 4× the motion around it
FLASH = 10.0               # mean-luma jump (0-255) between two samples 1/8 s apart: flash, fade or cut
BUSY = 0.48                # motion over 1 s above ~6 % of the frame width per second (before the slow-down)
FROZEN = 0.1               # mean raw change below this: a still picture or a title card, not footage
DARK = 4.0                 # a black frame
EDGE = 0.5                 # seconds kept clear of a cut at either end of the window

# 2026-11-21, measured from Gian's PC: upload.wikimedia.org (Commons video files and transcodes) answers 429
# "Retry-After: 11" after ~10 quick requests and at ~1 request a second, while one every 1.5-2 s ran 180+ requests
# without one (4 probers + 3 cutters lost most Commons clips to it). Every probe download and window cut there
# shares this pacing; one ffmpeg cut of a .webm makes ~4 ranged requests, of an .ogv original ~10 (Ogg seeking).
WIKIMEDIA_HOST = "upload.wikimedia.org"
WIKIMEDIA_GAP = 2.0
CHECK_SHEET = 12           # tiles per vision ask (vision.classify's contact sheet)

CLIP_NOTE = ("Each tile shows two frames (top and bottom) from ONE short video clip. Label the clip as a whole: "
             '"subject" only when both frames show the subject itself (real footage, or a clean animation or '
             'rendering of it); "reject" when either frame is a title card, captions or other text, a logo, a person '
             "talking to the camera, an interview or studio, a screen recording, or something else.\n")


def is_video(path: str) -> bool:
    return str(path).lower().endswith(VIDEO_SUFFIXES)


# ------------------------------------------------------------------ providers

def _best_pexels_file(files: list[dict], *, min_width: int = 1280, max_width: int = 1920) -> dict | None:
    mp4 = [f for f in files or [] if str(f.get("file_type")) == "video/mp4" and f.get("link")
           and int(f.get("width") or 0) >= int(f.get("height") or 1)]
    good = [f for f in mp4 if min_width <= int(f.get("width") or 0) <= max_width]
    pool = good or [f for f in mp4 if int(f.get("width") or 0) >= min_width]
    return min(pool, key=lambda f: abs(int(f.get("width") or 0) - max_width)) if pool else None


def _small_pexels_file(files: list[dict]) -> dict | None:
    mp4 = [f for f in files or [] if str(f.get("file_type")) == "video/mp4" and f.get("link")
           and int(f.get("width") or 0) >= 480]
    return min(mp4, key=lambda f: int(f.get("width") or 0)) if mp4 else None


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
        small = _small_pexels_file(v.get("video_files") or []) or f
        desc = str(v.get("url") or "").rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        out.append({"id": f"pexels-{v.get('id')}", "url": f["link"], "probe": small["link"], "preview": pics[len(pics) // 2],
                    "seconds": float(v.get("duration") or 0), "source": "pexels", "license": "Pexels License",
                    "page": str(v.get("url") or ""), "artist": str((v.get("user") or {}).get("name") or ""),
                    "desc": desc, "title": desc[:120], "query": query})
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
        small = next((vids[k] for k in ("tiny", "small") if (vids.get(k) or {}).get("url")), f)
        out.append({"id": f"pixabay-{h.get('id')}", "url": f["url"], "probe": small["url"], "preview": f.get("thumbnail") or "",
                    "seconds": float(h.get("duration") or 0), "source": "pixabay", "license": "Pixabay Content License",
                    "page": str(h.get("pageURL") or ""), "artist": str(h.get("user") or ""),
                    "desc": str(h.get("tags") or ""), "title": str(h.get("tags") or "")[:120], "query": query})
    return [o for o in out if o["preview"]]


def search(queries: list[str], *, pexels_key: str, pixabay_key: str, limit: int = 80, transport=None,
           terms: list[str] | None = None) -> list[dict]:
    """Candidate clips across both providers, interleaved query by query, de-duplicated. With ``terms`` (the
    subject's name terms, ``images.name_terms``) only clips whose own metadata names it are kept — Pexels: the
    words of the page URL, Pixabay: the tags (Gian, 2026-11: stock footage only when it names the subject)."""
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
                    if it["id"] not in seen and (not terms or images.mentions(it.get("desc", ""), terms)):
                        seen.add(it["id"])
                        out.append(it)
            if len(out) >= limit:
                break
    return out[:limit]


def _https(href: str) -> str:
    """NASA's asset manifest lists plain-http URLs with raw spaces in them."""
    href = str(href or "").strip().replace(" ", "%20")
    return "https://" + href[len("http://"):] if href.startswith("http://") else href


def search_nasa(c: httpx.Client, query: str, terms: list[str], *, limit: int = 12) -> list[dict]:
    """NASA Image and Video Library videos for ``query`` whose title, description or keywords name the subject
    (``terms``). Public domain, credited "NASA/<center>" (+ the named creator); third-party material (©) is
    skipped, as for the photos. The renditions are looked up later (``nasa_renditions``)."""
    r = c.get(NASA_SEARCH, params={"q": query, "media_type": "video", "page_size": max(20, limit * 3)})
    if r.status_code != 200:
        log.warning("nasa videos %r: HTTP %s", query, r.status_code)
        return []
    out = []
    for it in ((r.json().get("collection") or {}).get("items") or []):
        d = (it.get("data") or [{}])[0]
        nid = str(d.get("nasa_id") or "")
        title = str(d.get("title") or nid)
        text = " ".join([title, str(d.get("description") or ""), " ".join(map(str, d.get("keywords") or []))])
        if not nid or "©" in text or "copyright" in text.lower() or (terms and not images.mentions(text, terms)):
            continue
        center = str(d.get("center") or "").strip()
        artist = f"NASA/{center}" if center else "NASA"
        if str(d.get("secondary_creator") or "").strip():
            artist += f" / {str(d['secondary_creator']).strip()}"
        out.append({"id": f"nasa-{nid}", "nasa_id": nid, "source": "nasa", "title": title[:120], "artist": artist[:160],
                    "license": "NASA (public domain)", "page": f"https://images.nasa.gov/details/{quote(nid)}",
                    "desc": text[:400], "query": query,
                    "preview": _https(next((ln.get("href") for ln in it.get("links") or [] if ln.get("href")), ""))})
        if len(out) >= limit:
            break
    return out


def nasa_renditions(c: httpx.Client, nasa_id: str) -> dict | None:
    """``{"probe", "url", "fallback"}`` from the asset manifest: the small rendition to analyse, then the large
    one (else the original, else the medium) to show. None when the video has no usable mp4."""
    r = c.get(NASA_MANIFEST.format(id=quote(nasa_id)))
    if r.status_code != 200:
        return None
    mp4 = {}
    for it in ((r.json().get("collection") or {}).get("items") or []):
        m = re.search(r"~(\w+)\.mp4$", str(it.get("href") or ""), re.I)
        if m:
            mp4.setdefault(m.group(1).lower(), _https(it["href"]))
    probe = next((mp4[k] for k in ("small", "preview", "mobile", "medium") if k in mp4), None)
    show = [mp4[k] for k in ("large", "orig", "medium") if k in mp4]
    if not probe or not show:
        return None
    return {"probe": probe, "url": show[0], "fallback": show[1:]}


def _height(d: dict) -> int:
    h = d.get("height")
    if str(h or "").isdigit():
        return int(h)
    m = re.search(r"(\d{3,4})p", f"{d.get('transcodekey') or ''} {d.get('shorttitle') or ''} {d.get('title') or ''}", re.I)
    return int(m.group(1)) if m else 0


def commons_video(page: dict, *, query: str, terms: list[str]) -> dict | None:
    """One Commons ``videoinfo`` page → a candidate, or None (unfree, too small, portrait, too short, a map or
    diagram, or not naming the subject). Credited like the photos: ``source`` commons + artist + licence + file page,
    so ``captions.needs_credit`` decides about CC BY / BY-SA the same way."""
    vi = (page.get("videoinfo") or [None])[0]
    title = str(page.get("title") or "")
    if not vi or page.get("missing"):
        return None
    w, h = int(vi.get("width") or 0), int(vi.get("height") or 0)
    if h < MIN_HEIGHT or w / max(1, h) < MIN_ASPECT or float(vi.get("duration") or 0) < MIN_SECONDS:
        return None
    md = vi.get("extmetadata") or {}
    lic = images._meta(md, "LicenseShortName")
    if not images.license_ok(lic, images._meta(md, "NonFree")) or images._BAD_FILE.search(images._norm(title)):
        return None
    desc = f"{title.removeprefix('File:')} {images._plain(images._meta(md, 'ImageDescription'))[:300]}"
    if terms and not images.mentions(desc, terms):
        return None
    derivs = [d for d in vi.get("derivatives") or [] if d.get("src") and _height(d)]

    def rank(d: dict) -> tuple:
        return ("vp9" not in str(d.get("type") or "").lower(), -_height(d))

    show = [d["src"] for d in sorted((d for d in derivs if MIN_HEIGHT <= _height(d) <= 1080), key=rank)]
    if vi.get("url") and vi["url"] not in show and h <= 1440:
        show.append(vi["url"])                    # the original, if no transcode is big enough
    small = sorted((d for d in derivs if 240 <= _height(d) <= MIN_HEIGHT),
                   key=lambda d: (abs(_height(d) - 360), "vp9" not in str(d.get("type") or "").lower()))
    probe = small[0]["src"] if small else (show[-1] if show else "")
    if not show or not probe:
        return None
    artist = images._plain(images._meta(md, "Artist"))[:160] or "Unknown author"
    return {"id": f"commons-{title}", "source": "commons", "title": re.sub(r"\.\w{2,4}$", "", title.removeprefix("File:"))[:120],
            "artist": artist, "license": lic, "page": str(vi.get("descriptionurl") or ""), "desc": desc[:400],
            "query": query, "probe": probe, "url": show[0], "fallback": show[1:], "seconds": float(vi.get("duration") or 0),
            "width": w, "height": h, "preview": str(vi.get("thumburl") or "")}


def search_commons(c: httpx.Client, query: str, terms: list[str], *, limit: int = 12) -> list[dict]:
    """Free Wikimedia Commons videos for ``query`` (file search, ``filetype:video``), in search order."""
    data = images._api(c, images.COMMONS_API, action="query", generator="search", gsrsearch=f"{query} filetype:video",
                       gsrnamespace=6, gsrlimit=min(50, limit * 3), prop="videoinfo",
                       viprop="url|size|mediatype|extmetadata|derivatives", viurlwidth=640,
                       viextmetadatafilter="LicenseShortName|Artist|NonFree|ImageDescription")
    pages = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
    return [x for x in (commons_video(p, query=query, terms=terms) for p in pages) if x][:limit]


# ------------------------------------------------------------------ calm check

def frame_stats(path: str, *, seconds: float = ANALYSE_SECONDS) -> tuple[list[float], list[float], list[float]]:
    """Per sample (8 a second, 64×36 grey): mean luma, mean absolute change from the previous sample, mean
    spatial gradient. One low-resolution decode of the first ``seconds``."""
    import numpy as np

    r = subprocess.run(["ffmpeg", "-v", "error", "-t", f"{seconds:g}", "-i", path, "-an", "-vf",
                        f"fps={PROBE_FPS},scale={PROBE_W}:{PROBE_H}:flags=area,format=gray", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
    n = len(r.stdout) // (PROBE_W * PROBE_H)
    if n < 2:
        return [], [], []
    f = np.frombuffer(r.stdout[:n * PROBE_W * PROBE_H], np.uint8).reshape(n, PROBE_H, PROBE_W).astype(np.float32)
    luma = f.mean(axis=(1, 2))
    diff = np.concatenate([[0.0], np.abs(f[1:] - f[:-1]).mean(axis=(1, 2))])
    grad = (np.abs(np.diff(f, axis=2)).mean(axis=(1, 2)) + np.abs(np.diff(f, axis=1)).mean(axis=(1, 2))) / 2
    return luma.tolist(), diff.tolist(), grad.tolist()


def calm_window(luma: list[float], diff: list[float], grad: list[float], *, fps: float = PROBE_FPS, need: float,
                keep: float) -> tuple[float, float] | None:
    """``(start, length)`` in seconds of the longest calm stretch, at least ``need`` and at most ``keep`` long,
    or None. Pure.

    Motion is the change between samples divided by the picture's own gradient, so a dark planet on black and
    a bright landscape moving at the same speed score alike. A sample is out when it is a cut (motion far above
    ``CUT`` and ``CUT_RATIO``× its neighbours), a flash or fade (luma jumps more than ``FLASH``), black, or in a
    second whose average motion is above ``BUSY``. A stretch whose picture hardly changes at all (``FROZEN``)
    is a still or a title card, not footage. ``EDGE`` seconds stay clear of the stretch's ends."""
    n = min(len(luma), len(diff), len(grad))
    if n < 2:
        return None
    motion = [0.0] + [diff[i] / max(grad[i], grad[i - 1], 1.0) for i in range(1, n)]
    bad = [luma[i] < DARK for i in range(n)]
    for i in range(1, n):
        around = sorted(motion[max(1, i - 8):i] + motion[i + 1:i + 9])
        local = around[len(around) // 2] if around else 0.0
        if motion[i] > max(CUT, CUT_RATIO * local) or abs(luma[i] - luma[i - 1]) > FLASH:
            bad[i] = bad[i - 1] = True
    half = max(1, int(round(fps)) // 2)
    for i in range(n):
        span = motion[max(1, i - half):i + half + 1]
        if span and sum(span) / len(span) > BUSY:
            bad[i] = True
    best: tuple[float, float] | None = None
    i = 0
    while i < n:
        if bad[i]:
            i += 1
            continue
        j = i
        while j < n and not bad[j]:
            j += 1
        length = (j - i) / fps - 2 * EDGE
        if length >= need and sum(diff[i + 1:j]) / max(1, j - i - 1) >= FROZEN and (best is None or length > best[1]):
            best = (i / fps + EDGE, length)
        i = j
    return (round(best[0], 3), round(min(best[1], keep), 3)) if best else None


def slow_for(seconds: float, *, slot: float, slow: float, max_slow: float) -> float | None:
    """The slow-down that lets ``seconds`` of footage fill one ``slot`` (dwell + crossfade) without looping: the
    channel's ``slow`` when the window is long enough, more for a shorter one, None beyond ``max_slow``."""
    s = max(slow, (slot + 0.5) / max(seconds, 1e-6))
    return round(s, 3) if s <= max_slow + 1e-9 else None


# ------------------------------------------------------------------ probe, check frames, cut

def _probe(path: str) -> tuple[int, int, float]:
    """(width, height, duration s) of the first video stream; zeros when unreadable."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height:format=duration", "-of", "json", path], capture_output=True, text=True,
                       timeout=60, stdin=subprocess.DEVNULL)
    try:
        info = json.loads(r.stdout or "{}")
        st = (info.get("streams") or [{}])[0]
        return int(st.get("width") or 0), int(st.get("height") or 0), float((info.get("format") or {}).get("duration") or 0)
    except (ValueError, TypeError):
        return 0, 0, 0.0


def check_strip(clip: str, start: float, length: float, out: str, *, width: int = 480) -> str | None:
    """Two frames of the window (at ¼ and ¾ of it), stacked into one image for the vision check."""
    from PIL import Image

    frames = []
    for k, t in enumerate((start + length * 0.25, start + length * 0.75)):
        f = f"{out}.{k}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", clip, "-frames:v", "1", "-vf",
                        f"scale={width}:-2", "-q:v", "3", f], capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        if not os.path.exists(f):
            return None
        frames.append(f)
    try:
        ims = [Image.open(f).convert("RGB") for f in frames]
        sheet = Image.new("RGB", (width, sum(im.height for im in ims) + 8), (0, 0, 0))
        sheet.paste(ims[0], (0, 0))
        sheet.paste(ims[1], (0, ims[0].height + 8))
        sheet.save(out, "JPEG", quality=85)
    finally:
        for f in frames:
            Path(f).unlink(missing_ok=True)
    return out


def _stem(cand: dict) -> str:
    return hashlib.sha1(str(cand["id"]).encode("utf-8")).hexdigest()[:12]


def probe_clip(c: httpx.Client, cand: dict, folder: Path, *, need: float, keep: float) -> dict | None:
    """Download the low-res rendition, find its calm window and draw the check tile. Returns the candidate with
    ``skip`` / ``seconds`` (the window) and ``check`` (the tile), or None. The download is deleted either way."""
    if cand.get("source") == "nasa" and not cand.get("probe"):
        found = nasa_renditions(c, str(cand["nasa_id"]))
        if not found:
            return None
        cand = {**cand, **found}
    stem = _stem(cand)
    low = folder / f"{stem}.probe{Path(cand['probe'].split('?')[0]).suffix or '.mp4'}"
    try:
        if not _get(c, cand["probe"], low, max_bytes=PROBE_MAX_BYTES, deadline=120):
            return None
        w, h, _ = _probe(str(low))
        if not h or w / h < MIN_ASPECT:
            return None
        win = calm_window(*frame_stats(str(low)), need=need, keep=keep)
        if not win:
            return None
        tile = check_strip(str(low), win[0], win[1], str(folder / f"{stem}.check.jpg"))
        return {**cand, "skip": win[0], "seconds": win[1], "check": tile} if tile else None
    finally:
        low.unlink(missing_ok=True)


_pace_lock = threading.Lock()
_pace_at = [0.0]


def _pace(url: str, requests: int = 1) -> None:
    """Wait for ``requests`` turns at upload.wikimedia.org (``WIKIMEDIA_GAP`` apart, shared by every thread);
    any other host goes at once."""
    if WIKIMEDIA_HOST not in str(url):
        return
    with _pace_lock:
        now = time.monotonic()
        start = max(now, _pace_at[0])
        _pace_at[0] = start + WIKIMEDIA_GAP * requests
    time.sleep(start - now)


def _retry_after(headers) -> float:
    wait = str(headers.get("retry-after") or "")
    return min(30.0, float(wait)) if wait.isdigit() else 11.0


# Square pixels, at most 1080 lines, even sizes. 2026-11-21: NASA's ~orig renditions of film transfers are often
# anamorphic (720x486 at SAR 6:5 = 16:9), and the timeline's cover-crop ignores SAR, so they came out squeezed.
CUT_SCALE = "scale='trunc(min(1080,ih)*dar/2)*2':'trunc(min(1080,ih)/2)*2',setsar=1"


def cut_window(cand: dict, dst: Path, *, timeout: float = 300.0) -> bool:
    """ffmpeg reads only the window (``skip``, ``seconds``) from the full-resolution URL (ranged requests),
    scaled to at most 1080 lines, silent → ``dst``. Tries ``url`` then ``fallback``; ``timeout`` bounds all the
    attempts together (2026-11-21: it was per URL, so a slow server could hold one cut for 3-5× the budget).
    A Wikimedia 429 is waited out once (``Retry-After``, ~11 s) before the same URL is tried again."""
    part = dst.with_name(dst.stem + ".part.mp4")
    end = time.monotonic() + timeout
    for url in [cand["url"], *(cand.get("fallback") or [])]:
        for attempt in range(2):
            _pace(url, 10 if str(url).split("?")[0].lower().endswith((".ogv", ".ogg")) else 4)
            left = end - time.monotonic()
            if left <= 0:
                part.unlink(missing_ok=True)
                return False
            src = ["-user_agent", images.UA, "-rw_timeout", "30000000"] if str(url).startswith("http") else []
            cmd = ["ffmpeg", "-y", "-v", "error", *src, "-ss", f"{float(cand['skip']):.3f}", "-i", url,
                   "-t", f"{float(cand['seconds']):.3f}", "-an", "-vf", CUT_SCALE, "-c:v", "libx264",
                   "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-map_metadata", "-1", str(part)]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=left, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                log.warning("footage cut timed out: %s", url)
                break
            w, h, dur = _probe(str(part)) if r.returncode == 0 and part.exists() else (0, 0, 0.0)
            if h >= MIN_HEIGHT and dur >= float(cand["seconds"]) - 0.3:
                os.replace(part, dst)
                return True
            if attempt == 0 and "429 Too Many Requests" in (r.stderr or ""):
                time.sleep(11.0)
                continue
            log.warning("footage cut of %s unusable (%s, %dx%d): %s", cand.get("id"), r.returncode, w, h,
                        (r.stderr or "")[-200:])
            break
    part.unlink(missing_ok=True)
    return False


# ------------------------------------------------------------------ the whole stage

def _folder(key: str) -> str:
    if key == SUBJECT:
        return SUBJECT
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:40] or "thing"
    return f"{slug}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:6]}"


def _candidates(group: dict, *, pexels_key: str, pixabay_key: str, transport=None, stop=lambda: False) -> list[dict]:
    """NASA (when the group allows it), then Commons, then name-gated stock — the order Gian set. No new search
    once ``stop()`` (2026-11-21: the searches were the one step not bounded by the stage's budget)."""
    out: list[dict] = []
    finders = ([search_nasa] if group.get("nasa") else []) + [search_commons]
    with httpx.Client(timeout=30.0, transport=transport, follow_redirects=True, headers={"User-Agent": images.UA}) as c:
        for fn in finders:
            for q in group.get("queries") or []:
                if stop():
                    return out
                try:
                    out += fn(c, q, group.get("terms") or [], limit=group.get("per_query", 12))
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("%s %r failed: %s", fn.__name__, q, exc)
    if group.get("stock_queries") and (pexels_key or pixabay_key) and not stop():
        out += search(group["stock_queries"], pexels_key=pexels_key, pixabay_key=pixabay_key, transport=transport,
                      terms=group.get("terms") or [], limit=group.get("limit", 80))
    return out


def gather_footage(groups: list[dict], out_dir: str, *, classify, need: float, keep: float, slot: float, slow: float,
                   max_slow: float, pexels_key: str = "", pixabay_key: str = "", budget_seconds: float = 1080.0,
                   workers: int = 4, transport=None, clock=time.monotonic) -> tuple[dict[str, list[str]], dict]:
    """Checked clips per group: ``({group key: [clip paths]}, stats)``.

    ``groups`` in priority order, each ``{"key", "subject" (for the vision check), "queries", "stock_queries",
    "terms", "nasa", "want", "limit"}``: the named things first (a clip found for both a thing and the subject
    belongs to the thing), the video's subject (key ``SUBJECT``) last. ``classify(tiles, subject) → {tile: label}``.
    Stages share one deadline: no new search after 30 % of ``budget_seconds``, calm checks to 60 %, then each
    group is vision-checked (no new ask after 85 %) and its keepers cut (none started after 100 %) — the group
    with the most calm clips first; a group that runs out of time keeps photos.
    Each group's folder gets ``footage.json`` (credit fields + window) for the clips it kept.
    """
    t0 = clock()
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(groups) or 1))) as pool:
        found = list(pool.map(lambda g: _safe(lambda: _candidates(
            g, pexels_key=pexels_key, pixabay_key=pixabay_key, transport=transport,
            stop=lambda: clock() - t0 > budget_seconds * 0.3), []), groups))
    taken: set[str] = set()
    queues: list[list[dict]] = []
    for g, cands in zip(groups, found):
        mine = [x for x in cands if x["id"] not in taken][:g.get("limit", 80)]
        taken.update(x["id"] for x in mine)
        queues.append(mine)
    order = [(gi, q[depth]) for depth in range(max((len(q) for q in queues), default=0))
             for gi, q in enumerate(queues) if depth < len(q)]          # round-robin: every group gets probed early
    calm: list[list[tuple[int, dict]]] = [[] for _ in groups]
    lock = threading.Lock()
    pos = [0]

    def prober() -> None:
        with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True, headers={"User-Agent": images.UA}) as c:
            while clock() - t0 < budget_seconds * 0.6:
                with lock:
                    while pos[0] < len(order) and len(calm[order[pos[0]][0]]) >= 2 * groups[order[pos[0]][0]]["want"]:
                        pos[0] += 1
                    if pos[0] >= len(order):
                        return
                    k, (gi, cand) = pos[0], order[pos[0]]
                    pos[0] += 1
                folder = root / _folder(groups[gi]["key"])
                folder.mkdir(parents=True, exist_ok=True)
                got = _safe(lambda: probe_clip(c, cand, folder, need=need, keep=keep), None)
                if got:
                    with lock:
                        calm[gi].append((k, got))

    threads = [threading.Thread(target=prober, daemon=True) for _ in range(max(1, workers))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    def check(gi: int) -> list[dict]:
        # One contact sheet per ask, stopping once the group has its clips or at 85 % of the budget (2026-11-21:
        # the subject's five sheets went in one call that could run past the budget and leave no time to cut).
        rows = [x for _, x in sorted(calm[gi], key=lambda kx: kx[0])]
        ok: list[dict] = []
        for i in range(0, len(rows), CHECK_SHEET):
            if len(ok) >= groups[gi]["want"] or clock() - t0 > budget_seconds * 0.85:
                break
            part = rows[i:i + CHECK_SHEET]
            labels = classify([x["check"] for x in part], groups[gi]["subject"]) or {}
            ok += [x for x in part if labels.get(x["check"]) == "subject"]
        return ok[:groups[gi]["want"]]

    def cut(gi: int, cand: dict) -> dict | None:
        if clock() - t0 > budget_seconds:
            return None
        s = slow_for(cand["seconds"], slot=slot, slow=slow, max_slow=max_slow)
        dst = root / _folder(groups[gi]["key"]) / f"{_stem(cand)}.mp4"
        left = budget_seconds - (clock() - t0)
        if s is None or not (dst.exists() or cut_window(cand, dst, timeout=max(60.0, min(300.0, left)))):
            return None
        return {"file": dst.name, "kind": "clip", "label": "subject", "skip": 0.0, "seconds": cand["seconds"], "slow": s,
                "source_skip": cand["skip"], **{k: cand.get(k, "") for k in ("id", "source", "artist", "license", "page",
                                                                              "title", "query")}}

    approved = [0] * len(groups)

    def check_and_cut(gi: int) -> list[dict]:
        keep = check(gi)
        approved[gi] = len(keep)
        return [r for r in (_safe(lambda x=x: cut(gi, x), None) for x in keep) if r]

    # 2026-11-21: a group's clips are cut as soon as it is checked, the group with the most tiles (the subject: the
    # clips that fill most footage slots and the Shorts) first. All vision checks used to finish before the first
    # cut, and the paced Wikimedia cuts (~8 s each) then ran out of budget with the subject's clips last in line.
    with ThreadPoolExecutor(max_workers=max(1, min(3, workers))) as pool:
        futs = {gi: pool.submit(_safe, lambda gi=gi: check_and_cut(gi), [])
                for gi in sorted(range(len(groups)), key=lambda gi: -len(calm[gi]))}
    cut_rows = [futs[gi].result() for gi in range(len(groups))]
    out: dict[str, list[str]] = {}
    for gi, g in enumerate(groups):
        rows = cut_rows[gi]
        folder = root / _folder(g["key"])
        if folder.exists() or rows:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "footage.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
        out[g["key"]] = [str((folder / r["file"]).resolve()) for r in rows]
        log.info("footage for %r: %d candidates, %d calm, %d approved, %d kept", g["subject"], len(queues[gi]),
                 len(calm[gi]), approved[gi], len(rows))
    kept_rows = [r for rows in cut_rows for r in rows]
    stats = {"candidates": sum(len(q) for q in queues), "calm": sum(len(x) for x in calm),
             "approved": sum(approved), "clips": len(kept_rows),
             "sources": {s: sum(1 for r in kept_rows if r["source"] == s) for s in ("nasa", "commons", "pexels", "pixabay")},
             "seconds": round(clock() - t0)}
    return out, stats


def _safe(fn, default):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — one clip or one search must never cost the video
        log.warning("footage step failed: %s", exc)
        return default


# ------------------------------------------------------------------ what was kept (for the timeline, Shorts, credits)

def _rows(folder: Path) -> list[dict]:
    try:
        rows = json.loads((folder / "footage.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("file")] if isinstance(rows, list) else []


def kept(folder: str) -> list[str]:
    """Absolute paths of the clips a group folder kept (those still on disk)."""
    return [str((Path(folder) / r["file"]).resolve()) for r in _rows(Path(folder)) if (Path(folder) / r["file"]).is_file()]


def window(path: str) -> tuple[float, float, float] | None:
    """``(skip, seconds, slow)`` for a checked clip (from the ``footage.json`` beside it), None for any other."""
    p = Path(path)
    for r in _rows(p.parent):
        if r["file"] == p.name and r.get("slow"):
            return float(r.get("skip") or 0.0), float(r["seconds"]), float(r["slow"])
    return None


def credits_by_path(root: str) -> dict[str, dict]:
    """Absolute clip path → its footage.json row, across ``root/footage.json`` and ``root/*/footage.json``."""
    base = Path(root)
    out: dict[str, dict] = {}
    for folder in [base, *sorted(p for p in base.glob("*") if p.is_dir())] if base.is_dir() else []:
        for r in _rows(folder):
            f = folder / r["file"]
            if f.is_file():
                out[str(f.resolve())] = {"kind": "clip", **r}
    return out


# ------------------------------------------------------------------ download (stock previews, probes)

def _get(c: httpx.Client, url: str, dest: Path, *, max_bytes: int, deadline: float = 120.0) -> bool:
    try:
        for attempt in range(2):
            _pace(url)                     # Wikimedia's rate limit (WIKIMEDIA_GAP); one 429 is waited out
            with c.stream("GET", url) as r:
                if r.status_code == 429 and attempt == 0:
                    time.sleep(_retry_after(r.headers))
                    continue
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
            break
        return dest.exists() and dest.stat().st_size > PREVIEW_MIN_BYTES
    except (httpx.HTTPError, OSError) as exc:
        log.warning("footage download %s failed: %s", url, exc)
        dest.unlink(missing_ok=True)
        return False


def gather(candidates: list[dict], out_dir: str, *, classify_fn, want: int = 30, transport=None,
           budget_seconds: float = 600.0) -> list[str]:
    """Vision-check each candidate's preview frame, download the keepers' clips; returns clip paths
    (subject clips first). ``classify_fn(paths) -> {path: label}``. Writes ``footage.json`` beside them.
    (The b-roll of the other channels, cc/broll.py; Sleep On Facts uses ``gather_footage``.)"""
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
        # 2026-10-27: only clips the vision check says show the subject itself; generic scenery ("setting")
        # and unchecked clips read as off-topic against the narration (Gian: "stop wrong shots")
        keep = [(lab, path) for path, lab in labels.items() if lab == "subject"]
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

def mix_into_slots(slots: list[str], plan: list[dict] | None, clips: list[str], *,
                   entity_clips: dict[str, list[str]] | None = None, every: int = 2, max_uses: int = 3,
                   gap: int = 30) -> tuple[list[str], int]:
    """Give slots to footage. Returns (new slots, number of footage slots).

    Felix (Puello Media) 2026-11: footage must be a real share of the video, not 1 slot in 394. A slot whose
    plan row shows a named thing (``plan[k]["entity"]``) may take one of THAT thing's clips (``entity_clips``);
    every other slot (filler, a passage's own shot, a chapter photo, the subject reel) may take one of the
    subject's ``clips``. Never slot 0 (the video opens on its subject photo). Footage and photos alternate: within
    any ``every`` consecutive slots at most one is footage. A clip is used at most ``max_uses`` times and never
    again within ``gap`` slots; least-used, longest-unseen clips go first. A slot with no such clip keeps its photo.
    """
    entity_clips = {k: v for k, v in (entity_clips or {}).items() if v}
    if (not clips and not entity_clips) or every < 1:
        return list(slots), 0
    out = list(slots)
    uses: dict[str, int] = {}
    last_at: dict[str, int] = {}
    placed = 0
    for k in range(1, len(out)):
        if any(is_video(out[j]) for j in range(max(0, k - every + 1), k)):
            continue
        row = plan[k] if plan and k < len(plan) else {}
        pool = entity_clips.get(str(row.get("entity"))) if row.get("entity") else clips
        ok = [c for c in pool or [] if uses.get(c, 0) < max_uses and k - last_at.get(c, -10 ** 9) > gap]
        if not ok:
            continue
        c = min(ok, key=lambda c: (uses.get(c, 0), last_at.get(c, -10 ** 9), pool.index(c)))
        out[k] = c
        uses[c] = uses.get(c, 0) + 1
        last_at[c] = k
        placed += 1
    return out, placed
