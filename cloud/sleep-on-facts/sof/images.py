"""Topic-matched landscape photos from Pexels and Pixabay, padded with calm generics, deduped."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import httpx

log = logging.getLogger("sof.images")
GENERIC_QUERIES = ["night sky stars", "calm ocean", "forest fog", "desert dunes", "mountain lake dawn"]
MIN_BYTES = 50_000


def _pexels(c: httpx.Client, key: str, q: str, page: int) -> list[dict]:
    r = c.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
              params={"query": q, "orientation": "landscape", "size": "large", "per_page": 80, "page": page})
    if r.status_code != 200:
        log.warning("pexels %s page %d: HTTP %s", q, page, r.status_code)
        return []
    return [{"url": p["src"]["large2x"], "credit": f"{p.get('photographer', '')} / Pexels {p.get('url', '')}"}
            for p in (r.json().get("photos") or []) if (p.get("src") or {}).get("large2x")]


def _pixabay(c: httpx.Client, key: str, q: str, page: int) -> list[dict]:
    r = c.get("https://pixabay.com/api/", params={
        "key": key, "q": q, "image_type": "photo", "orientation": "horizontal", "min_width": 1920,
        "per_page": 100, "page": page, "safesearch": "true",
    })
    if r.status_code != 200:
        log.warning("pixabay %s page %d: HTTP %s", q, page, r.status_code)
        return []
    return [{"url": h["largeImageURL"], "credit": f"{h.get('user', '')} / Pixabay {h.get('pageURL', '')}"}
            for h in (r.json().get("hits") or []) if h.get("largeImageURL")]


def collect_urls(queries: list[str], *, target: int, pexels_key: str, pixabay_key: str, transport=None,
                 max_pages: int = 3) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []

    def add(items: list[dict]) -> None:
        for it in items:
            if it["url"] not in seen:
                seen.add(it["url"])
                out.append(it)

    pools = [[q for q in queries if q.strip()], GENERIC_QUERIES]
    with httpx.Client(timeout=30.0, transport=transport) as c:
        for pool in pools:
            for page in range(1, max_pages + 1):
                for q in pool:
                    if len(out) >= target:
                        return out[:target]
                    if pexels_key:
                        add(_pexels(c, pexels_key, q, page))
                    if len(out) < target and pixabay_key:
                        add(_pixabay(c, pixabay_key, q, page))
            if len(out) >= target:
                break
    return out[:target]


def _pillow_ok(path: str) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001
        return False


def download_all(urls: list[dict], out_dir: str, *, transport=None, verify_image=_pillow_ok) -> list[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    credits: list[dict] = []
    seen_hash: set[str] = set()
    with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True) as c:
        for i, it in enumerate(urls):
            p = Path(out_dir) / f"{i:03d}.jpg"
            try:
                if not p.exists():
                    r = c.get(it["url"])
                    if r.status_code != 200 or len(r.content) < MIN_BYTES:
                        continue
                    p.write_bytes(r.content)
            except httpx.HTTPError as exc:
                log.warning("image %s failed: %s", it["url"], exc)
                continue
            h = hashlib.sha1(p.read_bytes()).hexdigest()
            if h in seen_hash or not verify_image(str(p)):
                p.unlink(missing_ok=True)
                continue
            seen_hash.add(h)
            paths.append(str(p))
            credits.append({"file": p.name, **it})
    with open(Path(out_dir) / "credits.json", "w", encoding="utf-8") as f:
        json.dump(credits, f, indent=2, ensure_ascii=False)
    return paths
