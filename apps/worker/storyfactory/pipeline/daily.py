"""Daily pipeline - orchestrates the full daily content generation workflow."""

import random
import uuid
from datetime import datetime, timezone

from rich.console import Console

from storyfactory.channel_context import effective_config, use_channel
from storyfactory.config import get_settings
from storyfactory.db.engine import init_db, get_session
from storyfactory.db.models import Channel, DailyBatch, Story
from storyfactory.logger import setup_logging, get_logger
from storyfactory.services.channel_service import (
    ensure_default_channel,
    get_channel,
    list_channels,
)
from storyfactory.pipeline.topic_selector import select_daily_topic
from storyfactory.pipeline.story_generator import generate_short_stories, generate_long_form_stories
from storyfactory.services.policy_checker import check_story_policy, rewrite_flagged_story
from storyfactory.services.tts_service import generate_tts
from storyfactory.services.caption_service import generate_captions
from storyfactory.services.asset_collector import collect_story_relevant_assets, download_assets
from storyfactory.services.renderer import render_short, render_long_form

console = Console()
log = get_logger("daily_pipeline")


def run_daily_pipeline(channel_ref: str | None = None, dry_run: bool = False):
    """Run the daily pipeline for a single channel (default channel if omitted)."""
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
        with use_channel(session, channel) as ctx:
            _run_for_channel(session, channel, ctx, settings)
    finally:
        session.close()


def run_daily_pipeline_all_active(dry_run: bool = False):
    """Run the daily pipeline sequentially for every active channel.

    Continues to the next channel if one fails (quota-aware, fail-isolated).
    """
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

    if not channels:
        console.print("[yellow]No active channels found.[/yellow]")
        return

    console.print(f"[bold blue]Running daily pipeline for {len(channels)} active channel(s)[/bold blue]")
    results: list[tuple[str, str]] = []
    for channel in channels:
        console.print(f"\n[bold cyan]══ Channel: {channel.name} ({channel.slug}) ══[/bold cyan]")
        try:
            run_daily_pipeline(channel_ref=channel.id, dry_run=dry_run)
            results.append((channel.slug, "ok"))
        except Exception as e:  # isolate per-channel failures
            log.error("channel_pipeline_failed", channel=channel.slug, error=str(e))
            console.print(f"[red]Channel {channel.slug} failed: {e}[/red]")
            results.append((channel.slug, f"failed: {e}"))

    console.print("\n[bold green]═══ All-channel run summary ═══[/bold green]")
    for slug, status in results:
        console.print(f"  {slug}: {status}")


