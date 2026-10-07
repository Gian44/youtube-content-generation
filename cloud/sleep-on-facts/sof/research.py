"""Deep, keyless research from Wikipedia (MediaWiki Action API) — the script is written FROM this.

``build_corpus(topic)``:
  1. resolve the topic to an article (exact title, then full-text search);
  2. fetch the whole article as plain text (lead + every section);
  3. list the articles it links to, rank them by how often their title occurs in the body
     (a cheap relevance signal that needs no model), fetch the top ``max_linked`` in full;
  4. split everything into ~1,200-character paragraph chunks tagged with their source title.
``retrieve(corpus, query, k)`` then hands each movement the chunks that match its heading and
beats (TF-IDF cosine, no dependencies), so the writer has specific, sourced material instead of
its memory of the subject.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("sof.research")
API = "https://en.wikipedia.org/w/api.php"
UA = "SleepOnFacts/1.0 (+https://github.com/Gian44/youtube-content-generation) python-httpx"
CHUNK_CHARS = 1200
MIN_ARTICLE_CHARS = 20_000     # a subject needs this much text to carry three hours honestly
_SKIP_SECTIONS = {"see also", "references", "external links", "further reading", "notes", "bibliography",
                  "sources", "citations", "footnotes", "gallery"}
_WORD = re.compile(r"[a-z0-9]+")
_STOP = set("""a an the of and or in on at to for from by with as is are was were be been being this that these those it its
into over under about between through during after before above below up down out off than then there their they them we our
you your he she his her him who whom which what when where why how not no nor so such very can could would should may might
will shall do does did done has have had having also more most many much some any each few other another same own both all
one two three first second new old""".split())


@dataclass
class Chunk:
    source: str
    text: str


@dataclass
class Corpus:
    topic: str
    title: str
    url: str
    lead: str
    sections: list[str]
    chunks: list[Chunk]
    sources: list[str] = field(default_factory=list)   # article titles, main first

    def to_dict(self) -> dict:
        return {"topic": self.topic, "title": self.title, "url": self.url, "lead": self.lead, "sections": self.sections,
                "sources": self.sources, "chunks": [{"source": c.source, "text": c.text} for c in self.chunks]}

    @classmethod
    def from_dict(cls, d: dict) -> "Corpus":
        return cls(topic=d["topic"], title=d["title"], url=d["url"], lead=d.get("lead", ""), sections=list(d.get("sections") or []),
                   chunks=[Chunk(c["source"], c["text"]) for c in d.get("chunks") or []], sources=list(d.get("sources") or []))

    @property
    def chars(self) -> int:
        return sum(len(c.text) for c in self.chunks)


# ------------------------------------------------------------------ MediaWiki calls

def _client(transport=None) -> httpx.Client:
    return httpx.Client(timeout=30.0, transport=transport, headers={"User-Agent": UA})


def resolve_title(c: httpx.Client, topic: str) -> str | None:
    """Exact/redirected title first; otherwise the top full-text search hit."""
    r = c.get(API, params={"action": "query", "titles": topic, "redirects": "1", "format": "json"})
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    for pid, p in pages.items():
        if int(pid) > 0 and "missing" not in p:
            return p["title"]
    r = c.get(API, params={"action": "query", "list": "search", "srsearch": topic, "srlimit": 1, "format": "json"})
    r.raise_for_status()
    hits = (r.json().get("query") or {}).get("search") or []
    return hits[0]["title"] if hits else None


def fetch_article(c: httpx.Client, title: str) -> str:
    """Whole article as plain text (section headings kept as '== Heading =='). One page per call —
    the API only batches *intro* extracts."""
    r = c.get(API, params={"action": "query", "prop": "extracts", "explaintext": "1", "exsectionformat": "wiki",
                           "redirects": "1", "titles": title, "format": "json"})
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    return " ".join((p.get("extract") or "") for p in pages.values()).strip()


def linked_titles(c: httpx.Client, title: str) -> list[str]:
    """Main-namespace articles linked from ``title`` (follows continuation; capped at ~2,000)."""
    out: list[str] = []
    params = {"action": "query", "prop": "links", "plnamespace": "0", "pllimit": "max", "titles": title,
              "redirects": "1", "format": "json"}
    for _ in range(5):
        r = c.get(API, params=params)
        r.raise_for_status()
        data = r.json()
        for p in ((data.get("query") or {}).get("pages") or {}).values():
            out += [l["title"] for l in (p.get("links") or [])]
        cont = (data.get("continue") or {}).get("plcontinue")
        if not cont:
            break
        params = {**params, "plcontinue": cont}
    return out


def article_length(c: httpx.Client, title: str) -> int:
    """Byte length of the article's wikitext — cheap 'is this a substantial subject?' signal."""
    r = c.get(API, params={"action": "query", "prop": "info", "titles": title, "redirects": "1", "format": "json"})
    r.raise_for_status()
    pages = (r.json().get("query") or {}).get("pages") or {}
    return max((int(p.get("length") or 0) for pid, p in pages.items() if int(pid) > 0), default=0)


# ------------------------------------------------------------------ text processing

