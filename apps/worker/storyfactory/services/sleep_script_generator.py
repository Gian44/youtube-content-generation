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
_MAX_SEGMENT_WORDS = 1500          # realistic per-segment ask (the model under-writes high targets)
_DEFAULT_MAX_SEGMENTS = 80         # hard cap on total expansion calls (cost bound)
_MAX_REFILLS = 8                   # how many extra outline rounds we'll request
_FILL_RATIO = 0.85                 # stop once we reach this fraction of the word target


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
    max_segments: int = _DEFAULT_MAX_SEGMENTS,
    dry_run: bool = False,
) -> dict:
    """Produce a full long-form sleep narration for ``topic``.

    Returns a dict with the same shape the pipeline already consumes:
    ``{title, hook, body, asset_keywords, image_search_queries, word_count}``.

    The model reliably under-writes a high per-segment word target, so a fixed
    movement count rarely fills ~3h on its own. We therefore **keep requesting and
    expanding fresh movements until the cumulative word count approaches
    ``target_words``** (or we hit ``max_segments`` / run out of distinct sub-areas).
    A single failed movement is tolerated; only an empty outline or producing no
    narration at all raises.
    """
    if dry_run:
        return _dry_run_script(topic, target_minutes, num_movements)

    per_words = max(_MIN_SEGMENT_WORDS, min(_MAX_SEGMENT_WORDS, target_words // max(1, num_movements)))

    bodies: list[str] = []
    covered: list[str] = []
    seen: set[str] = set()
    state = {"tail": ""}

    def _outline(extra_hints: str) -> dict:
        prompt = build_outline_prompt(
            outline_template,
            topic=topic,
            hints=(hints + extra_hints) if extra_hints else hints,
            target_minutes=target_minutes,
            num_movements=num_movements,
        )
        return _parse_json(
            generate_text(
                prompt=prompt, provider=AIProvider.OPENAI, temperature=0.7,
                max_tokens=_OUTLINE_MAX_TOKENS, response_format="json",
            )
        )

    def _expand(movement: dict) -> None:
        heading = str(movement.get("heading") or f"Part {len(bodies) + 1}")
        seen.add(heading.strip().lower())  # mark even on failure so we don't re-propose it
        beats = [str(b) for b in (movement.get("beats") or []) if str(b).strip()]
        running_summary = ("Already covered: " + "; ".join(covered) + ".") if covered else ""
        seg_prompt = build_segment_prompt(
            segment_template, topic=topic, heading=heading, beats=beats,
            running_summary=running_summary, previous_tail=state["tail"], target_words=per_words,
        )
        try:
            seg = _parse_json(
                generate_text(
                    prompt=seg_prompt, provider=AIProvider.OPENAI, temperature=0.7,
                    max_tokens=segment_max_tokens, response_format="json",
                )
            )
            seg_body = str(seg.get("body") or "").strip()
        except Exception as exc:  # noqa: BLE001 — one bad movement must not abort the video
            log.warning("sleep_segment_failed", heading=heading, error=str(exc))
            return
        if not seg_body:
            log.warning("sleep_segment_empty", heading=heading)
            return
        bodies.append(seg_body)
        covered.append(heading)
        state["tail"] = _tail(seg_body)

    def _total_words() -> int:
        return sum(len(b.split()) for b in bodies)

    # 1. Outline pass — one call plans the opening set of movements.
    outline = _outline("")
    movements = [m for m in (outline.get("movements") or []) if isinstance(m, dict)]
    if not movements:
        raise RuntimeError("Sleep outline returned no movements")

    # 2. Expand the planned movements (the whole first outline).
    for movement in movements:
        if len(bodies) >= max_segments:
            break
        _expand(movement)

    # 3. Loop: request MORE distinct movements and expand them until we approach
    #    the word target. Stops on: target reached, segment cap, refill cap, the
    #    topic running out of fresh sub-areas, or a round that makes no progress.
    refills = 0
    while (
        _total_words() < target_words * _FILL_RATIO
        and len(bodies) < max_segments
        and refills < _MAX_REFILLS
    ):
        refills += 1
        extra_hints = (
            "\n\nAlready covered (do NOT repeat these; propose NEW, distinct sub-areas "
            "of the topic): " + "; ".join(covered) + "."
        )
        extra = _outline(extra_hints)
        fresh = [
            m for m in (extra.get("movements") or [])
            if isinstance(m, dict)
            and str(m.get("heading") or "").strip().lower() not in seen
        ]
        if not fresh:
            break  # the topic is exhausted; stop rather than loop forever
        before = len(bodies)
        for movement in fresh:
            if len(bodies) >= max_segments or _total_words() >= target_words:
                break
            _expand(movement)
        if len(bodies) == before:
            break  # no progress this round (e.g. all failed) → stop

    if not bodies:
        raise RuntimeError("Sleep script generation produced no narration")

    body = "\n\n".join(bodies)
    log.info(
        "sleep_script_generated",
        topic=topic,
        movements=len(bodies),
        word_count=len(body.split()),
        refills=refills,
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