def _run_for_channel(session, channel: Channel, ctx, settings):
    """Execute the full pipeline for one channel within an active context."""
    # Check for missing API keys (skip in dry-run)
    missing = settings.get_missing_keys()
    if missing and not settings.dry_run:
        console.print(f"[bold red]Missing API keys: {', '.join(missing)}[/bold red]")
        console.print("Run with --dry-run to test without API keys, or configure them in .env")
        return

    try:
        # ==========================================
        # Step 1: Create daily batch (scoped to channel)
        # ==========================================
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        existing = (
            session.query(DailyBatch)
            .filter_by(date=today, channel_id=channel.id)
            .first()
        )
        if existing and existing.status == "completed":
            console.print(
                f"[yellow]Daily batch already completed for {today} on '{channel.slug}'[/yellow]"
            )
            return

        batch = DailyBatch(
            id=str(uuid.uuid4()),
            channel_id=channel.id,
            date=today,
            category="",
            status="pending",
        )
        session.add(batch)
        session.commit()
        console.print(f"[green]✓ Batch created: {batch.id[:8]} (channel: {channel.slug})[/green]")

        # ==========================================
        # Step 2: Select topic (channel-scoped)
        # ==========================================
        category = select_daily_topic(channel_id=channel.id)
        batch.category = category
        batch.status = "topic_selected"
        session.commit()
        console.print(f"[green]✓ Topic selected: {category}[/green]")

        # ==========================================
        # Step 3: Generate short stories
        # ==========================================
        shorts_count = random.randint(
            int(effective_config("shorts_per_day_min", 3)),
            int(effective_config("shorts_per_day_max", 5)),
        )
        console.print(f"[blue]Generating {shorts_count} short stories...[/blue]")
        short_stories = generate_short_stories(batch, shorts_count)
        console.print(f"[green]✓ Generated {len(short_stories)} short stories[/green]")

        # ==========================================
        # Step 4: Generate long-form extra stories
        # ==========================================
        long_form_count = random.randint(2, 5)
        console.print(f"[blue]Generating {long_form_count} long-form stories...[/blue]")
        long_stories = generate_long_form_stories(batch, long_form_count)
        console.print(f"[green]✓ Generated {len(long_stories)} long-form stories[/green]")

        all_stories = short_stories + long_stories
        batch.status = "stories_generated"
        batch.shorts_count = len(short_stories)
        batch.long_form_count = len(long_stories)
        session.commit()

        # ==========================================
        # Step 5: Policy check
        # ==========================================
        console.print("[blue]Running policy checks...[/blue]")
        fail_safe = bool(effective_config("fail_safe_on_policy_flag", True))
        approved_stories = []
        for story in all_stories:
            result = check_story_policy(story)

            if result["action"] == "block":
                console.print(f"  [red]✗ BLOCKED: {story.title[:50]}[/red]")
                story.policy_status = "blocked"
                if fail_safe:
                    continue
            elif result["action"] == "rewrite":
                console.print(f"  [yellow]⟳ REWRITING: {story.title[:50]}[/yellow]")
                rewritten = rewrite_flagged_story(story, result["flags"])
                if rewritten:
                    story.title = rewritten.get("title", story.title)
                    story.hook = rewritten.get("hook", story.hook)
                    story.body = rewritten.get("body", story.body)
                    story.comment_bait = rewritten.get("comment_bait", story.comment_bait)
                    story.word_count = rewritten.get("word_count", story.word_count)
                    story.policy_status = "rewritten"
                    approved_stories.append(story)
                else:
                    story.policy_status = "blocked"
            else:
                story.policy_status = "clean"
                approved_stories.append(story)

        session.commit()
        batch.status = "policy_checked"
        session.commit()
        console.print(f"[green]✓ {len(approved_stories)}/{len(all_stories)} stories approved[/green]")

        if not approved_stories:
            batch.status = "failed"
            batch.error = "No stories passed policy checks"
            session.commit()
            console.print("[red]No stories passed policy checks. Batch failed.[/red]")
            return

        # Split approved stories
        approved_shorts = [s for s in approved_stories if s.type == "short"]
        approved_long = [s for s in approved_stories if s.type == "long_form_extra"]

        # ==========================================
        # Step 6: Generate TTS
        # ==========================================
        console.print("[blue]Generating TTS audio...[/blue]")
        tts_jobs = {}
        for story in approved_stories:
            tts_job = generate_tts(story)
            tts_jobs[story.id] = tts_job
            status = "✓" if tts_job.status == "completed" else "✗"
            console.print(f"  [{('green' if tts_job.status == 'completed' else 'red')}]{status} TTS: {story.title[:40]}[/]")

        batch.status = "tts_complete"
        session.commit()

        # ==========================================
        # Step 7: Generate captions
        # ==========================================
        console.print("[blue]Generating captions...[/blue]")
        caption_style = effective_config("caption_style", "word_highlight")
        caption_jobs = {}
        for story in approved_stories:
            tts_job = tts_jobs.get(story.id)
            if tts_job and tts_job.status == "completed":
                style = caption_style if story.type == "short" else "sentence"
                caption_job = generate_captions(story, tts_job, style=style)
                caption_jobs[story.id] = caption_job

        batch.status = "captions_complete"
        session.commit()
        console.print(f"[green]✓ Generated {len(caption_jobs)} caption files[/green]")

        # ==========================================
        # Step 8: Collect background assets PER STORY
        # ==========================================
        console.print("[blue]Collecting story-relevant background assets...[/blue]")
        story_assets = {}  # story_id -> list[Asset]

        for story in approved_stories:
            console.print(f"  [blue]Finding backgrounds for: {story.title[:40]}...[/blue]")
            assets = collect_story_relevant_assets(story, count=5)
            console.print(f"  [blue]Downloading {len(assets)} assets...[/blue]")
            assets = download_assets(assets)
            story_assets[story.id] = assets
            console.print(
                f"  [green]✓ {len(assets)} assets for: {story.title[:40]}[/green]"
            )

        batch.status = "assets_collected"
        session.commit()
        console.print(
            f"[green]✓ Collected story-relevant assets for "
            f"{len(story_assets)} stories[/green]"
        )

        # ==========================================
        # Step 9: Render Shorts
        # ==========================================
        console.print("[blue]Rendering Shorts...[/blue]")
        short_render_jobs = []
        for story in approved_shorts:
            tts_job = tts_jobs.get(story.id)
            caption_job = caption_jobs.get(story.id)
            assets = story_assets.get(story.id, [])

            if tts_job and tts_job.status == "completed":
                render_job = render_short(
                    story=story,
                    tts_job=tts_job,
                    caption_job=caption_job,
                    assets=assets,
                    batch_id=batch.id,
                )
                short_render_jobs.append(render_job)
                console.print(f"  [green]✓ Rendered Short: {story.title[:40]}[/green]")

        # ==========================================
        # Step 10: Render long-form
        # ==========================================
        console.print("[blue]Rendering long-form video...[/blue]")
        all_for_longform = approved_shorts + approved_long
        all_tts = [tts_jobs.get(s.id) for s in all_for_longform if tts_jobs.get(s.id)]
        all_captions = [caption_jobs.get(s.id) for s in all_for_longform if caption_jobs.get(s.id)]

        if all_tts:
            long_form_job = render_long_form(
                stories=all_for_longform,
                tts_jobs=all_tts,
                caption_jobs=all_captions,
                assets_map=story_assets,
                batch_id=batch.id,
            )
            console.print(f"[green]✓ Long-form video rendered[/green]")
        else:
            long_form_job = None
            console.print("[yellow]⊘ No TTS available for long-form render[/yellow]")

        batch.status = "rendered"
        session.commit()

        # ==========================================
        # Summary
        # ==========================================
        console.print("\n[bold green]═══ Daily Pipeline Complete ═══[/bold green]")
        console.print(f"  Channel: {channel.name} ({channel.slug})")
        console.print(f"  Date: {today}")
        console.print(f"  Category: {category}")
        console.print(f"  Shorts rendered: {len(short_render_jobs)}")
        console.print(f"  Long-form rendered: {'Yes' if long_form_job else 'No'}")
        console.print(f"  Batch ID: {batch.id}")

        if settings.dry_run:
            console.print("\n[yellow]⚠ DRY RUN MODE - No API calls or uploads were made[/yellow]")
        else:
            console.print("\n[blue]Run 'npm run worker:upload' to upload to YouTube[/blue]")

        batch.status = "completed"
        batch.completed_at = datetime.now(timezone.utc)
        session.commit()

    except Exception as e:
        log.error("daily_pipeline_failed", error=str(e))
        console.print(f"[bold red]Pipeline failed: {e}[/bold red]")

        try:
            batch.status = "failed"
            batch.error = str(e)
            session.commit()
        except Exception:
            pass

        raise
