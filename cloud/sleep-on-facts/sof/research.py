"""Keyless factual grounding: the Wikipedia lead section via the MediaWiki Action API. Best-effort."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("sof.research")
API = "https://en.wikipedia.org/w/api.php"
UA = "SleepOnFacts/1.0 (+https://github.com/Gian44/youtube-content-generation) python-httpx"


def wikipedia_hints(topic: str, *, transport=None, max_chars: int = 4000) -> str:
    try:
        with httpx.Client(timeout=20.0, transport=transport, headers={"User-Agent": UA}) as c:
            r = c.get(API, params={
                "action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1",
                "redirects": "1", "titles": topic, "format": "json",
            })
            r.raise_for_status()
            pages = (r.json().get("query") or {}).get("pages") or {}
            text = " ".join((p.get("extract") or "") for p in pages.values()).strip()
            return text[:max_chars]
    except Exception as exc:  # noqa: BLE001 — grounding is optional
        log.warning("wikipedia grounding unavailable for %s: %s", topic, exc)
        return ""
