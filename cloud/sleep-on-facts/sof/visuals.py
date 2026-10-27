"""Narration-aware photos: show what the narrator is talking about, when it is said.

The slideshow used to be a ~50-minute reel of subject photos looped under three hours of narration,
so the screen had nothing to do with the words. The video is now a sequence of fixed slots
(``dwell`` seconds each; the renderer crossfades between them) and this module picks every slot's
photo:

1. ``extract_mentions`` — one cheap LLM call per movement lists the photographable, NAMED things it
   talks about (moons, temples, statues, spacecraft, species …) with short phrases quoted from the
   text. A quote that is not in the text is dropped: it could not be timed.
2. ``resolve_titles`` (optional, a few API requests) — canonical Wikipedia titles, so "Cassini
   (spacecraft)" and "Cassini–Huygens" are one thing and a disambiguation page never feeds photos.
3. ``mention_times`` — each quote is found in the TTS chunk that speaks it; its character position,
   scaled by that chunk's measured mp3 duration, is the second it is heard.
4. ``rank_entities`` → ``gather_entity_photos`` — photos of the most-discussed things from their own
   Wikipedia article / Commons category (NASA for celestial things), vision-checked like the reel.
5. ``plan_slots`` — per slot: the thing just named, held while it is discussed, else the chapter's
   own photos, else the subject reel. Pure and deterministic; returns the slot list plus a per-slot
   plan explaining each choice (for timeline.json, to check alignment after a real run).

Pipeline order: extract_mentions(secs) → resolve_titles → mention_times(chunks of the SAME secs) →
rank_entities → gather_entity_photos → build_section_pools + windows_from_starts → plan_slots.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from sof import images

log = logging.getLogger("sof.visuals")

KINDS = ("place", "building", "monument", "landform", "water", "celestial", "spacecraft", "artwork", "artefact",
         "species", "person", "other")
NASA_KINDS = frozenset({"celestial", "spacecraft"})     # NASA's library only helps with space things
MAX_ITEMS = 12
# Commons-category budget per thing (the subject reel keeps gather_urls' 250 + 6×60): a thing needs ~8
# good photos, and 80 things × the full crawl would be ~1,500 needless API requests.
ENTITY_CATEGORY_LIMIT, ENTITY_SUBCATS, ENTITY_PER_SUBCAT = 100, 2, 30

_KIND_ALIASES = {
    "city": "place", "town": "place", "village": "place", "site": "place", "region": "place", "country": "place",
    "temple": "building", "palace": "building", "church": "building", "castle": "building", "tower": "building",
    "bridge": "building", "structure": "building", "gate": "building", "shrine": "building",
    "statue": "artwork", "sculpture": "artwork", "relief": "artwork", "painting": "artwork", "mural": "artwork",
    "fresco": "artwork", "artifact": "artefact", "object": "artefact", "manuscript": "artefact", "tool": "artefact",
    "mountain": "landform", "volcano": "landform", "canyon": "landform", "cave": "landform", "island": "landform",
    "valley": "landform", "desert": "landform", "glacier": "landform",
    "river": "water", "lake": "water", "sea": "water", "ocean": "water", "waterfall": "water", "bay": "water",
    "moon": "celestial", "planet": "celestial", "star": "celestial", "nebula": "celestial", "galaxy": "celestial",
    "comet": "celestial", "asteroid": "celestial", "ring": "celestial", "storm": "celestial", "crater": "celestial",
    "satellite": "celestial", "probe": "spacecraft", "telescope": "spacecraft", "instrument": "spacecraft",
    "mission": "spacecraft", "rover": "spacecraft", "lander": "spacecraft", "orbiter": "spacecraft",
    "animal": "species", "plant": "species", "tree": "species", "bird": "species", "fish": "species",
    "insect": "species", "flower": "species", "fungus": "species", "mammal": "species",
    "people": "person", "ruler": "person", "king": "person", "queen": "person", "scientist": "person",
}

MENTIONS_PROMPT = """You are the picture editor of a calm, narrated documentary about {{topic}}. While the
narration plays, the video shows one photograph at a time, and each photo should show what the narrator
is talking about at that moment.

Below is one section of the narration, titled "{{heading}}". List the PHOTOGRAPHABLE, NAMED things it
talks about:
- specific places, sites, buildings, temples, monuments, landforms, rivers, lakes and seas;
- moons, planets, stars and nebulae, and named features such as rings, gaps, storms and craters;
- spacecraft, probes, telescopes and instruments;
- artworks, statues, reliefs and named artefacts;
- named species and plants;
- people, but only those of whom statues, portraits or photographs exist.

Do NOT list:
- {{topic}} itself: its own photos are shown whenever nothing more specific is being discussed;
- abstract ideas, events, eras, processes, measurements, or generic nouns ("a river", "trade",
  "gravity", "the monsoon", "pilgrims", "the stars");
- things mentioned only in passing or only as a comparison.

For each thing give:
- "name": its name as a viewer would know it;
- "wiki_title": the exact title of its English Wikipedia article (your best guess; add the
  disambiguator when the plain name is ambiguous, e.g. "Titan (moon)");
- "kind": one of {{kinds}};
- "quotes": 1-3 short phrases (4-12 words each) copied EXACTLY, word for word, from the section text,
  where the thing is named or discussed, first mention first. Never paraphrase, never join two sentences.

