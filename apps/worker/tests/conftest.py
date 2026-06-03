"""Shared pytest fixtures for isolated, env-driven worker tests."""

import pytest


def _reset_singletons():
    import storyfactory.config as cfg
    import storyfactory.db.engine as eng

    cfg._settings = None
    eng._engine = None
    eng._SessionLocal = None


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Point the worker at a throwaway SQLite DB + key file and reset caches."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "sf.db"))
    monkeypatch.setenv("STORYFACTORY_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("STORYFACTORY_SECRET_KEY", raising=False)
    _reset_singletons()
    yield tmp_path
    # ensure_key() may have populated STORYFACTORY_SECRET_KEY via setdefault
    import os

    os.environ.pop("STORYFACTORY_SECRET_KEY", None)
    _reset_singletons()
