"""Lightweight versioned migration runner.

The original project evolved its schema only via ``Base.metadata.create_all``,
which creates missing tables but cannot ``ALTER`` existing ones or backfill
data. This runner adds ordered, idempotent migrations tracked in a
``schema_migrations`` table, working on both SQLite and PostgreSQL.

``init_db()`` (create_all) still handles table creation for fresh installs;
migrations handle column additions, constraint changes, and data backfill for
existing databases.
"""

from __future__ import annotations

from datetime import datetime, timezone

from rich.console import Console
from sqlalchemy import text
from sqlalchemy.orm import Session

from storyfactory.db.engine import get_engine, get_session, init_db
from storyfactory.logger import get_logger

console = Console()
log = get_logger("migrations")


# ----------------------------------------------------------------------------
# Dialect-aware helpers (used by data/DDL migrations)
# ----------------------------------------------------------------------------

def _dialect(session: Session) -> str:
    return session.bind.dialect.name  # 'sqlite' | 'postgresql'


def column_exists(session: Session, table: str, column: str) -> bool:
    if _dialect(session) == "sqlite":
        rows = session.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(row[1] == column for row in rows)
    row = session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    ).first()
    return row is not None


def add_column(session: Session, table: str, column: str, ddl_type: str) -> bool:
    """Idempotently add a (nullable) column. Returns True if it was added."""
    if column_exists(session, table, column):
        return False
    session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    return True


def table_exists(session: Session, table: str) -> bool:
    if _dialect(session) == "sqlite":
        row = session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name = :t"),
            {"t": table},
        ).first()
        return row is not None
    row = session.execute(
        text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
        {"t": table},
    ).first()
    return row is not None


# ----------------------------------------------------------------------------
# Migration tracking
# ----------------------------------------------------------------------------

def _ensure_tracking_table(session: Session) -> None:
    session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(50) PRIMARY KEY, applied_at VARCHAR(40))"
        )
    )
    session.commit()


def _applied_versions(session: Session) -> set[str]:
    _ensure_tracking_table(session)
    return {row[0] for row in session.execute(text("SELECT version FROM schema_migrations")).fetchall()}


def _record(session: Session, version: str) -> None:
    session.execute(
        text("INSERT INTO schema_migrations (version, applied_at) VALUES (:v, :t)"),
        {"v": version, "t": datetime.now(timezone.utc).isoformat()},
    )


# ----------------------------------------------------------------------------
# Migrations
# ----------------------------------------------------------------------------

def _m0001_default_channel(session: Session) -> None:
    """Create the default channel and backfill integrations from the env."""
    from storyfactory.services.channel_service import ensure_default_channel

    ensure_default_channel(session, commit=False)


# Operational tables that gain a direct channel_id for query scoping.
_CHANNEL_SCOPED_TABLES = (
    "daily_batches",
    "stories",
    "render_jobs",
    "youtube_uploads",
    "assets",
    "api_usage_logs",
    "policy_flags",
)


def _m0002_channel_id_columns(session: Session) -> None:
    """Add channel_id to operational tables and backfill to the default channel."""
    from storyfactory.services.channel_service import ensure_default_channel

    default_channel = ensure_default_channel(session, commit=False)
    for table in _CHANNEL_SCOPED_TABLES:
        if not table_exists(session, table):
            continue
        add_column(session, table, "channel_id", "VARCHAR(36)")
        session.execute(
            text(f"UPDATE {table} SET channel_id = :cid WHERE channel_id IS NULL"),
            {"cid": default_channel.id},
        )


