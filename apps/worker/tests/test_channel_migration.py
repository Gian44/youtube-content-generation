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


def test_integrations_enabled_by_env_presence(isolated_env, monkeypatch):
    _set_env(monkeypatch)
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import ChannelIntegration
    from storyfactory import crypto

    run_migrations()
    session = get_session()
    try:
        by_key = {i.provider_key: i for i in session.query(ChannelIntegration).all()}
        # Present, real keys -> enabled
        assert by_key["text.openai"].enabled is True
        assert by_key["assets.pexels"].enabled is True
        assert by_key["youtube"].enabled is True
        assert by_key["storage.local"].enabled is True
        # Placeholder keys -> disabled
        assert by_key["text.gemini"].enabled is False
        assert by_key["assets.pixabay"].enabled is False
        # Secrets are stored encrypted and decrypt back to the real value
        secrets = crypto.decrypt_secrets(by_key["text.openai"].secrets_encrypted)
        assert secrets["api_key"] == "sk-test-real-openai"
        # Placeholder secret was NOT stored
        assert crypto.decrypt_secrets(by_key["text.gemini"].secrets_encrypted) == {}
    finally:
        session.close()
