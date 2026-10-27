"""Per-passage shots ("cues"): pictures for the stretches of narration that name nothing photographable.

``sof/visuals.py`` gives a slot its own photo only when the narrator NAMES a thing (a moon, a temple, a
species). Everything else (how wind behaves at altitude, why a glacier moves, what a day at base camp
was like) used to fall back to general subject photos or keyword stock clips, so the picture drifted
from the words (Gian, 2026-10-27). Here every passage of the script gets its own shot:

1. ``split_passages`` cuts each section into passages of a few sentences (paragraph breaks first).
2. ``write_cues`` — one LLM call per section: per passage a concrete shot ("roped climbers crossing an
   ice ridge at dawn"), two stock queries and one Wikimedia Commons query (or no shot when nothing
   visual fits).
3. ``time_cues`` — each passage's first words are found in the TTS chunks (``visuals.mention_times``),
   giving the second it starts; it runs until the next passage of its section starts.
4. ``fetch_cue_media`` — for the passages that cover slots without a named thing: Pixabay + Pexels
   photos, Pixabay clips and a Commons file search, all shown on ONE contact sheet to the vision model
   with the passage text; only tiles it says fit the words survive (at most ``per_cue``).
5. ``apply_cues`` (pure) — those slots show the passage's approved pictures. Slots that answer a
   mention are never touched; a named thing still held long after it was named gives way to the
   passage being read.

Every stage degrades to "no cue" (the slot keeps its old picture): pictures must never cost the video.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path

import httpx

from sof import footage, images, visuals

log = logging.getLogger("sof.cues")

CUE_PROMPT = """You are the picture editor of a calm, narrated documentary about {{topic}}. Below is the
chapter "{{heading}}", split into numbered passages. While each passage is read aloud, one picture is on
screen, and it must show what that passage is describing.

For EACH passage give:
- "shot": one concrete, photographable scene or object a viewer would expect to see while it is read,
  true to {{topic}} (a passage about Everest's winds → "snow plume blowing off a high Himalayan summit",
  not "windy beach"). Real things only: no text, maps, charts, diagrams, illustrations or abstract ideas.
  If nothing visual fits the passage, use "".
- "stock": two short stock-photo search queries, 2-4 plain words of concrete nouns
  (e.g. "climbers ice ridge", "prayer flags wind"). No proper names except famous places.
- "commons": one Wikimedia Commons search query; proper names are welcome here
  (e.g. "Khumbu Icefall", "Tenzing Norgay 1953").

Return JSON only: {"passages": [{"n": 1, "shot": "...", "stock": ["...", "..."], "commons": "..."}, ...]}

