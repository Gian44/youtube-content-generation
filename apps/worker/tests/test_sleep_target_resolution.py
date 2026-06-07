"""Regression: a Sleep On Facts video must target its sleep-specific length
(~180 min) and NOT silently inherit the global ``LONG_FORM_TARGET_MINUTES``
default (10), which content_config_defaults() seeds into every channel config.
"""

from __future__ import annotations

import uuid


def test_target_minutes_uses_sleep_default_not_polluted_top_level(isolated_env, monkeypatch):
    # Global long-form default is 10; this must NOT shrink the sleep video.
    monkeypatch.setenv("LONG_FORM_TARGET_MINUTES", "10")
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")

    from storyfactory.channel_context import effective_config, use_channel
    from storyfactory.config import get_settings
    from storyfactory.content_defaults import SLEEP_FACTS_DEFAULTS
    from storyfactory.db.engine import get_session
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.models import DailyBatch
    from storyfactory.db.seed import run_seed
    from storyfactory.pipeline import sleep_facts
    from storyfactory.services.channel_service import create_channel

    run_migrations()
    run_seed()

    session = get_session()
    channel = create_channel(
        session,
        name="sleepres",
        slug="sleepres",
        config={
            "pipeline_mode": "sleep_facts",
            "enable_shorts": False,
            "enable_long_form": True,
            "long_form_per_day": 1,
            # NOTE: no nested long_form_target_minutes override → must fall to 180.
            "sleep_facts": {"topic_rotation": ["The Ocean"], "enable_wikipedia_grounding": False},
        },
        commit=True,
    )

    captured: dict = {}

    def _fake_generate(**kwargs):
        captured.update(kwargs)
        return {
            "title": "Calm Ocean Facts",
            "hook": "",
            "body": "The ocean is calm and vast. " * 60,
            "asset_keywords": [],
            "image_search_queries": [],
            "word_count": 240,
        }

    monkeypatch.setattr(sleep_facts, "generate_sleep_script", _fake_generate)

    settings = get_settings()
    with use_channel(session, channel):
        cfg = {**SLEEP_FACTS_DEFAULTS, **(effective_config("sleep_facts", {}) or {})}
        batch = DailyBatch(
            id=str(uuid.uuid4()),
            channel_id=channel.id,
            date="2026-06-07",
            category="the-ocean",
            status="stories_generated",
        )
        session.add(batch)
        session.commit()

        sleep_facts._make_sleep_story(
            session, channel, batch, "The Ocean", {"hints": "", "keywords": []}, cfg, settings
        )

    # The sleep default (180), not the polluted global default (10).
    assert captured["target_minutes"] == 180
    assert captured["target_words"] == 180 * 150
    session.close()
