"""Background gameplay for the Shorts: pre-cut vertical clips of Creative Commons (CC BY) Roblox gameplay, listed in
``gameplay.yml`` with the source video and the channel to credit. A run picks one clip per story (seeded by the date,
skipping the clips used lately), downloads it and credits its maker in the description. Pure helpers are unit-tested
without the network."""

from __future__ import annotations

import hashlib
import logging
import random
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger("bqd.gameplay")


@dataclass
class Clip:
    file: str
    seconds: float
    source: str          # key in ``sources``
    channel: str         # who to credit
    url: str             # the source video
    licence: str
    base_url: str
    local: str = ""      # where this run downloaded it

    @property
    def download_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.file}"

    @property
    def credit(self) -> str:
        return f"Gameplay: {self.channel} ({self.licence}) {self.url}"


def load(path: str) -> list[Clip]:
    """Every clip in the manifest; [] when the file is missing or lists none (the caller then uses stock footage)."""
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    base, sources = str(data.get("base_url") or ""), data.get("sources") or {}
    out: list[Clip] = []
    for c in data.get("clips") or []:
        src = sources.get(c.get("source")) or {}
        if not (base and c.get("file") and src.get("channel") and src.get("url") and src.get("licence")):
            continue                      # a clip without a credit line is never used
        out.append(Clip(file=str(c["file"]), seconds=float(c.get("seconds") or 0), source=str(c["source"]),
                        channel=str(src["channel"]), url=str(src["url"]), licence=str(src["licence"]), base_url=base))
    return out


def recent_files(rows: list[dict], *, limit: int) -> list[str]:
    files = [str(s.get("gameplay") or "") for r in rows for s in (r.get("stories") or [])]
    return [f for f in files if f][-limit:]


def pick(clips: list[Clip], *, count: int, seed: str, avoid: list[str], min_seconds: float = 0.0) -> list[Clip]:
    """``count`` different clips, deterministic for ``seed``, long enough for the narration when any are, skipping
    ``avoid`` (the clips used lately) while enough others remain."""
    pool = [c for c in clips if c.seconds >= min_seconds] or list(clips)
    fresh = [c for c in pool if c.file not in set(avoid)]
    if len(fresh) >= count:
        pool = fresh
    rng = random.Random(int(hashlib.sha1(("gameplay|" + seed).encode()).hexdigest(), 16))
    pool = sorted(pool, key=lambda c: c.file)
    rng.shuffle(pool)
    return pool[:count]


def fetch(clip: Clip, out_dir: str, *, timeout: int = 300) -> str:
    """Download ``clip`` once into ``out_dir``; raises on a failed or implausibly small download."""
    dst = Path(out_dir) / clip.file
    if dst.exists() and dst.stat().st_size > 200_000:
        return str(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    req = urllib.request.Request(clip.download_url, headers={"User-Agent": "block-quest-daily"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    if tmp.stat().st_size < 200_000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"gameplay clip {clip.file} is too small to be a video")
    tmp.replace(dst)
    return str(dst)
