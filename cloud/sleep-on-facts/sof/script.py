"""Segmented script generation for a ~3h calm narration (ported from StoryFactory).

Two passes: one outline call plans "movements" (sub-areas of the single topic);
one expansion call per movement writes flowing prose, threaded with a running
summary and the tail of the previous movement. The loop keeps requesting fresh
movements until the word count approaches the target, bounded by ``max_segments``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sof.prompts import METADATA_PROMPT, OUTLINE_PROMPT, SEGMENT_PROMPT

log = logging.getLogger("sof.script")
_MIN_SEG, _MAX_SEG, _TAIL, _MAX_REFILLS = 600, 1500, 400, 8


class ScriptTooLong(RuntimeError):
    """The generated narration exceeds max_words_ratio × target (cost guard, raised before TTS)."""


@dataclass
class Script:
    title: str
    description: str
    tags: list[str]
    body: str
    word_count: int
    movements: int
    image_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title, "description": self.description, "tags": self.tags, "body": self.body,
            "word_count": self.word_count, "movements": self.movements, "image_queries": self.image_queries,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Script":
        return cls(**{k: d[k] for k in ("title", "description", "tags", "body", "word_count", "movements")},
                   image_queries=list(d.get("image_queries") or []))


def _fill(template: str, **kw) -> str:
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def generate_script(
    llm,
    *,
    topic: str,
    hints: str,
    minutes: int,
    target_words: int,
    num_movements: int,
    max_segments: int,
    fill_ratio: float,
    max_words_ratio: float,
    segment_max_tokens: int = 2800,
) -> Script:
    per_words = max(_MIN_SEG, min(_MAX_SEG, target_words // max(1, num_movements)))
    bodies: list[str] = []
    covered: list[str] = []
    seen: set[str] = set()
    tail = ""

    def outline(extra: str) -> dict:
        return llm.generate_json(
            _fill(OUTLINE_PROMPT, topic=topic, topic_hints=(hints + extra) or "(no external notes available)",
                  target_minutes=minutes, num_movements=num_movements),
            max_tokens=3000,
        )

    def expand(m: dict) -> None:
        nonlocal tail
        heading = str(m.get("heading") or f"Part {len(bodies) + 1}")
        seen.add(heading.strip().lower())
        beats = "\n".join(f"- {b}" for b in (m.get("beats") or []) if str(b).strip()) or "(none provided)"
        summary = ("Already covered: " + "; ".join(covered) + ".") if covered else "(nothing yet — this is the opening)"
        prompt = _fill(
            SEGMENT_PROMPT, topic=topic, movement_heading=heading, movement_beats=beats,
            running_summary=summary, previous_tail=tail or "(this is the very beginning)", target_words=per_words,
        )
        try:
            body = str(llm.generate_json(prompt, max_tokens=segment_max_tokens).get("body") or "").strip()
        except Exception as exc:  # noqa: BLE001 — one bad movement must not abort the video
            log.warning("segment failed (%s): %s", heading, exc)
            return
        if body:
            bodies.append(body)
            covered.append(heading)
            tail = body[-_TAIL:]

    def total() -> int:
        return sum(len(b.split()) for b in bodies)

    first = outline("")
    movements = [m for m in (first.get("movements") or []) if isinstance(m, dict)]
    if not movements:
        raise RuntimeError("outline returned no movements")
    for m in movements:
        if len(bodies) >= max_segments:
            break
        expand(m)

    refills = 0
    while total() < target_words * fill_ratio and len(bodies) < max_segments and refills < _MAX_REFILLS:
        refills += 1
        extra = ("\n\nAlready covered (do NOT repeat these; propose NEW, distinct sub-areas of the topic): "
                 + "; ".join(covered) + ".")
        fresh = [m for m in (outline(extra).get("movements") or [])
                 if isinstance(m, dict) and str(m.get("heading") or "").strip().lower() not in seen]
        if not fresh:
            break
        before = len(bodies)
        for m in fresh:
            if len(bodies) >= max_segments or total() >= target_words:
                break
            expand(m)
        if len(bodies) == before:
            break

    if not bodies:
        raise RuntimeError("script generation produced no narration")
    body = "\n\n".join(bodies)
    words = len(body.split())
    log.info("script: %d words in %d movements (%d refills), target %d", words, len(bodies), refills, target_words)
    if words > target_words * max_words_ratio:
        raise ScriptTooLong(f"{words} words > {max_words_ratio}x target {target_words}")

    meta = llm.generate_json(
        _fill(METADATA_PROMPT, topic=topic, hours=max(1, round(minutes / 60)), opening=body[:600]), max_tokens=600,
    )
    return Script(
        title=str(meta.get("title") or f"Calm Facts About {topic} to Fall Asleep To")[:100],
        description=str(meta.get("description") or ""),
        tags=[str(t) for t in (meta.get("tags") or [])][:15],
        body=body,
        word_count=words,
        movements=len(bodies),
        image_queries=[str(q) for q in (first.get("image_search_queries") or [])],
    )
