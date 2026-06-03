"""Tests for channel config resolution and the integration resolver."""

from storyfactory import crypto


def test_resolve_channel_config_merges_overrides(isolated_env):
    from storyfactory.channel_context import resolve_channel_config
    from storyfactory.db.models import Channel

    channel = Channel(
        id="c1",
        slug="custom",
        name="Custom",
        status="active",
        config={"shorts_per_day_min": 9, "category_weights": {"aita": 99}},
    )
    cfg = resolve_channel_config(channel)

    # Override applied
    assert cfg["shorts_per_day_min"] == 9
    assert cfg["category_weights"]["aita"] == 99
    # Defaults still present for non-overridden keys
    assert "shorts_per_day_max" in cfg
    assert "disclosure_line" in cfg
    assert "voice_personas" in cfg


def test_integration_resolver_reads_enabled_config_and_secret(monkeypatch):
    from storyfactory.channel_context import IntegrationResolver

    key = crypto.generate_key()
    monkeypatch.setenv(crypto.ENV_KEY, key)

    enc = crypto.encrypt_secrets({"api_key": "sk-live-123"})
    resolver = IntegrationResolver(
        {
            "text.openai": {
                "enabled": True,
                "config": {"model": "gpt-4o"},
                "secrets_encrypted": enc,
            },
            "text.gemini": {"enabled": False, "config": {}, "secrets_encrypted": None},
        }
    )

    assert resolver.is_enabled("text.openai") is True
    assert resolver.is_enabled("text.gemini") is False
    assert resolver.is_enabled("assets.pexels") is False  # unknown -> False
    assert resolver.config("text.openai")["model"] == "gpt-4o"
    assert resolver.secret("text.openai", "api_key") == "sk-live-123"
    assert resolver.secret("text.gemini", "api_key") == ""


def test_resolve_api_key_prefers_channel_and_blocks_cross_channel_env(isolated_env, monkeypatch):
    """Non-default channels must NOT silently inherit the global env key."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-global-env-key")
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.engine import get_session
    from storyfactory.channel_context import resolve_api_key, use_channel
    from storyfactory.services.channel_service import create_channel, get_default_channel

    run_migrations()
    session = get_session()
    try:
        default = get_default_channel(session)
        # default channel imported the env key during migration
        with use_channel(session, default):
            assert resolve_api_key("text.openai", "openai_api_key") == "sk-global-env-key"

        # a fresh channel with no openai integration -> no env bleed
        other = create_channel(session, name="Other", slug="other")
        with use_channel(session, other):
            assert resolve_api_key("text.openai", "openai_api_key") == ""
    finally:
        session.close()


def test_youtube_credentials_shared_client_per_channel_token(isolated_env, monkeypatch):
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "shared-client-id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "shared-client-secret")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "default-channel-token")
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.engine import get_session
    from storyfactory.channel_context import use_channel, youtube_credentials
    from storyfactory.services.channel_service import get_default_channel

    run_migrations()
    session = get_session()
    try:
        default = get_default_channel(session)
        with use_channel(session, default):
            creds = youtube_credentials()
            assert creds["client_id"] == "shared-client-id"
            assert creds["client_secret"] == "shared-client-secret"
            assert creds["refresh_token"] == "default-channel-token"
    finally:
        session.close()
