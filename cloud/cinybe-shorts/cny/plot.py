"""The film's plot, from English Wikipedia (MediaWiki API) or an operator-written synopsis in ``plots/``.

Ported from StoryFactory's ``services/wiki_plot.py`` (branch feat/narrated-recap, 2026-10-05): the Plot /
Synopsis section as plain text, plus the article's lead paragraph (director, cast, genre in prose) for the
planner. Wikimedia returns 403 unless the User-Agent carries a contact URL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import httpx

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "CinybeShorts/1.0 (+https://github.com/Gian44/youtube-content-generation) python-httpx"
PLOT_HEADINGS = ("plot", "plot summary", "synopsis", "story", "premise")


@dataclass
class PlotText:
    title: str              # Wikipedia page title
    text: str
    word_count: int
    url: str
    section: str            # "Plot", "Synopsis", … or "operator"
    lead: str = ""          # first paragraph(s) of the article: who made it, who is in it

    @property
    def source(self) -> str:
        return "operator" if self.section == "operator" else "wikipedia"


def strip_wikitext(src: str) -> str:
    text = src
    text = re.sub(r"<ref[^>/]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    for _ in range(5):  # nested templates
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\[\[(?:File|Image|Category):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[https?://[^\s\]]+\s*([^\]]*)\]", r"\1", text)
    text = re.sub(r"'{2,5}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^==.*?==\s*$", "", text, flags=re.M)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def candidate_titles(title: str, year: int | None) -> list[str]:
    """Page titles to try when no Wikidata sitelink is known: the disambiguated film pages first."""
    out = []
    if year:
        out.append(f"{title} ({year} film)")
    out += [f"{title} (film)", title]
    return list(dict.fromkeys(out))


def trim_plot(text: str, max_chars: int) -> str:
    """Cut an over-long plot at a paragraph boundary, keeping the END (the planner needs the ending)."""
    if len(text) <= max_chars:
        return text
    paras = text.split("\n\n")
    kept: list[str] = []
    size = 0
    for p in reversed(paras):
        if size + len(p) > max_chars and kept:
            break
        kept.insert(0, p)
        size += len(p) + 2
    return "\n\n".join(kept)


class WikiPlot:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, timeout: float = 20.0):
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, transport=transport)

    def _get(self, **params) -> dict:
        params.update({"action": "parse", "format": "json", "redirects": "1"})
        resp = self._client.get(API, params=params)
        resp.raise_for_status()
        return resp.json()

    def fetch(self, page_title: str) -> PlotText | None:
        data = self._get(page=page_title, prop="sections")
        if "error" in data or "parse" not in data:
            return None
        sections = data["parse"].get("sections") or []
        hit = None
        for s in sections:
            line = (s.get("line") or "").strip().lower()
            if line in PLOT_HEADINGS and str(s.get("level")) in ("2", "3"):
                hit = s
                break
        if hit is None:
            return None
        canonical = str(data["parse"].get("title") or page_title)
        body = self._get(page=page_title, prop="wikitext", section=hit["index"])
        raw = ((body.get("parse") or {}).get("wikitext") or {}).get("*") or ""
        text = strip_wikitext(raw)
        if not text:
            return None
        lead = ""
        try:
            head = self._get(page=page_title, prop="wikitext", section="0")
            lead_raw = ((head.get("parse") or {}).get("wikitext") or {}).get("*") or ""
            lead = strip_wikitext(lead_raw)
            lead = "\n\n".join(p for p in lead.split("\n\n") if len(p.split()) > 12)[:1500]
        except httpx.HTTPError:
            pass
        anchor = (hit.get("line") or "Plot").replace(" ", "_")
        url = f"https://en.wikipedia.org/wiki/{canonical.replace(' ', '_')}#{anchor}"
        return PlotText(title=canonical, text=text, word_count=len(text.split()), url=url, section=hit["line"],
                        lead=lead)

    def fetch_any(self, titles: list[str]) -> PlotText | None:
        for t in titles:
            try:
                got = self.fetch(t)
            except httpx.HTTPError:
                got = None
            if got:
                return got
        return None


def operator_plot(plots_dir: str, slug: str, *, title: str) -> PlotText | None:
    """``plots/<slug>.md``: a synopsis the operator wrote when Wikipedia's plot was thin. Plain text; an
    optional first line ``Lead: …`` carries director/cast context."""
    p = Path(plots_dir) / f"{slug}.md"
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8").strip()
    lead = ""
    if raw.lower().startswith("lead:"):
        first, _, rest = raw.partition("\n")
        lead, raw = first[5:].strip(), rest.strip()
    raw = re.sub(r"^#.*$", "", raw, flags=re.M).strip()
    if not raw:
        return None
    return PlotText(title=title, text=raw, word_count=len(raw.split()), url="", section="operator", lead=lead)
