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


# Ordered registry. Append new migrations; never reorder or mutate shipped ones.
MIGRATIONS: list[tuple[str, callable]] = [
    ("0001_default_channel", _m0001_default_channel),
    ("0002_channel_id_columns", _m0002_channel_id_columns),
    ("0003_prompt_channel_overrides", _m0003_prompt_channel_overrides),
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