PASSAGES:
{{passages}}
"""

MATCH_PROMPT = (
    "These tiles are candidate pictures for a calm documentary about {topic}. The picture will be on screen "
    "while the narrator reads this passage:\n\n\"{text}\"\n\nThe picture editor asked for: {shot}.\n\n"
    "Which tiles would look RIGHT on screen during this passage? A tile fits when it clearly shows what the "
    "passage describes (or the requested shot) and nothing contradicts it: no wrong place, animal, era or "
    "object, no modern things in a historical passage, no visible text, logos, watermarks, maps or diagrams, "
    "no people posing for the camera. When unsure, leave the tile out: a missing picture is better than a "
    "wrong one.\nReturn JSON {{\"match\": [tile numbers, best first]}} (an empty list if none fit)."
)

_SENT = re.compile(r"(?<=[.!?…])[\"”’)]*\s+")


def _fill(template: str, **kw) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


# ------------------------------------------------------------------ 1. passages (pure)

def split_passages(text: str, *, target: int = 500, max_chars: int = 900, min_chars: int = 160) -> list[tuple[int, str]]:
    """``(char offset, passage)`` pairs covering ``text`` in order: paragraphs, long ones cut into groups of
    sentences of about ``target`` characters, scraps shorter than ``min_chars`` merged into the passage before."""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r"\S(?:.*?\S)?(?=\s*\n\s*\n|\s*$)", text or "", flags=re.S):
        para, base = m.group(0), m.start()
        if len(para) <= max_chars:
            pieces = [(base, para)]
        else:
            pieces, start, cur = [], 0, 0
            for sm in _SENT.finditer(para):
                cur = sm.end()
                if cur - start >= target:
                    pieces.append((base + start, para[start:cur].strip()))
                    start = cur
            if start < len(para):
                pieces.append((base + start, para[start:].strip()))
        for off, piece in pieces:
            if not piece:
                continue
            if out and len(piece) < min_chars:
                o, prev = out[-1]
                out[-1] = (o, prev + " " + piece)
            else:
                out.append((off, piece))
    return out


def _first_words(text: str, n: int = 12) -> str:
    return " ".join(str(text).split()[:n])


# ------------------------------------------------------------------ 2. one LLM call per section

def _clean_list(v, n: int) -> list[str]:
    vals = v if isinstance(v, list) else [v] if isinstance(v, str) else []
    out = []
    for x in vals:
        s = re.sub(r"\s+", " ", str(x or "")).strip().strip('"')
        if s and len(s) <= 80 and s.lower() not in (o.lower() for o in out):
            out.append(s)
    return out[:n]


def write_cues(llm, sections: list[dict], *, topic: str, model: str | None = None, max_workers: int = 6) -> list[dict]:
    """One row per passage that got a shot: ``{"section", "n", "text", "quote", "pos", "shot", "stock", "commons"}``.
    ``pos`` is the passage's start as a share of its section text (a timing fallback)."""

    def one(si: int) -> list[dict]:
        body = str(sections[si].get("body") or "")
        parts = split_passages(body)
        if not parts:
            return []
        listing = "\n\n".join(f"[{i + 1}] {p}" for i, (_, p) in enumerate(parts))
        heading = re.sub(r"\s+", " ", str(sections[si].get("heading") or "")).strip() or f"Part {si + 1}"
        data = llm.generate_json(_fill(CUE_PROMPT, topic=topic, heading=heading, passages=listing),
                                 max_tokens=4000, temperature=0.3, model=model)
        rows = []
        for it in (data.get("passages") if isinstance(data, dict) else None) or []:
            if not isinstance(it, dict):
                continue
            try:
                n = int(it.get("n"))
            except (TypeError, ValueError):
                continue
            shot = re.sub(r"\s+", " ", str(it.get("shot") or "")).strip()
            if not (1 <= n <= len(parts)) or not shot:
                continue
            stock, commons = _clean_list(it.get("stock"), 2), _clean_list(it.get("commons"), 1)
            if not stock and not commons:
                continue
            off, text = parts[n - 1]
            rows.append({"section": si, "n": n, "text": text, "quote": _first_words(text), "pos": off / max(1, len(body)),
                         "shot": shot[:200], "stock": stock, "commons": commons[0] if commons else ""})
        return rows

    per = visuals._parallel(one, range(len(sections)), max_workers, label=lambda si: f"cues for section {si}")
    rows = [r for rs in per for r in rs]
    log.info("cues: %d passages with a shot in %d sections", len(rows), len(sections))
    return rows


# ------------------------------------------------------------------ 3. timing

def time_cues(cues: list[dict], *, chunks: list[str], owner: list[int], chunk_durations: list[float],
              total: float) -> list[dict]:
    """Each cue with ``"start"``/``"end"`` (seconds): from its first words to the next cue of its section
    (the section's last cue runs to the start of the next section's first cue, or to ``total``)."""
    rows = [{**c, "key": f"cue-{c['section']}-{c['n']}", "name": c["quote"]} for c in cues]
    timed = visuals.mention_times(rows, chunks=chunks, owner=owner, chunk_durations=chunk_durations)
    timed.sort(key=lambda r: r["time"])
    out = []
    for i, r in enumerate(timed):
        end = timed[i + 1]["time"] if i + 1 < len(timed) else total
        if end > r["time"]:
            out.append({**{k: v for k, v in r.items() if k not in ("key", "name")}, "start": r["time"], "end": end})
    return out


def slots_of(cue: dict, *, dwell: float, n_slots: int) -> list[int]:
    """Slots whose midpoint falls inside the cue's passage."""
    first = max(0, int(cue["start"] / dwell - 0.5) - 1)
    return [k for k in range(first, n_slots) if cue["start"] <= (k + 0.5) * dwell < cue["end"]]


