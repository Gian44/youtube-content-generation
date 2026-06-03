"""Per-channel configuration and credential resolution.

A :class:`ChannelContext` bundles a channel's effective (merged) content config
with an :class:`IntegrationResolver` that decrypts per-channel credentials on
demand. The pipeline sets the *current* context (a contextvar) for the duration
of one channel's run; leaf services read config/credentials through the
module-level ``resolve_*`` / ``effective_config`` helpers.

Backward compatibility: when no context is active (e.g. isolated unit tests),
the helpers fall back to the global env-based :class:`Settings`. For the
**default** channel only, an empty per-channel secret also falls back to the
global env value, so an existing single-channel install keeps working even if
the user updates ``.env`` without re-importing.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from storyfactory import crypto
from storyfactory.config import get_settings
from storyfactory.content_defaults import (
    DEFAULT_CATEGORY_HINTS,
    DEFAULT_CATEGORY_WEIGHTS,
    DEFAULT_VOICE_PERSONAS,
)
from storyfactory.db.models import Channel, ChannelIntegration
from storyfactory.integrations import registry


class IntegrationResolver:
    """Read-only access to a channel's integrations, decrypting lazily.

    Built from plain data (not ORM instances) so it remains valid after the
    building session is closed.
    """

    def __init__(self, data: dict[str, dict]):
        self._data = data
        self._secret_cache: dict[str, dict] = {}

    def has(self, provider_key: str) -> bool:
        return provider_key in self._data

    def is_enabled(self, provider_key: str) -> bool:
        rec = self._data.get(provider_key)
        return bool(rec and rec.get("enabled"))

    def config(self, provider_key: str) -> dict:
        rec = self._data.get(provider_key)
        return dict(rec.get("config") or {}) if rec else {}

    def _secrets(self, provider_key: str) -> dict:
        if provider_key not in self._secret_cache:
            rec = self._data.get(provider_key)
            enc = rec.get("secrets_encrypted") if rec else None
            self._secret_cache[provider_key] = crypto.decrypt_secrets(enc) if enc else {}
        return self._secret_cache[provider_key]

    def secret(self, provider_key: str, field: str) -> str:
        return self._secrets(provider_key).get(field, "") or ""


@dataclass
class ChannelContext:
    channel_id: str
    slug: str
    name: str
    is_default: bool
    config: dict
    integrations: IntegrationResolver
    # Shared, app-level providers (OpenAI/Gemini/Pexels/Pixabay/TTS/storage).
    app_integrations: IntegrationResolver = field(default_factory=lambda: IntegrationResolver({}))

    def cfg(self, key: str, default=None):
        return self.config.get(key, default)


_current: contextvars.ContextVar[ChannelContext | None] = contextvars.ContextVar(
    "channel_context", default=None
)


def get_current() -> ChannelContext | None:
    return _current.get()


def require_current() -> ChannelContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("No active channel context. Wrap work in use_channel().")
    return ctx


def get_current_channel_id() -> str | None:
    ctx = _current.get()
    return ctx.channel_id if ctx else None


def resolve_channel_config(channel: Channel) -> dict:
    """Merge the app-level defaults with a channel's stored overrides."""
    settings = get_settings()
    base = settings.content_config_defaults()
    base["category_weights"] = dict(DEFAULT_CATEGORY_WEIGHTS)
    base["category_hints"] = dict(DEFAULT_CATEGORY_HINTS)
    base["voice_personas"] = list(DEFAULT_VOICE_PERSONAS)
    base.update(channel.config or {})
    return base


def _build_app_resolver(session: Session) -> IntegrationResolver:
    """Resolver over app-level (shared) integrations.

    Defensive: if the ``app_integrations`` table does not exist yet (e.g. a
    context built before migrations on a legacy DB), fall back to an empty
    resolver so the env-fallback path still works.
    """
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from storyfactory.services.app_integration_service import build_app_resolver

    try:
        return build_app_resolver(session)
    except (OperationalError, ProgrammingError):
        session.rollback()
        return IntegrationResolver({})


