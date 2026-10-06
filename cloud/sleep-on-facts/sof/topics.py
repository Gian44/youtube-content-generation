"""Topic list (topics.yml) and the round-robin picker."""

from __future__ import annotations

from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class Topic:
    name: str
    queries: list[str]


def load_topics(path: str) -> list[Topic]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for t in data.get("topics") or []:
        out.append(Topic(name=str(t["name"]).strip(), queries=[str(q) for q in (t.get("queries") or [])]))
    if not out:
        raise ValueError(f"no topics in {path}")
    return out


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
