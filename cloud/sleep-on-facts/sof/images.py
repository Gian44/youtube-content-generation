"""On-topic photos for the reel, and the thumbnail's photo.

Sources, most relevant first:
1. **Wikimedia Commons images embedded in the researched Wikipedia article** — chosen by the article's
   own editors, so they show the subject by construction (free licences only; credited).
2. **NASA Image and Video Library** for space topics — public domain, credited "NASA/…".
3. **The article's Commons category** (+ a few subcategories) — the subject's curated photo pool.
4. **Pexels / Pixabay stock**, kept only when the caption names the subject (word match on the
   subject's own name, never on generic words like "planet" or "ruins").

Then ``classify`` shows every download to a small vision model, which labels it subject / setting /
reject. The reel keeps subject shots first and a minority of setting shots; the thumbnail is chosen
only among subject shots (``pick_iconic``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger("sof.images")
UA = "SleepOnFacts/1.0 (+https://github.com/Gian44/youtube-content-generation) python-httpx"
GENERIC_QUERIES = ["night sky stars", "calm ocean", "forest fog", "desert dunes", "mountain lake dawn"]
MIN_BYTES = 20_000        # broken/placeholder files; real quality is checked by ``image_ok``
MAX_BYTES = 40_000_000
MIN_WIDTH = 1600          # stock search filter; anything narrower looks soft when shown at 1080p
PEXELS_WIDTH = 2560       # ask Pexels' image CDN for a 2560-px rendition of the original
COMMONS_WIDTH = 2560      # Commons thumbnail request; Wikimedia serves the nearest standard size
CONTAIN_BELOW = 1.3       # aspect ratio under which the renderer shows the whole picture (see render.cover_image)
ENWIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
NASA_SEARCH = "https://images-api.nasa.gov/search"
NASA_ASSET = "https://images-assets.nasa.gov/image/{id}/{id}~{size}.jpg"
VISION_URL = "https://api.openai.com/v1/chat/completions"

# Words that appear in captions of every other photo — never evidence that a photo shows THIS subject.
GENERIC_WORDS = set("""
planet planets ring rings ringed moon moons star stars space galaxy universe sky solar system orbit cosmos astronomy
ruin ruins archaeological archaeology ancient old history historic historical heritage site sites city cities town village
temple monument civilisation civilization culture empire kingdom valley great grand big little new world earth natural
forest forests rainforest jungle canopy tree trees river rivers lake water waters ocean oceans sea seas coast beach island
islands mountain mountains landscape nature wildlife animal animals bird birds fish plant plants flower flowers reef reefs
coral corals underwater marine ice snow desert sand rock rocks stone barrier engine engines machine machines machinery
mechanical metal industrial industry factory cylinder wheel wheels gear gears vintage retro close closeup view aerial
background texture abstract building buildings architecture people person man woman food farm farming field fields
""".split())
_BAD_FILE = re.compile(r"\b(map|maps|diagram|chart|graph|plot|logo|flag|coat of arms|seal of|icon|symbol|locator|"
                       r"schematic|infographic|timeline|compared|comparison|template|banner|screenshot|signature)\b")
_FREE = re.compile(r"(public domain|^pd\b|^pd-|cc0|cc[ -]?by|attribution|copyrighted free use|no restrictions|free art)", re.I)
_NONFREE = re.compile(r"(\bnc\b|non-?commercial|\bnd\b|no ?deriv|fair use|non-?free)", re.I)


# ------------------------------------------------------------------ subject names

def _norm_text(s: str) -> str:
    s = (s or "").lower().replace("-", " ").replace("_", " ").replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


def _norm(s: str) -> str:
    """A NAME, normalised: lower case, hyphens as spaces, disambiguators dropped ("Saturn (planet)" → "saturn")."""
    return _norm_text(re.sub(r"\s*\([^)]*\)", "", s or ""))


def _singular(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("uses", "sses", "xes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def name_terms(topic: str, wiki_title: str = "", keywords: list[str] | tuple = ()) -> list[str]:
    """Words/phrases whose presence in a caption means "this photo names the subject".

    The subject's own name (and the Wikipedia title, spelling and plural variants), its distinctive
    words, and the planner's keywords minus generic ones. "Indus Valley Civilisation" →
    ["indus valley civilisation", "indus valley civilization", "indus", ...]; "Saturn" → ["saturn"].
    """
    out: list[str] = []

    def add(t: str) -> None:
        t = _norm(t)
        if t and t not in out:
            out.append(t)

    for name in (topic, wiki_title):
        n = _norm(name)
        if not n:
            continue
        words = n.split()
        variants = {n, " ".join(words[:-1] + [_singular(words[-1])])}
        variants |= {v.replace("civilisation", "civilization") for v in variants} | {v.replace("civilization", "civilisation") for v in variants}
        for v in sorted(variants):
            add(v.removeprefix("the "))
        for w in words:
            w1 = _singular(w)
            if len(w1) >= 4 and w1 not in GENERIC_WORDS and w not in GENERIC_WORDS and w not in ("the", "and", "of"):
                add(w1)
    for raw in keywords or ():
        k = _norm(str(raw))
        if not k or k in GENERIC_WORDS or _singular(k) in GENERIC_WORDS:
            continue
        if " " in k or len(k) >= 4:
            add(k)
        if "-" in str(raw):
            add(k.replace(" ", ""))            # "mohenjo-daro" also as "mohenjodaro"
    return out


def mentions(desc: str, terms: list[str]) -> bool:
    """Whole-word (plural-tolerant) match: "indus" does not match "industrial", "ring" not "springs"."""
    d = " " + _norm_text(desc) + " "
    for t in terms:
        t = _norm(t)
        if t and re.search(rf"(?<![0-9a-z]){re.escape(t)}(?:s|es)?(?![0-9a-z])", d):
            return True
    return False


_mentions = mentions      # backwards-compatible name


def license_ok(lic: str, nonfree: str = "") -> bool:
    """Commercial reuse allowed (public domain, CC0, CC BY, CC BY-SA, …); NC/ND/fair-use never."""
    if str(nonfree or "").strip().lower() in ("true", "1", "yes"):
        return False
    lic = (lic or "").strip()
    return bool(lic) and bool(_FREE.search(lic)) and not _NONFREE.search(lic)


def is_space_topic(category: str, lead: str = "") -> bool:
    if re.search(r"space|astronom", category or "", re.I):
        return True
    hits = re.findall(r"\b(planet|moon|orbit|solar system|spacecraft|nasa|galaxy|telescope|asteroid|comet|nebula)s?\b",
                      (lead or "")[:2000], re.I)
    return len(hits) >= 2


def _big_enough(w: int, h: int) -> bool:
    """Landscape pictures fill the frame (need width); portrait/square ones are shown whole (need height)."""
    if w <= 0 or h <= 0:
        return False
    return w >= 1280 if w / h >= CONTAIN_BELOW else (h >= 1000 and w / h >= 0.5)


# ------------------------------------------------------------------ stock providers

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
                        "desc": str(p.get("alt") or ""), "title": str(p.get("alt") or "")[:120], "query": q, "source": "pexels",
                        "artist": str(p.get("photographer") or ""), "license": "Pexels License", "page": str(p.get("url") or "")})
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
             "desc": str(h.get("tags") or ""), "title": str(h.get("tags") or "")[:120], "query": q, "source": "pixabay",
             "artist": str(h.get("user") or ""),
             "license": "Pixabay Content License", "page": str(h.get("pageURL") or "")}
            for h in (r.json().get("hits") or []) if h.get("largeImageURL")]


def collect_urls(queries: list[str], *, target: int, pexels_key: str, pixabay_key: str, transport=None,
                 max_pages: int = 3, keywords: list[str] | None = None, setting_queries: list[str] | None = None,
                 setting_share: float = 0.3, generic_floor: int = 0, exclude: set[str] | None = None) -> list[dict]:
    """Stock photos: up to ``target`` URLs, subject shots first.

    * ``queries`` should depict the subject itself. With ``keywords`` (the subject's NAME terms, see
      ``name_terms``) a result only counts as a subject shot when its caption names the subject;
      otherwise it is demoted to scenery.
    * ``setting_queries`` (habitat, places) plus demoted results fill at most ``setting_share`` of the set.
    * Generic calm imagery is used only to reach ``generic_floor`` (the pipeline's hard minimum).
    """
    keywords = [k for k in (keywords or []) if k.strip()]
    seen: set[str] = set(exclude or ())
    subject: list[dict] = []
    scenery: list[dict] = []
    max_scenery = int(target * setting_share)

    def add(items: list[dict], *, as_setting: bool) -> None:
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            is_subject = (not as_setting) and (not keywords or mentions(it.get("desc", ""), keywords))
            it["tier"] = "subject" if is_subject else "setting"
            (subject if is_subject else scenery).append(it)

    sources = [("pexels", pexels_key, _pexels), ("pixabay", pixabay_key, _pixabay)]
    out: list[dict] = []
    if target > 0:
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
    log.info("stock photos: %d subject, %d setting, %d total (name terms: %s)",
             sum(1 for i in out if i.get("tier") == "subject"), sum(1 for i in out if i.get("tier") == "setting"), len(out), keywords[:8])
    return out


# ------------------------------------------------------------------ Wikimedia Commons

def _api(c: httpx.Client, api: str, **params) -> dict:
    params.update(format="json", formatversion="2")
    r = c.get(api, params=params)
    r.raise_for_status()
    return r.json()


def _meta(md: dict, key: str) -> str:
    return str((md.get(key) or {}).get("value") or "")


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def commons_candidate(page: dict, *, query: str) -> dict | None:
    """Turn one Commons ``imageinfo`` page into a reel candidate, or None if unusable/unfree."""
    ii = (page.get("imageinfo") or [None])[0]
    title = str(page.get("title") or "")
    if not ii or page.get("missing"):
        return None
    if ii.get("mime") not in ("image/jpeg", "image/tiff"):
        return None                               # SVG/PNG/GIF here are diagrams, icons and maps
    w, h = int(ii.get("width") or 0), int(ii.get("height") or 0)
    if not _big_enough(w, h) or _BAD_FILE.search(_norm(title)):
        return None
    md = ii.get("extmetadata") or {}
    lic = _meta(md, "LicenseShortName")
    if not license_ok(lic, _meta(md, "NonFree")):
        return None
    artist = _plain(_meta(md, "Artist"))[:160] or "Unknown author"
    page_url = str(ii.get("descriptionurl") or "")
    return {"url": str(ii.get("thumburl") or ii.get("url")), "credit": f"{artist} / {lic} / Wikimedia Commons {page_url}",
            "desc": f"{title.removeprefix('File:')} {_plain(_meta(md, 'ImageDescription'))[:300]}", "query": query,
            "title": re.sub(r"\.(jpe?g|tiff?)$", "", title.removeprefix("File:"), flags=re.I),
            "source": "commons", "artist": artist, "license": lic, "page": page_url, "width": w, "height": h}


def _imageinfo(c: httpx.Client, titles: list[str], *, query: str) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(titles), 50):
        data = _api(c, COMMONS_API, action="query", titles="|".join(titles[i:i + 50]), prop="imageinfo",
                    iiprop="url|size|mime|extmetadata", iiurlwidth=COMMONS_WIDTH,
                    iiextmetadatafilter="LicenseShortName|Artist|NonFree|ImageDescription")
        for p in (data.get("query") or {}).get("pages") or []:
            cand = commons_candidate(p, query=query)
            if cand:
                out.append(cand)
    return out


def commons_article_images(c: httpx.Client, wiki_title: str) -> list[dict]:
    """Free, large photos embedded in the English Wikipedia article (Commons-hosted only)."""
    data = _api(c, ENWIKI_API, action="query", titles=wiki_title, prop="images", imlimit="max", redirects=1)
    pages = (data.get("query") or {}).get("pages") or []
    files = [i["title"] for p in pages for i in (p.get("images") or [])]
    return _imageinfo(c, files, query=f"article:{wiki_title}")


def commons_category(c: httpx.Client, wiki_title: str) -> str | None:
    """The article's Commons category via Wikidata P373 (e.g. "Saturn (planet)")."""
    data = _api(c, ENWIKI_API, action="query", titles=wiki_title, prop="pageprops", ppprop="wikibase_item", redirects=1)
    pages = (data.get("query") or {}).get("pages") or []
    qid = next(((p.get("pageprops") or {}).get("wikibase_item") for p in pages if (p.get("pageprops") or {}).get("wikibase_item")), None)
    if not qid:
        return None
    r = c.get(WIKIDATA_API, params={"action": "wbgetclaims", "entity": qid, "property": "P373", "format": "json"})
    r.raise_for_status()
    claims = (r.json().get("claims") or {}).get("P373") or []
    for cl in claims:
        v = ((cl.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if v:
            return str(v)
    return None


def _category_files(c: httpx.Client, category: str, limit: int) -> list[dict]:
    out: list[dict] = []
    params = {"action": "query", "generator": "categorymembers", "gcmtitle": f"Category:{category}", "gcmtype": "file",
              "gcmlimit": 50, "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": COMMONS_WIDTH,
              "iiextmetadatafilter": "LicenseShortName|Artist|NonFree|ImageDescription"}
    seen_pages = 0
    while seen_pages < limit:
        data = _api(c, COMMONS_API, **params)
        pages = (data.get("query") or {}).get("pages") or []
        seen_pages += len(pages)
        for p in pages:
            cand = commons_candidate(p, query=f"category:{category}")
            if cand:
                out.append(cand)
        cont = (data.get("continue") or {})
        if not cont.get("gcmcontinue"):
            break
        params = {**params, **{k: v for k, v in cont.items() if k != "continue"}}
    return out


def commons_category_images(c: httpx.Client, wiki_title: str, *, limit: int = 250, subcats: int = 6,
                            per_subcat: int = 60, seed: str = "") -> list[dict]:
    """Free photos from the subject's Commons category and a few of its subcategories, shuffled so
    one photographer's 40-shot series does not play back to back."""
    cat = commons_category(c, wiki_title)
    if not cat:
        return []
    out = _category_files(c, cat, limit)
    data = _api(c, COMMONS_API, action="query", list="categorymembers", cmtitle=f"Category:{cat}", cmtype="subcat", cmlimit=50)
    subs = [m["title"].removeprefix("Category:") for m in (data.get("query") or {}).get("categorymembers") or []]
    for sub in [s for s in subs if not _BAD_FILE.search(_norm(s))][:subcats]:
        try:
            out += _category_files(c, sub, per_subcat)
        except httpx.HTTPError as exc:
            log.warning("commons subcategory %s failed: %s", sub, exc)
    random.Random(seed or wiki_title).shuffle(out)
    log.info("commons category %r: %d usable photos (incl. %d subcategories)", cat, len(out), min(subcats, len(subs)))
    return out


# ------------------------------------------------------------------ NASA

def _nasa(c: httpx.Client, q: str, page: int, *, page_size: int = 100) -> list[dict]:
    r = c.get(NASA_SEARCH, params={"q": q, "media_type": "image", "page": page, "page_size": page_size})
    if r.status_code != 200:
        log.warning("nasa %s page %d: HTTP %s", q, page, r.status_code)
        return []
    out = []
    for it in ((r.json().get("collection") or {}).get("items") or []):
        d = (it.get("data") or [{}])[0]
        nid = str(d.get("nasa_id") or "")
        text = " ".join([str(d.get("title") or ""), str(d.get("description") or ""), " ".join(map(str, d.get("keywords") or []))])
        if not nid or "©" in text or "copyright" in text.lower():
            continue                       # third-party material inside NASA's library is not public domain
        credit = str(d.get("secondary_creator") or d.get("photographer") or f"NASA {d.get('center') or ''}".strip())
        enc = quote(nid)
        page_url = f"https://images.nasa.gov/details/{enc}"
        out.append({"url": NASA_ASSET.format(id=enc, size="large"), "fallback": [NASA_ASSET.format(id=enc, size="orig")],
                    "credit": f"{credit} / NASA / {page_url}", "desc": f"{d.get('title') or ''} {str(d.get('description') or '')[:300]}",
                    "title": str(d.get("title") or nid),
                    "query": q, "source": "nasa", "artist": credit, "license": "NASA (public domain)", "page": page_url})
    return out


def nasa_images(c: httpx.Client, queries: list[str], terms: list[str], *, limit: int = 120, pages: int = 2) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        for q in queries:
            for it in _nasa(c, q, page):
                if it["url"] not in seen and mentions(it["desc"], terms):
                    seen.add(it["url"]); out.append(it)
            if len(out) >= limit:
                return out[:limit]
    return out


# ------------------------------------------------------------------ all sources

def gather_urls(*, topic: str, wiki_title: str | None, terms: list[str], queries: list[str], setting_queries: list[str],
                target: int, pexels_key: str, pixabay_key: str, use_commons: bool = True, use_nasa: bool = False,
                generic_floor: int = 0, transport=None) -> list[dict]:
    """Candidate URLs in priority order (see module docstring). Free sources never fail the run."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(items: list[dict], label: str) -> None:
        n = 0
        for it in items:
            if it["url"] not in seen:
                seen.add(it["url"]); it.setdefault("tier", "subject"); out.append(it); n += 1
        log.info("%s: %d candidates", label, n)

    with httpx.Client(timeout=30.0, transport=transport, headers={"User-Agent": UA}) as c:
        if use_commons and wiki_title:
            try:
                add(commons_article_images(c, wiki_title), "commons article")
            except Exception as exc:  # noqa: BLE001
                log.warning("commons article images unavailable: %s", exc)
        if use_nasa:
            try:
                nq = [topic] + ([wiki_title] if wiki_title and _norm(wiki_title) != _norm(topic) else [])
                add(nasa_images(c, nq, terms), "nasa")
            except Exception as exc:  # noqa: BLE001
                log.warning("nasa images unavailable: %s", exc)
        if use_commons and wiki_title and len(out) < target * 3:
            try:
                add(commons_category_images(c, wiki_title, seed=topic), "commons category")
            except Exception as exc:  # noqa: BLE001
                log.warning("commons category images unavailable: %s", exc)
    # Many free candidates fail later (small originals, CDN errors, vision rejects), so keep a wide
    # margin; downloads stop as soon as the reel is full. Stock only tops up a thin free pool.
    out = out[: target * 3]
    stock = collect_urls(queries, target=max(0, int(target * 1.5) - len(out)), pexels_key=pexels_key,
                         pixabay_key=pixabay_key, transport=transport, keywords=terms, setting_queries=setting_queries,
                         generic_floor=max(0, generic_floor - len(out)), exclude=seen)
    out += stock
    log.info("photos: %d candidates (%s)", len(out), ", ".join(f"{s} {sum(1 for i in out if i.get('source') == s)}"
                                                               for s in ("commons", "nasa", "pexels", "pixabay")))
    return out


# ------------------------------------------------------------------ download

def image_ok(path: str) -> bool:
    """Decodable and sharp enough for its display mode (landscape fills the frame; portrait is shown whole)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            w, h = im.size
            im.verify()
        return _big_enough(w, h)
    except Exception:  # noqa: BLE001
        return False


_pillow_ok = image_ok     # backwards-compatible name


def _fetch(c: httpx.Client, url: str) -> bytes | None:
    for attempt in range(2):
        try:
            with c.stream("GET", url) as r:
                if r.status_code == 429 and attempt == 0:
                    time.sleep(5)
                    continue
                if r.status_code != 200:
                    return None
                if int(r.headers.get("content-length") or 0) > MAX_BYTES:
                    return None
                data = r.read()
                return data if MIN_BYTES <= len(data) <= MAX_BYTES else None
        except httpx.HTTPError as exc:
            log.warning("image %s failed: %s", url, exc)
            return None
    return None


def download_range(urls: list[dict], out_dir: str, *, start: int = 0, want: int | None = None, transport=None,
                   verify_image=image_ok) -> tuple[list[str], int]:
    """Download ``urls[start:]`` in order until ``want`` good files exist; returns (paths, next index).

    Files are named by their index in ``urls`` so repeated top-up rounds never collide, and
    ``credits.json`` (source, author, licence, page per kept file) is extended, not overwritten.
    An item may carry ``fallback`` URLs (NASA: ~large, else ~orig).
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    cfile = Path(out_dir) / "credits.json"
    credits: list[dict] = json.loads(cfile.read_text(encoding="utf-8")) if cfile.exists() else []
    seen_hash: set[str] = set()
    for c in credits:
        f = Path(out_dir) / str(c.get("file") or "")
        if f.exists():
            seen_hash.add(hashlib.sha1(f.read_bytes()).hexdigest())
    paths: list[str] = []
    i = start
    with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True, headers={"User-Agent": UA}) as c:
        while i < len(urls):
            if want and len(paths) >= want:
                break
            it = urls[i]
            p = Path(out_dir) / f"{i:03d}.jpg"
            i += 1
            if not p.exists():
                data = None
                for u in [it["url"], *(it.get("fallback") or [])]:
                    data = _fetch(c, u)
                    if data:
                        break
                if not data:
                    continue
                p.write_bytes(data)
            h = hashlib.sha1(p.read_bytes()).hexdigest()
            if h in seen_hash or not verify_image(str(p)):
                p.unlink(missing_ok=True)
                continue
            seen_hash.add(h)
            paths.append(str(p))
            credits = [r for r in credits if r.get("file") != p.name]
            credits.append({"file": p.name, **{k: v for k, v in it.items() if k != "fallback"}})
    cfile.write_text(json.dumps(credits, indent=2, ensure_ascii=False), encoding="utf-8")
    return paths, i


def download_all(urls: list[dict], out_dir: str, *, transport=None, verify_image=image_ok,
                 want: int | None = None) -> list[str]:
    """Download in order until ``want`` good files exist (or the list runs out); extra URLs are the margin."""
    cfile = Path(out_dir) / "credits.json"
    if cfile.exists():
        cfile.unlink()
    return download_range(urls, out_dir, start=0, want=want, transport=transport, verify_image=verify_image)[0]


def gather_reel(urls: list[dict], out_dir: str, *, target: int, floor: int, classify_fn, setting_share: float = 0.3,
                rounds: int = 4, transport=None, verify_image=image_ok) -> tuple[list[str], dict[str, str]]:
    """Download → label → top up until the reel has ``target`` keepable photos or candidates run out.

    ``classify_fn(paths) -> {path: label}``. Returns (reel paths, labels of everything downloaded).
    """
    downloaded: list[str] = []
    labels: dict[str, str] = {}
    pos = 0
    for _ in range(rounds):
        have = len(select_reel(downloaded, labels, target=target, floor=0, setting_share=setting_share))
        if have >= target or pos >= len(urls):
            break
        new, pos = download_range(urls, out_dir, start=pos, want=int((target - have) * 1.4) + 4, transport=transport,
                                  verify_image=verify_image)
        labels.update(classify_fn(new) if new else {})
        downloaded += new
    reel = select_reel(downloaded, labels, target=target, floor=floor, setting_share=setting_share)
    log.info("reel: %d photos from %d downloaded (%d candidates seen of %d)", len(reel), len(downloaded), pos, len(urls))
    return reel, labels


# ------------------------------------------------------------------ vision

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


CLASSIFY_PROMPT = (
    "These {n} images are candidates for a calm, documentary-style video about: {subject}. Label each image:\n"
    '- "subject": it clearly shows {subject} itself, or a part, artifact, building, creature or close view that '
    "unmistakably belongs to it (photos, artworks and scientific renderings are fine).\n"
    '- "setting": it shows the real place or environment where {subject} is found, with no other main subject.\n'
    '- "reject": anything else — a different thing of the same kind (another planet, another ruin, another species), '
    "a vehicle, rocket or product merely named after it, people posing, text or document pages, maps, diagrams, "
    "charts, logos, or unrelated scenes.\n"
    'Return JSON {{"subject": [1-based indices], "setting": [...], "reject": [...]}} and nothing else.'
)


def classify(paths: list[str], *, subject: str, api_key: str, model: str = "gpt-4o-mini", batch: int = 8,
             transport=None) -> dict[str, str]:
    """path → "subject" | "setting" | "reject" | "unknown" (no key, or the batch's API call failed)."""
    labels = {p: "unknown" for p in paths}
    if not api_key or not paths:
        return labels
    with httpx.Client(timeout=120.0, transport=transport, headers={"Authorization": f"Bearer {api_key}"}) as c:
        for i in range(0, len(paths), batch):
            group, images_part = [], []
            for p in paths[i:i + batch]:
                try:
                    images_part.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64_small(p)}", "detail": "low"}})
                    group.append(p)
                except Exception as exc:  # noqa: BLE001 — unreadable file: never shown
                    log.warning("could not encode %s: %s", p, exc)
                    labels[p] = "reject"
            if not group:
                continue
            content = [{"type": "text", "text": CLASSIFY_PROMPT.format(n=len(group), subject=subject)}, *images_part]
            try:
                r = c.post(VISION_URL, json={"model": model, "messages": [{"role": "user", "content": content}],
                                             "response_format": {"type": "json_object"}, "max_tokens": 200})
                r.raise_for_status()
                data = json.loads(r.json()["choices"][0]["message"]["content"])
            except Exception as exc:  # noqa: BLE001 — fail open: unknown, not rejected
                log.warning("vision check failed for a batch (%s); keeping it", exc)
                continue

            def idx(key: str) -> set[int]:
                return {int(k) - 1 for k in (data.get(key) or []) if str(k).lstrip("-").isdigit()}

            subj, sett = idx("subject") | idx("keep"), idx("setting")
            for j, p in enumerate(group):
                labels[p] = "subject" if j in subj else "setting" if j in sett else "reject"
    counts = {k: sum(1 for v in labels.values() if v == k) for k in ("subject", "setting", "reject", "unknown")}
    log.info("vision labels: %s", counts)
    return labels


def verify_relevance(paths: list[str], *, subject: str, api_key: str, model: str = "gpt-4o-mini", batch: int = 8,
                     transport=None, keep_min: int = 0) -> list[str]:
    """Keep subject + setting shots (order preserved). If that would leave fewer than ``keep_min``,
    keep everything — better a loose photo than no video."""
    if not api_key or not paths:
        return paths
    labels = classify(paths, subject=subject, api_key=api_key, model=model, batch=batch, transport=transport)
    kept = [p for p in paths if labels[p] != "reject"]
    if len(kept) < keep_min:
        log.warning("vision check would leave %d < %d photos; keeping all", len(kept), keep_min)
        return paths
    log.info("vision check: kept %d, dropped %d off-topic photos", len(kept), len(paths) - len(kept))
    return kept


def select_reel(paths: list[str], labels: dict[str, str], *, target: int, floor: int, setting_share: float = 0.3) -> list[str]:
    """Subject shots first (source priority order), then setting shots up to ``setting_share``;
    rejected shots only if needed to reach ``floor``."""
    subject = [p for p in paths if labels.get(p, "unknown") in ("subject", "unknown")]
    setting = [p for p in paths if labels.get(p) == "setting"]
    room = max(0, target - len(subject))
    out = subject[:target] + setting[:min(int(target * setting_share), room)]
    if len(out) < floor:                      # last resort: more setting shots, then rejects (better than no video)
        spare = [p for p in setting if p not in out] + [p for p in paths if labels.get(p) == "reject"]
        out += spare[: floor - len(out)]
    return out


PICK_PROMPT = (
    "Which ONE of these {n} images would make the best YouTube thumbnail for a calm sleep video about {subject}? "
    "Choose the image that shows {subject} most clearly and recognizably — the classic view people picture — "
    "well composed, not cluttered, no text. Return JSON {{\"best\": <1-based index>}}."
)


def pick_iconic(paths: list[str], *, subject: str, api_key: str, model: str = "gpt-4o-mini", transport=None) -> str | None:
    if not api_key or not paths:
        return None
    content = [{"type": "text", "text": PICK_PROMPT.format(n=len(paths), subject=subject)}]
    for p in paths:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64_small(p)}", "detail": "low"}})
    try:
        with httpx.Client(timeout=120.0, transport=transport, headers={"Authorization": f"Bearer {api_key}"}) as c:
            r = c.post(VISION_URL, json={"model": model, "messages": [{"role": "user", "content": content}],
                                         "response_format": {"type": "json_object"}, "max_tokens": 50})
            r.raise_for_status()
            best = int(json.loads(r.json()["choices"][0]["message"]["content"]).get("best"))
        return paths[best - 1] if 1 <= best <= len(paths) else None
    except Exception as exc:  # noqa: BLE001
        log.warning("thumbnail pick failed (%s); using the heuristic", exc)
        return None
