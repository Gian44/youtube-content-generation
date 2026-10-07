"""Topic-matched landscape photos from Pexels and Pixabay.

Relevance is enforced in two layers, because stock search is loose ("octopus" returns aquarium
fish): (1) a text gate — a photo counts as showing the SUBJECT only if the provider's own
description (Pexels ``alt``, Pixabay ``tags``) contains one of the subject keywords; photos that
match a *setting* query but not the keywords are kept as a minority of scenery; (2) a vision gate —
``verify_relevance`` shows each downloaded photo to a small multimodal model and drops the ones that
don't depict the subject or its setting. Generic calm imagery is a last resort up to the floor only.
"""

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
            out.append({"url": url, "credit": f"{p.get('photographer', '')} / Pexels {p.get('url', '')}",
                        "desc": str(p.get("alt") or ""), "query": q})
    return out


def _pixabay(c: httpx.Client, key: str, q: str, page: int) -> list[dict]:
    r = c.get("https://pixabay.com/api/", params={
        "key": key, "q": q, "image_type": "photo", "orientation": "horizontal", "min_width": 1920,
        "per_page": 100, "page": page, "safesearch": "true",
    })
    if r.status_code != 200:
        log.warning("pixabay %s page %d: HTTP %s", q, page, r.status_code)
        return []
    return [{"url": h["largeImageURL"], "credit": f"{h.get('user', '')} / Pixabay {h.get('pageURL', '')}",
             "desc": str(h.get("tags") or ""), "query": q}
            for h in (r.json().get("hits") or []) if h.get("largeImageURL")]


def _mentions(desc: str, keywords: list[str]) -> bool:
    d = desc.lower()
    return any(k.lower() in d for k in keywords if k.strip())


def collect_urls(queries: list[str], *, target: int, pexels_key: str, pixabay_key: str, transport=None,
                 max_pages: int = 3, keywords: list[str] | None = None, setting_queries: list[str] | None = None,
                 setting_share: float = 0.3, generic_floor: int = 0) -> list[dict]:
    """Return up to ``target`` photo URLs, subject shots first.

    * ``queries`` should depict the subject itself. With ``keywords`` given, a result only counts as a
      subject shot when the provider's description mentions one of them; otherwise it is demoted to
      scenery.
    * ``setting_queries`` (habitat, places) plus demoted results fill at most ``setting_share`` of the set.
    * Generic calm imagery is used only to reach ``generic_floor`` (the pipeline's hard minimum).
    """
    keywords = [k for k in (keywords or []) if k.strip()]
    seen: set[str] = set()
    subject: list[dict] = []
    scenery: list[dict] = []
    max_scenery = int(target * setting_share)

    def add(items: list[dict], *, as_setting: bool) -> None:
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            is_subject = (not as_setting) and (not keywords or _mentions(it.get("desc", ""), keywords))
            it["tier"] = "subject" if is_subject else "setting"
            (subject if is_subject else scenery).append(it)

    sources = [("pexels", pexels_key, _pexels), ("pixabay", pixabay_key, _pixabay)]
    with httpx.Client(timeout=30.0, transport=transport) as c:
        for name, key, fetch in sources:
            if not key:
                continue
            for page in range(1, max_pages + 1):
                for q in [q for q in queries if q.strip()]:
                    if len(subject) >= target:
                        break
                    add(fetch(c, key, q, page), as_setting=False)
            for page in range(1, max_pages + 1):
                for q in [q for q in (setting_queries or []) if q.strip()]:
                    if len(subject) + min(len(scenery), max_scenery) >= target:
                        break
                    add(fetch(c, key, q, page), as_setting=True)
            if len(subject) >= target:
                break
        out = subject[:target] + scenery[:min(max_scenery, max(0, target - len(subject)))]
        if len(out) < generic_floor:
            log.warning("only %d on-topic photos; padding to the floor of %d with generic calm imagery", len(out), generic_floor)
            for name, key, fetch in sources:
                if not key:
                    continue
                for q in GENERIC_QUERIES:
                    if len(out) >= generic_floor:
                        break
                    for it in fetch(c, key, q, 1):
                        if it["url"] not in seen:
                            seen.add(it["url"]); it["tier"] = "generic"; out.append(it)
                        if len(out) >= generic_floor:
                            break
    log.info("photos: %d subject, %d setting, %d total (keywords: %s)",
             sum(1 for i in out if i.get("tier") == "subject"), sum(1 for i in out if i.get("tier") == "setting"), len(out), keywords[:6])
    return out


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


def download_all(urls: list[dict], out_dir: str, *, transport=None, verify_image=_pillow_ok,
                 want: int | None = None) -> list[str]:
    """Download in order until ``want`` good files exist (or the list runs out); extra URLs are the margin."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    credits: list[dict] = []
    seen_hash: set[str] = set()
    with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True) as c:
        for i, it in enumerate(urls):
            if want and len(paths) >= want:
                break
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


VISION_URL = "https://api.openai.com/v1/chat/completions"


def _b64_small(path: str, max_side: int = 512) -> str:
    import base64
    from io import BytesIO

    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


def verify_relevance(paths: list[str], *, subject: str, api_key: str, model: str = "gpt-4o-mini", batch: int = 8,
                     transport=None, keep_min: int = 0) -> list[str]:
    """Drop photos that do not depict ``subject`` or its natural setting (vision check, low detail).

    Fail-open: any API problem keeps the batch. If the check would leave fewer than ``keep_min``
    photos, the original list is returned and a warning logged (better off-topic than no video).
    """
    if not api_key or not paths:
        return paths
    kept: list[str] = []
    with httpx.Client(timeout=120.0, transport=transport, headers={"Authorization": f"Bearer {api_key}"}) as c:
        for i in range(0, len(paths), batch):
            group = paths[i:i + batch]
            content = [{"type": "text", "text": (
                f"These {len(group)} photos are candidates for a calm video about: {subject}. For each photo, "
                f"answer whether it clearly shows the subject itself OR its natural setting/habitat/landscape. "
                f"A different animal, object or unrelated scene is NOT acceptable. "
                f'Return JSON {{"keep": [<1-based indices to keep>]}} and nothing else.')}]
            for p in group:
                try:
                    content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64_small(p)}", "detail": "low"}})
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not encode %s: %s", p, exc)
            try:
                r = c.post(VISION_URL, json={"model": model, "messages": [{"role": "user", "content": content}],
                                             "response_format": {"type": "json_object"}, "max_tokens": 100})
                r.raise_for_status()
                keep = json.loads(r.json()["choices"][0]["message"]["content"]).get("keep") or []
                idx = {int(k) - 1 for k in keep if str(k).lstrip("-").isdigit()}
                kept += [p for j, p in enumerate(group) if j in idx]
            except Exception as exc:  # noqa: BLE001 — fail open
                log.warning("vision check failed for a batch (%s); keeping it", exc)
                kept += group
    dropped = len(paths) - len(kept)
    if len(kept) < keep_min:
        log.warning("vision check would leave %d < %d photos; keeping all", len(kept), keep_min)
        return paths
    log.info("vision check: kept %d, dropped %d off-topic photos", len(kept), dropped)
    return kept
