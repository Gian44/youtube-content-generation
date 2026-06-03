"""Declarative registry of integration providers.

Each :class:`ProviderSpec` declares the secret and config fields a provider
needs, plus how to import those values from the legacy global environment (used
when backfilling the default channel during migration).

Adding a new provider is a single entry here plus a small adapter in the
consuming service — no schema change is required. This is the extensibility
point referenced in the multi-channel plan; do not hardcode provider choices
elsewhere in the pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Integration "kinds" group providers by the pipeline capability they fulfil.
KIND_TEXT = "text"
KIND_TTS = "tts"
KIND_ASSETS = "assets"
KIND_YOUTUBE = "youtube"
KIND_STORAGE = "storage"

# Integration "scope" decides where credentials live:
#   app     — one set of credentials shared by every channel (OpenAI, Gemini,
#             Pexels, Pixabay, shared TTS, storage). Stored in ``app_integrations``.
#   channel — credentials are per-channel (YouTube OAuth, different account each).
#             Stored in ``channel_integrations``.
SCOPE_APP = "app"
SCOPE_CHANNEL = "channel"


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    kind: str
    label: str
    required_secrets: tuple[str, ...] = ()
    optional_secrets: tuple[str, ...] = ()
    config_fields: tuple[str, ...] = ()
    # Map secret/config field name -> legacy env var, for import-env backfill.
    env_secret_map: dict[str, str] = field(default_factory=dict)
    env_config_map: dict[str, str] = field(default_factory=dict)
    # Enable on the default channel during import-env only if these env vars
    # are present and non-placeholder. Falls back to required_secrets' env vars.
    enable_if_env: tuple[str, ...] = ()
    # Where this provider's credentials are stored/resolved (see SCOPE_* above).
    scope: str = SCOPE_APP


PROVIDERS: dict[str, ProviderSpec] = {
    "text.openai": ProviderSpec(
        key="text.openai",
        kind=KIND_TEXT,
        label="OpenAI (text)",
        required_secrets=("api_key",),
        config_fields=("model",),
        env_secret_map={"api_key": "OPENAI_API_KEY"},
        env_config_map={"model": "OPENAI_TEXT_MODEL"},
    ),
    "text.gemini": ProviderSpec(
        key="text.gemini",
        kind=KIND_TEXT,
        label="Gemini (text)",
        required_secrets=("api_key",),
        config_fields=("model",),
        env_secret_map={"api_key": "GEMINI_API_KEY"},
        env_config_map={"model": "GEMINI_TEXT_MODEL"},
    ),
    "tts.openai": ProviderSpec(
        key="tts.openai",
        kind=KIND_TTS,
        label="OpenAI (TTS)",
        required_secrets=("api_key",),
        config_fields=("ratio",),
        env_secret_map={"api_key": "OPENAI_API_KEY"},
        env_config_map={"ratio": "TTS_OPENAI_RATIO"},
    ),
    "tts.gemini": ProviderSpec(
        key="tts.gemini",
        kind=KIND_TTS,
        label="Gemini (TTS)",
        required_secrets=("api_key",),
        config_fields=("ratio",),
        env_secret_map={"api_key": "GEMINI_API_KEY"},
        env_config_map={"ratio": "TTS_GEMINI_RATIO"},
    ),
    "assets.pexels": ProviderSpec(
        key="assets.pexels",
        kind=KIND_ASSETS,
        label="Pexels",
        required_secrets=("api_key",),
        env_secret_map={"api_key": "PEXELS_API_KEY"},
    ),
    "assets.pixabay": ProviderSpec(
        key="assets.pixabay",
        kind=KIND_ASSETS,
        label="Pixabay",
        required_secrets=("api_key",),
        env_secret_map={"api_key": "PIXABAY_API_KEY"},
    ),
    "youtube": ProviderSpec(
        key="youtube",
        kind=KIND_YOUTUBE,
        label="YouTube",
        required_secrets=("refresh_token",),
        # client_id/secret default to the shared app-level OAuth app; channels
        # may override them here for advanced multi-project setups.
        optional_secrets=("client_id", "client_secret"),
        config_fields=("privacy",),
        env_secret_map={"refresh_token": "YOUTUBE_REFRESH_TOKEN"},
        env_config_map={"privacy": "UPLOAD_PRIVACY_MODE"},
        enable_if_env=("YOUTUBE_REFRESH_TOKEN",),
        scope=SCOPE_CHANNEL,  # different YouTube account per channel
    ),
    "storage.local": ProviderSpec(
        key="storage.local",
        kind=KIND_STORAGE,
        label="Local storage",
        config_fields=("path",),
        env_config_map={"path": "LOCAL_STORAGE_PATH"},
    ),
    "storage.r2": ProviderSpec(
        key="storage.r2",
        kind=KIND_STORAGE,
        label="Cloudflare R2",
        required_secrets=("access_key_id", "secret_access_key"),
        config_fields=("account_id", "bucket", "public_base_url"),
        env_secret_map={
            "access_key_id": "R2_ACCESS_KEY_ID",
            "secret_access_key": "R2_SECRET_ACCESS_KEY",
        },
        env_config_map={
            "account_id": "R2_ACCOUNT_ID",
            "bucket": "R2_BUCKET",
            "public_base_url": "R2_PUBLIC_BASE_URL",
        },
    ),
}

# Placeholder values shipped in .env.example that should not count as configured.
_PLACEHOLDER_FRAGMENTS = ("your-", "sk-your-")


def get_provider(provider_key: str) -> ProviderSpec:
    spec = PROVIDERS.get(provider_key)
    if spec is None:
        raise KeyError(f"Unknown integration provider: {provider_key}")
    return spec


def list_providers() -> list[ProviderSpec]:
    return list(PROVIDERS.values())


def providers_by_kind(kind: str) -> list[ProviderSpec]:
    return [spec for spec in PROVIDERS.values() if spec.kind == kind]


def providers_by_scope(scope: str) -> list[ProviderSpec]:
    return [spec for spec in PROVIDERS.values() if spec.scope == scope]


def app_providers() -> list[ProviderSpec]:
    """Shared providers whose credentials live at app level."""
    return providers_by_scope(SCOPE_APP)


def channel_providers() -> list[ProviderSpec]:
    """Providers whose credentials are per-channel (currently just YouTube)."""
    return providers_by_scope(SCOPE_CHANNEL)


def env_import_values(spec: ProviderSpec) -> tuple[bool, dict, dict]:
    """Compute ``(enable, clean_secrets, env_config)`` for backfilling a provider
    from the legacy global environment.

    Single source of truth shared by the app-level and per-channel env import so
    both produce identical results. Placeholder values from ``.env.example`` are
    treated as absent.
    """
    env_secrets = {
        field_name: os.environ.get(env_var, "")
        for field_name, env_var in spec.env_secret_map.items()
    }
    env_config = {
        field_name: os.environ.get(env_var, "")
        for field_name, env_var in spec.env_config_map.items()
    }
    env_config = {k: v for k, v in env_config.items() if v != ""}

    if spec.enable_if_env:
        enable = all(not is_placeholder(os.environ.get(var)) for var in spec.enable_if_env)
    elif spec.required_secrets:
        enable = all(not is_placeholder(env_secrets.get(f)) for f in spec.required_secrets)
    else:
        # No-secret providers (e.g. storage.local) are enabled by default.
        enable = spec.kind == KIND_STORAGE and spec.key == "storage.local"

    clean_secrets = {
        field_name: value
        for field_name, value in env_secrets.items()
        if value and not is_placeholder(value)
    }
    return enable, clean_secrets, env_config


def is_placeholder(value: str | None) -> bool:
    """Whether a value is empty or one of the .env.example placeholders."""
    if not value:
        return True
    text = str(value).strip()
    if not text:
        return True
    return any(fragment in text for fragment in _PLACEHOLDER_FRAGMENTS)


def validate_integration(
    provider_key: str,
    *,
    enabled: bool,
    config: dict | None,
    secrets: dict | None,
) -> list[str]:
    """Return a list of human-readable problems; empty means valid.

    Required fields are only enforced when the integration is ``enabled``.
    """
    spec = get_provider(provider_key)
    if not enabled:
        return []

    problems: list[str] = []
    secrets = secrets or {}
    config = config or {}

    for field_name in spec.required_secrets:
        if is_placeholder(secrets.get(field_name)):
            problems.append(f"{spec.label}: missing secret '{field_name}'")

    # Storage providers carry no secret but still need their config to be usable.
    if spec.kind == KIND_STORAGE:
        for field_name in spec.config_fields:
            # public_base_url is optional even for R2.
            if field_name == "public_base_url":
                continue
            if not str(config.get(field_name) or "").strip():
                problems.append(f"{spec.label}: missing config '{field_name}'")

    return problems
