"""Public-domain prints from the Internet Archive (Gian, 2026-11-08: "get the files for me" — the one source a
pipeline may pull a feature film from on its own). A film in ``films.yml`` names its item (``archive: <identifier>``);
the run reads the item's file list, picks the best video (largest h.264 / mp4 / mkv, never the 512 kb derivatives),
downloads it with resume, and takes the item's own ``.srt`` when it ships one."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("cny.archive")
UA = "cinybe-recaps/1.0 (narrated public-domain film recaps; GitHub Actions)"
VIDEO_EXTS = (".mp4", ".mkv", ".m4v", ".avi", ".mpeg", ".mpg", ".mov", ".ogv")
PREFERRED = (".mp4", ".mkv", ".m4v")
MIN_BYTES = 150_000_000               # smaller files on a feature-film item are trailers, clips or thumbnails
LOW_RES_MARKS = ("512kb", ".ia.mp4", "preview", "trailer", "sample")      # the Archive's own derivatives, extras


@dataclass
class ArchiveFile:
    name: str
    size: int
    fmt: str

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()


def _get_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def item_files(identifier: str) -> list[ArchiveFile]:
    meta = _get_json(f"https://archive.org/metadata/{urllib.parse.quote(identifier)}")
    out = []
    for f in meta.get("files") or []:
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append(ArchiveFile(name=str(f.get("name") or ""), size=size, fmt=str(f.get("format") or "")))
    if not out and not meta.get("metadata"):
        raise RuntimeError(f"no such Internet Archive item: {identifier}")
    return out


def best_video(files: list[ArchiveFile], *, max_bytes: int = 6_000_000_000, min_bytes: int = MIN_BYTES) -> ArchiveFile | None:
    """Pure: the largest proper video file (mp4/mkv first), skipping the Archive's low-res derivatives."""
    cands = []
    for f in files:
        low = f.name.lower()
        if f.ext not in VIDEO_EXTS or f.size < min_bytes or f.size > max_bytes:
            continue
        if f.ext == ".ogv" or any(m in low for m in LOW_RES_MARKS):
            continue
        cands.append(((1 if f.ext in PREFERRED else 0), f.size, f))
    if not cands:
        return None
    return max(cands, key=lambda c: (c[0], c[1]))[2]


def is_asr(f: ArchiveFile) -> bool:
    """The Archive's own machine transcript (``*.asr.srt``): timed to the file, but ASR quality."""
    return ".asr." in f.name.lower()


def stem(name: str) -> str:
    """``NightOfTheLivingDead_DVD5.asr.srt`` → ``nightofthelivingdead_dvd5``: the file-chain stem, for matching an
    .srt to the video it was made from."""
    base = name.rsplit("/", 1)[-1].lower()
    for suffix in (".asr", ".eng", ".en", ".english"):
        base = base.replace(suffix + ".", ".")
    return base.split(".")[0]


def subtitle_file(files: list[ArchiveFile], *, video: ArchiveFile | None = None) -> ArchiveFile | None:
    """An ``.srt`` the item ships, when any: a human one first (same stem as the video preferred); the Archive's
    ASR one only when it was made from the very video we take — timings from another transfer of the same film
    drift, and drifted timings put the shots in the wrong scenes (whisper on our file is safer then)."""
    srts = [f for f in files if f.ext == ".srt" and f.size > 2000]
    if not srts:
        return None
    vstem = stem(video.name) if video else None
    human = [f for f in srts if not is_asr(f)]
    same = [f for f in human if vstem and stem(f.name) == vstem]
    en = [f for f in human if any(k in f.name.lower() for k in ("en", "eng", "english"))]
    if human:
        return (same or en or human)[0]
    asr_same = [f for f in srts if is_asr(f) and (vstem is None or stem(f.name) == vstem)]
    return asr_same[0] if asr_same else None


def download_url(identifier: str, name: str) -> str:
    return f"https://archive.org/download/{urllib.parse.quote(identifier)}/{urllib.parse.quote(name)}"


def download(url: str, dest: str, *, expected: int = 0, retries: int = 6) -> str:
    """Stream to ``dest`` with HTTP range resume (the Archive is slow and drops connections on big files)."""
    part = dest + ".part"
    for attempt in range(retries):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if expected and have >= expected:
            break
        headers = {"User-Agent": UA}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r, open(part, "ab" if have else "wb") as f:
                if have and r.status != 206:                       # server ignored the range: start over
                    f.seek(0)
                    f.truncate()
                t0, got = time.monotonic(), 0
                while True:
                    chunk = r.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if time.monotonic() - t0 > 60:
                        print(f"  … {(have + got) / 1e9:.2f} GB", flush=True)
                        t0 = time.monotonic()
            break
        except Exception as exc:  # noqa: BLE001
            log.warning("download attempt %d failed: %s", attempt + 1, exc)
            time.sleep(min(60, 5 * (attempt + 1)))
    else:
        raise RuntimeError(f"could not download {url}")
    size = os.path.getsize(part)
    if expected and size < expected:
        raise RuntimeError(f"short download: {size} of {expected} bytes")
    os.replace(part, dest)
    return dest


def fetch(identifier: str, work: Path, *, max_bytes: int = 6_000_000_000) -> tuple[str, str | None, ArchiveFile, bool]:
    """Download the item's best video (and its .srt when it has one) into ``work/film/``; returns
    (video_path, srt_path | None, the chosen file, whether that .srt is the Archive's ASR one)."""
    files = item_files(identifier)
    video = best_video(files, max_bytes=max_bytes)
    if video is None:
        raise RuntimeError(f"Internet Archive item {identifier} has no usable video file "
                           f"({len(files)} files; videos: {[f.name for f in files if f.ext in VIDEO_EXTS][:6]})")
    d = work / "film"
    d.mkdir(parents=True, exist_ok=True)
    dest = d / ("source" + video.ext)
    if not dest.exists():
        print(f"film file: archive.org/{identifier}/{video.name} ({video.size / 1e9:.2f} GB, {video.fmt}) downloading…", flush=True)
        download(download_url(identifier, video.name), str(dest), expected=video.size)
    srt_path, asr = None, False
    srt = subtitle_file(files, video=video)
    if srt is not None:
        asr = is_asr(srt)
        sp = d / ("source.asr.srt" if asr else "source.srt")
        if not sp.exists():
            download(download_url(identifier, srt.name), str(sp), expected=srt.size)
        srt_path = str(sp)
    return str(dest), srt_path, video, asr
