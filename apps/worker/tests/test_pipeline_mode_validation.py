"""pipeline_mode must match the channel's enabled outputs (service-layer guard).

recap_shorts → Shorts only; sleep_facts → long-form only. The same invariant is
mirrored in the shared Zod schema and the dashboard API route.
"""

from __future__ import annotations

import pytest


def _bootstrap():
    from storyfactory.db.migrations import run_migrations

    run_migrations()


def _create(slug: str, config: dict):
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import create_channel

    session = get_session()
    try:
        return create_channel(session, name=slug, slug=slug, config=config, commit=True)
    finally:
        session.close()


def test_recap_mode_rejects_long_form_enabled(isolated_env):
    _bootstrap()
    with pytest.raises(ValueError):
        _create(
            "bad-recap",
            {"pipeline_mode": "recap_shorts", "enable_shorts": True, "enable_long_form": True},
        )


def test_sleep_mode_rejects_shorts_enabled(isolated_env):
    _bootstrap()
    with pytest.raises(ValueError):
        _create(
            "bad-sleep",
            {"pipeline_mode": "sleep_facts", "enable_shorts": True, "enable_long_form": True},
        )


def test_valid_recap_channel_creates(isolated_env):
    _bootstrap()
    ch = _create(
        "good-recap",
        {"pipeline_mode": "recap_shorts", "enable_shorts": True, "enable_long_form": False},
    )
    assert ch.config["pipeline_mode"] == "recap_shorts"


def test_valid_sleep_channel_creates(isolated_env):
    _bootstrap()
    ch = _create(
        "good-sleep",
        {"pipeline_mode": "sleep_facts", "enable_shorts": False, "enable_long_form": True},
    )
    assert ch.config["pipeline_mode"] == "sleep_facts"


def test_default_fiction_channel_unaffected(isolated_env):
    _bootstrap()
    ch = _create("fiction-default", {})
    assert ch.config["pipeline_mode"] == "fiction"
    assert ch.config["enable_shorts"] is True
    assert ch.config["enable_long_form"] is True
