"""Channel CRUD and environment-import logic.

Shared by the migration backfill and the ``channel`` CLI command group so the
dashboard (which shells out to the CLI for any secret mutation) and the
migration path produce identical results.
"""

from __future__ import annotations

import os
import re
import uuid

from sqlalchemy.orm import Session

from storyfactory import crypto
from storyfactory.config import get_settings
from storyfactory.content_defaults import DEFAULT_CATEGORY_WEIGHTS
from storyfactory.db.models import Channel, ChannelIntegration
from storyfactory.integrations import registry
from storyfactory.logger import get_logger

log = get_logger("channel_service")


def slugify(value: str) -> str:
    """Produce a url-safe slug from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or f"channel-{uuid.uuid4().hex[:8]}"


def get_channel(session: Session, identifier: str) -> Channel | None:
    """Resolve a channel by id or slug."""
    if not identifier:
        return None
    return (
        session.query(Channel)
        .filter((Channel.id == identifier) | (Channel.slug == identifier))
        .first()
    )


def get_default_channel(session: Session) -> Channel | None:
    settings = get_settings()
    return (
        get_channel(session, settings.default_channel_slug)
        or session.query(Channel).order_by(Channel.created_at.asc()).first()
    )


def list_channels(session: Session, *, active_only: bool = False) -> list[Channel]:
    query = session.query(Channel)
    if active_only:
        query = query.filter(Channel.status == "active")
    return query.order_by(Channel.created_at.asc()).all()


def _unique_slug(session: Session, base: str) -> str:
    slug = slugify(base)
    candidate = slug
    suffix = 2
    while session.query(Channel).filter_by(slug=candidate).first() is not None:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


def create_channel(
    session: Session,
    *,
    name: str,
    slug: str | None = None,
    description: str | None = None,
    niche: str | None = None,
    content_style: str | None = None,
    config: dict | None = None,
    status: str = "active",
    commit: bool = True,
) -> Channel:
    """Create a channel. Config is merged over the app-level content defaults."""
    settings = get_settings()
    base_config = settings.content_config_defaults()
    base_config["category_weights"] = dict(DEFAULT_CATEGORY_WEIGHTS)
    if config:
        base_config.update(config)

    channel = Channel(
        id=str(uuid.uuid4()),
        slug=_unique_slug(session, slug or name),
        name=name,
        description=description,
        niche=niche,
        content_style=content_style,
        status=status,
        config=base_config,
    )
    session.add(channel)
    if commit:
        session.commit()
    log.info("channel_created", slug=channel.slug, name=channel.name)
    return channel


def set_integration(
    session: Session,
    channel: Channel,
    provider_key: str,
    *,
    enabled: bool | None = None,
    config: dict | None = None,
    secrets: dict | None = None,
    merge_secrets: bool = True,
    commit: bool = True,
) -> ChannelIntegration:
    """Create or update a channel integration. Secrets are encrypted at rest."""
    registry.get_provider(provider_key)  # validates the key

    integration = (
        session.query(ChannelIntegration)
        .filter_by(channel_id=channel.id, provider_key=provider_key)
        .first()
    )
    if integration is None:
        integration = ChannelIntegration(
            id=str(uuid.uuid4()),
            channel_id=channel.id,
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
            current = crypto.decrypt_secrets(integration.secrets_encrypted) if integration.secrets_encrypted else {}
            current.update({k: v for k, v in secrets.items() if v is not None})
            payload = current
        else:
            payload = secrets
        integration.secrets_encrypted = crypto.encrypt_secrets(payload, create_key=True)

    if commit:
        session.commit()
    return integration


def import_env_into_channel(session: Session, channel: Channel, *, commit: bool = True) -> Channel:
    """Backfill a channel's integrations from the legacy global environment.

    Idempotent: existing integrations are updated in place, not duplicated.
    Only providers whose required secrets / enable conditions are satisfied in
    the environment are enabled.
    """
    settings = get_settings()
    crypto.ensure_key()  # guarantee a key exists before encrypting anything

    for spec in registry.list_providers():
        env_secrets = {
            field: os.environ.get(env_var, "")
            for field, env_var in spec.env_secret_map.items()
        }
        env_config = {
            field: os.environ.get(env_var, "")
            for field, env_var in spec.env_config_map.items()
        }
        env_config = {k: v for k, v in env_config.items() if v != ""}

        # Decide whether to enable: an explicit enable_if_env wins, else all
        # required secrets must be present and non-placeholder.
        if spec.enable_if_env:
            enable = all(
                not registry.is_placeholder(os.environ.get(var))
                for var in spec.enable_if_env
            )
        elif spec.required_secrets:
            enable = all(
                not registry.is_placeholder(env_secrets.get(field))
                for field in spec.required_secrets
            )
        else:
            # No-secret providers (e.g. storage.local) are enabled by default.
            enable = spec.kind == registry.KIND_STORAGE and spec.key == "storage.local"

        # Drop placeholder secrets so we never store junk.
        clean_secrets = {
            field: value
            for field, value in env_secrets.items()
            if value and not registry.is_placeholder(value)
        }

        set_integration(
            session,
            channel,
            spec.key,
            enabled=enable,
            config=env_config or None,
            secrets=clean_secrets or None,
            commit=False,
        )

    if commit:
        session.commit()
    log.info("channel_env_imported", slug=channel.slug)
    return channel


def update_channel(
    session: Session,
    channel: Channel,
    *,
    name: str | None = None,
    description: str | None = None,
    niche: str | None = None,
    content_style: str | None = None,
    status: str | None = None,
    config_updates: dict | None = None,
    commit: bool = True,
) -> Channel:
    """Update editable channel fields (non-secret). Config is merged."""
    if name is not None:
        channel.name = name
    if description is not None:
        channel.description = description
    if niche is not None:
        channel.niche = niche
    if content_style is not None:
        channel.content_style = content_style
    if status is not None:
        if status not in ("active", "paused"):
            raise ValueError(f"Invalid status: {status}")
        channel.status = status
    if config_updates:
        merged = dict(channel.config or {})
        merged.update(config_updates)
        channel.config = merged
    if commit:
        session.commit()
    log.info("channel_updated", slug=channel.slug)
    return channel


def duplicate_channel(
    session: Session,
    source: Channel,
    *,
    new_name: str,
    new_slug: str | None = None,
    copy_secrets: bool = True,
    commit: bool = True,
) -> Channel:
    """Clone a channel's config and integrations into a new channel.

    Secrets are copied as-is (same master key → still decryptable); no
    plaintext is ever materialized.
    """
    clone = Channel(
        id=str(uuid.uuid4()),
        slug=_unique_slug(session, new_slug or new_name),
        name=new_name,
        description=source.description,
        niche=source.niche,
        content_style=source.content_style,
        status="paused",  # start paused so it can be reviewed before going live
        config=dict(source.config or {}),
    )
    session.add(clone)
    session.flush()

    for src_integration in source.integrations:
        session.add(
            ChannelIntegration(
                id=str(uuid.uuid4()),
                channel_id=clone.id,
                provider_key=src_integration.provider_key,
                enabled=src_integration.enabled,
                config=dict(src_integration.config or {}),
                secrets_encrypted=src_integration.secrets_encrypted if copy_secrets else None,
                status="unknown",
            )
        )

    if commit:
        session.commit()
    log.info("channel_duplicated", source=source.slug, clone=clone.slug)
    return clone


def delete_channel(session: Session, channel: Channel, *, commit: bool = True) -> None:
    """Delete a channel and its integrations.

    Refuses if the channel has produced batches (to avoid orphaning history);
    pause such channels instead.
    """
    from storyfactory.db.models import DailyBatch

    batch_count = session.query(DailyBatch).filter_by(channel_id=channel.id).count()
    if batch_count > 0:
        raise ValueError(
            f"Channel '{channel.slug}' has {batch_count} batch(es) of history; "
            "pause it instead of deleting."
        )
    session.delete(channel)  # integrations cascade via relationship
    if commit:
        session.commit()
    log.info("channel_deleted", slug=channel.slug)


def set_prompt_override(
    session: Session,
    channel: Channel,
    name: str,
    template: str,
    *,
    description: str | None = None,
    category: str | None = None,
    variables: list | None = None,
    commit: bool = True,
):
    """Create or update a per-channel prompt override.

    Inherits category/variables from the matching global template when not
    provided, so an override only needs to supply the new template text.
    """
    from storyfactory.db.models import PromptTemplate

    global_tpl = (
        session.query(PromptTemplate).filter_by(name=name, channel_id=None).first()
    )
    override = (
        session.query(PromptTemplate)
        .filter_by(name=name, channel_id=channel.id)
        .first()
    )
    if override is None:
        override = PromptTemplate(
            id=str(uuid.uuid4()),
            channel_id=channel.id,
            name=name,
            category=category or (global_tpl.category if global_tpl else "story"),
            variables=variables if variables is not None else (global_tpl.variables if global_tpl else []),
            is_active=True,
        )
        session.add(override)
    override.template = template
    if description is not None:
        override.description = description
    elif global_tpl is not None and override.description is None:
        override.description = f"Channel override of '{name}'"
    if category is not None:
        override.category = category
    if variables is not None:
        override.variables = variables

    if commit:
        session.commit()
    log.info("prompt_override_set", slug=channel.slug, name=name)
    return override


def integration_status(session: Session, channel: Channel) -> list[dict]:
    """Return per-provider status for a channel (no secret values).

    `configured` means all required fields are present and non-placeholder.
    """
    existing = {i.provider_key: i for i in channel.integrations}
    statuses = []
    for spec in registry.list_providers():
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
                "enabled": enabled,
                "configured": len(problems) == 0,
                "missing": problems,
            }
        )
    return statuses


def ensure_default_channel(session: Session, *, commit: bool = True) -> Channel:
    """Create the default channel from env and backfill integrations, if absent.

    This is the migration entry point that makes existing single-channel
    installs work with zero re-setup.
    """
    existing = get_default_channel(session)
    if existing is not None:
        return existing

    settings = get_settings()
    channel = create_channel(
        session,
        name=settings.default_channel_name,
        slug=settings.default_channel_slug,
        description="Migrated from the original single-channel configuration.",
        status="active",
        commit=False,
    )
    import_env_into_channel(session, channel, commit=False)
    if commit:
        session.commit()
    return channel
