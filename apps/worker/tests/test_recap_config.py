"""channel_service.set_recap_options — deep-merge recap settings (audio_mode etc.)."""

from __future__ import annotations


def test_set_recap_options_merges_into_nested_recap_config(isolated_env):
    from storyfactory.db.migrations import run_migrations

    run_migrations()
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import (
        create_channel,
        get_channel,
        set_recap_options,
    )

    session = get_session()
    try:
        ch = create_channel(
            session,
            name="cinybe",
            slug="cinybe-shorts",
            config={
                "pipeline_mode": "recap_shorts",
                "enable_shorts": True,
                "enable_long_form": False,
                "recap": {"inbox_path": "/inbox", "max_shorts_per_run": 3},
            },
            commit=True,
        )

        set_recap_options(
            session, ch, audio_mode="original_audio", music_volume=0.08
        )

        reloaded = get_channel(session, ch.id)
        recap = reloaded.config["recap"]
        assert recap["audio_mode"] == "original_audio"
        assert recap["music_volume"] == 0.08
        # Existing recap keys are preserved (deep merge, not replace).
        assert recap["inbox_path"] == "/inbox"
        assert recap["max_shorts_per_run"] == 3
        # Other top-level config is untouched.
        assert reloaded.config["pipeline_mode"] == "recap_shorts"
    finally:
        session.close()


def test_set_recap_options_ignores_none_values(isolated_env):
    from storyfactory.db.migrations import run_migrations

    run_migrations()
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import create_channel, set_recap_options

    session = get_session()
    try:
        ch = create_channel(
            session,
            name="c2",
            slug="c2",
            config={
                "pipeline_mode": "recap_shorts",
                "enable_shorts": True,
                "enable_long_form": False,
                "recap": {"audio_mode": "original_audio"},
            },
            commit=True,
        )
        # Passing None must not clobber an existing value.
        set_recap_options(session, ch, audio_mode=None, music_volume=0.05)
        assert ch.config["recap"]["audio_mode"] == "original_audio"
        assert ch.config["recap"]["music_volume"] == 0.05
    finally:
        session.close()