def split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (lead, [(heading, body), ...]) from '== Heading ==' plain text; drops reference-type sections."""
    parts = re.split(r"\n=+\s*([^=\n]+?)\s*=+\n", "\n" + text)
    lead = parts[0].strip()
    sections: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        heading, body = parts[i].strip(), parts[i + 1].strip()
        if heading.lower() in _SKIP_SECTIONS or not body:
            continue
        sections.append((heading, body))
    return lead, sections


def chunk_text(source: str, text: str, size: int = CHUNK_CHARS) -> list[Chunk]:
    chunks, cur = [], ""
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if len(para) < 40:
            continue
        if cur and len(cur) + len(para) + 1 > size:
            chunks.append(Chunk(source, cur))
            cur = ""
        while len(para) > size:               # a very long paragraph
            if cur:
                chunks.append(Chunk(source, cur)); cur = ""
            cut = para.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 2 else size
            chunks.append(Chunk(source, para[:cut].strip()))
            para = para[cut:].strip()
        cur = f"{cur}\n{para}".strip() if cur else para
    if cur:
        chunks.append(Chunk(source, cur))
    return chunks


def _tokens(s: str) -> list[str]:
    return [w for w in _WORD.findall(s.lower()) if w not in _STOP and len(w) > 2]


def retrieve(corpus: Corpus, query: str, k: int = 8, *, max_chars: int = 9000) -> list[Chunk]:
    """Top-k chunks by TF-IDF cosine against ``query``; never more than ``max_chars`` of notes."""
    if not corpus.chunks:
        return []
    docs = [_tokens(c.text) for c in corpus.chunks]
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    n = len(docs)
    idf = {t: math.log((n + 1) / (df[t] + 1)) + 1 for t in df}
    q = Counter(_tokens(query))
    if not q:
        return corpus.chunks[:k]
    qv = {t: (1 + math.log(c)) * idf.get(t, 1.0) for t, c in q.items()}
    qn = math.sqrt(sum(v * v for v in qv.values())) or 1.0
    scored = []
    for i, d in enumerate(docs):
        if not d:
            continue
        tf = Counter(d)
        num = sum(((1 + math.log(tf[t])) * idf[t]) * qv[t] for t in qv if t in tf)
        if num == 0:
            continue
        dn = math.sqrt(sum(((1 + math.log(c)) * idf[t]) ** 2 for t, c in tf.items())) or 1.0
        scored.append((num / (dn * qn), i))
    scored.sort(reverse=True)
    if not scored:                       # nothing overlaps: hand over the lead chunks rather than nothing
        return corpus.chunks[:k]
    out, used = [], 0
    for _, i in scored:
        ch = corpus.chunks[i]
        if used + len(ch.text) > max_chars:
            continue
        out.append(ch); used += len(ch.text)
        if len(out) >= k:
            break
    return out


def notes_block(chunks: list[Chunk]) -> str:
    return "\n\n".join(f"[{c.source}] {c.text}" for c in chunks) if chunks else "(no notes retrieved)"


# ------------------------------------------------------------------ corpus build

def build_corpus(topic: str, *, transport=None, max_linked: int = 15, min_linked_chars: int = 6000) -> Corpus:
    with _client(transport) as c:
        title = resolve_title(c, topic)
        if not title:
            raise LookupError(f"no Wikipedia article for {topic!r}")
        main = fetch_article(c, title)
        lead, sections = split_sections(main)
        chunks = chunk_text(title, lead) + [ch for h, b in sections for ch in chunk_text(f"{title} — {h}", b)]
        # Rank linked articles by how often their title appears in the main text (minimum twice).
        low = main.lower()
        ranked = sorted(((low.count(t.lower()), t) for t in set(linked_titles(c, title)) if len(t) > 3),
                        reverse=True)
        sources, fetched = [title], 0
        for count, t in ranked:
            if fetched >= max_linked or count < 2:
                break
            try:
                text = fetch_article(c, t)
            except httpx.HTTPError as exc:
                log.warning("linked article %s failed: %s", t, exc)
                continue
            if len(text) < min_linked_chars:
                continue
            l2, s2 = split_sections(text)
            chunks += chunk_text(t, l2) + [ch for h, b in s2 for ch in chunk_text(f"{t} — {h}", b)]
            sources.append(t); fetched += 1
    corpus = Corpus(topic=topic, title=title, url="https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                    lead=lead[:3000], sections=[h for h, _ in sections], chunks=chunks, sources=sources)
    log.info("corpus for %r: %s + %d linked articles, %d chunks, %d chars", topic, title, fetched, len(chunks), corpus.chars)
    return corpus


def wikipedia_hints(topic: str, *, transport=None, max_chars: int = 4000) -> str:
    """Lead section only (kept for callers that want a cheap hint; the pipeline uses build_corpus)."""
    try:
        with _client(transport) as c:
            title = resolve_title(c, topic)
            if not title:
                return ""
            lead, _ = split_sections(fetch_article(c, title))
            return lead[:max_chars]
    except Exception as exc:  # noqa: BLE001 — grounding is optional
        log.warning("wikipedia grounding unavailable for %s: %s", topic, exc)
        return ""