def build_context(session: Session, channel: Channel) -> ChannelContext:
    settings = get_settings()
    integrations = (
        session.query(ChannelIntegration).filter_by(channel_id=channel.id).all()
    )
    data = {
        i.provider_key: {
            "enabled": bool(i.enabled),
            "config": dict(i.config or {}),
            "secrets_encrypted": i.secrets_encrypted,
        }
        for i in integrations
    }
    return ChannelContext(
        channel_id=channel.id,
        slug=channel.slug,
        name=channel.name,
        is_default=(channel.slug == settings.default_channel_slug),
        config=resolve_channel_config(channel),
        integrations=IntegrationResolver(data),
        app_integrations=_build_app_resolver(session),
    )


@contextmanager
def use_channel(session: Session, channel: Channel):
    """Activate a channel context for the duration of the block."""
    ctx = build_context(session, channel)
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


# ----------------------------------------------------------------------------
# Resolution helpers used by leaf services (context-aware, env-fallback safe)
# ----------------------------------------------------------------------------

def effective_config(key: str, default=None):
    """Return a config value: active channel override > global default."""
    ctx = _current.get()
    if ctx is not None and key in ctx.config:
        return ctx.config[key]
    settings = get_settings()
    if hasattr(settings, key):
        return getattr(settings, key)
    return default


def _env_fallback_allowed(ctx: ChannelContext | None) -> bool:
    # No context (tests/legacy) or the default channel may use the global env.
    return ctx is None or ctx.is_default


def _is_app_scoped(provider_key: str) -> bool:
    spec = registry.PROVIDERS.get(provider_key)
    return spec is not None and spec.scope == registry.SCOPE_APP


def resolve_api_key(provider_key: str, fallback_attr: str) -> str:
    """Resolve a provider API key.

    - **App-scoped** (shared) providers resolve from the app-level integration
      store, then fall back to the global env value (always allowed — these are
      one set of tools for the whole app).
    - **Channel-scoped** providers (YouTube) resolve from the active channel,
      with an env fallback only for the default channel (no cross-channel bleed).
    """
    ctx = _current.get()
    settings = get_settings()
    env_value = getattr(settings, fallback_attr, "") or ""

    if _is_app_scoped(provider_key):
        if ctx is not None:
            app_secret = ctx.app_integrations.secret(provider_key, "api_key")
            if app_secret:
                return app_secret
        return env_value

    # Channel-scoped (e.g. youtube): keep per-channel isolation.
    fallback = env_value if _env_fallback_allowed(ctx) else ""
    if ctx is None:
        return fallback
    return ctx.integrations.secret(provider_key, "api_key") or fallback


def resolve_model(provider_key: str, fallback_attr: str, explicit: str | None = None) -> str | None:
    """Resolve a model name: explicit > integration config > env.

    App-scoped providers read their config from the app-level store; channel-
    scoped providers read it from the active channel integration.
    """
    if explicit:
        return explicit
    ctx = _current.get()
    if ctx is not None:
        resolver = ctx.app_integrations if _is_app_scoped(provider_key) else ctx.integrations
        model = resolver.config(provider_key).get("model")
        if model:
            return model
    return getattr(get_settings(), fallback_attr, None)


def youtube_credentials() -> dict:
    """Resolve YouTube OAuth credentials for the active channel.

    client_id/client_secret default to the shared app-level OAuth app and may be
    overridden per channel. The refresh token is per channel (with env fallback
    for the default channel only).
    """
    ctx = _current.get()
    settings = get_settings()
    res = ctx.integrations if ctx is not None else None

    client_id = (res.secret("youtube", "client_id") if res else "") or settings.youtube_client_id
    client_secret = (
        (res.secret("youtube", "client_secret") if res else "") or settings.youtube_client_secret
    )
    refresh_token = res.secret("youtube", "refresh_token") if res else ""
    if not refresh_token and _env_fallback_allowed(ctx):
        refresh_token = settings.youtube_refresh_token

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