def _m0003_prompt_channel_overrides(session: Session) -> None:
    """Allow per-channel prompt overrides via (channel_id, name).

    Existing prompts stay global (channel_id NULL). On SQLite the old
    column-level UNIQUE(name) constraint cannot be dropped in place, so the
    table is rebuilt; PostgreSQL uses ALTER.
    """
    if not table_exists(session, "prompt_templates"):
        return
    if column_exists(session, "prompt_templates", "channel_id"):
        return  # fresh DBs already have the new schema via create_all

    if _dialect(session) == "sqlite":
        session.execute(text(
            """
            CREATE TABLE prompt_templates_new (
                id VARCHAR(36) PRIMARY KEY,
                channel_id VARCHAR(36),
                name VARCHAR(100) NOT NULL,
                description TEXT,
                template TEXT NOT NULL,
                variables JSON,
                category VARCHAR(30) NOT NULL,
                version INTEGER,
                is_active BOOLEAN,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        ))
        session.execute(text(
            """
            INSERT INTO prompt_templates_new
                (id, channel_id, name, description, template, variables,
                 category, version, is_active, created_at, updated_at)
            SELECT id, NULL, name, description, template, variables,
                   category, version, is_active, created_at, updated_at
            FROM prompt_templates
            """
        ))
        session.execute(text("DROP TABLE prompt_templates"))
        session.execute(text("ALTER TABLE prompt_templates_new RENAME TO prompt_templates"))
        session.execute(text("CREATE INDEX ix_prompt_templates_category ON prompt_templates (category)"))
        session.execute(text(
            "CREATE UNIQUE INDEX ix_prompt_templates_channel_name ON prompt_templates (channel_id, name)"
        ))
        session.execute(text(
            "CREATE INDEX ix_prompt_templates_channel_id ON prompt_templates (channel_id)"
        ))
    else:
        add_column(session, "prompt_templates", "channel_id", "VARCHAR(36)")
        session.execute(text(
            "ALTER TABLE prompt_templates DROP CONSTRAINT IF EXISTS prompt_templates_name_key"
        ))
        session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_prompt_templates_channel_name "
            "ON prompt_templates (channel_id, name)"
        ))
        session.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_prompt_templates_channel_id "
            "ON prompt_templates (channel_id)"
        ))


def _m0004_app_integrations(session: Session) -> None:
    """Consolidate shared provider credentials from per-channel rows to app level.

    Shared providers (OpenAI/Gemini/Pexels/Pixabay/TTS/storage) move from
    ``channel_integrations`` into the single ``app_integrations`` store. YouTube
    stays per-channel and is untouched.

    Conflict policy: the **default channel's** value is canonical. If another
    channel stored a *different* secret for the same provider, it is logged (not
    silently lost) so the operator can reconcile it in Settings, then the
    per-channel rows for shared providers are removed.
    """
    from storyfactory import crypto
    from storyfactory.db.models import ChannelIntegration
    from storyfactory.integrations import registry
    from storyfactory.services.app_integration_service import (
        get_app_integration,
        import_env_into_app,
        set_app_integration,
    )
    from storyfactory.services.channel_service import get_default_channel

    crypto.ensure_key()
    app_keys = [spec.key for spec in registry.app_providers()]

    # Fresh install (no channel_integrations table yet): just seed from env.
    if not table_exists(session, "channel_integrations"):
        import_env_into_app(session, commit=False)
        return

    default = get_default_channel(session)

    def _safe_decrypt(blob):
        if not blob:
            return {}
        try:
            return crypto.decrypt_secrets(blob)
        except Exception as exc:  # bad/old key — surface, don't migrate this blob
            log.warning("app_integration_decrypt_failed", error=str(exc))
            return None

    for spec in registry.app_providers():
        if get_app_integration(session, spec.key) is not None:
            continue  # idempotent — already consolidated

        rows = (
            session.query(ChannelIntegration)
            .filter_by(provider_key=spec.key)
            .all()
        )
        canonical = None
        if default is not None:
            canonical = next((r for r in rows if r.channel_id == default.id), None)
        if canonical is None and rows:
            canonical = rows[0]

        if canonical is None:
            continue  # no per-channel value; env seeding happens below

        canon_secrets = _safe_decrypt(canonical.secrets_encrypted)
        if canon_secrets is None:
            continue  # could not decrypt the canonical secret; leave for manual setup

        # Log any other channel whose secret differs from the canonical one.
        for row in rows:
            if row is canonical:
                continue
            other = _safe_decrypt(row.secrets_encrypted)
            if other and other != canon_secrets:
                log.warning(
                    "app_integration_conflict",
                    provider=spec.key,
                    channel_id=row.channel_id,
                )
                console.print(
                    f"  [yellow]⚠ {spec.label}: channel {row.channel_id[:8]} had a "
                    f"different value; kept the default channel's. Re-enter it under "
                    f"Settings → Integrations if that was intentional.[/yellow]"
                )

        set_app_integration(
            session,
            spec.key,
            enabled=bool(canonical.enabled),
            config=dict(canonical.config or {}) or None,
            secrets=canon_secrets or None,
            commit=False,
        )

    # Seed any still-missing app providers from the environment.
    import_env_into_app(session, commit=False)

    # The session uses autoflush=False, so flush the pending app_integrations
    # inserts before querying them below.
    session.flush()

    # Remove per-channel rows ONLY for shared providers that were successfully
    # consolidated to app level. A provider whose secret could not be decrypted
    # (wrong/rotated/lost master key) and had no env value to seed from has no
    # app_integrations row — KEEP its per-channel row so the encrypted secret is
    # not destroyed. The operator can restore the key and re-enter it; a bulk
    # delete here would permanently lose an unrecoverable credential.
    migrated_keys = [k for k in app_keys if get_app_integration(session, k) is not None]
    if migrated_keys:
        deleted = (
            session.query(ChannelIntegration)
            .filter(ChannelIntegration.provider_key.in_(migrated_keys))
            .delete(synchronize_session=False)
        )
        kept = [k for k in app_keys if k not in migrated_keys]
        if kept:
            log.warning("app_integration_unmigrated_kept", providers=kept)
            console.print(
                f"  [yellow]⚠ Kept per-channel rows for {kept} — could not consolidate "
                f"(decrypt failed and no .env value). Fix the master key and re-enter under "
                f"Settings → Integrations.[/yellow]"
            )
        log.info("app_integrations_consolidated", deleted_channel_rows=deleted)


def _m0005_recap_media(session: Session) -> None:
    """Create the recap media tables (series / episode / segment ledger).

    These are brand-new tables with no data backfill. ``init_db()`` (create_all)
    at the start of the runner already creates them on fresh installs; this
    migration explicitly (and idempotently) creates them on existing databases
    so the upgrade path does not depend on create_all ordering.
    """
    from storyfactory.db.models import (
        Base,
        MediaSeries,
        MediaEpisode,
        RecapSegment,
    )

    Base.metadata.create_all(
        bind=session.bind,
        tables=[
            MediaSeries.__table__,
            MediaEpisode.__table__,
            RecapSegment.__table__,
        ],
    )


def _m0006_media_scenes(session: Session) -> None:
    """Create the ``media_scenes`` table (frozen scene plan for scene mode).

    Brand-new table, no backfill. Idempotent: ``create_all`` skips it if present.
    """
    from storyfactory.db.models import Base, MediaScene

    Base.metadata.create_all(bind=session.bind, tables=[MediaScene.__table__])


def _m0007_media_scene_unique(session: Session) -> None:
    """Make ``(episode_id, scene_index)`` unique on ``media_scenes``.

    The scene plan is computed once and frozen; a unique key lets the DB reject a
    duplicate concurrent insert instead of silently corrupting the plan. The
    table is new (created in 0006) so the index rebuild is safe. Idempotent.
    """
    if not table_exists(session, "media_scenes"):
        return
    session.execute(text("DROP INDEX IF EXISTS ix_media_scenes_episode_idx"))
    session.execute(text(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_media_scenes_episode_idx "
        "ON media_scenes (episode_id, scene_index)"
    ))


# Ordered registry. Append new migrations; never reorder or mutate shipped ones.
MIGRATIONS: list[tuple[str, callable]] = [
    ("0001_default_channel", _m0001_default_channel),
    ("0002_channel_id_columns", _m0002_channel_id_columns),
    ("0003_prompt_channel_overrides", _m0003_prompt_channel_overrides),
    ("0004_app_integrations", _m0004_app_integrations),
    ("0005_recap_media", _m0005_recap_media),
    ("0006_media_scenes", _m0006_media_scenes),
    ("0007_media_scene_unique", _m0007_media_scene_unique),
]


def run_migrations() -> list[str]:
    """Apply all pending migrations. Returns the versions applied this run."""
    init_db()  # create_all: tables (incl. channels/channel_integrations) for fresh installs
    session = get_session()
    applied_now: list[str] = []
    try:
        already = _applied_versions(session)
        for version, fn in MIGRATIONS:
            if version in already:
                continue
            console.print(f"[blue]Applying migration {version}...[/blue]")
            fn(session)
            _record(session, version)
            session.commit()
            applied_now.append(version)
            console.print(f"  [green]✓[/green] {version}")
        if not applied_now:
            console.print("[green]Database is up to date.[/green]")
        return applied_now
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
