"""Topic selection with weighted random, cooldown, and round-robin support.

Channel-aware: category weights, the selection mode, and the cooldown window all
come from the active channel's effective config, and the recency lookups are
scoped to that channel so each channel rotates its own topics independently.
"""

import random
from datetime import datetime, timezone, timedelta

from sqlalchemy import desc

from storyfactory.channel_context import effective_config, get_current_channel_id
from storyfactory.content_defaults import DEFAULT_CATEGORY_WEIGHTS, STORY_CATEGORIES
from storyfactory.db.engine import get_session
from storyfactory.db.models import DailyBatch
from storyfactory.logger import get_logger

log = get_logger("topic_selector")

# Re-exported for backward compatibility with existing imports.
DEFAULT_WEIGHTS = DEFAULT_CATEGORY_WEIGHTS


def _weights() -> dict[str, int | float]:
    weights = effective_config("category_weights", DEFAULT_CATEGORY_WEIGHTS)
    return weights or DEFAULT_CATEGORY_WEIGHTS


def _resolve_channel_id(channel_id: str | None) -> str | None:
    return channel_id or get_current_channel_id()


def select_daily_topic(channel_id: str | None = None) -> str:
    """Select a topic for today's batch using the configured selection method."""
    mode = effective_config("daily_topic_mode", "weighted_random")
    channel_id = _resolve_channel_id(channel_id)

    if mode == "weighted_random":
        return _weighted_random_selection(channel_id)
    elif mode == "round_robin":
        return _round_robin_selection(channel_id)
    else:
        raise ValueError(f"Unknown topic mode: {mode}")


def _scoped_batches(session, channel_id: str | None):
    query = session.query(DailyBatch)
    if channel_id:
        query = query.filter(DailyBatch.channel_id == channel_id)
    return query


def _weighted_random_selection(channel_id: str | None) -> str:
    """Select a topic using weighted random with cooldown enforcement."""
    cooldown_days = effective_config("topic_cooldown_days", 2)
    weights = _weights()
    categories_all = list(weights.keys()) or STORY_CATEGORIES
    session = get_session()

    try:
        cooldown_date = (
            datetime.now(timezone.utc) - timedelta(days=cooldown_days)
        ).strftime("%Y-%m-%d")

        recent_batches = (
            _scoped_batches(session, channel_id)
            .with_entities(DailyBatch.category)
            .filter(DailyBatch.date >= cooldown_date)
            .all()
        )
        recent_categories = {b.category for b in recent_batches}

        eligible = {
            cat: weights.get(cat, 1)
            for cat in categories_all
            if cat not in recent_categories
        }

        if not eligible:
            log.warning("All categories in cooldown, allowing all categories")
            eligible = dict(weights)

        categories = list(eligible.keys())
        category_weights = [eligible[c] for c in categories]
        total = sum(category_weights)
        probabilities = [w / total for w in category_weights]

        selected = random.choices(categories, weights=probabilities, k=1)[0]
        log.info(
            "topic_selected",
            category=selected,
            eligible_count=len(eligible),
            channel_id=channel_id,
        )
        return selected
    finally:
        session.close()


def _round_robin_selection(channel_id: str | None) -> str:
    """Select the next topic in a round-robin fashion (per channel)."""
    weights = _weights()
    categories_all = list(weights.keys()) or STORY_CATEGORIES
    session = get_session()

    try:
        last_batch = (
            _scoped_batches(session, channel_id)
            .order_by(desc(DailyBatch.created_at))
            .first()
        )

        if last_batch and last_batch.category in categories_all:
            last_idx = categories_all.index(last_batch.category)
            next_idx = (last_idx + 1) % len(categories_all)
        else:
            next_idx = 0

        selected = categories_all[next_idx]
        log.info("topic_selected_rr", category=selected, index=next_idx, channel_id=channel_id)
        return selected
    finally:
        session.close()


def get_recent_categories(days: int = 7, channel_id: str | None = None) -> list[str]:
    """Get categories used in the last N days (optionally scoped to a channel)."""
    channel_id = _resolve_channel_id(channel_id)
    session = get_session()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        batches = (
            _scoped_batches(session, channel_id)
            .with_entities(DailyBatch.category)
            .filter(DailyBatch.date >= cutoff)
            .all()
        )
        return [b.category for b in batches]
    finally:
        session.close()
