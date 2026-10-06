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
MIN_WIDTH = 1600          # anything narrower gets upscaled to 1080p and looks soft — reject it
PEXELS_WIDTH = 2560       # ask Pexels' image CDN for a 2560-px rendition of the original


def _pexels_url(src: dict) -> str | None:
    # ``large2x`` is only 1880 px. The CDN behind ``original`` accepts resize params, so request a
    # 2560-px JPEG: sharper than large2x, a fraction of the size of a 6000-px original.
    orig = src.get("original")
    if not orig:
        return src.get("large2x")
    return f"{orig}?auto=compress&cs=tinysrgb&w={PEXELS_WIDTH}"


def _pexels(c: httpx.Client, key: str, q: str, page: int) -> list[dict]:
    r = c.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
              params={"query": q, "orientation": "landscape", "size": "large", "per_page": 80, "page": page})
    if r.status_code != 200:
        log.warning("pexels %s page %d: HTTP %s", q, page, r.status_code)
        return []
    out = []
    for p in (r.json().get("photos") or []):
        url = _pexels_url(p.get("src") or {})
        if url and int(p.get("width") or 0) >= MIN_WIDTH:
            out.append({"url": url, "credit": f"{p.get('photographer', '')} / Pexels {p.get('url', '')}"})
    return out


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
    # Pexels first for everything (2560-px renditions); Pixabay's free tier caps at 1280 px, so it
    # is only consulted when Pexels cannot fill the target — those frames are visibly softer.
    sources = [("pexels", pexels_key, _pexels), ("pixabay", pixabay_key, _pixabay)]
    with httpx.Client(timeout=30.0, transport=transport) as c:
        for name, key, fetch in sources:
            if not key:
                continue
            for pool in pools:
                for page in range(1, max_pages + 1):
                    for q in pool:
                        if len(out) >= target:
                            return out[:target]
                        add(fetch(c, key, q, page))
                if len(out) >= target:
                    break
            if name == "pexels" and len(out) < target:
                log.warning("pexels filled %d/%d; topping up from pixabay (1280 px)", len(out), target)
    return out[:target]


def _pillow_ok(path: str) -> bool:
    """Decodable JPEG/PNG at least MIN_WIDTH wide (Pixabay 1280-px top-ups are kept: they are
    the fallback by design, and the check only rejects what would be blurrier than that)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            width = im.size[0]
            im.verify()
        return width >= 1280
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
