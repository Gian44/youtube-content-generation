"""Content policy checker - validates stories against safety guardrails."""

import json
import uuid
from datetime import datetime, timezone

from storyfactory.config import get_settings
from storyfactory.db.engine import get_session
from storyfactory.db.models import Story, PolicyFlagRecord, PromptTemplate
from storyfactory.logger import get_logger
from storyfactory.services.ai_provider import generate_text, AIProvider

log = get_logger("policy_checker")

# Local keyword-based pre-screening
BLOCK_KEYWORDS = {
    "minor_sexual": ["child porn", "underage sex", "cp ", "loli"],
    "self_harm": ["how to kill yourself", "suicide method", "cut yourself"],
    "crime_instructions": ["how to make a bomb", "how to cook meth", "how to hack"],
    "doxxing": ["their address is", "their phone number is", "their social security"],
}

WARN_KEYWORDS = {
    "excessive_profanity": ["fuck", "shit", "bitch", "damn", "ass"],
    "graphic_violence": ["disembowel", "decapitat", "dismember", "mutilat"],
}


def check_story_policy(story: Story) -> dict:
    """Run policy checks on a story.

    Returns:
        dict with keys: is_clean, flags, action (pass/rewrite/block)
    """
    settings = get_settings()
    result = {"is_clean": True, "flags": [], "action": "pass"}

    # Phase 1: Local keyword screening (fast, free)
    local_flags = _local_keyword_check(story.body + " " + story.hook)
    if local_flags:
        result["flags"].extend(local_flags)

    # Check for immediate blocks
    block_flags = [f for f in result["flags"] if f["severity"] == "block"]
    if block_flags:
        result["is_clean"] = False
        result["action"] = "block"
        _save_flags(story.id, result["flags"])
        return result

    # Phase 2: AI-based policy review (more nuanced)
    if not settings.dry_run:
        ai_flags = _ai_policy_check(story)
        if ai_flags:
            result["flags"].extend(ai_flags)

    # Determine action
    if any(f["severity"] == "block" for f in result["flags"]):
        result["action"] = "block"
        result["is_clean"] = False
    elif any(f["severity"] == "rewrite" for f in result["flags"]):
        result["action"] = "rewrite"
        result["is_clean"] = False
    elif any(f["severity"] == "warn" for f in result["flags"]):
        result["action"] = "pass"  # Warn but allow
        result["is_clean"] = True

    # Save flags to database
    if result["flags"]:
        _save_flags(story.id, result["flags"])

    log.info(
        "policy_check_complete",
        story_id=story.id,
        is_clean=result["is_clean"],
        action=result["action"],
        flag_count=len(result["flags"]),
    )

    return result


def _local_keyword_check(text: str) -> list[dict]:
    """Fast local keyword-based screening."""
    text_lower = text.lower()
    flags = []

    for flag_type, keywords in BLOCK_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text_lower:
                flags.append({
                    "type": flag_type,
                    "severity": "block",
                    "description": f"Blocked keyword detected: '{keyword}'",
                    "excerpt": keyword,
                })

    for flag_type, keywords in WARN_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw.lower() in text_lower)
        if flag_type == "excessive_profanity" and count >= 3:
            flags.append({
                "type": flag_type,
                "severity": "rewrite",
                "description": f"Excessive profanity detected ({count} instances)",
                "excerpt": "",
            })
        elif flag_type == "graphic_violence" and count >= 1:
            flags.append({
                "type": flag_type,
                "severity": "rewrite",
                "description": "Graphic violence detected",
                "excerpt": "",
            })

    return flags


def _ai_policy_check(story: Story) -> list[dict]:
    """AI-based policy review using the policy template."""
    session = get_session()
    try:
        template = (
            session.query(PromptTemplate)
            .filter_by(name="policy_review", is_active=True)
            .first()
        )
        if not template:
            log.warning("policy_template_not_found_skipping_ai_check")
            return []

        prompt = template.template.replace("{{story_text}}", story.body)

        raw_output = generate_text(
            prompt=prompt,
            provider=AIProvider.OPENAI,
            temperature=0.1,
            max_tokens=500,
            response_format="json",
        )

        try:
            result = json.loads(raw_output)
        except json.JSONDecodeError:
            start = raw_output.find("{")
            end = raw_output.rfind("}") + 1
            if start >= 0 and end > start:
                result = json.loads(raw_output[start:end])
            else:
                log.error("policy_check_parse_failed")
                return []

        if result.get("is_clean", True):
            return []

        return result.get("flags", [])

    except Exception as e:
        log.error("ai_policy_check_failed", error=str(e))
        return []
    finally:
        session.close()


def _save_flags(story_id: str, flags: list[dict]):
    """Save policy flags to the database."""
    from storyfactory.channel_context import get_current_channel_id

    session = get_session()
    channel_id = get_current_channel_id()
    try:
        for flag in flags:
            record = PolicyFlagRecord(
                id=str(uuid.uuid4()),
                channel_id=channel_id,
                story_id=story_id,
                flag_type=flag.get("type", "unknown"),
                severity=flag.get("severity", "info"),
                description=flag.get("description", ""),
            )
            session.add(record)
        session.commit()
    except Exception as e:
        session.rollback()
        log.error("save_flags_failed", error=str(e))
    finally:
        session.close()


def rewrite_flagged_story(story: Story, flags: list[dict]) -> dict | None:
    """Attempt to rewrite a flagged story to pass policy checks."""
    settings = get_settings()
    session = get_session()

    try:
        template = (
            session.query(PromptTemplate)
            .filter_by(name="story_rewrite", is_active=True)
            .first()
        )
        if not template:
            log.warning("rewrite_template_not_found")
            return None

        violations = json.dumps(flags, indent=2)
        prompt = (
            template.template
            .replace("{{story_text}}", story.body)
            .replace("{{violations}}", violations)
            .replace("{{persona}}", story.voice_persona)
        )

        if settings.dry_run:
            return {
                "title": story.title,
                "hook": story.hook,
                "body": "[REWRITTEN - DRY RUN] " + story.body,
                "comment_bait": story.comment_bait,
                "word_count": story.word_count,
                "changes_made": ["dry_run_placeholder"],
            }

        raw_output = generate_text(
            prompt=prompt,
            provider=AIProvider.OPENAI,
            temperature=0.7,
            max_tokens=800,
        )

        try:
            result = json.loads(raw_output)
            return result
        except json.JSONDecodeError:
            start = raw_output.find("{")
            end = raw_output.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(raw_output[start:end])
            log.error("rewrite_parse_failed")
            return None

    except Exception as e:
        log.error("rewrite_failed", error=str(e))
        return None
    finally:
        session.close()
