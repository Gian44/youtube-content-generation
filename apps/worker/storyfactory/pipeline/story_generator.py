"""Story generation using AI providers (OpenAI / Gemini)."""

import json
import hashlib
import random
import uuid
from datetime import datetime, timezone

from storyfactory.channel_context import effective_config
from storyfactory.config import get_settings
from storyfactory.content_defaults import DEFAULT_CATEGORY_HINTS, DEFAULT_VOICE_PERSONAS
from storyfactory.db.engine import get_session
from storyfactory.db.models import Story, PromptTemplate, DailyBatch
from storyfactory.logger import get_logger
from storyfactory.services.ai_provider import generate_text, AIProvider

log = get_logger("story_generator")

# Re-exported for backward compatibility; channel config overrides these.
VOICE_PERSONAS = DEFAULT_VOICE_PERSONAS
CATEGORY_HINTS = DEFAULT_CATEGORY_HINTS


def _channel_personas() -> list[str]:
    return effective_config("voice_personas", DEFAULT_VOICE_PERSONAS) or DEFAULT_VOICE_PERSONAS


def _channel_hint(category: str) -> str:
    hints = effective_config("category_hints", DEFAULT_CATEGORY_HINTS) or DEFAULT_CATEGORY_HINTS
    return hints.get(category, "")


def _resolve_prompt(session, name: str, channel_id: str | None) -> PromptTemplate | None:
    """Return a channel-specific prompt override if present, else the global one."""
    if channel_id:
        override = (
            session.query(PromptTemplate)
            .filter_by(name=name, channel_id=channel_id, is_active=True)
            .first()
        )
        if override:
            return override
    return (
        session.query(PromptTemplate)
        .filter_by(name=name, channel_id=None, is_active=True)
        .first()
    )


def generate_short_stories(batch: DailyBatch, count: int) -> list[Story]:
    """Generate short stories for YouTube Shorts."""
    settings = get_settings()
    session = get_session()

    try:
        # Get the prompt template (channel override falls back to global)
        template = _resolve_prompt(session, "short_story_generation", batch.channel_id)
        if not template:
            raise RuntimeError("Short story prompt template not found. Run: npm run seed")

        # Get recently used hooks to avoid duplicates (scoped to this channel)
        recent_query = session.query(Story.hook).filter(
            Story.category == batch.category, Story.type == "short"
        )
        if batch.channel_id:
            recent_query = recent_query.filter(Story.channel_id == batch.channel_id)
        recent_stories = recent_query.order_by(Story.created_at.desc()).limit(20).all()
        used_hooks = [s.hook for s in recent_stories]

        personas = _channel_personas()
        stories = []
        for i in range(count):
            persona = random.choice(personas)
            hints = _channel_hint(batch.category)

            prompt = template.template.replace(
                "{{category}}", batch.category
            ).replace(
                "{{persona}}", persona
            ).replace(
                "{{topic_hints}}", hints
            ).replace(
                "{{used_hooks}}", json.dumps(used_hooks[-5:]) if used_hooks else "[]"
            )

            log.info("generating_short_story", batch_id=batch.id, index=i, persona=persona)

            if settings.dry_run:
                # Generate a deterministic sample story in dry-run mode
                story_data = _generate_dry_run_story(batch.category, persona, i)
            else:
                raw_output = generate_text(
                    prompt=prompt,
                    provider=AIProvider.OPENAI,
                    temperature=0.9,
                    max_tokens=500,
                    response_format="json",
                )
                try:
                    story_data = json.loads(raw_output)
                except json.JSONDecodeError:
                    # Try to extract JSON from the response
                    start = raw_output.find("{")
                    end = raw_output.rfind("}") + 1
                    if start >= 0 and end > start:
                        story_data = json.loads(raw_output[start:end])
                    else:
                        log.error("failed_to_parse_story", raw=raw_output[:200])
                        continue

            # Create originality hash
            content_hash = hashlib.sha256(
                story_data.get("body", "").encode()
            ).hexdigest()

            # Check for duplicate
            existing = session.query(Story).filter_by(originality_hash=content_hash).first()
            if existing:
                log.warning("duplicate_story_detected", hash=content_hash[:16])
                continue

            story = Story(
                id=str(uuid.uuid4()),
                batch_id=batch.id,
                channel_id=batch.channel_id,
                category=batch.category,
                type="short",
                title=story_data.get("title", f"Story {i+1}"),
                hook=story_data.get("hook", ""),
                body=story_data.get("body", ""),
                comment_bait=story_data.get("comment_bait", ""),
                word_count=story_data.get("word_count", len(story_data.get("body", "").split())),
                estimated_duration_seconds=_estimate_duration(story_data.get("body", "")),
                voice_persona=persona,
                originality_hash=content_hash,
                novelty_score=_calculate_novelty(story_data.get("body", ""), session),
                prompt_used=prompt,
                raw_output=json.dumps(story_data),
                policy_status="pending",
                policy_flags=[],
                order_index=i,
            )
            session.add(story)
            stories.append(story)
            used_hooks.append(story.hook)

            log.info("short_story_generated", story_id=story.id, title=story.title[:50])

        session.commit()
        return stories

    except Exception as e:
        session.rollback()
        log.error("story_generation_failed", error=str(e))
        raise
    finally:
        session.close()


