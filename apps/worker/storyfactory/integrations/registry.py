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

from dataclasses import dataclass, field

# Integration "kinds" group providers by the pipeline capability they fulfil.
KIND_TEXT = "text"
KIND_TTS = "tts"
KIND_ASSETS = "assets"
KIND_YOUTUBE = "youtube"
KIND_STORAGE = "storage"


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
