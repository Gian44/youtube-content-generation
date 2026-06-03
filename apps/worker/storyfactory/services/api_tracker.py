"""API usage tracking service."""

import uuid
from datetime import datetime, timezone

from storyfactory.db.engine import get_session
from storyfactory.db.models import ApiUsageLog
from storyfactory.logger import get_logger

log = get_logger("api_tracker")


def track_api_call(
    provider: str,
    endpoint: str,
    tokens_used: int = 0,
    cost_estimate: float = 0.0,
    status_code: int | None = None,
    error: str | None = None,
    request_data: dict | None = None,
    response_summary: str | None = None,
):
    """Log an API call for usage tracking and cost monitoring."""
    from storyfactory.channel_context import get_current_channel_id

    session = get_session()
    try:
        log_entry = ApiUsageLog(
            id=str(uuid.uuid4()),
            channel_id=get_current_channel_id(),
            provider=provider,
            endpoint=endpoint,
            tokens_used=tokens_used,
            cost_estimate=cost_estimate,
            status_code=status_code,
            error=error,
            request_data=request_data,
            response_summary=response_summary,
        )
        session.add(log_entry)
        session.commit()
    except Exception as e:
        session.rollback()
        log.warning("api_tracking_failed", error=str(e))
    finally:
        session.close()


def get_daily_usage(provider: str | None = None) -> dict:
    """Get today's API usage summary."""
    session = get_session()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        query = session.query(ApiUsageLog).filter(
            ApiUsageLog.created_at >= f"{today}T00:00:00"
        )
        if provider:
            query = query.filter(ApiUsageLog.provider == provider)

        logs = query.all()
        return {
            "total_calls": len(logs),
            "total_tokens": sum(l.tokens_used or 0 for l in logs),
            "total_cost": round(sum(l.cost_estimate or 0 for l in logs), 4),
            "errors": sum(1 for l in logs if l.error),
        }
    finally:
        session.close()


def get_youtube_quota_used() -> int:
    """Get today's YouTube API quota usage."""
    session = get_session()
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        logs = (
            session.query(ApiUsageLog)
            .filter(
                ApiUsageLog.provider == "youtube",
                ApiUsageLog.created_at >= f"{today}T00:00:00",
            )
            .all()
        )
        # YouTube videos.insert costs 1600 quota units
        return sum(l.tokens_used or 0 for l in logs)
    finally:
        session.close()


YOUTUBE_DAILY_QUOTA = 10000  # Default YouTube API quota


def check_youtube_quota() -> bool:
    """Check if we have remaining YouTube API quota for today."""
    used = get_youtube_quota_used()
    remaining = YOUTUBE_DAILY_QUOTA - used
    log.info("youtube_quota_check", used=used, remaining=remaining)
    return remaining >= 1600  # Minimum for one upload
