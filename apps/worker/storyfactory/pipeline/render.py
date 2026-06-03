"""Render pipeline - processes the render queue."""

from rich.console import Console

from storyfactory.channel_context import effective_config, use_channel
from storyfactory.config import get_settings
from storyfactory.db.engine import init_db, get_session
from storyfactory.db.models import Channel, RenderJob, DailyBatch, Story, TTSJob, CaptionJob, Asset
from storyfactory.logger import setup_logging, get_logger
from storyfactory.services.channel_service import ensure_default_channel, get_channel
from storyfactory.services.renderer import render_short, render_long_form
from storyfactory.services.tts_service import generate_tts
from storyfactory.services.caption_service import generate_captions
from storyfactory.services.asset_collector import collect_story_relevant_assets, download_assets

console = Console()
log = get_logger("render_pipeline")


def run_render_pipeline(
    batch_id: str | None = None,
    channel_ref: str | None = None,
    dry_run: bool = False,
):
    """Process pending render jobs (optionally scoped to a channel)."""
    setup_logging()
    settings = get_settings()
    if dry_run:
        settings.dry_run = True

    init_db()
    session = get_session()

    try:
        scoped_channel = get_channel(session, channel_ref) if channel_ref else None
        if channel_ref and scoped_channel is None:
            console.print(f"[bold red]Channel not found: {channel_ref}[/bold red]")
            return

        # Find batches ready for rendering
        query = session.query(DailyBatch).filter(
            DailyBatch.status.in_(["policy_checked", "tts_complete", "captions_complete", "assets_collected"])
        )
        if batch_id:
            query = query.filter(DailyBatch.id == batch_id)
        if scoped_channel is not None:
            query = query.filter(DailyBatch.channel_id == scoped_channel.id)

        batches = query.all()

        if not batches:
            console.print("[yellow]No batches ready for rendering.[/yellow]")
            return

        default_channel = ensure_default_channel(session)
        for batch in batches:
            console.print(f"[blue]Processing batch {batch.id[:8]} ({batch.date})...[/blue]")

            batch_channel = (
                session.get(Channel, batch.channel_id) if batch.channel_id else None
            ) or default_channel
            with use_channel(session, batch_channel):
                _render_batch(session, batch)

        console.print("[bold green]Render pipeline complete.[/bold green]")

    except Exception as e:
        log.error("render_pipeline_failed", error=str(e))
        console.print(f"[bold red]Render pipeline failed: {e}[/bold red]")
        raise
    finally:
        session.close()


def _render_batch(session, batch):
    """Render a single batch within an active channel context."""
    stories = session.query(Story).filter_by(
        batch_id=batch.id, policy_status="clean"
    ).all()

    if not stories:
        stories = session.query(Story).filter(
            Story.batch_id == batch.id,
            Story.policy_status.in_(["clean", "rewritten"]),
        ).all()

    if not stories:
        console.print(f"  [yellow]No approved stories in batch[/yellow]")
        return

    # Ensure TTS exists for all stories
    tts_jobs = {}
    for story in stories:
        tts = session.query(TTSJob).filter_by(
            story_id=story.id, status="completed"
        ).first()
        if not tts:
            tts = generate_tts(story)
        tts_jobs[story.id] = tts

    # Ensure captions exist
    default_caption_style = effective_config("caption_style", "word_highlight")
    caption_jobs = {}
    for story in stories:
        tts = tts_jobs.get(story.id)
        if tts and tts.status == "completed":
            cap = session.query(CaptionJob).filter_by(
                story_id=story.id, status="completed"
            ).first()
            if not cap:
                style = default_caption_style if story.type == "short" else "sentence"
                cap = generate_captions(story, tts, style=style)
            caption_jobs[story.id] = cap

    # Collect and download story-relevant assets PER STORY
    story_assets = {}  # story_id -> list[Asset]
    for story in stories:
        console.print(f"  [blue]Collecting assets for: {story.title[:40]}...[/blue]")
        assets = collect_story_relevant_assets(story, count=5)
        assets = download_assets(assets)
        story_assets[story.id] = assets

    # Render shorts
    shorts = [s for s in stories if s.type == "short"]
    for story in shorts:
        tts = tts_jobs.get(story.id)
        cap = caption_jobs.get(story.id)
        assets = story_assets.get(story.id, [])

        if tts and tts.status == "completed":
            render_short(story, tts, cap, assets, batch.id)
            console.print(f"  [green]✓ Short rendered: {story.title[:40]}[/green]")

    # Render long-form
    all_stories = stories
    all_tts = [tts_jobs[s.id] for s in all_stories if s.id in tts_jobs and tts_jobs[s.id].status == "completed"]
    all_caps = [caption_jobs.get(s.id) for s in all_stories if s.id in caption_jobs]

    if all_tts:
        render_long_form(all_stories, all_tts, all_caps, story_assets, batch.id)
        console.print(f"  [green]✓ Long-form rendered[/green]")

    batch.status = "rendered"
    session.commit()
