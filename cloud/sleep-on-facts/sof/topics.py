"""Topic list (topics.yml) and the round-robin picker."""

from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class Topic:
    name: str
    queries: list[str]
    category: str = ""      # one of the file's categories → title phrase, playlist, hashtag
    wiki_title: str = ""    # English Wikipedia article, when it differs from the name


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_topics(path: str) -> list[Topic]:
    """Optional seed topics (may be empty when the file only has categories)."""
    out = []
    for t in _load(path).get("topics") or []:
        out.append(Topic(name=str(t["name"]).strip(), queries=[str(q) for q in (t.get("queries") or [])],
                         category=str(t.get("category") or "").strip(), wiki_title=str(t.get("wiki_title") or "").strip()))
    return out


def _category_entries(path: str) -> list[tuple[str, float]]:
    out = []
    for c in _load(path).get("categories") or []:
        if isinstance(c, dict):
            name, weight = str(c.get("name") or "").strip(), c.get("weight", 1)
        else:
            name, weight = str(c).strip(), 1
        try:
            w = float(weight)
        except (TypeError, ValueError):
            w = 1.0
        if name and w > 0:
            out.append((name, w))
    return out


def load_categories(path: str) -> list[str]:
    cats = [n for n, _ in _category_entries(path)]
    if not cats and not load_topics(path):
        raise ValueError(f"{path} has neither categories nor topics")
    return cats


def load_category_weights(path: str) -> dict[str, float]:
    """{category: weight}; plain-string entries weigh 1."""
    return dict(_category_entries(path))


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