At most {{max_items}} things, the most-discussed first. If nothing qualifies, return an empty list.

SECTION TEXT:
<<<
{{text}}
>>>

Return JSON: {"items": [{"name": "...", "wiki_title": "...", "kind": "...", "quotes": ["...", "..."]}]}"""


# ------------------------------------------------------------------ text matching

def _fill(template: str, **kw) -> str:
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def _norm_map(text: str) -> tuple[str, list[int]]:
    """``text`` lower-cased, accents folded and every run of other characters turned into one space,
    plus the index in ``text`` of each output character — so a quote found in the normalised text
    maps back to a character offset, and from there to a time in the chunk's audio."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text or ""):
        letters = [c for c in unicodedata.normalize("NFKD", ch).lower() if c.isalnum() and not unicodedata.combining(c)]
        if letters:
            out.extend(letters)
            idx.extend([i] * len(letters))
        elif out and out[-1] != " ":
            out.append(" ")
            idx.append(i)
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def _norm(text: str) -> str:
    return _norm_map(text)[0]


def _find_words(hay: str, needle: str, start: int = 0) -> int:
    """First index of ``needle`` in ``hay`` on word boundaries (both normalised), or -1:
    "titan" must not match inside "titanic"."""
    if not needle:
        return -1
    i = hay.find(needle, start)
    while i >= 0:
        end = i + len(needle)
        if (i == 0 or hay[i - 1] == " ") and (end == len(hay) or hay[end] == " "):
            return i
        i = hay.find(needle, i + 1)
    return -1


def _base_title(title: str) -> str:
    """"Titan (moon)" → "Titan"; "Rings_of_Saturn#Cassini" → "Rings of Saturn"."""
    return re.sub(r"\s*\([^)]*\)", "", str(title or "").split("#")[0].replace("_", " ")).strip()


def entity_key(name: str, wiki_title: str = "") -> str:
    """Stable id of one thing across sections: its Wikipedia title (else its name) without the
    disambiguator, accents, punctuation or a leading "the" — "Titan (moon)" and "Titan" → "titan"."""
    for raw in (wiki_title, name):
        k = _norm(_base_title(raw))
        k = k[4:] if k.startswith("the ") else k
        if k:
            return k
    return ""


def _clean(v: object) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip().strip("\"'")


def _kind(v: object) -> str:
    """The LLM's kind folded onto ``KINDS`` ("temple" → building, "natural satellite" → celestial)."""
    words = _norm(str(v or "")).split()
    for w in [" ".join(words), *reversed(words)]:
        for cand in (w, w[:-1] if w.endswith("s") else w):
            if cand in KINDS:
                return cand
            if cand in _KIND_ALIASES:
                return _KIND_ALIASES[cand]
    return "other"


# ------------------------------------------------------------------ 1. what is mentioned

def _section_items(data: object, text: str, si: int, max_items: int, skip_keys: set[str] = frozenset()) -> list[dict]:
    """Validated rows of one section: quotes not found verbatim (modulo case, spacing and
    punctuation) are dropped, and so is an item left without any quote. Items whose key is in
    ``skip_keys`` (the topic itself) do not use up the ``max_items`` budget."""
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    hay = _norm(text)
    out: list[dict] = []
    kept = dropped = 0
    for it in items:
        if kept >= max_items:
            break
        if not isinstance(it, dict):
            continue
        name, wiki = _clean(it.get("name")), _clean(it.get("wiki_title"))
        if not (name or wiki) or entity_key(name, wiki) in skip_keys:
            continue
        raw_quotes = it.get("quotes") if it.get("quotes") is not None else it.get("quote")
        quotes = [raw_quotes] if isinstance(raw_quotes, str) else [q for q in (raw_quotes or []) if isinstance(q, str)]
        names = {_norm(name), _norm(_base_title(wiki))} - {""}
        good: list[tuple[str, int]] = []
        for q in quotes:
            nq = _norm(q)
            # A one-word "quote" is only trusted when it IS the name (its first occurrence is the first mention).
            pos = _find_words(hay, nq) if (len(nq.split()) >= 2 or nq in names) else -1
            if pos < 0:
                dropped += 1
                continue
            if pos not in {p for _, p in good}:
                good.append((_clean(q), pos))
            if len(good) == 3:
                break
        if not good:
            continue
        for q, pos in good:
            out.append({"name": name or _base_title(wiki), "wiki_title": wiki, "kind": _kind(it.get("kind")), "section": si,
                        "quote": q, "pos": round(pos / max(1, len(hay)), 4), "rank": kept})
        kept += 1
    log.info("section %d: %d things, %d quotes kept, %d not in the text", si, kept, len(out), dropped)
    return out