def open_slot(row: dict, *, hold_after: float) -> bool:
    """May a cue take this slot? Never the opening or a slot that answers a mention; a named thing only
    once it has been on screen ``hold_after`` s since it was last named."""
    why = str(row.get("why") or "")
    if row.get("slot") == 0 or why.startswith("opening") or row.get("mention_time") is not None:
        return False
    if row.get("entity"):
        held = row.get("held_for")
        return isinstance(held, (int, float)) and held >= hold_after
    return True


# ------------------------------------------------------------------ 4. search + vision check

def _commons_search(c: httpx.Client, q: str, limit: int = 6) -> list[dict]:
    data = images._api(c, images.COMMONS_API, action="query", generator="search", gsrsearch=q, gsrnamespace=6,
                       gsrlimit=limit * 2, prop="imageinfo", iiprop="url|size|mime|extmetadata",
                       iiurlwidth=images.COMMONS_WIDTH,
                       iiextmetadatafilter="LicenseShortName|Artist|NonFree|ImageDescription")
    pages = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
    return [x for x in (images.commons_candidate(p, query=q) for p in pages) if x][:limit]


def candidates_for(cue: dict, *, pexels_key: str, pixabay_key: str, transport=None, per_query: int = 3,
                   clips: int = 2) -> list[dict]:
    """Photos (``kind`` "photo") and clips (``kind`` "clip", with a ``preview`` frame) for one cue. Pexels is
    asked once per cue (its free tier allows 200 requests an hour); every provider failure just yields fewer."""
    out: list[dict] = []
    stock = cue.get("stock") or []
    with httpx.Client(timeout=30.0, transport=transport, follow_redirects=True,
                      headers={"User-Agent": images.UA}) as c:
        calls = []
        if pixabay_key:
            calls += [lambda q=q: images._pixabay(c, pixabay_key, q, 1)[:per_query] for q in stock]
        if pexels_key and stock:
            calls.append(lambda: images._pexels(c, pexels_key, stock[0], 1)[:per_query])
        if cue.get("commons"):
            calls.append(lambda: _commons_search(c, cue["commons"], per_query))
        if pixabay_key and stock and clips:
            calls.append(lambda: [{**v, "kind": "clip"} for v in footage.pixabay_videos(c, pixabay_key, stock[0],
                                                                                         per_page=10)[:clips]])
        for call in calls:
            try:
                out += [{"kind": "photo", **it} if "kind" not in it else it for it in call()]
            except Exception as exc:  # noqa: BLE001
                log.warning("cue search (%s) failed: %s", cue.get("shot"), exc)
    seen, uniq = set(), []
    for it in out:
        if it.get("url") and it["url"] not in seen:
            seen.add(it["url"])
            uniq.append(it)
    return uniq