def generate_long_form_stories(batch: DailyBatch, count: int) -> list[Story]:
    """Generate extra longer stories for the long-form compilation."""
    settings = get_settings()
    session = get_session()

    try:
        template = _resolve_prompt(session, "long_form_story_generation", batch.channel_id)
        if not template:
            raise RuntimeError("Long-form story prompt template not found. Run: npm run seed")

        personas = _channel_personas()
        stories = []
        for i in range(count):
            persona = random.choice(personas)
            hints = _channel_hint(batch.category)

            prompt = template.template.replace(
                "{{category}}", batch.category
            ).replace(
                "{{persona}}", persona
            ).replace(
                "{{topic_hints}}", hints
            )

            log.info("generating_long_form_story", batch_id=batch.id, index=i)

            if settings.dry_run:
                story_data = _generate_dry_run_long_story(batch.category, persona, i)
            else:
                raw_output = generate_text(
                    prompt=prompt,
                    provider=AIProvider.OPENAI,
                    temperature=0.9,
                    max_tokens=1200,
                    response_format="json",
                )
                try:
                    story_data = json.loads(raw_output)
                except json.JSONDecodeError:
                    start = raw_output.find("{")
                    end = raw_output.rfind("}") + 1
                    if start >= 0 and end > start:
                        story_data = json.loads(raw_output[start:end])
                    else:
                        log.error("failed_to_parse_long_story", raw=raw_output[:200])
                        continue

            content_hash = hashlib.sha256(
                story_data.get("body", "").encode()
            ).hexdigest()

            existing = session.query(Story).filter_by(originality_hash=content_hash).first()
            if existing:
                log.warning("duplicate_long_story_detected", hash=content_hash[:16])
                continue

            story = Story(
                id=str(uuid.uuid4()),
                batch_id=batch.id,
                channel_id=batch.channel_id,
                category=batch.category,
                type="long_form_extra",
                title=story_data.get("title", f"Long Story {i+1}"),
                hook=story_data.get("hook", ""),
                body=story_data.get("body", ""),
                comment_bait=story_data.get("comment_bait", ""),
                word_count=story_data.get("word_count", len(story_data.get("body", "").split())),
                estimated_duration_seconds=_estimate_duration(story_data.get("body", "")),
                voice_persona=persona,
                originality_hash=content_hash,
                novelty_score=_calculate_novelty(story_data.get("body", ""), session),
                prompt_used=prompt,
                raw_output=json.dumps(story_data),
                policy_status="pending",
                policy_flags=[],
                order_index=i,
            )
            session.add(story)
            stories.append(story)

            log.info("long_story_generated", story_id=story.id, title=story.title[:50])

        session.commit()
        return stories

    except Exception as e:
        session.rollback()
        log.error("long_story_generation_failed", error=str(e))
        raise
    finally:
        session.close()


def _estimate_duration(text: str) -> float:
    """Estimate reading duration in seconds (average 150 words per minute)."""
    word_count = len(text.split())
    return (word_count / 150) * 60


def _calculate_novelty(text: str, session) -> float:
    """Calculate a simple novelty score based on content uniqueness."""
    if not text:
        return 0.0

    words = set(text.lower().split())
    # Compare with recent stories
    recent = (
        session.query(Story.body)
        .order_by(Story.created_at.desc())
        .limit(50)
        .all()
    )

    if not recent:
        return 1.0

    total_overlap = 0
    for r in recent:
        recent_words = set(r.body.lower().split())
        if recent_words:
            overlap = len(words & recent_words) / max(len(words), 1)
            total_overlap += overlap

    avg_overlap = total_overlap / len(recent)
    novelty = max(0.0, min(1.0, 1.0 - avg_overlap))
    return round(novelty, 3)


def _generate_dry_run_story(category: str, persona: str, index: int) -> dict:
    """Generate a placeholder story for dry-run mode."""
    import uuid as _uuid
    unique_id = _uuid.uuid4().hex[:8]
    return {
        "title": f"[DRY RUN] Sample {category} Story #{index + 1}",
        "hook": f"You won't believe what happened when I tried to be reasonable about {category.replace('_', ' ')}...",
        "body": f"This is sample story {unique_id} (#{index + 1}) generated in dry-run mode for the {category.replace('_', ' ')} category. "
        f"In a real run, this would be an original fictional story generated by AI. "
        f"The story would follow the standard structure: hook, setup, escalation, twist, and comment bait. "
        f"It would be narrated in a {persona} voice style and target 35-60 seconds of content. "
        f"The word count would be between 100-160 words to fit the YouTube Shorts format perfectly.",
        "comment_bait": f"What would you do in this situation? Drop your {category.replace('_', ' ')} stories below!",
        "word_count": 85,
    }


def _generate_dry_run_long_story(category: str, persona: str, index: int) -> dict:
    """Generate a placeholder long story for dry-run mode."""
    import uuid as _uuid
    unique_id = _uuid.uuid4().hex[:8]
    return {
        "title": f"[DRY RUN] Long {category} Story #{index + 1}",
        "hook": f"What started as a normal day turned into the most dramatic {category.replace('_', ' ')} situation I've ever faced...",
        "body": f"This is sample long-form story {unique_id} (#{index + 1}) generated in dry-run mode for the {category.replace('_', ' ')} category. "
        f"In production, this would be a detailed 300-600 word story with rich character development. "
        f"The story would include backstory establishing the characters and their relationships, "
        f"an inciting incident that triggers the central conflict, two to three escalating events "
        f"that build tension, a climactic confrontation, and a resolution with a twist. "
        f"The narrator would reflect on the experience with a {persona} tone throughout. "
        f"This longer format allows for more nuance and emotional depth than the Shorts version. " * 3,
        "comment_bait": f"Have you ever experienced something like this? Share your story!",
        "word_count": 350,
    }
