"""Per-channel content flags gate the pipeline shape (shorts / long-form / both).

All tests run the real daily pipeline in dry-run mode (no external APIs, no
network, ffmpeg stubbed), then assert which stories and render jobs were
produced for the channel.
"""

from __future__ import annotations

import pytest


def _bootstrap():
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.seed import run_seed

    run_migrations()  # creates schema + the default channel
    run_seed()  # seeds the global short/long prompt templates


def _make_channel(slug: str, config: dict):
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import create_channel

    session = get_session()
    try:
        channel = create_channel(session, name=slug, slug=slug, config=config, commit=True)
        return channel.id
    finally:
        session.close()


def _stories_and_renders(channel_id: str):
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import RenderJob, Story, DailyBatch

    session = get_session()
    try:
        stories = session.query(Story).filter_by(channel_id=channel_id).all()
        renders = session.query(RenderJob).filter_by(channel_id=channel_id).all()
        batches = session.query(DailyBatch).filter_by(channel_id=channel_id).all()
        return (
            [s.type for s in stories],
            [r.type for r in renders],
            batches,
        )
    finally:
        session.close()


def test_shorts_only_channel_makes_only_shorts(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    channel_id = _make_channel(
        "shortsonly",
        {
            "enable_shorts": True,
            "enable_long_form": False,
            "shorts_per_day_min": 1,
            "shorts_per_day_max": 1,
        },
    )

    run_daily_pipeline(channel_ref=channel_id, dry_run=True)

    story_types, render_types, batches = _stories_and_renders(channel_id)
    assert story_types, "expected at least one story"
    assert set(story_types) == {"short"}
    assert render_types, "expected a rendered Short"
    assert set(render_types) == {"short"}
    assert "long_form" not in render_types
    assert batches and batches[0].long_form_count == 0


def test_long_form_only_channel_makes_only_long_form(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    channel_id = _make_channel(
        "longonly",
        {
            "enable_shorts": False,
            "enable_long_form": True,
            "long_form_segments_min": 2,
            "long_form_segments_max": 2,
        },
    )

    run_daily_pipeline(channel_ref=channel_id, dry_run=True)

    story_types, render_types, batches = _stories_and_renders(channel_id)
    assert story_types, "expected long-form stories"
    assert set(story_types) == {"long_form_extra"}
    assert render_types == ["long_form"], render_types
    assert "short" not in render_types
    assert batches and batches[0].shorts_count == 0


def test_both_outputs_is_default_behavior(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    channel_id = _make_channel("mixed", {})  # defaults: both enabled

    run_daily_pipeline(channel_ref=channel_id, dry_run=True)

    story_types, render_types, _ = _stories_and_renders(channel_id)
    assert "short" in story_types and "long_form_extra" in story_types
    assert "short" in render_types and "long_form" in render_types


def test_long_form_per_day_zero_disables_long_form(isolated_env, monkeypatch):
    """long_form_per_day == 0 acts as an off-switch even when enable_long_form is True."""
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    channel_id = _make_channel(
        "noLong",
        {
            "enable_shorts": True,
            "enable_long_form": True,
            "long_form_per_day": 0,
            "shorts_per_day_min": 1,
            "shorts_per_day_max": 1,
        },
    )

    run_daily_pipeline(channel_ref=channel_id, dry_run=True)

    story_types, render_types, _ = _stories_and_renders(channel_id)
    assert set(story_types) == {"short"}
    assert set(render_types) == {"short"}


def test_all_off_config_is_rejected_at_creation(isolated_env, monkeypatch):
    """A channel with no outputs enabled cannot be created (service-layer guard)."""
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import create_channel

    _bootstrap()
    session = get_session()
    try:
        with pytest.raises(ValueError):
            create_channel(
                session,
                name="empty",
                slug="empty",
                config={"enable_shorts": False, "enable_long_form": False},
                commit=True,
            )
    finally:
        session.close()


def test_pipeline_guard_handles_directly_injected_all_off_config(isolated_env, monkeypatch):
    """Defense-in-depth: if an all-off config somehow lands in the DB, the
    pipeline produces nothing (no batch) instead of crashing."""
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import Channel
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    channel_id = _make_channel("mixedstart", {})  # valid config to start

    # Bypass validation and force an all-off config straight into the DB.
    session = get_session()
    try:
        ch = session.query(Channel).filter_by(id=channel_id).first()
        ch.config = {**(ch.config or {}), "enable_shorts": False, "enable_long_form": False}
        session.commit()
    finally:
        session.close()

    run_daily_pipeline(channel_ref=channel_id, dry_run=True)

    story_types, render_types, batches = _stories_and_renders(channel_id)
    assert story_types == []
    assert render_types == []
    assert batches == [], "no batch should be created when no outputs are enabled"
