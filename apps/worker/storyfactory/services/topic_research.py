"""Topic research for the Sleep On Facts pipeline.

Grounds a single-topic sleep video with free, keyless reference data from the
Wikipedia REST API (no API key, no account). The extract becomes factual
``topic_hints`` for the LLM and seeds on-topic visual search keywords.

Network is best-effort: any failure (offline, rate limit, missing page, dry-run)
degrades gracefully to using the topic string alone — the pipeline never fails
because research was unavailable.
"""

from __future__ import annotations

import re

import httpx

from storyfactory.logger import get_logger

log = get_logger("topic_research")

# Wikimedia enforces its robot policy (https://w.wiki/4wJS): a User-Agent that
# does not identify the app AND a way to contact gets a 403. Include a URL/email.
_USER_AGENT = (
    "StoryFactory/1.0 (local desktop content pipeline; "
    "+https://github.com/storyfactory; contact: storyfactory-bot@users.noreply.github.com)"
)
# The MediaWiki Action API is the stable, permissive public endpoint; the legacy
# rest_v1 summary API is being restricted/deprecated. prop=extracts with exintro
# returns the lead section as plain text.
_ACTION_API = "https://en.wikipedia.org/w/api.php"
_MAX_HINT_CHARS = 1500

# Generic words to drop when deriving visual keywords from a topic title.
_STOPWORDS = {"the", "a", "an", "of", "and", "in", "on", "to", "for"}


def _title_keywords(topic: str) -> list[str]:
    words = [w for w in re.split(r"[^a-zA-Z0-9]+", topic or "") if w]
    significant = [w for w in words if w.lower() not in _STOPWORDS]
    keywords = [topic.strip()] if topic.strip() else []
    keywords.extend(significant)
    # De-dupe preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for kw in keywords:
        key = kw.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(kw)
    return out


def research_topic(topic: str, *, dry_run: bool = False, enabled: bool = True) -> dict:
    """Return grounding for a topic: ``{"hints": str, "keywords": list[str]}``.

    ``hints`` is a factual paragraph (empty when research is unavailable);
    ``keywords`` are on-topic visual search terms (always at least the topic).
    """
    base = {"hints": "", "keywords": _title_keywords(topic)}
    if not topic or not topic.strip():
        return base
    if dry_run or not enabled:
        return base

    try:
        with httpx.Client(follow_redirects=True, timeout=15) as client:
            resp = client.get(
                _ACTION_API,
                params={
                    "action": "query",
                    "prop": "extracts",
                    "exintro": 1,        # lead section only
                    "explaintext": 1,    # plain text, no HTML
                    "redirects": 1,      # follow "Ancient Egypt" -> canonical title
                    "format": "json",
                    "formatversion": 2,  # pages as a list, not a pageid-keyed dict
                    "titles": topic.strip(),
                },
                headers={"User-Agent": _USER_AGENT, "accept": "application/json"},
            )
        if resp.status_code != 200:
            log.warning("wikipedia_lookup_non200", topic=topic, status=resp.status_code)
            return base

        pages = (resp.json().get("query") or {}).get("pages") or []
        extract, canonical = "", topic.strip()
        if pages and not pages[0].get("missing"):
            extract = str(pages[0].get("extract") or "").strip()
            canonical = str(pages[0].get("title") or topic).strip()
        if extract:
            base["hints"] = extract[:_MAX_HINT_CHARS]
        # Fold the canonical Wikipedia title into the keyword list.
        if canonical and canonical.lower() != topic.strip().lower():
            base["keywords"] = _title_keywords(f"{topic} {canonical}")
        log.info("wikipedia_grounded", topic=topic, has_extract=bool(extract))
        return base
    except Exception as exc:  # network / parse failure — degrade gracefully
        log.warning("wikipedia_lookup_failed", topic=topic, error=str(exc))
        return base