def _assign_keys(rows: list[dict], *, topic: str, wiki_title: str = "") -> list[dict]:
    """Give every row its entity ``key``. A row without a wiki title borrows the key of a titled row
    with the same name ("Titan" → "titan" from "Titan (moon)"). Rows about the topic itself are dropped:
    its photos are the fallback reel."""
    topic_keys = {entity_key(topic), entity_key("", wiki_title)} - {""}
    alias: dict[str, str] = {}
    for r in rows:
        if r["wiki_title"]:
            k = entity_key(r["name"], r["wiki_title"])
            alias.setdefault(entity_key(r["name"]), k)
            alias.setdefault(entity_key("", r["wiki_title"]), k)
    out: list[dict] = []
    for r in rows:
        k = entity_key(r["name"], r["wiki_title"]) if r["wiki_title"] else alias.get(entity_key(r["name"]), entity_key(r["name"]))
        if k and k not in topic_keys:
            out.append({"key": k, **r})
    return out


def extract_mentions(llm, sections: list[dict], *, topic: str, model: str | None = None, max_workers: int = 6,
                     wiki_title: str = "", max_items: int = MAX_ITEMS) -> list[dict]:
    """Photographable named things each section talks about, one row per (thing, quote):
    ``{"key", "name", "wiki_title", "kind", "section", "quote", "pos", "rank"}``.

    ``sections`` must be the exact list given to ``tts.chunk_sections`` (same indices). ``pos`` is the
    quote's position in its section text (0-1, a timing fallback); ``rank`` is the thing's order in its
    section, most-discussed first. One LLM call per section on a thread pool; a section whose call
    fails yields nothing (logged) — pictures are an enhancement and must never cost the video.
    """
    texts = [str(s.get("body") or "") for s in sections]
    topic_keys = {entity_key(topic), entity_key("", wiki_title)} - {""}

    def one(si: int) -> list[dict]:
        if not texts[si].strip():
            return []
        prompt = _fill(MENTIONS_PROMPT, topic=topic, heading=_clean(sections[si].get("heading")) or f"Part {si + 1}",
                       kinds=", ".join(KINDS), max_items=max_items, text=texts[si])
        data = llm.generate_json(prompt, max_tokens=2500, temperature=0.2, model=model)
        return _section_items(data, texts[si], si, max_items, topic_keys)

    per_section = _parallel(one, range(len(sections)), max_workers, label=lambda si: f"mentions for section {si}")
    rows = _assign_keys([r for rows in per_section for r in rows], topic=topic, wiki_title=wiki_title)
    log.info("mentions: %d quotes of %d things in %d sections", len(rows), len({r["key"] for r in rows}), len(sections))
    return rows


# ------------------------------------------------------------------ 2. canonical titles (optional)

def _lookup_titles(c: httpx.Client, titles: list[str]) -> dict[str, dict]:
    """title → {"title": canonical or None (missing), "disambiguation": bool}. Whole-page redirects are
    followed; a redirect to a SECTION of another article ("Cassini Division" → "Rings of Saturn#…")
    keeps its own title: it is a distinct named feature, not a synonym."""
    out: dict[str, dict] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        q = images._api(c, images.ENWIKI_API, action="query", titles="|".join(batch), redirects=1, prop="pageprops",
                        ppprop="disambiguation").get("query") or {}
        norm = {n.get("from"): n.get("to") for n in q.get("normalized") or []}
        redir = {r.get("from"): r for r in q.get("redirects") or []}
        pages = {p.get("title"): p for p in q.get("pages") or []}
        for t in batch:
            t1 = norm.get(t, t)
            r = redir.get(t1)
            frag = bool(r and r.get("tofragment"))
            page = pages.get(r.get("to") if r else t1)
            if page is None or page.get("missing") or page.get("invalid"):
                out[t] = {"title": None, "disambiguation": False}
            else:
                out[t] = {"title": t1 if frag else str(page.get("title")),
                          "disambiguation": not frag and "disambiguation" in (page.get("pageprops") or {})}
    return out


def _search_title(c: httpx.Client, name: str, topic: str, topic_keys: set[str]) -> str | None:
    """Best article for a thing whose given title is missing or ambiguous: full-text search for
    "<name> <topic>", keeping only a hit that shares a distinctive word with the name."""
    data = images._api(c, images.ENWIKI_API, action="query", list="search", srsearch=f"{name} {topic}", srlimit=5,
                       srnamespace=0, srprop="")
    words = {w for w in _norm(name).split() if len(w) >= 4 and w not in images.GENERIC_WORDS}
    for hit in (data.get("query") or {}).get("search") or []:
        title = str(hit.get("title") or "")
        if not title or "disambiguation" in title.lower() or entity_key("", title) in topic_keys:
            continue
        if words & set(_norm(title).split()):
            return title
    return None


