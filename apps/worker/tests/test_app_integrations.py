"""App-level (shared) integration store, resolver tier, and migration backfill."""

from __future__ import annotations

import pytest

from storyfactory import crypto


def _clear_provider_env(monkeypatch):
    for var in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "PEXELS_API_KEY",
        "PIXABAY_API_KEY",
        "YOUTUBE_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def test_set_and_get_app_integration_roundtrips_secret(isolated_env, monkeypatch):
    _clear_provider_env(monkeypatch)
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.app_integration_service import (
        get_app_integration,
        set_app_integration,
    )

    init_db()
    session = get_session()
    try:
        set_app_integration(
            session,
            "text.openai",
            enabled=True,
            config={"model": "gpt-4o"},
            secrets={"api_key": "sk-app-level"},
            commit=True,
        )
        row = get_app_integration(session, "text.openai")
        assert row is not None and row.enabled is True
        assert row.config["model"] == "gpt-4o"
        assert crypto.decrypt_secrets(row.secrets_encrypted)["api_key"] == "sk-app-level"
    finally:
        session.close()


def test_set_app_integration_rejects_channel_scoped_provider(isolated_env, monkeypatch):
    _clear_provider_env(monkeypatch)
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.app_integration_service import set_app_integration

    init_db()
    session = get_session()
    try:
        with pytest.raises(ValueError):
            set_app_integration(session, "youtube", secrets={"refresh_token": "x"})
    finally:
        session.close()


def test_resolve_model_reads_app_level_config(isolated_env, monkeypatch):
    _clear_provider_env(monkeypatch)
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.engine import get_session
    from storyfactory.channel_context import resolve_model, use_channel
    from storyfactory.services.app_integration_service import set_app_integration
    from storyfactory.services.channel_service import get_default_channel

    run_migrations()
    session = get_session()
    try:
        set_app_integration(
            session, "text.openai", enabled=True, config={"model": "gpt-4o-mini"},
            secrets={"api_key": "k"}, commit=True,
        )
        default = get_default_channel(session)
        with use_channel(session, default):
            assert resolve_model("text.openai", "openai_text_model") == "gpt-4o-mini"
    finally:
        session.close()


def test_import_env_into_app_seeds_then_never_clobbers(isolated_env, monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.services.app_integration_service import (
        get_app_integration,
        import_env_into_app,
        set_app_integration,
    )

    init_db()
    session = get_session()
    try:
        import_env_into_app(session, commit=True)
        row = get_app_integration(session, "text.openai")
        assert crypto.decrypt_secrets(row.secrets_encrypted)["api_key"] == "env-key"

        # A user edits the key via the UI/CLI...
        set_app_integration(session, "text.openai", secrets={"api_key": "ui-key"}, commit=True)
        # ...and a later env re-import must NOT overwrite it.
        import_env_into_app(session, commit=True)
        row = get_app_integration(session, "text.openai")
        assert crypto.decrypt_secrets(row.secrets_encrypted)["api_key"] == "ui-key"
    finally:
        session.close()


def test_migration_consolidates_channel_secrets_default_wins(isolated_env, monkeypatch):
    """Existing per-channel shared secrets are consolidated to app level.

    Default channel's value is canonical; a differing value on another channel is
    logged (not used); per-channel shared rows are removed; YouTube is untouched.
    """
    _clear_provider_env(monkeypatch)
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.db import migrations as mig
    from storyfactory.db.models import ChannelIntegration
    from storyfactory.services.channel_service import create_channel, set_integration
    from storyfactory.services.app_integration_service import get_app_integration

    init_db()
    session = get_session()
    try:
        # Simulate a pre-refactor install: shared secrets stored per channel.
        default = create_channel(session, name="Default", slug="default", commit=True)
        other = create_channel(session, name="Other", slug="other", commit=True)
        set_integration(session, default, "text.openai", enabled=True,
                        secrets={"api_key": "key-default"}, commit=True)
        set_integration(session, other, "text.openai", enabled=True,
                        secrets={"api_key": "key-OTHER-different"}, commit=True)
        set_integration(session, default, "youtube", enabled=True,
                        secrets={"refresh_token": "yt-default"}, commit=True)

        mig._m0004_app_integrations(session)
        session.commit()

        # Default channel's value wins at app level.
        app_openai = get_app_integration(session, "text.openai")
        assert app_openai is not None
        assert crypto.decrypt_secrets(app_openai.secrets_encrypted)["api_key"] == "key-default"

        # Shared rows removed from channel_integrations; YouTube preserved.
        ch_keys = {(i.channel_id, i.provider_key) for i in session.query(ChannelIntegration).all()}
        assert not any(pk == "text.openai" for _, pk in ch_keys)
        assert (default.id, "youtube") in ch_keys

        # Idempotent: a second run changes nothing and does not raise.
        mig._m0004_app_integrations(session)
        session.commit()
        app_openai = get_app_integration(session, "text.openai")
        assert crypto.decrypt_secrets(app_openai.secrets_encrypted)["api_key"] == "key-default"
    finally:
        session.close()


def test_migration_preserves_undecryptable_channel_secret(isolated_env, monkeypatch):
    """A secret that cannot be decrypted must NOT be deleted by the migration."""
    _clear_provider_env(monkeypatch)
    from storyfactory.db.engine import init_db, get_session
    from storyfactory.db import migrations as mig
    from storyfactory.db.models import ChannelIntegration
    from storyfactory.services.app_integration_service import get_app_integration
    from storyfactory.services.channel_service import create_channel

    init_db()
    session = get_session()
    try:
        default = create_channel(session, name="Default", slug="default", commit=True)
        # Encrypt under a DIFFERENT key so the active master key cannot decrypt it.
        other_key = crypto.generate_key().encode()
        bad_blob = crypto.encrypt_secrets({"api_key": "unrecoverable"}, key=other_key)
        session.add(
            ChannelIntegration(
                id="ci-bad",
                channel_id=default.id,
                provider_key="text.openai",
                enabled=True,
                config={},
                secrets_encrypted=bad_blob,
            )
        )
        session.commit()

        mig._m0004_app_integrations(session)
        session.commit()

        # Could not consolidate (decrypt failed, no env) -> no app row...
        assert get_app_integration(session, "text.openai") is None
        # ...and the per-channel row + its encrypted blob are PRESERVED, not lost.
        rows = session.query(ChannelIntegration).filter_by(provider_key="text.openai").all()
        assert len(rows) == 1
        assert rows[0].secrets_encrypted == bad_blob
    finally:
        session.close()
