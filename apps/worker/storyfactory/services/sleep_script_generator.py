"""Segmented script generation for long-form (~3h) Sleep On Facts videos.

A single LLM call is bounded by a token ceiling (~3k words) — far short of the
~20k+ words a 3-hour calm narration needs. This module builds the full script in
two passes:

  1. **Outline** — one call returns an ordered list of "movements" (sub-areas of
     the single topic), each with a heading and a few factual "beats".
  2. **Expansion** — one call per movement turns its beats into flowing bedtime
     prose, threaded with a running summary + the tail of the previous movement so
     the narration stays continuous and never repeats itself.

The prompt *templates* are passed in (resolved from the DB by the pipeline) so
this module stays storage-agnostic and unit-testable.
"""

from __future__ import annotations

import json
import uuid

from storyfactory.logger import get_logger
from storyfactory.services.ai_provider import AIProvider, generate_text

log = get_logger("sleep_script_generator")

_OUTLINE_MAX_TOKENS = 3000
_PREVIOUS_TAIL_CHARS = 400
_MIN_SEGMENT_WORDS = 600


def build_outline_prompt(
    template: str, *, topic: str, hints: str, target_minutes: int, num_movements: int
) -> str:
    """Substitute outline-prompt variables. Pure (no I/O)."""
    return (
        template
        .replace("{{topic}}", topic)
        .replace("{{topic_hints}}", hints or "(no external notes available)")
        .replace("{{target_minutes}}", str(target_minutes))
        .replace("{{num_movements}}", str(num_movements))
    )


def build_segment_prompt(
    template: str,
    *,
    topic: str,
    heading: str,
    beats: list[str],
    running_summary: str,
    previous_tail: str,
    target_words: int,
) -> str:
    """Substitute per-movement expansion variables. Pure (no I/O)."""
    beats_text = "\n".join(f"- {b}" for b in beats) if beats else "(none provided)"
    return (
        template
        .replace("{{topic}}", topic)
        .replace("{{movement_heading}}", heading or topic)
        .replace("{{movement_beats}}", beats_text)
        .replace("{{running_summary}}", running_summary or "(nothing yet — this is the opening)")
        .replace("{{previous_tail}}", previous_tail or "(this is the very beginning)")
        .replace("{{target_words}}", str(target_words))
    )


def generate_sleep_script(
    *,
    topic: str,
    hints: str,
    target_minutes: int,
    target_words: int,
    outline_template: str,
    segment_template: str,
    num_movements: int = 16,
    segment_max_tokens: int = 2800,
    dry_run: bool = False,
) -> dict:
    """Produce a full long-form sleep narration for ``topic``.

    Returns a dict with the same shape the pipeline already consumes:
    ``{title, hook, body, asset_keywords, image_search_queries, word_count}``.
    A single failed movement is tolerated (logged + skipped); only an empty
    outline or a total expansion failure raises.
    """
    if dry_run:
        return _dry_run_script(topic, target_minutes, num_movements)

    # 1. Outline pass — one call plans the whole video.
    outline_prompt = build_outline_prompt(
        outline_template,
        topic=topic,
        hints=hints,
        target_minutes=target_minutes,
        num_movements=num_movements,
    )
    outline = _parse_json(
        generate_text(
            prompt=outline_prompt,
            provider=AIProvider.OPENAI,
            temperature=0.7,
            max_tokens=_OUTLINE_MAX_TOKENS,
            response_format="json",
        )
    )
    movements = [m for m in (outline.get("movements") or []) if isinstance(m, dict)]
    if not movements:
        raise RuntimeError("Sleep outline returned no movements")

    per_words = max(_MIN_SEGMENT_WORDS, target_words // max(1, len(movements)))

    # 2. Expansion pass — one call per movement, threaded for continuity.
    bodies: list[str] = []
    covered_headings: list[str] = []
    previous_tail = ""
    for idx, movement in enumerate(movements):
        heading = str(movement.get("heading") or f"Part {idx + 1}")
        beats = [str(b) for b in (movement.get("beats") or []) if str(b).strip()]
        running_summary = (
            "Already covered: " + "; ".join(covered_headings) + "."
            if covered_headings
            else ""
        )
        seg_prompt = build_segment_prompt(
            segment_template,
            topic=topic,
            heading=heading,
            beats=beats,
            running_summary=running_summary,
            previous_tail=previous_tail,
            target_words=per_words,
        )
        try:
            seg = _parse_json(
                generate_text(
                    prompt=seg_prompt,
                    provider=AIProvider.OPENAI,
                    temperature=0.7,
                    max_tokens=segment_max_tokens,
                    response_format="json",
                )
            )
            seg_body = str(seg.get("body") or "").strip()
        except Exception as exc:  # noqa: BLE001 — one bad movement must not abort the video
            log.warning("sleep_segment_failed", heading=heading, error=str(exc))
            continue
        if not seg_body:
            log.warning("sleep_segment_empty", heading=heading)
            continue
        bodies.append(seg_body)
        covered_headings.append(heading)
        previous_tail = _tail(seg_body)

    if not bodies:
        raise RuntimeError("Sleep script generation produced no narration")

    body = "\n\n".join(bodies)
    log.info(
        "sleep_script_generated",
        topic=topic,
        movements=len(bodies),
        word_count=len(body.split()),
    )
    return {
        "title": str(outline.get("title") or f"Calm Facts About {topic} to Fall Asleep To")[:200],
        "hook": str(outline.get("hook") or ""),
        "body": body,
        "asset_keywords": list(outline.get("asset_keywords") or []),
        "image_search_queries": list(outline.get("image_search_queries") or []),
        "word_count": len(body.split()),
    }


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _parse_json(raw: str) -> dict:
    """Parse LLM JSON output, tolerating leading/trailing prose."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        if isinstance(raw, str):
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass
        raise RuntimeError("Sleep facts LLM output was not valid JSON")


def _tail(text: str, n: int = _PREVIOUS_TAIL_CHARS) -> str:
    """Last ``n`` characters of ``text`` (used to seed the next movement)."""
    return (text or "").strip()[-n:]


def _dry_run_script(topic: str, target_minutes: int, num_movements: int) -> dict:
    """Synthesize a script without any API calls (dry-run / tests)."""
    unique = uuid.uuid4().hex[:8]
    hours = target_minutes // 60 if target_minutes >= 60 else target_minutes
    para = (
        f"Tonight we drift gently through the calm world of {topic}. "
        f"This is a dry-run narration ({unique}); in a real run each movement would "
        f"be a long, soothing passage of gentle facts about {topic}, narrated slowly "
        f"to ease you toward sleep. Every sentence stays softly on {topic}. "
    )
    body = "\n\n".join("\n\n".join([para] * 4) for _ in range(max(1, num_movements)))
    return {
        "title": f"[DRY RUN] {hours} Hours of Calm {topic} Facts to Fall Asleep To",
        "hook": f"Close your eyes, and let us explore {topic} together.",
        "body": body,
        "asset_keywords": [topic, f"{topic} landscape", f"{topic} calm", f"{topic} nature"],
        "image_search_queries": [f"{topic} calm", f"{topic} cinematic", f"{topic} landscape"],
        "word_count": len(body.split()),
    }