def resolve_titles(mentions: list[dict], *, topic: str, wiki_title: str = "", transport=None) -> list[dict]:
    """Canonical English Wikipedia titles for the mentions, re-keyed so variants of one thing merge.

    One request per 50 titles, plus one search per thing whose title is missing or a disambiguation
    page (rare). Rows that turn out to be the topic itself are dropped. Any network failure returns
    the mentions unchanged: titles are an optimisation, never a reason to fail.
    """
    rows = [dict(m) for m in mentions]
    titles = sorted(t for t in {_title_key(m) for m in rows} | {wiki_title or topic} if t and "|" not in t)
    if not titles:
        return rows
    try:
        with httpx.Client(timeout=30.0, transport=transport, headers={"User-Agent": images.UA}) as c:
            info = _lookup_titles(c, titles)
            topic_info = info.get(wiki_title or topic) or {}
            topic_keys = {entity_key(topic), entity_key("", wiki_title), entity_key("", topic_info.get("title") or "")} - {""}
            fixed: dict[str, str | None] = {}
            for t in titles:
                got = info.get(t) or {}
                if got.get("title") and not got.get("disambiguation"):
                    fixed[t] = got["title"]
            for m in rows:
                t = _title_key(m)
                if t in fixed or t not in info:
                    continue
                try:
                    fixed[t] = _search_title(c, m.get("name") or t, topic, topic_keys)
                except httpx.HTTPError as exc:
                    log.warning("title search for %r failed: %s", t, exc)
                    fixed[t] = None
    except Exception as exc:  # noqa: BLE001 — never fail the video over title lookups
        log.warning("wikipedia title lookup failed (%s); keeping the LLM's titles", exc)
        return rows
    out: list[dict] = []
    for m in rows:
        canon = fixed.get(_title_key(m))
        if canon:
            m["wiki_title"] = canon
            m["key"] = entity_key(m.get("name", ""), canon)
        if m.get("key") and m["key"] not in topic_keys:
            out.append(m)
    log.info("titles: %d asked, %d resolved, %d things after merging", len(titles), sum(1 for v in fixed.values() if v),
             len({m["key"] for m in out}))
    return out


def _title_key(m: dict) -> str:
    t = str(m.get("wiki_title") or "").split("#")[0].strip()
    return t or str(m.get("name") or "")


# ------------------------------------------------------------------ 3. when it is said

def mention_times(mentions: list[dict], *, chunks: list[str], owner: list[int], chunk_durations: list[float]) -> list[dict]:
    """Each mention with ``"time"`` (seconds into the narration) and ``"timing"`` ("quote" | "estimate").

    ``chunks``/``owner`` come from ``tts.chunk_sections`` (chunk i belongs to section ``owner[i]``) and
    ``chunk_durations[i]`` is the measured length of chunk i's mp3, so chunk i starts at the sum of the
    ones before it. The quote is searched in its section's chunks (joined, so a quote spanning a chunk
    boundary still matches); the time is the chunk start plus the character offset of the thing's NAME
    inside the quote (else the quote start) as a share of the chunk, times the chunk's duration —
    speech is close to constant characters per second, and the error resets at every chunk (measured
    on 2,000-char chunks of real speech, ffmpeg's flite voice: sentence starts off by 0.5 s on average,
    2 s at most — far inside a 20 s slot; a TTS with long pauses will be somewhat worse). A quote
    that is not found falls back to its section-relative position (``pos``) across the section's audio.
    Mentions of sections without chunks are dropped. Sorted by time.
    """
    if not (len(chunks) == len(owner) == len(chunk_durations)):
        raise ValueError(f"chunks ({len(chunks)}), owner ({len(owner)}) and chunk_durations "
                         f"({len(chunk_durations)}) must have the same length")
    starts: list[float] = []
    t = 0.0
    for d in chunk_durations:
        starts.append(t)
        t += max(0.0, float(d))
    by_section: dict[int, list[int]] = {}
    for ci, si in enumerate(owner):
        by_section.setdefault(int(si), []).append(ci)
    cache: dict[int, tuple[str, list[tuple[int, int]]]] = {}

    def section_text(si: int) -> tuple[str, list[tuple[int, int]]]:
        if si not in cache:
            text, where = "", []
            for ci in by_section.get(si, []):
                nt, idx = _norm_map(chunks[ci])
                if not nt:
                    continue
                if text:
                    text += " "
                    where.append((ci, 0))
                text += nt
                where.extend((ci, j) for j in idx)
            cache[si] = (text, where)
        return cache[si]

    out: list[dict] = []
    for m in mentions:
        si = int(m.get("section", -1))
        cis = by_section.get(si)
        if not cis:
            log.warning("mention %r: section %s has no narration chunks; dropped", m.get("name"), si)
            continue
        hay, where = section_text(si)
        nq = _norm(str(m.get("quote") or ""))
        p = _find_words(hay, nq) if nq else -1
        if p >= 0:
            span = hay[p:p + len(nq)]
            offs = [j for j in (_find_words(span, n) for n in _name_variants(m)) if j >= 0]
            ci, off = where[p + (min(offs) if offs else 0)]
            when = starts[ci] + off / max(1, len(chunks[ci])) * float(chunk_durations[ci])
            timing = "quote"
        else:
            a = starts[cis[0]]
            b = starts[cis[-1]] + float(chunk_durations[cis[-1]])
            pos = m.get("pos")
            frac = min(1.0, max(0.0, float(pos))) if isinstance(pos, (int, float)) else 0.5
            when, timing = a + frac * (b - a), "estimate"
        out.append({**m, "time": when, "timing": timing})     # unrounded: a section's first word is AT its start
    out.sort(key=lambda r: (r["time"], r.get("section", 0), str(r.get("key")), str(r.get("quote"))))
    n_est = sum(1 for r in out if r["timing"] == "estimate")
    if n_est:
        log.warning("mention times: %d of %d estimated (quote not found in the chunks)", n_est, len(out))
    return out


