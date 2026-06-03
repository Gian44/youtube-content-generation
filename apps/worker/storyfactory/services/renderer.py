"""Video rendering service using FFmpeg.

Key design principle: Audio (TTS narration) is the master clock.
Background videos are looped/concatenated to match the audio duration.
The story is never cut short — the video length equals the narration length.
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from storyfactory.channel_context import get_current_channel_id
from storyfactory.config import get_settings
from storyfactory.db.engine import get_session
from storyfactory.db.models import RenderJob, Story, Asset, TTSJob, CaptionJob
from storyfactory.logger import get_logger

log = get_logger("renderer")

VIDEO_FORMATS = {
    "short": {"width": 1080, "height": 1920, "fps": 30},
    "long_form": {"width": 1920, "height": 1080, "fps": 30},
}


def render_short(
    story: Story,
    tts_job: TTSJob,
    caption_job: CaptionJob,
    assets: list[Asset] | Asset | None,
    batch_id: str,
    output_dir: str = "./data/renders",
) -> RenderJob:
    """Render a YouTube Short video.

    Composites:
    - Background footage (looped to match TTS audio duration)
    - TTS audio (master clock — determines video length)
    - Word-by-word highlighted captions (ASS subtitles)
    """
    settings = get_settings()
    session = get_session()

    # Normalize assets to a list
    if assets is None:
        assets_list = []
    elif isinstance(assets, list):
        assets_list = assets
    else:
        assets_list = [assets]

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir, f"short_{story.id[:8]}.mp4")
    thumbnail_path = os.path.join(output_dir, f"thumb_{story.id[:8]}.jpg")

    fmt = VIDEO_FORMATS["short"]

    render_job = RenderJob(
        id=str(uuid.uuid4()),
        batch_id=batch_id,
        channel_id=get_current_channel_id(),
        type="short",
        status="processing",
        story_ids=[story.id],
        asset_ids=[a.id for a in assets_list],
        width=fmt["width"],
        height=fmt["height"],
        fps=fmt["fps"],
        render_started_at=datetime.now(timezone.utc),
        render_config={
            "voice_persona": story.voice_persona,
            "caption_style": caption_job.style if caption_job else "word_highlight",
        },
    )

    try:
        if settings.dry_run:
            # Create a placeholder render
            _create_dry_run_render(output_path, fmt)
            render_job.output_path = output_path
            render_job.thumbnail_path = thumbnail_path
            render_job.duration_seconds = tts_job.duration_seconds or 45.0
            render_job.status = "completed"
            render_job.render_completed_at = datetime.now(timezone.utc)
            log.info("dry_run_render_short", story_id=story.id)
        else:
            # Get background paths from assets
            bg_paths = _get_valid_background_paths(assets_list)

            _ffmpeg_render_short(
                background_paths=bg_paths,
                audio_path=tts_job.output_path,
                caption_path=caption_job.output_path if caption_job else None,
                output_path=output_path,
                thumbnail_path=thumbnail_path,
                width=fmt["width"],
                height=fmt["height"],
                fps=fmt["fps"],
            )
            render_job.output_path = output_path
            render_job.thumbnail_path = thumbnail_path
            render_job.duration_seconds = _get_video_duration(output_path)
            render_job.status = "completed"
            render_job.render_completed_at = datetime.now(timezone.utc)

    except Exception as e:
        render_job.status = "failed"
        render_job.error = str(e)
        log.error("render_short_failed", story_id=story.id, error=str(e))

    session.add(render_job)
    session.commit()
    session.close()
    return render_job


def render_long_form(
    stories: list[Story],
    tts_jobs: list[TTSJob],
    caption_jobs: list[CaptionJob],
    assets_map: dict[str, list[Asset]] | list[Asset],
    batch_id: str,
    output_dir: str = "./data/renders",
) -> RenderJob:
    """Render a long-form YouTube video.

    Composites:
    - Title cards between stories
    - Background footage (looped per story segment to match TTS)
    - TTS audio for each story
    - Sentence captions
    - Chapter markers

    Args:
        assets_map: Either a dict mapping story_id -> list of assets,
                    or a flat list of assets (legacy mode, distributed round-robin)
    """
    settings = get_settings()
    session = get_session()

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir, f"longform_{batch_id[:8]}.mp4")
    thumbnail_path = os.path.join(output_dir, f"thumb_longform_{batch_id[:8]}.jpg")

    fmt = VIDEO_FORMATS["long_form"]

    # Collect all asset IDs
    all_asset_ids = []
    if isinstance(assets_map, dict):
        for asset_list in assets_map.values():
            all_asset_ids.extend([a.id for a in asset_list])
    elif isinstance(assets_map, list):
        all_asset_ids = [a.id for a in assets_map]

    render_job = RenderJob(
        id=str(uuid.uuid4()),
        batch_id=batch_id,
        channel_id=get_current_channel_id(),
        type="long_form",
        status="processing",
        story_ids=[s.id for s in stories],
        asset_ids=all_asset_ids,
        width=fmt["width"],
        height=fmt["height"],
        fps=fmt["fps"],
        render_started_at=datetime.now(timezone.utc),
        render_config={
            "story_count": len(stories),
            "chapter_markers": True,
        },
    )

    try:
        if settings.dry_run:
            _create_dry_run_render(output_path, fmt)
            total_duration = sum(
                t.duration_seconds or 45.0 for t in tts_jobs
            )
            render_job.output_path = output_path
            render_job.thumbnail_path = thumbnail_path
            render_job.duration_seconds = total_duration + len(stories) * 5  # +5s per title card
            render_job.status = "completed"
            render_job.render_completed_at = datetime.now(timezone.utc)
            log.info("dry_run_render_longform", batch_id=batch_id, stories=len(stories))
        else:
            _ffmpeg_render_long_form(
                stories=stories,
                tts_jobs=tts_jobs,
                caption_jobs=caption_jobs,
                assets_map=assets_map,
                output_path=output_path,
                thumbnail_path=thumbnail_path,
                width=fmt["width"],
                height=fmt["height"],
                fps=fmt["fps"],
            )
            render_job.output_path = output_path
            render_job.thumbnail_path = thumbnail_path
            render_job.duration_seconds = _get_video_duration(output_path)
            render_job.status = "completed"
            render_job.render_completed_at = datetime.now(timezone.utc)

    except Exception as e:
        render_job.status = "failed"
        render_job.error = str(e)
        log.error("render_longform_failed", batch_id=batch_id, error=str(e))

    session.add(render_job)
    session.commit()
    session.close()
    return render_job


# ============================================================
# FFmpeg render functions — Audio is the master clock
# ============================================================


def _ffmpeg_render_short(
    background_paths: list[str],
    audio_path: str | None,
    caption_path: str | None,
    output_path: str,
    thumbnail_path: str,
    width: int,
    height: int,
    fps: int,
):
    """Render a Short using FFmpeg.

    The TTS audio duration drives the video length. Background footage
    is looped (-stream_loop -1) to fill the entire audio duration.
    """
    import subprocess

    # Get audio duration — this is the master duration for the entire video
    audio_duration = None
    if audio_path and os.path.exists(audio_path):
        audio_duration = _get_audio_duration_ffprobe(audio_path)

    filter_complex_parts = []
    inputs = []
    input_idx = 0

    # Step 1: Prepare background video with looping
    bg_path = _prepare_background(background_paths, width, height, fps, audio_duration)

    if bg_path and os.path.exists(bg_path):
        # Loop the background video indefinitely; we'll trim with -t later
        inputs.extend(["-stream_loop", "-1", "-i", bg_path])
        filter_complex_parts.append(
            f"[{input_idx}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},setsar=1[bg_video]"
        )
        input_idx += 1
    else:
        # Generate a dark gradient background
        dur = audio_duration or 60
        inputs.extend([
            "-f", "lavfi",
            "-i", f"color=c=0x1a1a2e:s={width}x{height}:d={dur}:r={fps}"
        ])
        filter_complex_parts.append(f"[{input_idx}:v]fps={fps}[bg_video]")
        input_idx += 1

    # Step 2: Apply captions via ASS subtitles
    if caption_path and os.path.exists(caption_path):
        safe_caption_path = caption_path.replace("\\", "/").replace(":", "\\:")
        filter_complex_parts.append(f"[bg_video]ass={safe_caption_path}[bg]")
    else:
        filter_complex_parts.append("[bg_video]null[bg]")

    # Step 3: Add audio input
    if audio_path and os.path.exists(audio_path):
        inputs.extend(["-i", audio_path])
        audio_idx = input_idx
        input_idx += 1
    else:
        audio_idx = None

    # Step 4: Build final FFmpeg command
    cmd = ["ffmpeg", "-y"]
    cmd.extend(inputs)

    filter_str = ";".join(filter_complex_parts)
    if filter_str:
        cmd.extend(["-filter_complex", filter_str])
        cmd.extend(["-map", "[bg]"])
    else:
        cmd.extend(["-map", "0:v"])

    if audio_idx is not None:
        cmd.extend(["-map", f"{audio_idx}:a"])

    # Use audio duration as the output duration — NO -shortest flag
    if audio_duration and audio_duration > 0:
        cmd.extend(["-t", str(audio_duration)])

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ])

    log.info("ffmpeg_render_short", cmd=" ".join(cmd[:30]))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg render failed: {result.stderr[-500:]}")

    # Extract thumbnail
    _extract_thumbnail(output_path, thumbnail_path, time="00:00:01")


def _ffmpeg_render_long_form(
    stories: list,
    tts_jobs: list,
    caption_jobs: list,
    assets_map,
    output_path: str,
    thumbnail_path: str,
    width: int,
    height: int,
    fps: int,
):
    """Render a long-form video by concatenating stories with title cards.

    Each story segment uses its own background footage, looped to match
    that story's TTS audio duration.
    """
    import subprocess

    # Create individual segments, then concatenate
    segments = []
    temp_dir = Path("./data/tmp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    for i, (story, tts_job) in enumerate(zip(stories, tts_jobs)):
        # Create title card
        title_path = temp_dir / f"title_{i}.mp4"
        _create_title_card(
            text=story.title,
            story_num=i + 1,
            total=len(stories),
            output_path=str(title_path),
            width=width,
            height=height,
            fps=fps,
            duration=4,
        )
        segments.append(str(title_path))

        # Get assets for this story
        if isinstance(assets_map, dict):
            story_assets = assets_map.get(story.id, [])
        elif isinstance(assets_map, list) and assets_map:
            story_assets = [assets_map[i % len(assets_map)]]
        else:
            story_assets = []

        bg_paths = _get_valid_background_paths(story_assets)

        # Render story segment
        caption_job = caption_jobs[i] if i < len(caption_jobs) else None

        segment_path = temp_dir / f"segment_{i}.mp4"
        _render_story_segment(
            audio_path=tts_job.output_path,
            caption_path=caption_job.output_path if caption_job else None,
            background_paths=bg_paths,
            output_path=str(segment_path),
            width=width,
            height=height,
            fps=fps,
        )
        segments.append(str(segment_path))

    # Create concat file
    concat_file = temp_dir / "concat.txt"
    with open(concat_file, "w") as f:
        for seg in segments:
            f.write(f"file '{os.path.abspath(seg)}'\n")

    # Concatenate
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-500:]}")

    _extract_thumbnail(output_path, thumbnail_path, time="00:00:05")


def _render_story_segment(
    audio_path: str | None,
    caption_path: str | None,
    background_paths: list[str],
    output_path: str,
    width: int,
    height: int,
    fps: int,
):
    """Render an individual story segment for long-form composition.

    Audio is the master clock — background is looped to fill the full
    narration duration.
    """
    import subprocess

    # Get audio duration — this determines the segment length
    audio_duration = None
    if audio_path and os.path.exists(audio_path):
        audio_duration = _get_audio_duration_ffprobe(audio_path)

    cmd = ["ffmpeg", "-y"]
    filter_parts = []

    # Prepare background with looping
    bg_path = _prepare_background(background_paths, width, height, fps, audio_duration)

    if bg_path and os.path.exists(bg_path):
        # Loop background video indefinitely
        cmd.extend(["-stream_loop", "-1", "-i", bg_path])
        filter_parts.append(
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},setsar=1[bg_video]"
        )
    else:
        dur = audio_duration or 300
        cmd.extend([
            "-f", "lavfi",
            "-i", f"color=c=0x16213e:s={width}x{height}:d={dur}:r={fps}"
        ])
        filter_parts.append(f"[0:v]fps={fps}[bg_video]")

    # Audio
    if audio_path and os.path.exists(audio_path):
        cmd.extend(["-i", audio_path])
        has_audio = True
    else:
        has_audio = False

    # Captions inside filter_complex
    if caption_path and os.path.exists(caption_path):
        safe_caption_path = caption_path.replace("\\", "/").replace(":", "\\:")
        filter_parts.append(f"[bg_video]ass={safe_caption_path}[bg]")
    else:
        filter_parts.append("[bg_video]null[bg]")

    filter_str = ";".join(filter_parts)
    cmd.extend(["-filter_complex", filter_str])
    cmd.extend(["-map", "[bg]"])

    if has_audio:
        cmd.extend(["-map", "1:a"])

    # Use audio duration as the output limit — NO -shortest flag
    if audio_duration and audio_duration > 0:
        cmd.extend(["-t", str(audio_duration)])

    cmd.extend([
        "-c:v", "libx264", "-preset", "fast", "-crf", "25",
        "-c:a", "aac", "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-pix_fmt", "yuv420p",
        output_path,
    ])

    log.info("ffmpeg_render_segment", cmd=" ".join(cmd[:30]))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"Segment render failed: {result.stderr[-500:]}")


# ============================================================
# Helper functions
# ============================================================


def _get_valid_background_paths(assets: list[Asset]) -> list[str]:
    """Extract valid local file paths from a list of assets."""
    paths = []
    for asset in assets:
        if asset and asset.local_path and os.path.exists(asset.local_path):
            paths.append(asset.local_path)
    return paths


def _prepare_background(
    background_paths: list[str],
    width: int,
    height: int,
    fps: int,
    target_duration: float | None = None,
) -> str | None:
    """Prepare a single background video from multiple clips.

    If multiple background clips are provided, concatenate them into one
    file. The result will be looped by FFmpeg (-stream_loop -1) during
    rendering to fill the audio duration.

    Returns:
        Path to the prepared background video, or None if no clips available.
    """
    if not background_paths:
        return None

    # Filter to only existing files
    valid_paths = [p for p in background_paths if os.path.exists(p)]
    if not valid_paths:
        return None

    # Single clip — use directly (will be looped by FFmpeg)
    if len(valid_paths) == 1:
        return valid_paths[0]

    # Multiple clips — concatenate them into one background
    return _concat_backgrounds(valid_paths)


def _concat_backgrounds(paths: list[str]) -> str | None:
    """Concatenate multiple background video clips into one file.

    This creates a single video from multiple clips. The caller will
    then loop this concatenated video using -stream_loop -1 to fill
    the audio duration.
    """
    import subprocess

    if not paths:
        return None

    temp_dir = Path("./data/tmp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    concat_list = temp_dir / f"bg_concat_{uuid.uuid4().hex[:8]}.txt"
    output_path = str(temp_dir / f"bg_combined_{uuid.uuid4().hex[:8]}.mp4")

    with open(concat_list, "w") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        output_path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(output_path):
            log.info("backgrounds_concatenated", count=len(paths), output=output_path)
            return output_path
        else:
            log.warning("background_concat_failed", stderr=result.stderr[-300:])
            # Fallback: use the first clip
            return paths[0]
    except Exception as e:
        log.warning("background_concat_error", error=str(e))
        return paths[0]


def _get_audio_duration_ffprobe(audio_path: str) -> float | None:
    """Get audio duration in seconds using ffprobe.

    This is the master clock for video rendering — the audio duration
    determines the final video length.
    """
    import subprocess

    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            audio_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            duration = float(result.stdout.strip())
            log.info("audio_duration_detected", path=audio_path, duration=duration)
            return duration
    except Exception as e:
        log.warning("ffprobe_duration_failed", path=audio_path, error=str(e))

    return None


def _create_title_card(
    text: str,
    story_num: int,
    total: int,
    output_path: str,
    width: int,
    height: int,
    fps: int,
    duration: int = 4,
):
    """Create a title card video segment."""
    import subprocess

    # Escape text for FFmpeg drawtext
    safe_text = text.replace("'", "\\'").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x0f3460:s={width}x{height}:d={duration}:r={fps}",
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
        "-vf", (
            f"drawtext=text='Story {story_num} of {total}':"
            f"fontcolor=white:fontsize=36:x=(w-text_w)/2:y=h/2-80,"
            f"drawtext=text='{safe_text[:60]}':"
            f"fontcolor=white:fontsize=28:x=(w-text_w)/2:y=h/2+20"
        ),
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-c:a", "aac",
        "-ar", "44100",
        "-ac", "2",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_path,
    ]

    subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _extract_thumbnail(video_path: str, thumbnail_path: str, time: str = "00:00:01"):
    """Extract a thumbnail frame from a video."""
    import subprocess

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", time,
        "-vframes", "1",
        "-q:v", "2",
        thumbnail_path,
    ]

    subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    import subprocess

    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _create_dry_run_render(output_path: str, fmt: dict):
    """Create a placeholder render file for dry-run mode."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(b"DRY_RUN_VIDEO_PLACEHOLDER")

    # Also create a placeholder thumbnail
    thumb_path = output_path.replace(".mp4", "_thumb.jpg")
    with open(thumb_path, "wb") as f:
        f.write(b"DRY_RUN_THUMBNAIL_PLACEHOLDER")
