"""Tests for the channel migration / env-backfill path."""


def _set_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-real-openai")
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-real-key")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "rt-real-token")
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client-id-123")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "client-secret-123")
    # Left as .env.example placeholders -> must stay disabled.
    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-api-key")
    monkeypatch.setenv("PIXABAY_API_KEY", "your-pixabay-api-key")


def test_migration_creates_default_channel(isolated_env, monkeypatch):
    _set_env(monkeypatch)
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import Channel

    applied = run_migrations()
    assert "0001_default_channel" in applied

    session = get_session()
    try:
        channels = session.query(Channel).all()
        assert len(channels) == 1
        ch = channels[0]
        assert ch.slug == "default"
        assert ch.status == "active"
        # config inherits app-level defaults + canonical category weights
        assert ch.config["shorts_per_day_min"] == 3
        assert ch.config["category_weights"]["aita"] == 15
        assert ch.config["disclosure_line"]
    finally:
        session.close()


def test_migration_is_idempotent(isolated_env, monkeypatch):
    _set_env(monkeypatch)
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import Channel

    run_migrations()
    second = run_migrations()  # should apply nothing
    assert second == []

    session = get_session()
    try:
        assert session.query(Channel).count() == 1
    finally:
        session.close()


def test_shared_integrations_live_at_app_level_youtube_per_channel(isolated_env, monkeypatch):
    """Shared providers are stored once at app level; only YouTube is per-channel."""
    _set_env(monkeypatch)
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import ChannelIntegration
    from storyfactory.services.app_integration_service import (
        app_integration_status,
        get_app_integration,
    )
    from storyfactory import crypto

    run_migrations()
    session = get_session()
    try:
        status = {s["provider_key"]: s for s in app_integration_status(session)}
        # Present, real keys -> enabled at app level
        assert status["text.openai"]["enabled"] is True
        assert status["assets.pexels"]["enabled"] is True
        assert status["storage.local"]["enabled"] is True
        # Placeholder keys -> disabled (and never persisted as junk rows)
        assert status["text.gemini"]["enabled"] is False
        assert status["assets.pixabay"]["enabled"] is False
        assert get_app_integration(session, "text.gemini") is None

        # The configured secret is stored encrypted and decrypts to the real value.
        row = get_app_integration(session, "text.openai")
        assert crypto.decrypt_secrets(row.secrets_encrypted)["api_key"] == "sk-test-real-openai"

        # YouTube stays per-channel; shared providers are NOT in channel_integrations.
        ch_keys = {i.provider_key for i in session.query(ChannelIntegration).all()}
        assert "youtube" in ch_keys
        assert "text.openai" not in ch_keys
        assert "assets.pexels" not in ch_keys
    finally:
        session.close()