def _name_variants(m: dict) -> list[str]:
    """Normalised ways the thing's name may appear inside its quote, most specific first."""
    full = [_norm(m.get("name") or ""), _norm(_base_title(m.get("wiki_title") or ""))]
    words = [w for w in " ".join(full).split() if len(w) >= 4 and w not in images.GENERIC_WORDS]
    seen: list[str] = []
    for v in full + words:
        if v and v not in seen:
            seen.append(v)
    return seen


# ------------------------------------------------------------------ 4. photos of each thing

def rank_entities(mentions: list[dict], *, max_entities: int = 80, per_section: int = 3) -> list[dict]:
    """Distinct things, most worth fetching first: ``{"key", "name", "wiki_title", "kind", "mentions",
    "sections"}``.

    Every section first keeps its top ``per_section`` things (most quotes there, then the LLM's
    "most-discussed first" order), so no chapter is left without pictures; the rest of the
    ``max_entities`` budget goes to the things quoted most across the video. Sorted by mentions, then
    first appearance. The most common name / title / kind variant wins.
    """
    stats: dict[str, dict] = {}
    for i, m in enumerate(mentions):
        k = str(m.get("key") or "")
        if not k:
            continue
        s = stats.setdefault(k, {"n": 0, "first": (math.inf, math.inf), "names": Counter(), "titles": Counter(),
                                 "kinds": Counter(), "per_sec": Counter(), "best_rank": {}, "order": i})
        s["n"] += 1
        sec = int(m.get("section", 0))
        s["first"] = min(s["first"], (float(m["time"]) if "time" in m else sec, float(m.get("pos") or 0.0)))
        s["names"][str(m.get("name") or k)] += 1
        if m.get("wiki_title"):
            s["titles"][str(m["wiki_title"])] += 1
        s["kinds"][str(m.get("kind") or "other")] += 1
        s["per_sec"][sec] += 1
        rank = m.get("rank")
        s["best_rank"][sec] = min(s["best_rank"].get(sec, 10 ** 6), int(rank) if isinstance(rank, (int, float)) else 10 ** 6)
    if not stats:
        return []
    sections = sorted({sec for s in stats.values() for sec in s["per_sec"]})
    quota = max(1, min(per_section, max_entities // max(1, len(sections))))
    chosen: list[str] = []
    for sec in sections:
        local = sorted((k for k, s in stats.items() if sec in s["per_sec"]),
                       key=lambda k: (-stats[k]["per_sec"][sec], stats[k]["best_rank"][sec], stats[k]["first"]))
        chosen += [k for k in local[:quota] if k not in chosen]
    overall = sorted(stats, key=lambda k: (-stats[k]["n"], stats[k]["first"], stats[k]["order"]))
    chosen = chosen[:max_entities]
    chosen += [k for k in overall if k not in chosen][:max(0, max_entities - len(chosen))]
    chosen.sort(key=lambda k: (-stats[k]["n"], stats[k]["first"], stats[k]["order"]))

    def top(counter: Counter, default: str) -> str:
        # most common; ties → the more specific (has a disambiguator), then the first seen
        order = list(counter)
        return max(order, key=lambda v: (counter[v], "(" in v, -order.index(v))) if counter else default

    return [{"key": k, "name": top(stats[k]["names"], k), "wiki_title": top(stats[k]["titles"], ""),
             "kind": top(stats[k]["kinds"], "other"), "mentions": stats[k]["n"], "sections": sorted(stats[k]["per_sec"])}
            for k in chosen]


def _folder_name(key: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:48] or "thing"
    return f"{slug}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:6]}"


def _sha1(path: str) -> str | None:
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _parallel(fn, items, workers: int, *, label) -> list:
    """``[fn(x) for x in items]`` on a small thread pool, in input order; a failing item yields []."""
    items = list(items)
    out: list = [[] for _ in items]
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=max(1, min(int(workers), len(items)))) as pool:
        futures = [pool.submit(fn, x) for x in items]
        for i, fut in enumerate(futures):
            try:
                out[i] = fut.result()
            except Exception as exc:  # noqa: BLE001 — one thing without photos must not cost the video
                log.warning("%s failed: %s", label(items[i]), exc)
    return out


def gather_entity_photos(entities: list[dict], out_dir: str, *, topic: str, use_nasa: bool, classify_fn,
                         per_entity: int = 6, max_entities: int = 80, exclude_urls=(), exclude_paths=(),
                         transport=None, workers: int = 3, keep_labels=("subject",), rounds: int = 2,
                         budget_seconds: float | None = None, clock=time.monotonic) -> dict[str, list[str]]:
    """key → up to ``per_entity`` vision-approved photos (absolute paths, best first) of each thing.

    ``entities`` in priority order (see ``rank_entities``); only the first ``max_entities`` are fetched
    and only they appear in the result. For each: ``images.gather_urls`` (the thing's own Wikipedia
    article photos → NASA if it is celestial/a spacecraft and ``use_nasa`` → its Commons category, on a
    small budget; licence-filtered), then ``download_range`` into ``out_dir/<key-slug>/`` (which writes
    that folder's credits.json), then ``classify_fn(paths, subject)`` with subject "<name> (<topic>)";
    only ``keep_labels`` survive. A second round tops up when vision rejects too many.

    A photo URL is used by one thing only — the higher-ranked one — and never if it is in
    ``exclude_urls`` (the subject reel's URLs); identical files are dropped across things and against
    ``exclude_paths`` (the reel's files), so two consecutive slots can never show one picture under two
    names. A thing without usable photos maps to []. Never raises for one thing's failure.

    ``budget_seconds`` bounds the whole stage: things whose turn comes after it are skipped (they map
    to []), so a slow day on Wikimedia costs some pictures, never the run.
    """
    stop_at = clock() + budget_seconds if budget_seconds is not None else None

    def over_budget() -> bool:
        return stop_at is not None and clock() > stop_at

    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    ents: list[dict] = []
    for e in entities:
        k = str(e.get("key") or "")
        if k and k not in {x["key"] for x in ents}:
            ents.append({**e, "key": k})
        if len(ents) >= max_entities:
            break

    def name_of(e: dict) -> str:
        return str(e.get("name") or e["key"])

    def candidates(e: dict) -> list[dict]:
        if over_budget():
            return []
        name = name_of(e)
        title = str(e.get("wiki_title") or "") or name
        return images.gather_urls(topic=name, wiki_title=title, terms=images.name_terms(name, title), queries=[],
                                  setting_queries=[], target=per_entity * 2, pexels_key="", pixabay_key="",
                                  use_commons=True, use_nasa=bool(use_nasa) and str(e.get("kind") or "") in NASA_KINDS,
                                  generic_floor=0, transport=transport, category_limit=ENTITY_CATEGORY_LIMIT,
                                  category_subcats=ENTITY_SUBCATS, category_per_subcat=ENTITY_PER_SUBCAT)

    found = _parallel(candidates, ents, workers, label=lambda e: f"photo search for {name_of(e)!r}")
    # Deterministic de-duplication: in rank order, each thing reserves the candidates it may download.
    taken = set(exclude_urls or ())
    window = (per_entity + 2) * max(1, rounds) + 4
    reserved: list[list[dict]] = []
    for urls in found:
        mine = [u for u in urls if u.get("url") and u["url"] not in taken][:window]
        taken.update(u["url"] for u in mine)
        reserved.append(mine)

    def fetch(i: int) -> list[str]:
        e, urls = ents[i], reserved[i]
        if not urls or over_budget():
            return []
        folder = root / _folder_name(e["key"])
        if folder.exists():                       # a stale partial download: download_range would treat
            for f in folder.iterdir():            # its files as duplicates of themselves
                if f.is_file():
                    f.unlink()
        subject = f"{name_of(e)} ({topic})"
        kept: list[str] = []
        pos = 0
        for _ in range(max(1, rounds)):
            need = per_entity - len(kept)
            if need <= 0 or pos >= len(urls):
                break
            if over_budget():
                break
            new, pos = images.download_range(urls, str(folder), start=pos, want=need + 2, transport=transport)
            if new:
                labels = classify_fn(new, subject) or {}
                kept += [p for p in new if labels.get(p) in keep_labels]
        log.info("photos of %r: %d candidates, %d kept", name_of(e), len(urls), min(len(kept), per_entity))
        return kept[:per_entity]

    kept_lists = _parallel(fetch, range(len(ents)), workers, label=lambda i: f"photos of {name_of(ents[i])!r}")
    seen = {h for h in (_sha1(p) for p in exclude_paths or ()) if h}
    out: dict[str, list[str]] = {}
    for e, paths in zip(ents, kept_lists):
        good: list[str] = []
        for p in paths:
            h = _sha1(p)
            if h and h not in seen:
                seen.add(h)
                good.append(str(Path(p).resolve()))
        out[e["key"]] = good
    log.info("thing photos: %d of %d things have photos, %d photos", sum(1 for v in out.values() if v), len(out),
             sum(len(v) for v in out.values()))
    return out


def credits_by_path(out_dir: str) -> dict[str, dict]:
    """Absolute photo path → its credits.json row, across ``out_dir/credits.json`` and every
    ``out_dir/*/credits.json``. Keyed by full path because every folder numbers its files from
    000.jpg, so the reel's by-file-name lookup (``captions.captions_for``) would mix things up."""
    root = Path(out_dir)
    out: dict[str, dict] = {}
    for cfile in [root / "credits.json", *sorted(root.glob("*/credits.json"))]:
        if not cfile.exists():
            continue
        try:
            rows = json.loads(cfile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("unreadable %s: %s", cfile, exc)
            continue
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and r.get("file") and (cfile.parent / str(r["file"])).is_file():
                out[str((cfile.parent / str(r["file"])).resolve())] = r
    return out


# ------------------------------------------------------------------ 5. the slot plan

def build_section_pools(mentions: list[dict], entity_photos: dict[str, list[str]]) -> dict[int, list[str]]:
    """section → photos of the things it mentions, things in order of first mention and interleaved
    (a[0], b[0], c[0], a[1], …) so filler slots vary instead of showing one thing six times."""
    order: dict[int, list[str]] = {}
    for m in sorted(mentions, key=lambda m: (int(m.get("section", 0)), float(m.get("time", m.get("pos", 0.0)) or 0.0))):
        keys = order.setdefault(int(m.get("section", 0)), [])
        if m.get("key") and m["key"] not in keys:
            keys.append(m["key"])
    pools: dict[int, list[str]] = {}
    for sec, keys in order.items():
        lists = [entity_photos.get(k) or [] for k in keys]
        pool: list[str] = []
        for j in range(max((len(x) for x in lists), default=0)):
            pool += [x[j] for x in lists if j < len(x) and x[j] not in pool]
        if pool:
            pools[sec] = pool
    return pools


def windows_from_starts(starts: list[float], total: float) -> list[tuple[float, float]]:
    """[(start, end)] per section from ``tts.section_starts`` and the narration's duration."""
    return [(float(s), float(starts[i + 1]) if i + 1 < len(starts) else float(total)) for i, s in enumerate(starts)]


def _unique(paths) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for p in paths or ():
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _section_at(windows: list[tuple[float, float]], t: float) -> int | None:
    """Index of the window holding ``t``; outside all of them, the nearest one."""
    if not windows:
        return None
    for i, (a, b) in enumerate(windows):
        if a <= t < b:
            return i
    return min(range(len(windows)), key=lambda i: (min(abs(t - windows[i][0]), abs(t - windows[i][1])), i))


def plan_slots(n_slots: int, *, dwell: float, mentions: list[dict], entity_photos: dict[str, list[str]],
               section_windows: list[tuple[float, float]] | None, fallback: list[str],
               section_pools: dict[int, list[str]] | None = None, lead: float = 5.0, hold: float = 75.0,
               avoid_repeat_within: int = 8, recall: float = 150.0) -> tuple[list[str], list[dict]]:
    """One photo per slot; slot k covers narration [k·dwell, (k+1)·dwell). Pure and deterministic.

    * **Opening.** Slot 0 is ``fallback[0]``, the best subject photo: the video opens on the subject.
    * **Claims.** A mention at time t claims slot ``ceil((t − lead) / dwell)`` — the first slot that does
      not start more than ``lead`` s before the thing is named. Its photo is on screen at most ``lead`` s
      early and at most ``dwell − lead`` s late (5 s / 15 s by default; the quote timing itself is good to
      a few seconds, and the renderer's crossfade begins ``crossfade`` s before each slot). When several
      mentions claim one slot, the one named LAST wins: the narrator has moved on to it, so it is what is
      heard for most of that slot. The others are skipped rather than shown late.
    * **Hold.** The winner stays on screen in the following slots, a different photo of it each slot,
      until another mention claims a slot, ``hold`` s have passed since it was last named (every new
      mention of it renews the hold), the narration enters another section (a new movement is a new
      subject), or its photos run out (a run never shows a photo twice, nor one seen in the last
      ``avoid_repeat_within`` slots).
    * **A mention of a thing without photos** that wins a slot ends the current run (the narrator has
      moved on, so the previous thing's photos would now be wrong) and the slot is filled as below.
    * **Filler slots** take the least recently shown photo of the current section's pool (the section
      whose window holds the slot's midpoint), then of the ``fallback`` reel (best first, rotating) — only
      photos not shown in the last ``avoid_repeat_within`` slots; if none qualifies, the reel's least
      recently shown photo. A pool photo of a thing (per ``entity_photos``) is only eligible once that
      thing has been named in the current section, and only within ``recall`` s of that: filler recalls
      what was just said, never spoils what is coming, and long after a mention neutral subject photos
      read better than a specific thing the narrator has left behind. A claim may repeat its thing's least recently shown photo (the photo shows what is being
      named), but no slot ever repeats the previous slot's photo unless only one photo exists at all.

    ``mentions`` need ``"key"`` and ``"time"`` (see ``mention_times``); ``entity_photos`` maps key →
    photos (a key that is missing or empty has no photos). Returns ``(slots, plan)``: ``plan[k]`` is
    ``{"slot", "start", "image", "entity", "name", "section", "mention_time", "why"}`` where
    ``mention_time`` is set on the slot that answers a mention, so ``start − mention_time`` is the lag.
    """
    if n_slots <= 0:
        return [], []
    if dwell <= 0:
        raise ValueError("dwell must be positive")
    photos = {str(k): _unique(v) for k, v in (entity_photos or {}).items() if v}
    pools = {int(s): _unique(v) for s, v in (section_pools or {}).items() if v}
    everything = _unique(list(fallback or []) + [p for v in pools.values() for p in v] + [p for v in photos.values() for p in v])
    reel = _unique(fallback) or everything
    if not reel:
        raise ValueError("plan_slots needs at least one photo")
    windows = [(float(a), float(b)) for a, b in (section_windows or [])]

    claims: dict[int, list[dict]] = {}
    named_at: dict[str, list[float]] = {}        # key → sorted times it is named (for the no-spoiler rule)
    timed = [(float(m["time"]), i, m) for i, m in enumerate(mentions or [])
             if m.get("key") and isinstance(m.get("time"), (int, float)) and math.isfinite(m["time"])]
    for t, _, m in sorted(timed, key=lambda x: (x[0], x[1])):
        named_at.setdefault(str(m["key"]), []).append(t)
        k = max(0, math.ceil((t - lead) / dwell - 1e-9))
        if 1 <= k < n_slots:                      # slot 0 belongs to the opening
            claims.setdefault(k, []).append({**m, "time": t})
    owners: dict[str, list[str]] = {}
    for key, ps in photos.items():
        for p in ps:
            owners.setdefault(p, []).append(key)

    def named_in_section(p: str, k: int, sec: int | None) -> bool:
        """A pool photo of a thing may fill slot k once the thing was named in this section (by the
        time it may first appear, as for claims); photos that belong to no thing always may."""
        if p not in owners:
            return True
        lo = max(windows[sec][0] if sec is not None else -math.inf, k * dwell - recall)
        hi = k * dwell + lead
        return any(lo <= t <= hi for key in owners[p] for t in named_at.get(key, ()))

    last_used: dict[str, int] = {}

    def pick(pool: list[str], k: int, prev: str | None, *, strict: bool, exclude=()) -> str | None:
        """Never-shown photos first (pool order = best first), then the least recently shown one."""
        best, best_rank = None, None
        for i, p in enumerate(pool):
            if p == prev or p in exclude:
                continue
            lu = last_used.get(p)
            if strict and lu is not None and k - lu <= avoid_repeat_within:
                continue
            rank = (0, 0, i) if lu is None else (1, lu, i)
            if best_rank is None or rank < best_rank:
                best, best_rank = p, rank
        return best

    def filler(k: int, prev: str | None, sec: int | None) -> tuple[str, str]:
        pool = [p for p in pools.get(sec, [])] if sec is not None else []
        tiers = (([p for p in pool if named_in_section(p, k, sec)], True, "section photo"), (reel, True, "subject reel"),
                 (reel, False, "subject reel, repeat"), (everything, False, "any photo"))
        for cands, strict, why in tiers:
            img = pick(cands, k, prev, strict=strict)
            if img:
                return img, why
        return (prev or reel[0]), "the only photo"

    slots: list[str] = []
    plan: list[dict] = []
    cur: str | None = None
    cur_name, cur_t, cur_sec, run = "", 0.0, None, set()
    for k in range(n_slots):
        prev = slots[-1] if slots else None
        sec = _section_at(windows, (k + 0.5) * dwell)
        img, why, note, answered = None, "", "", None
        if k == 0:
            img, why, cur = reel[0], "opening: best subject photo", None
        else:
            fresh = claims.get(k)
            if fresh:
                m = fresh[-1]
                key, t, name = str(m["key"]), float(m["time"]), str(m.get("name") or m["key"])
                skipped = f"; {len(fresh) - 1} earlier mention(s) in this slot skipped" if len(fresh) > 1 else ""
                again = key == cur
                if again:
                    cur_t = max(cur_t, t)
                else:
                    cur, cur_name, cur_t, run = key, name, t, set()
                cur_sec = _section_at(windows, t)
                img = pick(photos.get(key, []), k, prev, strict=again, exclude=run)
                if img:
                    why, answered = f"{name!r} named at {t:.1f}s{' again' if again else ''}{skipped}", t
                elif key not in photos:
                    note = f"{name!r} named at {t:.1f}s has no photos{skipped}"
                else:
                    note = f"no unused photo of {name!r} (named at {t:.1f}s){skipped}"
            elif cur is not None and sec != cur_sec:
                note = f"new section; {cur_name!r} released"
            elif cur is not None and k * dwell < cur_t + hold:
                img = pick(photos.get(cur, []), k, prev, strict=True, exclude=run)
                if img:
                    why = f"holding {cur_name!r} (named at {cur_t:.1f}s)"
                else:
                    note = f"photos of {cur_name!r} used up"
            elif cur is not None:
                note = f"hold for {cur_name!r} expired"
            if img is None:
                cur, run = None, set()
                img, why = filler(k, prev, sec)
                why = f"{why} ({note})" if note else why
            else:
                run.add(img)
        last_used[img] = k
        slots.append(img)
        plan.append({"slot": k, "start": round(k * dwell, 3), "image": img, "entity": cur,
                     "name": cur_name if cur else None, "section": sec, "mention_time": answered, "why": why,
                     "held_for": round(k * dwell - cur_t, 1) if why.startswith("holding") else None})
    return slots, plan


def plan_stats(plan: list[dict]) -> dict:
    """Numbers to judge a plan by (log them, compare runs): how much of the video follows the
    narration, how late the photos come, and how often photos repeat."""
    delays = sorted(round(p["start"] - p["mention_time"], 2) for p in plan if p.get("mention_time") is not None)
    uses: dict[str, list[int]] = {}
    for p in plan:
        uses.setdefault(p["image"], []).append(p["slot"])
    gaps = [b - a for slots in uses.values() for a, b in zip(slots, slots[1:])]

    def q(f: float) -> float | None:
        return delays[min(len(delays) - 1, int(f * len(delays)))] if delays else None

    return {"slots": len(plan), "thing_slots": sum(1 for p in plan if p.get("entity")),
            "named_slots": len(delays), "things_shown": len({p["entity"] for p in plan if p.get("entity")}),
            "distinct_photos": len(uses), "max_uses": max((len(v) for v in uses.values()), default=0),
            "min_reuse_gap": min(gaps, default=None),
            "consecutive_repeats": sum(1 for a, b in zip(plan, plan[1:]) if a["image"] == b["image"]),
            "lag_min": delays[0] if delays else None, "lag_median": q(0.5), "lag_p90": q(0.9),
            "lag_max": delays[-1] if delays else None}
