"""Sleep On Facts pipeline — pipeline_mode="sleep_facts".

One calm, single-topic long-form video per run (~1/day). The topic comes from a
per-channel rotation cursor; facts are grounded with free, keyless Wikipedia
data; visuals are matched to the topic (not generic drama b-roll). The whole
script stays on ONE subject — unrelated domains are never mixed in a video.

Reuses the shared long-form path: one Story → TTS → sentence captions →
topic-matched assets → render_long_form → upload (existing pipeline).
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from rich.console import Console

from storyfactory.channel_context import effective_config
from storyfactory.content_defaults import DEFAULT_SLEEP_TOPICS, SLEEP_FACTS_DEFAULTS
from storyfactory.db.models import DailyBatch, SettingsModel, Story
from storyfactory.logger import get_logger
from storyfactory.pipeline.story_generator import _resolve_prompt
from storyfactory.services.asset_collector import collect_topic_assets, download_assets
from storyfactory.services.channel_service import slugify
from storyfactory.services.policy_checker import check_story_policy, rewrite_flagged_story
from storyfactory.services.sleep_script_generator import generate_sleep_script
from storyfactory.services.slideshow_renderer import render_sleep_video
from storyfactory.services.topic_research import research_topic
from storyfactory.services.tts_service import generate_tts

console = Console()
log = get_logger("sleep_facts_pipeline")

# Fallback narration pace (words per spoken minute) when not set in channel config.
_DEFAULT_WORDS_PER_MINUTE = 150
_CURSOR_KEY_PREFIX = "sleep_facts_cursor:"


def run_sleep_facts_for_channel(session, channel, ctx, settings) -> None:
    """Produce one calm, single-topic long-form video for a channel."""
    cfg = {**SLEEP_FACTS_DEFAULTS, **(effective_config("sleep_facts", {}) or {})}

    if int(effective_config("long_form_per_day", 1)) < 1:
        console.print("[yellow]long_form_per_day is 0 — Sleep On Facts produces nothing.[/yellow]")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = (
        session.query(DailyBatch).filter_by(date=today, channel_id=channel.id).first()
    )
    # Block on any non-failed batch for today (not just "completed"): a crash mid-run
    # leaves the batch at "stories_generated"/"rendered", and re-running must not
    # create a second batch (and a second uploaded video) for the same day.
    if existing and existing.status != "failed":
        console.print(
            f"[yellow]Sleep video already started/produced today for '{channel.slug}'.[/yellow]"
        )
        return

    # 1. Pick the next topic from the rotation cursor.
    rotation = cfg.get("topic_rotation") or DEFAULT_SLEEP_TOPICS
    rotation = [str(t).strip() for t in rotation if str(t).strip()]
    if not rotation:
        console.print("[red]No sleep topics configured. Add a topic_rotation in the channel config.[/red]")
        return
    cursor = _get_cursor(session, channel.id)
    topic = rotation[cursor % len(rotation)]
    console.print(f"[bold blue]Sleep topic ({cursor % len(rotation) + 1}/{len(rotation)}): {topic}[/bold blue]")

    # 2. Ground the topic with free Wikipedia data (best-effort).
    research = research_topic(
        topic,
        dry_run=settings.dry_run,
        enabled=bool(cfg.get("enable_wikipedia_grounding", True)),
    )
    if research["hints"]:
        console.print(f"[green]✓ Grounded with Wikipedia ({len(research['hints'])} chars)[/green]")

    # 3. Pre-flight: the segmented-generation prompts must exist before we create a
    # batch (avoids leaving a doomed batch that would wedge the one-per-day guard).
    if (
        _resolve_prompt(session, "sleep_facts_outline", channel.id) is None
        or _resolve_prompt(session, "sleep_facts_segment", channel.id) is None
    ):
        console.print(
            "[red]sleep_facts_outline / sleep_facts_segment prompts not found. Run: npm run seed[/red]"
        )
        return

    # 4. Batch.
    batch = DailyBatch(
        id=str(uuid.uuid4()),
        channel_id=channel.id,
        date=today,
        category=slugify(topic)[:50],
        status="stories_generated",
    )
    session.add(batch)
    session.commit()

    # 5. Produce the video. On ANY failure, mark the batch failed so a crash does
    # not wedge the day (the one-per-day guard skips only non-failed batches).
    try:
        _produce_sleep_video(
            session, channel, batch, topic, research, cursor, rotation, cfg, settings
        )
    except Exception as exc:
        log.error("sleep_facts_failed", channel=channel.slug, error=str(exc))
        try:
            session.rollback()
            batch.status = "failed"
            batch.error = str(exc)[:500]
            session.commit()
        except Exception:
            session.rollback()
        raise


def _produce_sleep_video(
    session, channel, batch, topic, research, cursor, rotation, cfg, settings
) -> None:
    """Generate → TTS → captions → assets → render the single long-form video."""
    # Generate the calm, single-topic narration (single-domain validated).
    story, keywords = _make_sleep_story(session, channel, batch, topic, research, cfg, settings)

    # Policy check.
    result = check_story_policy(story)
    fail_safe = bool(effective_config("fail_safe_on_policy_flag", True))
    if result["action"] == "block" and fail_safe:
        batch.status = "failed"
        batch.error = "Sleep narration blocked by policy"
        session.commit()
        console.print("[red]Narration blocked by policy. Batch failed.[/red]")
        return
    if result["action"] == "rewrite":
        rewritten = rewrite_flagged_story(story, result["flags"])
        if rewritten:
            story.title = rewritten.get("title", story.title)
            story.hook = rewritten.get("hook", story.hook)
            story.body = rewritten.get("body", story.body)
            story.policy_status = "rewritten"
        else:
            story.policy_status = "clean"
    else:
        story.policy_status = "clean"
    session.commit()

    # 6. TTS — ONE calm voice (onyx) for the whole narration, chunked internally
    # for the multi-hour script. No captions for sleep content.
    tts_job = generate_tts(
        story,
        provider="openai",
        voice=cfg.get("tts_voice", "onyx"),
        model=cfg.get("tts_model", "tts-1-hd"),
        speed=cfg.get("tts_speed", 0.9),
        instructions=cfg.get("tts_instructions"),
    )
    if tts_job.status != "completed":
        batch.status = "failed"
        batch.error = "TTS failed"
        session.commit()
        console.print("[red]TTS failed. Batch failed.[/red]")
        return

    # 7. Topic-matched stock IMAGES (slideshow), not background video.
    images_target = int(cfg.get("images_target", cfg.get("assets_per_video", 150)))
    asset_type = cfg.get("asset_type", "image")
    assets = download_assets(
        collect_topic_assets(keywords, count=images_target, asset_type=asset_type)
    )
    console.print(f"[green]✓ Collected {len(assets)} topic-matched {asset_type}s[/green]")

    # 8. Render the long-form sleep video: image slideshow looped under narration.
    long_form_job = render_sleep_video(
        story,
        tts_job,
        assets,
        batch.id,
        dwell_seconds=float(cfg.get("slideshow_dwell_seconds", 20)),
        crossfade_seconds=float(cfg.get("crossfade_seconds", 2)),
        ken_burns=bool(cfg.get("ken_burns", True)),
        fps=int(cfg.get("slideshow_fps", 24)),
        clip_workers=(int(cfg["slideshow_clip_workers"]) if cfg.get("slideshow_clip_workers") else None),
    )
    # render_sleep_video returns a failed RenderJob (it does not raise) on FFmpeg
    # errors. Only advance the topic cursor when a usable video was produced —
    # otherwise the topic would be silently burned with nothing to upload.
    if not long_form_job or getattr(long_form_job, "status", None) != "completed":
        batch.status = "failed"
        batch.error = getattr(long_form_job, "error", None) or "Sleep render failed"
        session.commit()
        console.print("[red]Render failed. Batch failed; topic cursor not advanced.[/red]")
        return

    # 9. Advance the cursor and finalize.
    _set_cursor(session, channel.id, cursor + 1)
    batch.shorts_count = 0
    batch.long_form_count = 1
    batch.status = "completed"
    batch.completed_at = datetime.now(timezone.utc)
    session.commit()

    console.print("\n[bold green]═══ Sleep On Facts Run Complete ═══[/bold green]")
    console.print(f"  Channel: {channel.name} ({channel.slug})")
    console.print(f"  Topic: {topic}")
    console.print(f"  Long-form rendered: {'Yes' if long_form_job else 'No'}")
    console.print(f"  Next topic index: {(cursor + 1) % len(rotation)}")
    if settings.dry_run:
        console.print("\n[yellow]⚠ DRY RUN MODE - no uploads were made[/yellow]")


# ----------------------------------------------------------------------------
# Story generation
# ----------------------------------------------------------------------------

def _make_sleep_story(session, channel, batch, topic, research, cfg, settings) -> tuple[Story, list[str]]:
    """Generate the calm narration (segmented); regenerate once if it drifts off-topic."""
    outline_tpl = _resolve_prompt(session, "sleep_facts_outline", channel.id)
    segment_tpl = _resolve_prompt(session, "sleep_facts_segment", channel.id)
    if not outline_tpl or not segment_tpl:
        raise RuntimeError(
            "sleep_facts_outline / sleep_facts_segment prompts not found. Run: npm run seed"
        )

    # Sleep length comes from the sleep-specific config: ``cfg`` already merges
    # SLEEP_FACTS_DEFAULTS (180) with the channel's nested ``sleep_facts`` override.
    # We deliberately do NOT read the top-level ``long_form_target_minutes`` via
    # effective_config — that key is ALWAYS seeded into ``ctx.config`` from the
    # global Settings default (10) by content_config_defaults(), which would short-
    # circuit the lookup and silently shrink every sleep video to ~10 minutes.
    target_minutes = int(cfg.get("long_form_target_minutes", 180))
    wpm = int(cfg.get("narration_wpm", _DEFAULT_WORDS_PER_MINUTE))
    target_words = target_minutes * wpm
    persona = cfg.get("voice_persona", "calm")

    def _generate() -> dict:
        return generate_sleep_script(
            topic=topic,
            hints=research.get("hints") or "",
            target_minutes=target_minutes,
            target_words=target_words,
            outline_template=outline_tpl.template,
            segment_template=segment_tpl.template,
            num_movements=int(cfg.get("gen_num_movements", 16)),
            segment_max_tokens=int(cfg.get("gen_segment_max_tokens", 2800)),
            max_segments=int(cfg.get("gen_max_segments", 80)),
            dry_run=settings.dry_run,
        )

    data = _generate()
    body = str(data.get("body") or "")

    # Single-domain post-validation: the topic must actually appear in the script.
    # On a persisted miss we FAIL (rather than publish off-topic content); the outer
    # handler marks the batch failed and the topic cursor is not advanced.
    if not settings.dry_run and not _on_topic(topic, data.get("title", ""), body):
        log.warning("sleep_topic_drift_retry", topic=topic)
        data = _generate()
        body = str(data.get("body") or "")
        if not _on_topic(topic, data.get("title", ""), body):
            log.warning("sleep_topic_drift_persisted", topic=topic)
            raise RuntimeError(
                f"Sleep narration for '{topic}' drifted off-topic after a retry; not publishing"
            )

    content_hash = hashlib.sha256(f"{topic}:{body}".encode()).hexdigest()
    story = Story(
        id=str(uuid.uuid4()),
        batch_id=batch.id,
        channel_id=channel.id,
        category=slugify(topic)[:50],
        type="long_form_extra",
        title=(data.get("title") or f"Calm Facts About {topic}")[:200],
        hook=data.get("hook", "") or "",
        body=body,
        comment_bait="",
        word_count=int(data.get("word_count") or len(body.split())),
        estimated_duration_seconds=_estimate_duration(body),
        voice_persona=persona,
        originality_hash=content_hash,
        novelty_score=1.0,
        prompt_used="sleep_facts_outline+segment",
        # Store metadata only — the full ~20k-word body already lives in `body`.
        raw_output=json.dumps(
            {
                "title": data.get("title"),
                "hook": data.get("hook"),
                "word_count": data.get("word_count"),
            }
        ),
        policy_status="pending",
        policy_flags=[],
        order_index=0,
    )
    session.add(story)
    session.commit()

    keywords = list(data.get("image_search_queries") or [])
    keywords += list(data.get("asset_keywords") or [])
    keywords += research.get("keywords", [])
    return story, keywords


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _on_topic(topic: str, title: str, body: str) -> bool:
    """Lightweight single-domain check: the topic's key word must appear."""
    haystack = f"{title}\n{body}".lower()
    words = [w for w in topic.lower().replace("-", " ").split() if len(w) > 3]
    if not words:
        return topic.lower() in haystack
    return any(word in haystack for word in words)


def _estimate_duration(text: str) -> float:
    return (len(text.split()) / 150) * 60


def _get_cursor(session, channel_id: str) -> int:
    row = session.query(SettingsModel).filter_by(key=_CURSOR_KEY_PREFIX + channel_id).first()
    if row is None:
        return 0
    try:
        return int(row.value)
    except (TypeError, ValueError):
        return 0


def _set_cursor(session, channel_id: str, value: int) -> None:
    key = _CURSOR_KEY_PREFIX + channel_id
    row = session.query(SettingsModel).filter_by(key=key).first()
    if row is None:
        row = SettingsModel(key=key, value=str(int(value)), description="Sleep On Facts topic cursor")
        session.add(row)
    else:
        row.value = str(int(value))
    session.commit()
