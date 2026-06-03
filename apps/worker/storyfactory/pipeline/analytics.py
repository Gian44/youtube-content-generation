"""Analytics pipeline - fetches YouTube analytics snapshots."""

import uuid
from datetime import datetime, timezone

from rich.console import Console

from storyfactory.channel_context import use_channel, youtube_credentials
from storyfactory.config import get_settings
from storyfactory.db.engine import init_db, get_session
from storyfactory.db.models import YouTubeUpload, AnalyticsSnapshot
from storyfactory.logger import setup_logging, get_logger
from storyfactory.services.api_tracker import track_api_call
from storyfactory.services.channel_service import (
    ensure_default_channel,
    get_channel,
    list_channels,
)

console = Console()
log = get_logger("analytics_pipeline")


def run_analytics_pipeline(channel_ref: str | None = None):
    """Fetch analytics for a single channel's uploaded YouTube videos.

    With no channel_ref, runs for every active channel sequentially.
    """
    setup_logging()
    init_db()

    if channel_ref:
        session = get_session()
        try:
            channel = get_channel(session, channel_ref)
            if channel is None:
                console.print(f"[bold red]Channel not found: {channel_ref}[/bold red]")
                return
            with use_channel(session, channel):
                _analytics_for_channel(session, channel)
        finally:
            session.close()
        return

    # All active channels
    session = get_session()
    try:
        ensure_default_channel(session)
        channels = list_channels(session, active_only=True)
    finally:
        session.close()

    for channel in channels:
        sess = get_session()
        try:
            with use_channel(sess, channel):
                _analytics_for_channel(sess, channel)
        except Exception as e:
            log.error("channel_analytics_failed", channel=channel.slug, error=str(e))
            console.print(f"[red]Channel {channel.slug} analytics failed: {e}[/red]")
        finally:
            sess.close()


def _analytics_for_channel(session, channel):
    """Fetch and persist analytics snapshots for one channel."""
    settings = get_settings()
    uploads = (
        session.query(YouTubeUpload)
        .filter(
            YouTubeUpload.status == "completed",
            YouTubeUpload.youtube_video_id.isnot(None),
            ~YouTubeUpload.youtube_video_id.like("DRY_RUN%"),
            YouTubeUpload.channel_id == channel.id,
        )
        .all()
    )

    if not uploads:
        console.print(f"[yellow]No uploaded videos for '{channel.slug}'.[/yellow]")
        return

    console.print(f"[blue]Fetching analytics for {len(uploads)} videos ('{channel.slug}')...[/blue]")

    if settings.dry_run:
        console.print("[yellow]Dry run - generating sample analytics[/yellow]")
        for upload in uploads:
            _create_sample_snapshot(session, upload)
        session.commit()
        return

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = youtube_credentials()
        credentials = Credentials(
            token=None,
            refresh_token=creds["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
        )

        youtube = build("youtube", "v3", credentials=credentials)

        for upload in uploads:
            _fetch_video_stats(youtube, session, upload)

        track_api_call(
            provider="youtube",
            endpoint="videos.list",
            tokens_used=len(uploads),  # 1 quota unit per video
        )

        session.commit()

    except Exception as e:
        log.error("youtube_analytics_failed", error=str(e))
        console.print(f"[red]Analytics fetch failed: {e}[/red]")

    console.print(f"[bold green]Analytics complete for '{channel.slug}'.[/bold green]")


def _fetch_video_stats(youtube, session, upload: YouTubeUpload):
    """Fetch stats for a single video."""
    try:
        response = youtube.videos().list(
            part="statistics,contentDetails",
            id=upload.youtube_video_id,
        ).execute()

        items = response.get("items", [])
        if not items:
            return

        stats = items[0].get("statistics", {})
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Check if snapshot already exists for today
        existing = (
            session.query(AnalyticsSnapshot)
            .filter_by(
                youtube_upload_id=upload.id,
                snapshot_date=today,
            )
            .first()
        )

        if existing:
            existing.views = int(stats.get("viewCount", 0))
            existing.likes = int(stats.get("likeCount", 0))
            existing.comments = int(stats.get("commentCount", 0))
        else:
            snapshot = AnalyticsSnapshot(
                id=str(uuid.uuid4()),
                youtube_upload_id=upload.id,
                youtube_video_id=upload.youtube_video_id,
                snapshot_date=today,
                views=int(stats.get("viewCount", 0)),
                likes=int(stats.get("likeCount", 0)),
                comments=int(stats.get("commentCount", 0)),
            )
            session.add(snapshot)

        console.print(
            f"  [green]✓ {upload.youtube_video_id}: "
            f"{stats.get('viewCount', 0)} views, "
            f"{stats.get('likeCount', 0)} likes[/green]"
        )

    except Exception as e:
        log.warning("video_stats_failed", video_id=upload.youtube_video_id, error=str(e))


def _create_sample_snapshot(session, upload: YouTubeUpload):
    """Create a sample analytics snapshot for dry-run mode."""
    import random
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    snapshot = AnalyticsSnapshot(
        id=str(uuid.uuid4()),
        youtube_upload_id=upload.id,
        youtube_video_id=upload.youtube_video_id or "SAMPLE",
        snapshot_date=today,
        views=random.randint(100, 10000),
        likes=random.randint(10, 500),
        comments=random.randint(5, 100),
        watch_time_minutes=random.uniform(10, 500),
        avg_view_duration_seconds=random.uniform(15, 120),
        subscribers_gained=random.randint(0, 50),
        impressions=random.randint(500, 50000),
        ctr=random.uniform(2.0, 15.0),
    )
    session.add(snapshot)