def fetch_cue_media(cues: list[dict], out_dir: str, *, topic: str, vis, pexels_key: str, pixabay_key: str,
                    per_cue: int = 3, workers: int = 3, budget_seconds: float = 1200.0, exclude_urls=(),
                    transport=None, clock=time.monotonic) -> dict[int, list[str]]:
    """cue index → up to ``per_cue`` approved pictures (absolute paths; .mp4 for clips). Cue photos land in
    ``out_dir/_cueNNN/`` with ``download_range``'s credits.json, so on-screen credits find them."""
    stop_at = clock() + budget_seconds
    root = Path(out_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    taken = set(exclude_urls or ())
    lock = threading.Lock()

    def one(i: int) -> list[str]:
        if clock() > stop_at:
            return []
        cue = cues[i]
        cands = candidates_for(cue, pexels_key=pexels_key, pixabay_key=pixabay_key, transport=transport)
        with lock:
            cands = [x for x in cands if x["url"] not in taken]
            taken.update(x["url"] for x in cands)
        if not cands or clock() > stop_at:
            return []
        folder = root / f"_cue{i:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        photos = [x for x in cands if x["kind"] == "photo"]
        clips = [x for x in cands if x["kind"] == "clip"]
        tiles: list[tuple[str, dict]] = []
        got, _ = images.download_range(photos, str(folder), transport=transport)
        by_name = {f"{j:03d}.jpg": photos[j] for j in range(len(photos))}
        tiles += [(p, by_name.get(Path(p).name, {})) for p in got]
        with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True) as c:
            for it in clips:
                prev = folder / f"clip_{hashlib.sha1(it['url'].encode()).hexdigest()[:10]}.jpg"
                if prev.exists() or footage._get(c, it["preview"], prev, max_bytes=5_000_000, deadline=20):
                    tiles.append((str(prev), it))
        if not tiles or clock() > stop_at:
            return []
        sheet = str(folder / "sheet.jpg")
        from sof import vision
        kept = vision.contact_sheet([p for p, _ in tiles], sheet)
        if not kept:
            return []
        text = cue["text"] if len(cue["text"]) <= 700 else cue["text"][:700].rsplit(" ", 1)[0] + " …"
        data = vis.ask(vision.SHEET_NOTE.format(n=len(kept))
                       + MATCH_PROMPT.format(topic=topic, text=text.replace('"', "'"), shot=cue["shot"]), sheet)
        picks = []
        for v in (data or {}).get("match") or []:
            try:
                j = int(v) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= j < len(kept) and kept[j] not in picks:
                picks.append(kept[j])
        info = dict(tiles)
        approved: list[str] = []
        with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True) as c:
            for p in picks:
                if len(approved) >= per_cue:
                    break
                it = info.get(p, {})
                if it.get("kind") == "clip":
                    clip = Path(p).with_suffix(".mp4")
                    if clip.exists() or footage._get(c, it["url"], clip, max_bytes=footage.MAX_CLIP_BYTES):
                        approved.append(str(clip.resolve()))
                else:
                    approved.append(str(Path(p).resolve()))
        (folder / "cue.json").write_text(json.dumps({"cue": {k: cue.get(k) for k in ("section", "n", "shot", "stock",
                                                                                     "commons", "start", "end")},
                                                     "tiles": len(kept), "approved": approved}, indent=1,
                                                    ensure_ascii=False), encoding="utf-8")
        return approved

    lists = visuals._parallel(one, range(len(cues)), workers, label=lambda i: f"cue {i} ({cues[i].get('shot')!r})")
    out = {i: v for i, v in enumerate(lists) if v}
    log.info("cue pictures: %d of %d passages got pictures (%d total)", len(out), len(cues),
             sum(len(v) for v in out.values()))
    return out


# ------------------------------------------------------------------ 5. slot assignment (pure)

def apply_cues(slots: list[str], plan: list[dict], cues: list[dict], media: dict[int, list[str]], *, dwell: float,
               hold_after: float = 60.0) -> tuple[list[str], list[dict], int]:
    """Give each open slot inside a cue's passage one of the cue's pictures (rotating, never the previous
    slot's picture when another exists). Returns (slots, plan, number of slots changed)."""
    out, rows = list(slots), [dict(r) for r in plan]
    n = len(out)
    changed = 0
    for ci, cue in enumerate(cues):
        pics = media.get(ci) or []
        if not pics:
            continue
        j = 0
        for k in slots_of(cue, dwell=dwell, n_slots=n):
            if k >= len(rows) or not open_slot(rows[k], hold_after=hold_after):
                continue
            prev = out[k - 1] if k > 0 else None
            pick = pics[j % len(pics)]
            if pick == prev and len(pics) > 1:
                j += 1
                pick = pics[j % len(pics)]
            j += 1
            out[k] = pick
            rows[k].update(image=pick, entity=None, name=None, cue=cue["shot"], why=f"cue: {cue['shot']}")
            changed += 1
    return out, rows, changed


def select(cues: list[dict], plan: list[dict], *, dwell: float, max_cues: int, hold_after: float = 60.0) -> list[dict]:
    """The cues worth fetching: those covering open slots, most open slots first, at most ``max_cues``,
    returned in time order."""
    n = len(plan)
    scored = []
    for i, c in enumerate(cues):
        open_n = sum(1 for k in slots_of(c, dwell=dwell, n_slots=n) if open_slot(plan[k], hold_after=hold_after))
        if open_n:
            scored.append((open_n, -i, c))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    keep = [c for _, _, c in scored[:max(0, max_cues)]]
    return sorted(keep, key=lambda c: c["start"])
