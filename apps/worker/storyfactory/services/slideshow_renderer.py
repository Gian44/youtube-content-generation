"""Image-slideshow renderer for Sleep On Facts (~3h) videos.

Sleep videos open directly on a slideshow of 100+ topic-matched stock images —
no blue title card, no captions, no music. The expensive part of a 3-hour 1080p
video is encoding, so the design is:

  1. Render each image into a short Ken Burns clip.
  2. Crossfade (``xfade``) the clips into ONE reel, encoded once. To keep the
     FFmpeg filter graph manageable at 100+ images, clips are combined in bounded
     batches (partial reels), then the partials are crossfaded together.
  3. Loop that reel under the narration with ``-stream_loop -1 ... -c:v copy``,
     trimmed to the audio duration — so the 3-hour final assembly does NOT
     re-encode video (only the once-built reel is encoded).

Shared low-level ffmpeg helpers are reused from :mod:`renderer`.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from storyfactory.channel_context import get_current_channel_id
from storyfactory.config import get_settings
from storyfactory.db.engine import get_session
from storyfactory.db.models import RenderJob
from storyfactory.logger import get_logger
from storyfactory.services.renderer import (
    _create_dry_run_render,
    _extract_thumbnail,
    _get_audio_duration_ffprobe,
    _get_valid_background_paths,
    _get_video_duration,
)

log = get_logger("slideshow_renderer")

SLEEP_VIDEO = {"width": 1920, "height": 1080}
_XFADE_BATCH = 10            # clips combined per xfade graph (bounds graph size)
_REEL_TMP = "./data/tmp"
_MAX_CLIP_WORKERS = 6        # cap on concurrent Ken Burns encodes


def _default_clip_workers() -> int:
    """Concurrent clip encodes: leave a core free, cap at _MAX_CLIP_WORKERS."""
    return max(2, min(_MAX_CLIP_WORKERS, (os.cpu_count() or 4) - 1))


def _fmt(value: float) -> str:
    """Format a number without a trailing ``.0`` (e.g. 20.0 -> '20')."""
    return f"{value:g}"


# ============================================================
# Pure FFmpeg command builders (unit-tested without running ffmpeg)
# ============================================================


def _build_ken_burns_clip_cmd(
    image_path: str,
    output_path: str,
    *,
    dwell: float,
    crossfade: float,
    width: int,
    height: int,
    fps: int,
    ken_burns: bool,
) -> list[str]:
    """Build the command that turns one still image into a slideshow clip.

    The clip lasts ``dwell + crossfade`` seconds so the crossfade overlap with the
    next clip is hidden. With ``ken_burns`` a slow, edge-safe zoom-in is applied;
    otherwise the image is a static scaled-to-cover frame.
    """
    clip_dur = dwell + crossfade
    cover = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    if ken_burns:
        # d=1 + pzoom accumulation is the form that works with a LOOPED still:
        # one output frame per (looped) input frame, the zoom carried across frames
        # via the previous-zoom value, so it doesn't restart each frame. The number
        # of frames comes from `-loop 1 -t clip_dur -r fps`.
        vf = (
            f"{cover},zoompan=z='min(max(zoom,pzoom)+0.0008,1.12)':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps},setsar=1"
        )
    else:
        vf = f"{cover},fps={fps},setsar=1"

    return [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-t", _fmt(clip_dur),
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-an",
        "-map_metadata", "-1",
        output_path,
    ]


def _build_xfade_chain_cmd(
    clip_paths: list[str],
    durations: list[float],
    output_path: str,
    *,
    crossfade: float,
    width: int,
    height: int,
    fps: int,
) -> list[str]:
    """Build a single command that crossfades ``clip_paths`` into one video.

    ``durations`` is each input's length; transition offsets are derived from the
    running (cumulative) duration, so clips of unequal length (e.g. partial reels)
    work too. Requires at least two clips.
    """
    if len(clip_paths) < 2:
        raise ValueError("xfade chain needs at least two clips")

    cmd: list[str] = ["ffmpeg", "-y"]
    for path in clip_paths:
        cmd += ["-i", path]

    filters: list[str] = []
    prev_label = "[0]"
    acc = durations[0]
    for k in range(1, len(clip_paths)):
        offset = acc - crossfade
        out_label = f"[vx{k}]"
        filters.append(
            f"{prev_label}[{k}]xfade=transition=fade:"
            f"duration={_fmt(crossfade)}:offset={_fmt(offset)}{out_label}"
        )
        prev_label = out_label
        acc = acc + durations[k] - crossfade

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", prev_label,
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-pix_fmt", "yuv420p",
        "-an",
        "-map_metadata", "-1",
        output_path,
    ]
    return cmd


def _build_loop_assembly_cmd(
    reel_path: str,
    audio_path: str,
    output_path: str,
    *,
    audio_duration: float | None,
    fps: int,
) -> list[str]:
    """Loop the (already-encoded) reel under the narration WITHOUT re-encoding video.

    ``-stream_loop -1`` repeats the reel; ``-c:v copy`` avoids a multi-hour encode;
    output length is bounded by the narration (``-t`` when known, else ``-shortest``).
    No title card and no captions — the video opens straight on the first image.
    """
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", reel_path,
        "-i", audio_path,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
    ]
    if audio_duration and audio_duration > 0:
        cmd += ["-t", str(audio_duration)]
    else:
        cmd += ["-shortest"]
    cmd += ["-movflags", "+faststart", "-map_metadata", "-1", output_path]
    return cmd


# ============================================================
# Orchestration (shells ffmpeg)
# ============================================================


def _render_clips(
    image_paths: list[str],
    tmp_dir: Path,
    *,
    dwell: float,
    crossfade: float,
    width: int,
    height: int,
    fps: int,
    ken_burns: bool,
    workers: int | None = None,
) -> list[str]:
    """Encode one Ken Burns clip per image, CONCURRENTLY, preserving input order.

    The clips are independent, so encoding them in a thread pool (each waiting on
    its own ffmpeg subprocess) is far faster than the old sequential loop. Failed
    clips are logged and skipped; the surviving clips keep their original order.
    """
    import subprocess
    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = max(1, min(workers or _default_clip_workers(), len(image_paths)))
    results: list[str | None] = [None] * len(image_paths)

    def _one(idx: int, image: str):
        clip = str(tmp_dir / f"clip_{idx:04d}.mp4")
        cmd = _build_ken_burns_clip_cmd(
            image, clip, dwell=dwell, crossfade=crossfade,
            width=width, height=height, fps=fps, ken_burns=ken_burns,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0 and os.path.exists(clip):
            return idx, clip, None
        return idx, None, (result.stderr[-200:] if result.stderr else "")

    log.info("ken_burns_render_start", images=len(image_paths), workers=n)
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_one, i, img) for i, img in enumerate(image_paths)]
        for fut in as_completed(futures):
            idx, clip, err = fut.result()
            if clip:
                results[idx] = clip
            else:
                log.warning("ken_burns_clip_failed", index=idx, stderr=err)

    return [c for c in results if c]  # successful clips, in original order


def build_slideshow_reel(
    image_paths: list[str],
    *,
    dwell: float,
    crossfade: float,
    width: int,
    height: int,
    fps: int,
    ken_burns: bool,
    batch_size: int = _XFADE_BATCH,
    clip_workers: int | None = None,
) -> str:
    """Render the image set into a single crossfaded reel video; return its path.

    Falls back to a plain concat (hard cuts) if the xfade assembly fails, so a
    reel is always produced when at least one image clip renders.
    """
    tmp_dir = Path(_REEL_TMP) / f"reel_{uuid.uuid4().hex[:8]}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    clip_dur = dwell + crossfade

    clips = _render_clips(
        image_paths, tmp_dir, dwell=dwell, crossfade=crossfade,
        width=width, height=height, fps=fps, ken_burns=ken_burns, workers=clip_workers,
    )

    if not clips:
        raise RuntimeError("No slideshow clips could be rendered")
    if len(clips) == 1:
        return clips[0]

    try:
        return _assemble_reel_xfade(clips, clip_dur, crossfade, width, height, fps, tmp_dir, batch_size)
    except Exception as e:  # noqa: BLE001 — degrade to hard-cut concat rather than fail the video
        log.warning("xfade_reel_failed_fallback_concat", error=str(e))
        return _concat_clips(clips, tmp_dir)


def _assemble_reel_xfade(
    clips: list[str],
    clip_dur: float,
    crossfade: float,
    width: int,
    height: int,
    fps: int,
    tmp_dir: Path,
    batch_size: int,
) -> str:
    """Crossfade clips into a reel, batching to keep each filter graph small."""
    import subprocess

    partials: list[str] = []
    partial_durs: list[float] = []
    for start in range(0, len(clips), batch_size):
        batch = clips[start : start + batch_size]
        if len(batch) == 1:
            partials.append(batch[0])
            partial_durs.append(clip_dur)
            continue
        out = str(tmp_dir / f"partial_{start // batch_size:03d}.mp4")
        cmd = _build_xfade_chain_cmd(
            batch, [clip_dur] * len(batch), out,
            crossfade=crossfade, width=width, height=height, fps=fps,
        )
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f"xfade batch failed: {result.stderr[-300:]}")
        partials.append(out)
        partial_durs.append(len(batch) * clip_dur - (len(batch) - 1) * crossfade)

    if len(partials) == 1:
        return partials[0]

    final = str(tmp_dir / "reel.mp4")
    cmd = _build_xfade_chain_cmd(
        partials, partial_durs, final,
        crossfade=crossfade, width=width, height=height, fps=fps,
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0 or not os.path.exists(final):
        raise RuntimeError(f"xfade final failed: {result.stderr[-300:]}")
    return final


def _concat_clips(clips: list[str], tmp_dir: Path) -> str:
    """Hard-cut concat fallback when crossfade assembly fails."""
    import subprocess

    list_file = tmp_dir / "concat.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for clip in clips:
            f.write(f"file '{os.path.abspath(clip)}'\n")
    out = str(tmp_dir / "reel_concat.mp4")
    copy_cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", out]
    result = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0 or not os.path.exists(out):
        reencode = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p", out,
        ]
        result = subprocess.run(reencode, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(f"reel concat failed: {result.stderr[-300:]}")
    return out


# ============================================================
# Public render entrypoint
# ============================================================


def render_sleep_video(
    story,
    tts_job,
    image_assets: list,
    batch_id: str,
    *,
    dwell_seconds: float = 20,
    crossfade_seconds: float = 2,
    ken_burns: bool = True,
    fps: int = 24,
    clip_workers: int | None = None,
    output_dir: str = "./data/renders",
) -> RenderJob:
    """Render a long-form sleep video: image slideshow reel looped under narration.

    Returns a :class:`RenderJob` (same contract the upload pipeline expects). A
    failed render is recorded on the job rather than raised, mirroring
    :func:`renderer.render_long_form`.
    """
    settings = get_settings()
    session = get_session()

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    output_path = os.path.join(output_dir, f"sleep_{batch_id[:8]}.mp4")
    # Must match what _create_dry_run_render writes (output_path with .mp4 -> _thumb.jpg)
    # so the dry-run thumbnail path on the job points at a file that actually exists.
    thumbnail_path = os.path.join(output_dir, f"sleep_{batch_id[:8]}_thumb.jpg")
    width, height = SLEEP_VIDEO["width"], SLEEP_VIDEO["height"]
    image_paths = _get_valid_background_paths(image_assets)

    render_job = RenderJob(
        id=str(uuid.uuid4()),
        batch_id=batch_id,
        channel_id=get_current_channel_id(),
        type="long_form",
        status="processing",
        story_ids=[story.id],
        asset_ids=[a.id for a in image_assets if getattr(a, "id", None)],
        width=width,
        height=height,
        fps=fps,
        render_started_at=datetime.now(timezone.utc),
        render_config={
            "style": "sleep_slideshow",
            "images": len(image_paths),
            "ken_burns": ken_burns,
            "captions": False,
            "music": False,
        },
    )

    try:
        if settings.dry_run:
            _create_dry_run_render(output_path, SLEEP_VIDEO)
            render_job.output_path = output_path
            render_job.thumbnail_path = thumbnail_path
            render_job.duration_seconds = getattr(tts_job, "duration_seconds", None) or 10800.0
            render_job.status = "completed"
            render_job.render_completed_at = datetime.now(timezone.utc)
            log.info("dry_run_render_sleep", batch_id=batch_id, images=len(image_paths))
        else:
            if not image_paths:
                raise RuntimeError("No images available for the sleep slideshow")
            # Fail fast (before the expensive reel encode) if there's no narration.
            audio_path = getattr(tts_job, "output_path", None)
            if not audio_path:
                raise RuntimeError("tts_job has no output_path; cannot assemble sleep video")
            reel_path = build_slideshow_reel(
                image_paths,
                dwell=dwell_seconds,
                crossfade=crossfade_seconds,
                width=width,
                height=height,
                fps=fps,
                ken_burns=ken_burns,
                clip_workers=clip_workers,
            )
            reel_dir = Path(reel_path).parent
            try:
                audio_duration = _get_audio_duration_ffprobe(audio_path)
                cmd = _build_loop_assembly_cmd(
                    reel_path, audio_path, output_path, audio_duration=audio_duration, fps=fps
                )

                import subprocess

                log.info("ffmpeg_sleep_assembly", cmd=" ".join(cmd[:20]))
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
                if result.returncode != 0:
                    raise RuntimeError(f"Sleep assembly failed: {result.stderr[-500:]}")

                _extract_thumbnail(output_path, thumbnail_path, time="00:00:05")
                render_job.output_path = output_path
                render_job.thumbnail_path = thumbnail_path
                render_job.duration_seconds = _get_video_duration(output_path)
                render_job.status = "completed"
                render_job.render_completed_at = datetime.now(timezone.utc)
            finally:
                # The reel + its 100+ source clips are large; drop them once the
                # final video (in output_dir) has been muxed.
                import shutil

                shutil.rmtree(reel_dir, ignore_errors=True)

    except Exception as e:
        render_job.status = "failed"
        render_job.error = str(e)
        log.error("render_sleep_failed", batch_id=batch_id, error=str(e))

    session.add(render_job)
    session.commit()
    session.close()
    return render_job
