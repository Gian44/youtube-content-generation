"""Jamendo music provider — config + integration-registry wiring."""

from __future__ import annotations


def test_validate_api_keys_reports_jamendo_when_set():
    from storyfactory.config import Settings

    configured = Settings(JAMENDO_CLIENT_ID="real-client-id")
    assert configured.validate_api_keys()["jamendo"] is True

    missing = Settings(JAMENDO_CLIENT_ID="")
    assert missing.validate_api_keys()["jamendo"] is False


def test_validate_api_keys_treats_placeholder_jamendo_as_missing():
    from storyfactory.config import Settings

    placeholder = Settings(JAMENDO_CLIENT_ID="your-jamendo-client-id")
    assert placeholder.validate_api_keys()["jamendo"] is False


def test_jamendo_registered_as_app_scoped_provider():
    from storyfactory.integrations import registry

    spec = registry.PROVIDERS.get("assets.jamendo")
    assert spec is not None, "assets.jamendo must be registered"
    assert spec.scope == registry.SCOPE_APP
    assert spec.kind == registry.KIND_ASSETS
    assert spec.required_secrets == ("api_key",)
    assert spec.env_secret_map.get("api_key") == "JAMENDO_CLIENT_ID"
