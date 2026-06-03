"""Tests that channel_id is added and threaded through the pipeline."""


def test_migration_adds_channel_id_columns(isolated_env):
    from storyfactory.db.migrations import run_migrations, column_exists
    from storyfactory.db.engine import get_session

    applied = run_migrations()
    assert "0002_channel_id_columns" in applied

    session = get_session()
    try:
        for table in ("daily_batches", "stories", "render_jobs", "youtube_uploads",
                      "assets", "api_usage_logs", "policy_flags"):
            assert column_exists(session, table, "channel_id"), f"{table}.channel_id missing"
    finally:
        session.close()


def test_story_generator_stamps_channel_id(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.seed import run_seed
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import DailyBatch
    from storyfactory.services.channel_service import create_channel
    from storyfactory.pipeline.story_generator import generate_short_stories

    run_migrations()
    run_seed()  # seeds the global prompt templates

    session = get_session()
    try:
        channel = create_channel(session, name="Scoped", slug="scoped")
        batch = DailyBatch(id="b1", channel_id=channel.id, date="2026-01-01",
                           category="aita", status="topic_selected")
        session.add(batch)
        session.commit()

        stories = generate_short_stories(batch, 2)
        assert len(stories) == 2
        assert all(s.channel_id == channel.id for s in stories)
    finally:
        session.close()


def test_prompt_channel_override_resolves_over_global(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.db.migrations import run_migrations, column_exists
    from storyfactory.db.seed import run_seed
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import create_channel, set_prompt_override
    from storyfactory.pipeline.story_generator import _resolve_prompt

    run_migrations()
    run_seed()

    session = get_session()
    try:
        assert column_exists(session, "prompt_templates", "channel_id")
        channel = create_channel(session, name="Override", slug="override")
        set_prompt_override(
            session, channel, "short_story_generation", "CHANNEL OVERRIDE TEMPLATE"
        )

        # Channel-scoped lookup returns the override...
        scoped = _resolve_prompt(session, "short_story_generation", channel.id)
        assert scoped.template == "CHANNEL OVERRIDE TEMPLATE"
        # ...while the global lookup is untouched.
        global_tpl = _resolve_prompt(session, "short_story_generation", None)
        assert global_tpl.template != "CHANNEL OVERRIDE TEMPLATE"
        # A different channel with no override also falls back to global.
        other = create_channel(session, name="Other2", slug="other2")
        fallback = _resolve_prompt(session, "short_story_generation", other.id)
        assert fallback.template == global_tpl.template
    finally:
        session.close()


def test_topic_selector_cooldown_is_per_channel(isolated_env):
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import DailyBatch
    from storyfactory.services.channel_service import create_channel
    from storyfactory.pipeline.topic_selector import get_recent_categories

    run_migrations()
    session = get_session()
    try:
        ch_a = create_channel(session, name="A", slug="a")
        ch_b = create_channel(session, name="B", slug="b")
        session.add(DailyBatch(id="ba", channel_id=ch_a.id, date="2030-01-01",
                               category="revenge", status="completed"))
        session.commit()

        # Channel A sees its recent category; channel B does not.
        assert "revenge" in get_recent_categories(days=3650, channel_id=ch_a.id)
        assert "revenge" not in get_recent_categories(days=3650, channel_id=ch_b.id)
    finally:
        session.close()
