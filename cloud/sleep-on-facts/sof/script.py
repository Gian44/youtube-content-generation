"""Segmented script generation for a ~3h calm narration, written FROM a research corpus.

Two passes: one outline call plans "movements" from the article's real section structure; one
expansion call per movement writes prose from the chunks retrieved for that movement's heading
and beats. A cross-movement n-gram check catches the model repeating itself and asks once for a
rewrite. The loop keeps requesting fresh movements until the word count approaches the target.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sof import research
from sof.prompts import DESCRIPTION_FOOTER, METADATA_PROMPT, OUTLINE_PROMPT, SEGMENT_PROMPT

log = logging.getLogger("sof.script")
_MAX_SEG, _TAIL, _MAX_REFILLS = 1500, 400, 8
REPEAT_NGRAM, REPEAT_LIMIT = 8, 0.12          # >12 % of a movement's 8-grams already heard → rewrite once


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
    sources: list[str] = field(default_factory=list)
    image_keywords: list[str] = field(default_factory=list)
    setting_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title, "description": self.description, "tags": self.tags, "body": self.body,
            "word_count": self.word_count, "movements": self.movements, "image_queries": self.image_queries,
            "sources": self.sources, "image_keywords": self.image_keywords, "setting_queries": self.setting_queries,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Script":
        return cls(**{k: d[k] for k in ("title", "description", "tags", "body", "word_count", "movements")},
                   image_queries=list(d.get("image_queries") or []), sources=list(d.get("sources") or []),
                   image_keywords=list(d.get("image_keywords") or []), setting_queries=list(d.get("setting_queries") or []))


def _fill(template: str, **kw) -> str:
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def _ngrams(text: str, n: int = REPEAT_NGRAM) -> set[tuple[str, ...]]:
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def repeat_ratio(body: str, seen: set[tuple[str, ...]]) -> float:
    g = _ngrams(body)
    return (len(g & seen) / len(g)) if g else 0.0


def sources_footer(corpus: research.Corpus | None) -> str:
    if not corpus:
        return "Narration is AI-generated; facts are drawn from public sources."
    lines = "\n".join(f"- {t}: https://en.wikipedia.org/wiki/{t.replace(' ', '_')}" for t in corpus.sources[:12])
    return _fill(DESCRIPTION_FOOTER, sources=lines)


def generate_script(
    llm,
    *,
    topic: str,
    corpus: research.Corpus | None,
    minutes: int,
    target_words: int,
    num_movements: int,
    max_segments: int,
    fill_ratio: float,
    max_words_ratio: float,
    segment_max_tokens: int = 2800,
    writer_model: str | None = None,
    fast_model: str | None = None,
    hints: str = "",
) -> Script:
    # Scale the plan to the target so a 3-minute smoke run does not get 16×600-word movements.
    num_movements = max(2, min(num_movements, round(target_words / 1200)))
    per_words = max(150, min(_MAX_SEG, target_words // num_movements))
    bodies: list[str] = []
    covered: list[str] = []
    seen_headings: set[str] = set()
    heard: set[tuple[str, ...]] = set()
    tail = ""
    lead = (corpus.lead if corpus else hints) or "(no notes available)"
    sections = "\n".join(f"- {s}" for s in (corpus.sections if corpus else [])) or "(none)"
    wiki_title = corpus.title if corpus else topic

    def outline(extra: str) -> dict:
        return llm.generate_json(
            _fill(OUTLINE_PROMPT, topic=topic, wiki_title=wiki_title, lead=lead[:3000] + extra, sections=sections,
                  target_minutes=minutes, num_movements=num_movements),
            max_tokens=3000, model=fast_model,
        )

    def write(prompt: str) -> str:
        return str(llm.generate_json(prompt, max_tokens=segment_max_tokens, model=writer_model).get("body") or "").strip()

    def expand(m: dict) -> None:
        nonlocal tail, heard
        heading = str(m.get("heading") or f"Part {len(bodies) + 1}")
        seen_headings.add(heading.strip().lower())
        beat_list = [str(b).strip() for b in (m.get("beats") or []) if str(b).strip()]
        beats = "\n".join(f"- {b}" for b in beat_list) or "(none provided)"
        notes = research.notes_block(research.retrieve(corpus, heading + " " + " ".join(beat_list))) if corpus else "(no notes)"
        summary = ("Already covered: " + "; ".join(covered) + ".") if covered else "(nothing yet — this is the opening)"
        prompt = _fill(
            SEGMENT_PROMPT, topic=topic, movement_heading=heading, movement_beats=beats, notes=notes,
            running_summary=summary, previous_tail=tail or "(this is the very beginning)", target_words=per_words,
        )
        try:
            body = write(prompt)
            ratio = repeat_ratio(body, heard)
            if body and ratio > REPEAT_LIMIT:
                log.warning("movement %r repeats %.0f%% of earlier phrasing; rewriting", heading, ratio * 100)
                body2 = write(prompt + "\n\nYour previous draft repeated phrases already narrated. Write this movement "
                                       "again with entirely fresh wording and different specific details from the notes.")
                if body2 and repeat_ratio(body2, heard) < ratio:
                    body = body2
        except Exception as exc:  # noqa: BLE001 — one bad movement must not abort the video
            log.warning("segment failed (%s): %s", heading, exc)
            return
        if body:
            bodies.append(body)
            covered.append(heading)
            heard |= _ngrams(body)
            tail = body[-_TAIL:]

    def total() -> int:
        return sum(len(b.split()) for b in bodies)

    first = outline("")
    movements = [m for m in (first.get("movements") or []) if isinstance(m, dict)]
    if not movements:
        raise RuntimeError("outline returned no movements")
    for m in movements[:num_movements]:
        if len(bodies) >= max_segments or total() >= target_words:
            break
        expand(m)

    refills = 0
    while total() < target_words * fill_ratio and len(bodies) < max_segments and refills < _MAX_REFILLS:
        refills += 1
        extra = ("\n\nAlready covered (do NOT repeat these; propose NEW, distinct sub-areas of the topic): "
                 + "; ".join(covered) + ".")
        fresh = [m for m in (outline(extra).get("movements") or [])
                 if isinstance(m, dict) and str(m.get("heading") or "").strip().lower() not in seen_headings]
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
        model=fast_model,
    )
    description = (str(meta.get("description") or "").strip() + "\n\n" + sources_footer(corpus)).strip()
    return Script(
        title=str(meta.get("title") or f"Calm Facts About {topic} to Fall Asleep To")[:100],
        description=description,
        tags=[str(t) for t in (meta.get("tags") or [])][:15],
        body=body,
        word_count=words,
        movements=len(bodies),
        image_queries=[str(q) for q in (first.get("image_search_queries") or [])],
        sources=list(corpus.sources) if corpus else [],
        image_keywords=[str(k).lower() for k in (first.get("image_keywords") or [])] or [w for w in topic.lower().split() if len(w) > 3],
        setting_queries=[str(q) for q in (first.get("image_setting_queries") or [])],
    )
