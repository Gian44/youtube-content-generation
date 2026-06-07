"""Recap Shorts pipeline (CinybeShorts) — pipeline_mode="recap_shorts".

From ONE user-supplied episode file, produce many Shorts proportional to its
runtime. One episode is processed at a time per active series; when every
planned window has a :class:`RecapSegment` the episode is marked completed and
the next pending episode advances on the following run.

Each Short reuses the shared content path: a transformative recap narration is
written by the LLM, voiced (TTS), captioned, composited over the cut source clip
(or stock b-roll), rendered as a Short, and uploaded by the existing upload
pipeline. The :class:`RecapSegment` ledger guarantees a window is never re-cut.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

from rich.console import Console

from storyfactory.channel_context import effective_config
from storyfactory.content_defaults import RECAP_DEFAULTS
from storyfactory.db.models import Asset, DailyBatch, Story
from storyfactory.logger import get_logger
from storyfactory.pipeline.story_generator import _resolve_prompt
from storyfactory.services import (
    episode_transcriber,
    music_service,
    recap_service,
    scene_planner,
)
from storyfactory.services.ai_provider import AIProvider, generate_text
from storyfactory.services.asset_collector import collect_topic_assets, download_assets
from storyfactory.services.caption_service import (
    build_dialogue_captions,
    generate_captions,
    transcribe_dialogue,
)
from storyfactory.services.policy_checker import check_story_policy, rewrite_flagged_story
from storyfactory.services.renderer import render_recap_short, render_short
from storyfactory.services.tts_service import generate_tts

console = Console()
log = get_logger("recap_shorts_pipeline")


def run_recap_shorts_for_channel(session, channel, ctx, settings) -> None:
    """Generate the next batch of recap Shorts for a channel (runs in its context)."""
    cfg = {**RECAP_DEFAULTS, **(effective_config("recap", {}) or {})}
    inbox = cfg.get("inbox_path") or RECAP_DEFAULTS["inbox_path"]

    # 1. Ingest any new files dropped into the inbox.
    scan = recap_service.scan_inbox(session, channel, inbox, commit=True)
    if scan.get("error"):
        console.print(f"[yellow]⚠ {scan['error']}[/yellow]")
    else:
        console.print(
            f"[green]✓ Inbox scan: {scan['new_episodes']} new episode(s) "
            f"from {scan['scanned']} file(s) in {scan['inbox']}[/green]"
        )

    # 2. Resolve which series + episode to work on now.
    series = recap_service.get_active_series(session, channel, cfg.get("active_series_slug"))
    if series is None:
        console.print(
            "[yellow]No series yet. Drop episode files into the inbox "
            "(e.g. 'Breaking Bad S01E03.mkv') or run `recap series create`.[/yellow]"
        )
        return

    episode = recap_service.get_active_episode(session, series)
    if episode is None:
        console.print(f"[green]All episodes completed for series '{series.slug}'. Nothing to do.[/green]")
        return

    if not _ensure_duration(session, episode, settings):
        return

    # 3. Plan deterministic windows; skip the ones already in the ledger.
    #    scene mode → content-aware montage windows (one per integral scene);
    #    time mode → fixed back-to-back windows. scene_by_start maps a window to
    #    its frozen MediaScene; episode_words is the cached transcript (subtitles).
    planned, scene_by_start, episode_words = _plan_windows(
        session, episode, channel, cfg, settings
    )
    pending = recap_service.pending_windows(
        planned, recap_service.existing_segment_windows(session, episode)
    )
    if not pending:
        recap_service.mark_episode(session, episode, "completed")
        console.print(
            f"[green]✓ Episode '{_episode_label(episode)}' exhausted "
            f"({len(planned)} Shorts). Re-run to advance to the next episode.[/green]"
        )
        return

    # Pre-flight: ensure the recap prompt exists before marking the episode
    # in_progress and ledgering windows — a missing prompt would otherwise be
    # caught per-segment and ledger every window as "failed", prematurely
    # completing the episode.
    if _resolve_prompt(session, "recap_short_script", channel.id) is None:
        console.print("[red]recap_short_script prompt not found. Run: npm run seed[/red]")
        return

    batch_windows = pending[: int(cfg.get("max_shorts_per_run", 5))]
    recap_service.mark_episode(session, episode, "in_progress")
    console.print(
        f"[bold blue]Series '{series.slug}' · {_episode_label(episode)} · "
        f"{len(planned) - len(pending)}/{len(planned)} Shorts done · "
        f"rendering {len(batch_windows)} this run[/bold blue]"
    )

    # 4. Daily batch (scoped to the channel; recap is NOT one-short-per-day).
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    batch = DailyBatch(
        id=str(uuid.uuid4()),
        channel_id=channel.id,
        date=today,
        category=series.slug[:50],
        status="stories_generated",
    )
    session.add(batch)
    session.commit()

    caption_style = effective_config("caption_style", "word_highlight")
    fail_safe = bool(effective_config("fail_safe_on_policy_flag", True))
    rendered = 0

    for (start, end) in batch_windows:
        seg_index = planned.index((start, end)) + 1
        console.print(
            f"  [blue]Segment {seg_index}/{len(planned)} "
            f"[{_fmt(start)}–{_fmt(end)}]...[/blue]"
        )
        try:
            ok = _produce_short(
                session, channel, series, episode, batch, cfg,
                start, end, seg_index, len(planned),
                caption_style, fail_safe, settings,
                scene=scene_by_start.get(start),
                episode_words=episode_words,
            )
            if ok:
                rendered += 1
        except Exception as exc:  # isolate a bad segment; keep the run going
            log.error("recap_segment_failed", episode_id=episode.id, start=start, error=str(exc))
            console.print(f"  [red]✗ Segment {seg_index} failed: {exc}[/red]")
            # A failed commit inside the segment leaves the session in an invalid
            # (rolled-back) state — roll it back before reusing it for the ledger.
            try:
                session.rollback()
                recap_service.record_segment(
                    session, episode, channel_id=channel.id,
                    start_seconds=start, end_seconds=end, status="failed",
                )
            except Exception as ledger_exc:
                log.error("recap_ledger_failed", start=start, error=str(ledger_exc))
                session.rollback()

    # 5. Finalize the batch and advance the episode if it is now exhausted.
    try:
        batch.shorts_count = rendered
        batch.long_form_count = 0
        batch.status = "completed"
        batch.completed_at = datetime.now(timezone.utc)
        session.commit()
    except Exception as exc:
        session.rollback()
        log.error("recap_batch_finalize_failed", batch_id=batch.id, error=str(exc))

    # Exhaustion is checked against the SAME planned windows used this run (scene
    # or time), not a re-derived time-based plan, so a scene-mode episode is only
    # marked complete once every integral scene has a ledger row.
    still_pending = recap_service.pending_windows(
        planned, recap_service.existing_segment_windows(session, episode)
    )
    if not still_pending:
        recap_service.mark_episode(session, episode, "completed")
        console.print(f"[green]✓ Episode '{_episode_label(episode)}' fully completed.[/green]")

    console.print("\n[bold green]═══ Recap Shorts Run Complete ═══[/bold green]")
    console.print(f"  Channel: {channel.name} ({channel.slug})")
    console.print(f"  Series: {series.title} · Episode: {_episode_label(episode)}")
    console.print(f"  Shorts rendered this run: {rendered}")
    if settings.dry_run:
        console.print("\n[yellow]⚠ DRY RUN MODE - no clips were cut and no uploads were made[/yellow]")


# ----------------------------------------------------------------------------
# Per-segment production
# ----------------------------------------------------------------------------

def _produce_short(
    session, channel, series, episode, batch, cfg,
    start, end, seg_index, total_segments,
    caption_style, fail_safe, settings,
    scene=None, episode_words=None,
) -> bool:
    """Produce, render, and ledger one recap Short. Returns True if rendered.

    ``scene`` (a frozen :class:`MediaScene`) selects the content-aware montage
    path — title/hook/cuts come from the plan, no per-segment LLM call. Without
    a scene the window is produced by the time-based recap path (original_audio
    or tts_narration per ``audio_mode``).
    """
    if scene is not None:
        story = _make_scene_story(session, channel, batch, series, episode, scene)
    else:
        story = _make_recap_story(
            session, channel, batch, series, episode,
            start, end, seg_index, total_segments, cfg, settings,
        )

    # Policy: a recap Short is transformative commentary over the user's own,
    # disclosed footage — the screened text is just descriptive metadata. So a
    # "block" SANITIZES the title/hook (rewrite, else a neutral fallback) rather
    # than dropping the Short and producing zero output. The footage itself
    # remains the owner's responsibility (and carries the recap disclosure).
    result = check_story_policy(story)
    if result["action"] in ("block", "rewrite"):
        rewritten = rewrite_flagged_story(story, result["flags"])
        if rewritten:
            story.title = rewritten.get("title", story.title)
            story.hook = rewritten.get("hook", story.hook)
            story.body = rewritten.get("body", story.body)
            story.policy_status = "rewritten"
        elif result["action"] == "block":
            # Couldn't rewrite a blocked story — neutralize the metadata and
            # proceed instead of silently producing nothing.
            story.title = f"{series.title} — {_episode_label(episode)} recap"[:200]
            story.hook = "A key moment, recapped."
            story.body = story.hook
            story.policy_status = "rewritten"
            log.warning(
                "recap_policy_block_neutralized",
                story_id=story.id, flags=result.get("flags"),
            )
            console.print("  [yellow]⚠ Policy flag on metadata; neutralized title/hook, continuing[/yellow]")
        else:
            story.policy_status = "clean"
    else:
        story.policy_status = "clean"
    session.commit()

    # Scene mode (CinybeShorts): edited montage of the scene's key beats, keeping
    # the original audio + a quiet music bed + dialogue subtitles. No AI narration.
    if scene is not None:
        return _produce_montage_short(
            session, channel, episode, batch, cfg, story, scene,
            episode_words or [], start, end, caption_style, settings,
        )

    # Original-audio mode (CinybeShorts): keep the clip's own audio, add a quiet
    # music bed and dialogue subtitles — no AI narration. The TTS path below is
    # only used by channels still configured for tts_narration.
    if cfg.get("audio_mode", "tts_narration") == "original_audio":
        return _produce_original_audio_short(
            session, channel, series, episode, batch, cfg, story,
            start, end, caption_style, settings,
        )

    tts_job = generate_tts(story)
    if tts_job.status != "completed":
        recap_service.record_segment(
            session, episode, channel_id=channel.id,
            start_seconds=start, end_seconds=end, story_id=story.id, status="failed",
        )
        console.print("  [red]✗ TTS failed; segment ledgered as failed[/red]")
        return False

    caption_job = generate_captions(story, tts_job, style=caption_style)
    assets = _segment_assets(session, channel, series, episode, story, cfg, start, end, settings)

    render_job = render_short(
        story=story, tts_job=tts_job, caption_job=caption_job,
        assets=assets, batch_id=batch.id,
    )
    recap_service.record_segment(
        session, episode, channel_id=channel.id,
        start_seconds=start, end_seconds=end,
        story_id=story.id, render_job_id=render_job.id, status="rendered",
    )
    console.print(f"  [green]✓ Rendered Short: {story.title[:48]}[/green]")
    return True


def _produce_original_audio_short(
    session, channel, series, episode, batch, cfg, story,
    start, end, caption_style, settings,
) -> bool:
    """Render one Short that keeps the clip's ORIGINAL audio (no AI narration).

    Cuts the clip with its audio, transcribes the real dialogue for subtitles,
    infers a scene-appropriate music mood, mixes a quiet CC music bed under the
    original audio, and renders. Subtitles and music both degrade gracefully:
    if either is unavailable the Short still renders (original audio only).
    """
    # 1. Cut the clip WITH its original audio (the clip is the master clock).
    clip_path = recap_service.cut_clip(
        episode.file_path, start, end - start, dry_run=settings.dry_run, keep_audio=True
    )
    clip_asset = Asset(
        id=str(uuid.uuid4()),
        channel_id=channel.id,
        provider="local",
        type="video",
        original_url=f"file://{clip_path}",
        creator="Source file",
        license="User-provided",
        attribution="User-supplied source footage (recap/commentary)",
        source_query=f"{series.slug} {int(start)}-{int(end)}",
        checksum="",
        local_path=clip_path,
        usage_count=1,
        policy_status="verified",
    )
    session.add(clip_asset)
    session.commit()

    # 2. Transcribe the clip's dialogue ONCE — shared by subtitles AND music
    #    mood, so mood works even if subtitles are disabled (best-effort).
    want_subtitles = cfg.get("subtitles_from_dialogue", True)
    want_music = cfg.get("music_enabled", True)
    words: list[dict] = []
    if not settings.dry_run and (want_subtitles or want_music):
        words = transcribe_dialogue(clip_path)

    # 3. Subtitles from the real dialogue.
    caption_job = None
    if not settings.dry_run and want_subtitles and words:
        caption_job = build_dialogue_captions(story, words, style=caption_style)
    caption_path = caption_job.output_path if caption_job else None

    # 4. Scene-mood music bed (mood derived from the same transcript).
    music_asset = None
    if not settings.dry_run and want_music:
        transcript = " ".join(w["word"] for w in words)
        mood = music_service.infer_music_mood(
            transcript, cfg.get("music_mood_fallback", "cinematic ambient")
        )
        music_asset = music_service.fetch_music_track(
            mood,
            min_duration=float(end - start),
            allow_noncommercial=bool(cfg.get("music_allow_noncommercial", False)),
        )
    music_path = music_asset.local_path if music_asset else None
    music_attribution = music_asset.attribution if music_asset else None

    # 5. Render: original clip audio + quiet music + burned dialogue subtitles.
    asset_ids = [clip_asset.id] + ([music_asset.id] if music_asset else [])
    render_job = render_recap_short(
        story=story,
        clip_path=clip_path,
        batch_id=batch.id,
        music_path=music_path,
        caption_path=caption_path,
        music_volume=float(cfg.get("music_volume", 0.10)),
        music_attribution=music_attribution,
        asset_ids=asset_ids,
    )

    # 6. Ledger the window with the ACTUAL render outcome so a failed FFmpeg
    #    render is not recorded as "rendered" (which would skip upload silently).
    if render_job.status == "completed":
        recap_service.record_segment(
            session, episode, channel_id=channel.id,
            start_seconds=start, end_seconds=end,
            story_id=story.id, render_job_id=render_job.id, status="rendered",
        )
        console.print(f"  [green]✓ Rendered original-audio Short: {story.title[:48]}[/green]")
        return True

    recap_service.record_segment(
        session, episode, channel_id=channel.id,
        start_seconds=start, end_seconds=end,
        story_id=story.id, render_job_id=render_job.id, status="failed",
    )
    console.print(f"  [red]✗ Render failed; segment ledgered as failed: {render_job.error}[/red]")
    return False


def _produce_montage_short(
    session, channel, episode, batch, cfg, story, scene,
    episode_words, start, end, caption_style, settings,
) -> bool:
    """Render one edited montage Short for a frozen integral scene.

    Stitches the scene's cut-list (original audio preserved), burns dialogue
    subtitles sliced from the cached episode transcript (no second Whisper pass),
    mixes a quiet scene-mood music bed, and renders. Subtitles and music both
    degrade gracefully. Ledgers the window by the scene's start so the dedupe
    ledger and the exhaustion check stay consistent with the plan.
    """
    cut_list = scene.cut_list or [[scene.start_seconds, scene.end_seconds]]

    # 1. Build the montage clip from the key-beat cuts (keeps original audio).
    clip_path = recap_service.build_montage_clip(
        episode.file_path, cut_list, dry_run=settings.dry_run
    )
    montage_seconds = sum(float(c[1]) - float(c[0]) for c in cut_list)
    clip_asset = Asset(
        id=str(uuid.uuid4()),
        channel_id=channel.id,
        provider="local",
        type="video",
        original_url=f"file://{clip_path}",
        creator="Source file",
        license="User-provided",
        attribution="User-supplied source footage (recap/commentary)",
        source_query=f"{episode.id} scene {scene.scene_index}",
        checksum="",
        local_path=clip_path,
        usage_count=1,
        policy_status="verified",
    )
    session.add(clip_asset)
    session.commit()

    # 2. Subtitles: rebase cached transcript words onto the montage timeline.
    caption_job = None
    if not settings.dry_run and cfg.get("subtitles_from_dialogue", True) and episode_words:
        montage_words = recap_service.rebase_words_to_montage(episode_words, cut_list)
        if montage_words:
            caption_job = build_dialogue_captions(story, montage_words, style=caption_style)
    caption_path = caption_job.output_path if caption_job else None

    # 3. Scene-mood music bed (mood is from the frozen plan; fallback otherwise).
    music_asset = None
    if not settings.dry_run and cfg.get("music_enabled", True):
        mood = scene.mood or cfg.get("music_mood_fallback", "cinematic ambient")
        music_asset = music_service.fetch_music_track(
            mood,
            min_duration=float(montage_seconds or (end - start)),
            allow_noncommercial=bool(cfg.get("music_allow_noncommercial", False)),
        )
    music_path = music_asset.local_path if music_asset else None
    music_attribution = music_asset.attribution if music_asset else None

    # 4. Render: montage video + original audio + quiet music + burned subtitles.
    asset_ids = [clip_asset.id] + ([music_asset.id] if music_asset else [])
    render_job = render_recap_short(
        story=story,
        clip_path=clip_path,
        batch_id=batch.id,
        music_path=music_path,
        caption_path=caption_path,
        music_volume=float(cfg.get("music_volume", 0.10)),
        music_attribution=music_attribution,
        asset_ids=asset_ids,
    )

    status = "rendered" if render_job.status == "completed" else "failed"
    recap_service.record_segment(
        session, episode, channel_id=channel.id,
        start_seconds=start, end_seconds=end,
        story_id=story.id, render_job_id=render_job.id, status=status,
    )
    if status == "rendered":
        console.print(f"  [green]✓ Rendered montage Short: {story.title[:48]}[/green]")
        return True
    console.print(f"  [red]✗ Montage render failed; ledgered failed: {render_job.error}[/red]")
    return False


def _make_scene_story(session, channel, batch, series, episode, scene) -> Story:
    """Persist a Story from a frozen MediaScene's planner metadata (no LLM call).

    The planner already wrote the curiosity title / hook / comment-bait / tags;
    they are carried onto the Story (tags into ``raw_output`` for the uploader's
    optimizer metadata builder) so the montage path needs no extra generation.
    """
    title = scene.title or f"{series.title} — {_episode_label(episode)}"
    hook = scene.hook or ""
    body = hook or title
    montage_seconds = sum(float(c[1]) - float(c[0]) for c in (scene.cut_list or []))
    content_hash = hashlib.sha256(
        f"{episode.id}:scene:{scene.start_seconds}:{title}".encode()
    ).hexdigest()
    story = Story(
        id=str(uuid.uuid4()),
        batch_id=batch.id,
        channel_id=channel.id,
        category=series.slug[:50],
        type="short",
        title=title[:200],
        hook=hook,
        body=body,
        comment_bait=scene.comment_bait or "",
        word_count=len((body or "").split()),
        estimated_duration_seconds=montage_seconds or 18.0,
        voice_persona="original_audio",
        originality_hash=content_hash,
        novelty_score=float(scene.importance or 0.0),
        prompt_used="scene_planner",
        raw_output=json.dumps({
            "tags": list(scene.tags or []),
            "mood": scene.mood,
            "importance": scene.importance,
            "cut_list": scene.cut_list,
            "reason": scene.reason,
        }),
        policy_status="pending",
        policy_flags=[],
        order_index=scene.scene_index,
    )
    session.add(story)
    session.commit()
    return story


def _segment_assets(session, channel, series, episode, story, cfg, start, end, settings) -> list[Asset]:
    """Background footage for the Short: the cut source clip, or stock b-roll."""
    footage_mode = cfg.get("footage_mode", "with_source_video")
    if footage_mode == "stock_metaphor":
        queries = [story.title, series.title]
        assets = collect_topic_assets(queries, count=4, asset_type="video")
        return download_assets(assets)

    clip_path = recap_service.cut_clip(
        episode.file_path, start, end - start, dry_run=settings.dry_run
    )
    asset = Asset(
        id=str(uuid.uuid4()),
        channel_id=channel.id,
        provider="local",
        type="video",
        original_url=f"file://{clip_path}",
        creator="Source file",
        license="User-provided",
        attribution="User-supplied source footage (recap/commentary)",
        source_query=f"{series.slug} {int(start)}-{int(end)}",
        checksum="",
        local_path=clip_path,
        usage_count=1,
        policy_status="verified",
    )
    session.add(asset)
    session.commit()
    return [asset]


def _make_recap_story(
    session, channel, batch, series, episode,
    start, end, seg_index, total_segments, cfg, settings,
) -> Story:
    """Generate the recap narration and persist a short Story for the segment."""
    template = _resolve_prompt(session, "recap_short_script", channel.id)
    if not template:
        raise RuntimeError("recap_short_script prompt not found. Run: npm run seed")

    persona = cfg.get("voice_persona", "dramatic")
    episode_label = _episode_label(episode)

    if settings.dry_run:
        data = _dry_run_recap(series, episode_label, seg_index, start, end)
    else:
        prompt = (
            template.template
            .replace("{{show_title}}", series.title)
            .replace("{{episode_label}}", episode_label)
            .replace("{{start_time}}", _fmt(start))
            .replace("{{end_time}}", _fmt(end))
            .replace("{{segment_index}}", str(seg_index))
            .replace("{{total_segments}}", str(total_segments))
            .replace("{{context}}", episode.title or "")
            .replace("{{persona}}", persona)
        )
        data = _generate_json(prompt)

    body = data.get("body", "") or ""
    # Salt the originality hash with the episode + window so each segment is
    # unique (a recap is intrinsically tied to its source span).
    content_hash = hashlib.sha256(f"{episode.id}:{start}:{body}".encode()).hexdigest()

    story = Story(
        id=str(uuid.uuid4()),
        batch_id=batch.id,
        channel_id=channel.id,
        category=series.slug[:50],
        type="short",
        title=(data.get("title") or f"{series.title} recap")[:200],
        hook=data.get("hook", "") or "",
        body=body,
        comment_bait=data.get("comment_bait", "") or "",
        word_count=int(data.get("word_count") or len(body.split())),
        estimated_duration_seconds=_estimate_duration(body),
        voice_persona=persona,
        originality_hash=content_hash,
        novelty_score=1.0,
        prompt_used="recap_short_script",
        raw_output=json.dumps(data),
        policy_status="pending",
        policy_flags=[],
        order_index=seg_index,
    )
    session.add(story)
    session.commit()
    return story


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _plan(episode, cfg) -> list[tuple[float, float]]:
    return recap_service.plan_segments(
        episode.duration_seconds,
        target_short_seconds=cfg.get("target_short_seconds", 52),
        overlap_seconds=cfg.get("overlap_seconds", 0),
        min_short_seconds=cfg.get("min_short_seconds", 40),
        min_count=cfg.get("min_shorts_per_episode", 1),
        max_count=cfg.get("max_shorts_per_episode", 60),
    )


def _plan_windows(session, episode, channel, cfg, settings):
    """Return ``(planned_windows, scene_by_start, episode_words)`` for the episode.

    scene mode → content-aware montage windows (one per integral scene), each
    mapped to its frozen :class:`MediaScene`, plus the cached transcript words for
    subtitles. Falls back to time-based windows when no transcript is available
    (dry-run / no OpenAI key). time mode → ``(time_windows, {}, [])``.
    """
    mode = cfg.get("segmentation_mode", "time")
    if mode == "scene":
        scenes = scene_planner.plan_episode_scenes(
            session, episode, channel, cfg, dry_run=settings.dry_run
        )
        if scenes:
            planned = [(round(s.start_seconds, 2), round(s.end_seconds, 2)) for s in scenes]
            scene_by_start = {round(s.start_seconds, 2): s for s in scenes}
            # Re-derive the transcript (cache-or-transcribe) rather than only
            # reading the cache: on a re-run with a frozen plan the planner does
            # NOT re-transcribe, so a missing cache file would silently drop
            # subtitles forever. transcribe_episode returns the cache when present
            # and [] in dry-run.
            words = episode_transcriber.transcribe_episode(
                episode.id, episode.file_path, episode.duration_seconds,
                dry_run=settings.dry_run,
            ) or []
            console.print(
                f"[bold blue]Scene plan: {len(planned)} integral scene(s) "
                f"→ montage Shorts[/bold blue]"
            )
            return planned, scene_by_start, words
        console.print(
            "[yellow]Scene plan unavailable (no transcript / dry-run); "
            "using time-based windows.[/yellow]"
        )
    return _plan(episode, cfg), {}, []


def _ensure_duration(session, episode, settings) -> bool:
    """Backfill a missing episode duration via ffprobe. Returns False to abort."""
    if episode.duration_seconds:
        return True
    duration = recap_service.probe_duration(episode.file_path)
    if duration:
        episode.duration_seconds = duration
        session.commit()
        return True
    if settings.dry_run:
        console.print(
            f"[yellow]⚠ No duration for {episode.file_path}; register it with "
            f"`recap episode register --duration <seconds>` for a real run.[/yellow]"
        )
        return False
    console.print(
        f"[red]Could not determine duration for {episode.file_path}. "
        f"Install FFmpeg or register the episode with --duration.[/red]"
    )
    return False


def _generate_json(prompt: str) -> dict:
    raw = generate_text(
        prompt=prompt,
        provider=AIProvider.OPENAI,
        temperature=0.8,
        max_tokens=400,
        response_format="json",
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        raise RuntimeError("Recap script LLM output was not valid JSON")


def _episode_label(episode) -> str:
    if episode.season_number and episode.episode_number:
        return f"S{episode.season_number:02d}E{episode.episode_number:02d}"
    if episode.part_index:
        return f"Part {episode.part_index}"
    return episode.title or "feature"


def _fmt(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def _estimate_duration(text: str) -> float:
    return (len(text.split()) / 150) * 60


def _dry_run_recap(series, episode_label: str, seg_index: int, start: float, end: float) -> dict:
    unique = uuid.uuid4().hex[:8]
    return {
        "title": f"[DRY RUN] {series.title} {episode_label} recap #{seg_index}",
        "hook": f"You won't believe what happens at {_fmt(start)} in {series.title}...",
        "body": (
            f"This is a dry-run recap narration ({unique}) for {series.title} "
            f"{episode_label}, segment {seg_index} covering {_fmt(start)} to {_fmt(end)}. "
            f"In a real run this would be transformative commentary written by the LLM, "
            f"describing the key moment of this span in an energetic voice and ending "
            f"with a call to action to watch the full breakdown."
        ),
        "comment_bait": "Did you catch this moment? Tell me in the comments!",
        "word_count": 48,
    }
