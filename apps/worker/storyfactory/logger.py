"""Structured logging configuration for StoryFactory."""

import os
import structlog
import logging
import sys
from pathlib import Path
from datetime import datetime, timezone

from storyfactory.config import get_settings


def setup_logging():
    """Configure structured logging."""
    settings = get_settings()
    log_level = getattr(logging, settings.worker_log_level.upper(), logging.INFO)

    # Create logs directory
    log_dir = Path(os.environ.get("STORYFACTORY_LOG_DIR", "./logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if sys.stderr.isatty() else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=log_level,
    )


def get_logger(name: str = None):
    """Get a structured logger instance."""
    return structlog.get_logger(name)
