"""Database engine and session management."""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from storyfactory.config import get_settings
from storyfactory.db.models import Base

_engine = None
_SessionLocal = None


def get_engine():
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.effective_database_url

        if settings.is_sqlite:
            _engine = create_engine(
                url,
                echo=False,
                connect_args={"check_same_thread": False},
            )
            # Enable WAL mode for better concurrent access
            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        else:
            _engine = create_engine(
                url,
                echo=False,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
            )
    return _engine


def get_session_factory() -> sessionmaker:
    """Get the session factory."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionLocal


def get_session() -> Session:
    """Create a new database session."""
    factory = get_session_factory()
    return factory()


def init_db():
    """Create all tables."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def drop_db():
    """Drop all tables (use with caution)."""
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
