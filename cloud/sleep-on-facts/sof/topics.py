"""Topic list (topics.yml) and the round-robin picker."""

from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class Topic:
    name: str
    queries: list[str]


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_topics(path: str) -> list[Topic]:
    """Optional seed topics (may be empty when the file only has categories)."""
    out = []
    for t in _load(path).get("topics") or []:
        out.append(Topic(name=str(t["name"]).strip(), queries=[str(q) for q in (t.get("queries") or [])]))
    return out


def load_categories(path: str) -> list[str]:
    cats = [str(c).strip() for c in (_load(path).get("categories") or []) if str(c).strip()]
    if not cats and not load_topics(path):
        raise ValueError(f"{path} has neither categories nor topics")
    return cats


def unused_seed(topics: list[Topic], ledger: list[dict]) -> Topic | None:
    """First seed topic with no ledger entry, in list order; None once all seeds are used."""
    used = {str(e.get("topic")).lower() for e in ledger}
    for t in topics:
        if t.name.lower() not in used:
            return t
    return None


def pick_next(topics: list[Topic], ledger: list[dict], forced: str | None = None) -> Topic:
    """Fewest ledger entries wins; ties by list order. ``forced`` selects by name (case-insensitive)."""
    if forced:
        for t in topics:
            if t.name.lower() == forced.strip().lower():
                return t
        return Topic(name=forced.strip(), queries=[forced.strip()])
    counts = {t.name: 0 for t in topics}
    for e in ledger:
        if e.get("topic") in counts:
            counts[e["topic"]] += 1
    return min(topics, key=lambda t: (counts[t.name], topics.index(t)))
