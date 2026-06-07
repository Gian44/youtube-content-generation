"""Upload pipeline - uploads rendered videos to YouTube (per channel)."""

from datetime import datetime, timezone

from rich.console import Console

from storyfactory.channel_context import effective_config, use_channel
from storyfactory.config import get_settings
from storyfactory.db.engine import init_db, get_session
from storyfactory.db.models import RenderJob, DailyBatch, Story, YouTubeUpload
from storyfactory.logger import setup_logging, get_logger
from storyfactory.services.channel_service import (
    ensure_default_channel,
    get_channel,
    list_channels,
)
from storyfactory.services.youtube_uploader import upload_video, build_upload_metadata

console = Console()
log = get_logger("upload_pipeline")


def run_upload_pipeline(channel_ref: str | None = None, dry_run: bool = False):
    """Upload rendered, un-uploaded videos for a single channel."""
    setup_logging()
    settings = get_settings()
    if dry_run:
        settings.dry_run = True

    init_db()
    session = get_session()
    try:
        channel = (
            get_channel(session, channel_ref)
            if channel_ref
            else ensure_default_channel(session)
        )
        if channel is None:
            console.print(f"[bold red]Channel not found: {channel_ref}[/bold red]")
            return
        with use_channel(session, channel):
            _upload_for_channel(session, channel)
    finally:
        session.close()


def run_upload_pipeline_all_active(dry_run: bool = False):
    """Upload for every active channel sequentially (fail-isolated)."""
    setup_logging()
    settings = get_settings()
    if dry_run:
        settings.dry_run = True

    init_db()
    session = get_session()
    try:
        ensure_default_channel(session)
        channels = list_channels(session, active_only=True)
    finally:
        session.close()

    for channel in channels:
        console.print(f"\n[bold cyan]══ Upload: {channel.name} ({channel.slug}) ══[/bold cyan]")
        try:
            run_upload_pipeline(channel_ref=channel.id, dry_run=dry_run)
        except Exception as e:
            log.error("channel_upload_failed", channel=channel.slug, error=str(e))
            console.print(f"[red]Channel {channel.slug} upload failed: {e}[/red]")


def _upload_for_channel(session, channel):
    """Upload all completed, un-uploaded renders for one channel."""
    render_jobs = (
        session.query(RenderJob)
        .filter(
            RenderJob.status == "completed",
            RenderJob.channel_id == channel.id,
            ~RenderJob.id.in_(
                session.query(YouTubeUpload.render_job_id)
                .filter(YouTubeUpload.status.in_(["completed", "processing"]))
            ),
        )
        .all()
    )

    if not render_jobs:
        console.print(f"[yellow]No videos ready for upload on '{channel.slug}'.[/yellow]")
        return

    # Optimizer cadence: at most N Short uploads per channel per UTC day (default
    # 1; 0 = unlimited). Long-form is never throttled by this. Excess Shorts stay
    # queued and drain oldest-first on subsequent days.
    render_jobs = _apply_short_upload_throttle(session, channel, render_jobs)
    if not render_jobs:
        console.print(
            f"[yellow]Daily Short upload limit already reached for '{channel.slug}'. "
            f"Remaining Shorts will upload on the next day's run.[/yellow]"
        )
        return

    console.print(f"[blue]Found {len(render_jobs)} videos to upload[/blue]")

    for render_job in render_jobs:
        stories = (
            session.query(Story)
            .filter(Story.id.in_(render_job.story_ids or []))
            .all()
        )

        metadata = build_upload_metadata(
            render_job=render_job,
            stories=stories,
            video_type=render_job.type,
        )

        console.print(f"[blue]Uploading: {metadata['title'][:60]}...[/blue]")

        upload = upload_video(render_job, metadata)

        if upload.status == "completed":
            console.print(
                f"  [green]✓ Uploaded: {upload.youtube_video_id} "
                f"({upload.actual_privacy})[/green]"
            )
        else:
            console.print(
                f"  [red]✗ Failed: {upload.error[:80] if upload.error else 'Unknown'}[/red]"
            )

    # Update batch status for this channel's affected batches
    batch_ids = {rj.batch_id for rj in render_jobs}
    for batch_id in batch_ids:
        batch = session.query(DailyBatch).filter_by(id=batch_id).first()
        if batch:
            batch.status = "uploaded"
    session.commit()

    console.print(f"[bold green]Upload pipeline complete for '{channel.slug}'.[/bold green]")


def _apply_short_upload_throttle(session, channel, render_jobs: list[RenderJob]) -> list[RenderJob]:
    """Cap Short uploads to ``max_short_uploads_per_day`` per channel per UTC day.

    Returns the subset of ``render_jobs`` to upload now: every non-Short, plus up
    to ``limit - (Shorts already uploaded today)`` Shorts (oldest first). A limit
    of 0 means unlimited (legacy behavior). Long-form is never held back.
    """
    limit = int(effective_config("max_short_uploads_per_day", 0) or 0)
    shorts = [rj for rj in render_jobs if rj.type == "short"]
    others = [rj for rj in render_jobs if rj.type != "short"]
    if limit <= 0 or not shorts:
        return render_jobs

    start_of_day = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    uploaded_today = (
        session.query(YouTubeUpload)
        .join(RenderJob, YouTubeUpload.render_job_id == RenderJob.id)
        .filter(
            RenderJob.channel_id == channel.id,
            RenderJob.type == "short",
            YouTubeUpload.status == "completed",
            YouTubeUpload.uploaded_at.isnot(None),
            YouTubeUpload.uploaded_at >= start_of_day,
        )
        .count()
    )
    allowed = max(0, limit - uploaded_today)
    shorts.sort(key=lambda r: r.created_at or start_of_day)  # FIFO drain
    held = len(shorts) - allowed
    if held > 0:
        console.print(
            f"[yellow]⏳ Holding {held} Short(s) for later — optimizer cap is "
            f"{limit}/day ({uploaded_today} already uploaded today).[/yellow]"
        )
    return others + shorts[:allowed]
