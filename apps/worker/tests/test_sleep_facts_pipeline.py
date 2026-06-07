"""Sleep On Facts pipeline — dry-run + topic-cursor rotation tests.

Runs the real daily pipeline in sleep_facts mode (dry-run: no API calls, no
network, Wikipedia disabled), then asserts a single long-form video is produced,
no Shorts, and the per-channel topic cursor advances through the rotation.
"""

from __future__ import annotations


def _bootstrap():
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.seed import run_seed

    run_migrations()
    run_seed()  # seeds sleep_facts_long_form + the other prompts


def _make_sleep_channel(slug: str, topics: list[str]) -> str:
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import create_channel

    session = get_session()
    try:
        ch = create_channel(
            session,
            name=slug,
            slug=slug,
            config={
                "pipeline_mode": "sleep_facts",
                "enable_shorts": False,
                "enable_long_form": True,
                "long_form_per_day": 1,
                "sleep_facts": {
                    "topic_rotation": topics,
                    "long_form_target_minutes": 3,
                    "enable_wikipedia_grounding": False,  # no network in tests
                },
            },
            commit=True,
        )
        return ch.id
    finally:
        session.close()


def _state(channel_id: str):
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import DailyBatch, RenderJob, SettingsModel, Story

    session = get_session()
    try:
        stories = session.query(Story).filter_by(channel_id=channel_id).all()
        renders = session.query(RenderJob).filter_by(channel_id=channel_id).all()
        batches = session.query(DailyBatch).filter_by(channel_id=channel_id).all()
        cursor_row = (
            session.query(SettingsModel)
            .filter_by(key=f"sleep_facts_cursor:{channel_id}")
            .first()
        )
        cursor = int(cursor_row.value) if cursor_row else 0
        return {
            "story_types": [s.type for s in stories],
            "story_categories": [s.category for s in stories],
            "render_types": [r.type for r in renders],
            "batches": batches,
            "cursor": cursor,
        }
    finally:
        session.close()


def _age_batches(channel_id: str) -> None:
    """Simulate "next day": move existing batches to a past date so the
    one-video-per-day guard (which keys on today's date) lets the next run pass.
    """
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import DailyBatch

    session = get_session()
    try:
        for batch in session.query(DailyBatch).filter_by(channel_id=channel_id).all():
            batch.date = "2000-01-01"
        session.commit()
    finally:
        session.close()


def test_sleep_pipeline_makes_one_long_form_no_shorts(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    # Keep the dry-run hermetic: with no asset keys, collection yields placeholders
    # instead of hitting Pexels/Pixabay over the network.
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    cid = _make_sleep_channel("sleepchan", ["Ancient Egypt", "The Deep Sea"])

    run_daily_pipeline(channel_ref=cid, dry_run=True)

    state = _state(cid)
    assert set(state["story_types"]) == {"long_form_extra"}
    assert state["render_types"] == ["long_form"]
    assert "short" not in state["render_types"]
    assert state["batches"][0].shorts_count == 0
    assert state["batches"][0].long_form_count == 1
    assert state["story_categories"] == ["ancient-egypt"]
    assert state["cursor"] == 1


def test_sleep_cursor_advances_through_rotation(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    # Keep the dry-run hermetic: with no asset keys, collection yields placeholders
    # instead of hitting Pexels/Pixabay over the network.
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    cid = _make_sleep_channel("sleepchan2", ["Ancient Egypt", "The Deep Sea"])

    run_daily_pipeline(channel_ref=cid, dry_run=True)
    first = _state(cid)
    assert first["story_categories"] == ["ancient-egypt"]
    assert first["cursor"] == 1

    _age_batches(cid)  # next "day"
    run_daily_pipeline(channel_ref=cid, dry_run=True)
    second = _state(cid)
    # Both videos persist; the cursor walked the rotation in order.
    assert set(second["story_categories"]) == {"ancient-egypt", "the-deep-sea"}
    assert second["cursor"] == 2


def test_sleep_pipeline_skips_when_already_done_today(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    # Keep the dry-run hermetic: with no asset keys, collection yields placeholders
    # instead of hitting Pexels/Pixabay over the network.
    monkeypatch.setenv("PEXELS_API_KEY", "")
    monkeypatch.setenv("PIXABAY_API_KEY", "")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    cid = _make_sleep_channel("sleepchan3", ["Ancient Egypt", "The Deep Sea"])

    run_daily_pipeline(channel_ref=cid, dry_run=True)
    run_daily_pipeline(channel_ref=cid, dry_run=True)  # same day → guarded

    state = _state(cid)
    assert state["render_types"] == ["long_form"]  # not two
    assert state["cursor"] == 1  # cursor did not advance on the skipped run
