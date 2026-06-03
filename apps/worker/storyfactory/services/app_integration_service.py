"""App-level (shared) integration credentials.

Shared providers — OpenAI, Gemini, Pexels, Pixabay, shared TTS, storage — are
configured once for the whole app and used by every channel. Their encrypted
credentials live in the ``app_integrations`` table. YouTube is NOT handled here;
it stays per-channel (see :mod:`storyfactory.services.channel_service`).

This module mirrors the per-channel ``set_integration`` pattern so the CLI, the
migration backfill, and the dashboard all produce identical results. All
encryption happens in the worker via :mod:`storyfactory.crypto`.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from storyfactory import crypto
from storyfactory.db.models import AppIntegration
from storyfactory.integrations import registry
from storyfactory.logger import get_logger

log = get_logger("app_integration_service")


def _require_app_provider(provider_key: str) -> registry.ProviderSpec:
    spec = registry.get_provider(provider_key)  # validates the key
    if spec.scope != registry.SCOPE_APP:
        raise ValueError(
            f"Provider '{provider_key}' is per-channel (scope={spec.scope}); "
            "configure it on the channel, not at app level."
        )
    return spec


def get_app_integration(session: Session, provider_key: str) -> AppIntegration | None:
    return (
        session.query(AppIntegration)
        .filter_by(provider_key=provider_key)
        .first()
    )


def list_app_integrations(session: Session) -> list[AppIntegration]:
    return session.query(AppIntegration).all()


def set_app_integration(
    session: Session,
    provider_key: str,
    *,
    enabled: bool | None = None,
    config: dict | None = None,
    secrets: dict | None = None,
    merge_secrets: bool = True,
    commit: bool = True,
) -> AppIntegration:
    """Create or update a shared app-level integration. Secrets are encrypted."""
    _require_app_provider(provider_key)

    integration = get_app_integration(session, provider_key)
    if integration is None:
        integration = AppIntegration(
            id=str(uuid.uuid4()),
            provider_key=provider_key,
            enabled=bool(enabled),
            config={},
        )
        session.add(integration)

    if enabled is not None:
        integration.enabled = enabled
    if config is not None:
        merged = dict(integration.config or {})
        merged.update({k: v for k, v in config.items() if v is not None})
        integration.config = merged
    if secrets is not None:
        if merge_secrets:
            current = (
                crypto.decrypt_secrets(integration.secrets_encrypted)
                if integration.secrets_encrypted
                else {}
            )
            current.update({k: v for k, v in secrets.items() if v is not None})
            payload = current
        else:
            payload = secrets
        integration.secrets_encrypted = crypto.encrypt_secrets(payload, create_key=True)

    if commit:
        session.commit()
    return integration


def build_app_resolver(session: Session):
    """Build an :class:`IntegrationResolver` over all app-level integrations.

    Imported lazily to avoid a circular import with ``channel_context``.
    """
    from storyfactory.channel_context import IntegrationResolver

    data = {
        i.provider_key: {
            "enabled": bool(i.enabled),
            "config": dict(i.config or {}),
            "secrets_encrypted": i.secrets_encrypted,
        }
        for i in list_app_integrations(session)
    }
    return IntegrationResolver(data)


def import_env_into_app(session: Session, *, commit: bool = True) -> None:
    """Seed shared app-level integrations from the legacy global environment.

    Safe to call repeatedly: env only *seeds* providers that have no app-level
    row yet. Once a provider is configured (via the Settings UI / CLI / a prior
    seed), the database value is authoritative and the env never overwrites it —
    the env still serves as a runtime fallback in ``resolve_api_key``. Per-channel
    providers (YouTube) are skipped.
    """
    crypto.ensure_key()  # guarantee a key exists before encrypting anything

    for spec in registry.app_providers():
        if get_app_integration(session, spec.key) is not None:
            continue  # already configured at app level — never clobber from env
        enable, clean_secrets, env_config = registry.env_import_values(spec)
        if not (enable or clean_secrets or env_config):
            continue  # nothing in the env for this provider
        set_app_integration(
            session,
            spec.key,
            enabled=enable,
            config=env_config or None,
            secrets=clean_secrets or None,
            commit=False,
        )

    if commit:
        session.commit()
    log.info("app_env_imported")


def app_integration_status(session: Session) -> list[dict]:
    """Per-provider status for app-level integrations (no secret values)."""
    existing = {i.provider_key: i for i in list_app_integrations(session)}
    statuses = []
    for spec in registry.app_providers():
        integration = existing.get(spec.key)
        enabled = bool(integration and integration.enabled)
        secrets = (
            crypto.decrypt_secrets(integration.secrets_encrypted)
            if integration and integration.secrets_encrypted
            else {}
        )
        config = dict(integration.config or {}) if integration else {}
        problems = registry.validate_integration(
            spec.key, enabled=True, config=config, secrets=secrets
        )
        statuses.append(
            {
                "provider_key": spec.key,
                "kind": spec.kind,
                "label": spec.label,
                "scope": spec.scope,
                "enabled": enabled,
                "configured": len(problems) == 0,
                "missing": problems,
                "config": config,
            }
        )
    return statuses
