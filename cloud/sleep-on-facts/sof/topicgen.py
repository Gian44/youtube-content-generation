"""Daily topic from rotating categories: the model proposes, Wikipedia decides.

The next category is the one with the fewest past videos (ties → list order). A cheap model
proposes several specific subjects in that category that are NOT in the ledger; each candidate
must resolve to a Wikipedia article of at least ``MIN_ARTICLE_CHARS`` of wikitext — that is the
test for "there is three hours of real material here". First candidate to pass wins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sof import research
from sof.prompts import TOPIC_PROMPT

log = logging.getLogger("sof.topicgen")


@dataclass
class Pick:
    topic: str          # display name used in titles/thumbnails
    wiki_title: str     # resolved article
    category: str


def next_category(categories: list[str], ledger: list[dict]) -> str:
    counts = {c: 0 for c in categories}
    for row in ledger:
        c = row.get("category")
        if c in counts:
            counts[c] += 1
    return min(categories, key=lambda c: (counts[c], categories.index(c)))


def _fill(t: str, **kw) -> str:
    for k, v in kw.items():
        t = t.replace("{{" + k + "}}", str(v))
    return t


def propose(llm, *, category: str, used: list[str], model: str | None, n: int = 6, avoid: list[str] | None = None) -> list[dict]:
    data = llm.generate_json(
        _fill(TOPIC_PROMPT, category=category, n=n, used="; ".join(used[-60:]) or "(none yet)",
              avoid="; ".join(avoid or []) or "(none)"),
        max_tokens=1200, model=model,
    )
    out = []
    for c in data.get("candidates") or []:
        if isinstance(c, dict) and str(c.get("topic") or "").strip():
            out.append({"topic": str(c["topic"]).strip(), "wiki_title": str(c.get("wikipedia_title") or c["topic"]).strip()})
    return out


def choose(llm, *, categories: list[str], ledger: list[dict], model: str | None = None, transport=None,
           rounds: int = 2, min_chars: int = research.MIN_ARTICLE_CHARS) -> Pick:
    category = next_category(categories, ledger)
    used_topics = [str(r.get("topic")) for r in ledger if r.get("topic")]
    used_titles = {str(r.get("wiki_title") or r.get("topic")).lower() for r in ledger}
    rejected: list[str] = []
    with research._client(transport) as c:
        for _ in range(rounds):
            for cand in propose(llm, category=category, used=used_topics, model=model, avoid=rejected):
                title = research.resolve_title(c, cand["wiki_title"]) or research.resolve_title(c, cand["topic"])
                if not title or title.lower() in used_titles:
                    rejected.append(cand["topic"]); continue
                length = research.article_length(c, title)
                if length < min_chars:
                    log.info("rejecting %r: article %r only %d bytes", cand["topic"], title, length)
                    rejected.append(cand["topic"]); continue
                log.info("topic: %r → %r (%s, %d bytes)", cand["topic"], title, category, length)
                return Pick(topic=cand["topic"], wiki_title=title, category=category)
    raise RuntimeError(f"could not find a substantial unused topic in category {category!r}")
