"""Tests for the integration provider registry and validation."""

import pytest

from storyfactory.integrations import registry


def test_known_providers_present():
    keys = set(registry.PROVIDERS)
    assert {"text.openai", "tts.openai", "assets.pexels", "youtube", "storage.r2"} <= keys


def test_get_unknown_provider_raises():
    with pytest.raises(KeyError):
        registry.get_provider("does.not.exist")


def test_disabled_integration_never_reports_problems():
    problems = registry.validate_integration(
        "text.openai", enabled=False, config={}, secrets={}
    )
    assert problems == []


def test_enabled_missing_secret_reports_problem():
    problems = registry.validate_integration(
        "text.openai", enabled=True, config={}, secrets={}
    )
    assert any("api_key" in p for p in problems)


def test_enabled_with_secret_is_valid():
    problems = registry.validate_integration(
        "text.openai", enabled=True, config={"model": "gpt-4o"}, secrets={"api_key": "sk-x"}
    )
    assert problems == []


def test_placeholder_secret_counts_as_missing():
    assert registry.is_placeholder("your-pexels-api-key") is True
    assert registry.is_placeholder("sk-your-openai-api-key") is True
    assert registry.is_placeholder("real-key") is False
    problems = registry.validate_integration(
        "assets.pexels", enabled=True, config={}, secrets={"api_key": "your-pexels-api-key"}
    )
    assert problems


def test_storage_r2_requires_config_when_enabled():
    problems = registry.validate_integration(
        "storage.r2",
        enabled=True,
        config={"bucket": ""},
        secrets={"access_key_id": "a", "secret_access_key": "b"},
    )
    # account_id and bucket are required; public_base_url is not.
    assert any("account_id" in p for p in problems)
    assert any("bucket" in p for p in problems)
    assert not any("public_base_url" in p for p in problems)
