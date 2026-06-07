"""Recap media service (CinybeShorts) — series / episode / segment ledger.

Owns everything about turning user-supplied local video files into many Shorts:

* parse a filename convention into a series + episode,
* scan an inbox folder and register new episodes,
* plan deterministic [start, end] segmentation windows for an episode,
* read/write the :class:`RecapSegment` dedupe ledger ("already made a Short here"),
* cut a clip from the source file with FFmpeg.

Legal/scope: this only ever reads local files the user placed in their inbox.
It is NOT a stream ripper and never fetches from streaming services.
"""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from storyfactory.db.models import (
    Channel,
    MediaEpisode,
    MediaSeries,
    RecapSegment,
)
from storyfactory.logger import get_logger

log = get_logger("recap_service")

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}

# Windows whose start matches an existing ledger row within this many seconds are
# treated as the same window (the plan is deterministic, so starts line up).
# Kept strictly below the minimum possible step (1.0s, see plan_segments) so that
# adjacent windows under a high-overlap config are never falsely deduped; starts
# are rounded to 2 dp, so cross-run float drift is far under this.
_WINDOW_MATCH_TOLERANCE = 0.5

# Filename convention patterns (case-insensitive). The first match wins.
_SERIES_SXXEXX = re.compile(r"^(?P<title>.+?)[ ._-]+s(?P<season>\d{1,2})[ ._-]?e(?P<episode>\d{1,3})", re.IGNORECASE)
_SERIES_NXNN = re.compile(r"^(?P<title>.+?)[ ._-]+(?P<season>\d{1,2})x(?P<episode>\d{1,3})", re.IGNORECASE)
_MOVIE_YEAR = re.compile(r"^(?P<title>.+?)[ ._(\[]+(?P<year>(?:19|20)\d{2})", re.IGNORECASE)
_MOVIE_PART = re.compile(r"(?:part|pt|cd|disc)[ ._-]*(?P<part>\d{1,2})", re.IGNORECASE)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or f"series-{uuid.uuid4().hex[:8]}"


def _clean_title(raw: str) -> str:
    """Turn a dotted/underscored filename fragment into a readable title."""
    text = re.sub(r"[._]+", " ", raw or "").strip(" -_")
    text = re.sub(r"\s+", " ", text)
    return text.title() if text and text == text.lower() else (text or "Untitled")


def parse_filename(filename: str) -> dict:
    """Parse a source filename into series/episode metadata.

    Returns a dict with: ``series_title``, ``type`` ('series' | 'movie'),
    ``season``, ``episode``, ``part_index``, ``episode_title``.

    Supported conventions (documented in docs/multi-channel.md):
      * ``Breaking Bad S01E03.mkv``           → series, S1 E3
      * ``The Office 2x05.mp4``               → series, S2 E5
      * ``Inception (2010).mp4``              → movie
      * ``Dune Part 2.mkv``                   → movie, part 2
      * anything else                         → movie, whole name as title
    """
    stem = Path(filename).stem

    m = _SERIES_SXXEXX.match(stem) or _SERIES_NXNN.match(stem)
    if m:
        return {
            "series_title": _clean_title(m.group("title")),
            "type": "series",
            "season": int(m.group("season")),
            "episode": int(m.group("episode")),
            "part_index": None,
            "episode_title": None,
        }

    part_m = _MOVIE_PART.search(stem)
    movie_m = _MOVIE_YEAR.match(stem)
    if movie_m:
        title = _clean_title(movie_m.group("title"))
    else:
        # Strip a trailing part marker from the title if present.
        title = _clean_title(_MOVIE_PART.sub("", stem))
    return {
        "series_title": title or _clean_title(stem),
        "type": "movie",
        "season": None,
        "episode": None,
        "part_index": int(part_m.group("part")) if part_m else None,
        "episode_title": None,
    }


# ----------------------------------------------------------------------------
# Series CRUD
# ----------------------------------------------------------------------------

def list_series(session: Session, channel: Channel) -> list[MediaSeries]:
    return (
        session.query(MediaSeries)
        .filter_by(channel_id=channel.id)
        .order_by(MediaSeries.created_at.asc())
        .all()
    )


def get_series(session: Session, channel: Channel, slug: str) -> MediaSeries | None:
    return (
        session.query(MediaSeries)
        .filter_by(channel_id=channel.id, slug=slug)
        .first()
    )


def ensure_series(
    session: Session,
    channel: Channel,
    *,
    title: str,
    slug: str | None = None,
    type: str = "series",
    commit: bool = True,
) -> MediaSeries:
    """Create the series if absent (idempotent by channel + slug)."""
    slug = slug or slugify(title)
    series = get_series(session, channel, slug)
    if series is None:
        series = MediaSeries(
            id=str(uuid.uuid4()),
            channel_id=channel.id,
            slug=slug,
            title=title,
            type=type if type in ("series", "movie") else "series",
        )
        session.add(series)
        if commit:
            session.commit()
        log.info("series_created", channel=channel.slug, slug=slug, type=series.type)
    return series


def set_active_series(session: Session, channel: Channel, slug: str, *, commit: bool = True) -> MediaSeries:
    """Mark exactly one series active for the channel (clears the others)."""
    target = get_series(session, channel, slug)
    if target is None:
        raise ValueError(f"Series not found for channel '{channel.slug}': {slug}")
    for series in list_series(session, channel):
        series.is_active = series.id == target.id
    if commit:
        session.commit()
    log.info("series_activated", channel=channel.slug, slug=slug)
    return target


def get_active_series(
    session: Session, channel: Channel, fallback_slug: str | None = None
) -> MediaSeries | None:
    """Resolve which series to process now.

    Precedence: an explicitly active series → ``fallback_slug`` (from config) →
    the first series that still has a non-completed episode → the first series.
    """
    active = (
        session.query(MediaSeries)
        .filter_by(channel_id=channel.id, is_active=True)
        .first()
    )
    if active is not None:
        return active
    if fallback_slug:
        match = get_series(session, channel, fallback_slug)
        if match is not None:
            return match
    all_series = list_series(session, channel)
    for series in all_series:
        if any(ep.status != "completed" for ep in series.episodes):
            return series
    return all_series[0] if all_series else None


# ----------------------------------------------------------------------------
# Episode CRUD
# ----------------------------------------------------------------------------

def list_episodes(session: Session, series: MediaSeries) -> list[MediaEpisode]:
    return (
        session.query(MediaEpisode)
        .filter_by(series_id=series.id)
        .order_by(MediaEpisode.order_index.asc(), MediaEpisode.created_at.asc())
        .all()
    )


def register_episode(
    session: Session,
    series: MediaSeries,
    *,
    file_path: str,
    season: int | None = None,
    episode: int | None = None,
    part_index: int | None = None,
    title: str | None = None,
    duration_seconds: float | None = None,
    commit: bool = True,
) -> tuple[MediaEpisode, bool]:
    """Register an episode for a series. Idempotent on (series_id, file_path).

    Returns ``(episode, created)``. An existing episode is returned untouched
    (its status is preserved so completed episodes are never reprocessed); only
    a missing duration is backfilled.
    """
    norm_path = str(file_path)
    existing = (
        session.query(MediaEpisode)
        .filter_by(series_id=series.id, file_path=norm_path)
        .first()
    )
    if existing is not None:
        if existing.duration_seconds is None and duration_seconds is not None:
            existing.duration_seconds = duration_seconds
            if commit:
                session.commit()
        return existing, False

    order_index = session.query(MediaEpisode).filter_by(series_id=series.id).count()
    ep = MediaEpisode(
        id=str(uuid.uuid4()),
        series_id=series.id,
        channel_id=series.channel_id,
        season_number=season,
        episode_number=episode,
        part_index=part_index,
        title=title,
        file_path=norm_path,
        duration_seconds=duration_seconds,
        status="pending",
        order_index=order_index,
    )
    session.add(ep)
    if commit:
        session.commit()
    log.info("episode_registered", series=series.slug, file=os.path.basename(norm_path))
    return ep, True


def get_active_episode(session: Session, series: MediaSeries) -> MediaEpisode | None:
    """Return the episode currently being processed for a series.

    An ``in_progress`` episode wins; otherwise the first ``pending`` episode in
    order. Returns None when every episode is ``completed``.
    """
    episodes = list_episodes(session, series)
    in_progress = next((e for e in episodes if e.status == "in_progress"), None)
    if in_progress is not None:
        return in_progress
    return next((e for e in episodes if e.status == "pending"), None)


def mark_episode(session: Session, episode: MediaEpisode, status: str, *, commit: bool = True) -> MediaEpisode:
    if status not in ("pending", "in_progress", "completed"):
        raise ValueError(f"Invalid episode status: {status}")
    episode.status = status
    if commit:
        session.commit()
    log.info("episode_status", episode_id=episode.id[:8], status=status)
    return episode


# ----------------------------------------------------------------------------
# Inbox scan
# ----------------------------------------------------------------------------

def scan_inbox(session: Session, channel: Channel, inbox_path: str, *, commit: bool = True) -> dict:
    """Scan the inbox folder and register any new episodes.

    Files directly in the inbox are parsed by filename. Files inside a
    subfolder use the subfolder name as the series title (the filename still
    supplies season/episode/part). Returns a summary dict.
    """
    root = Path(inbox_path)
    summary = {"inbox": str(root), "new_episodes": 0, "new_series": 0, "scanned": 0}
    if not root.exists():
        log.warning("inbox_missing", path=str(root))
        summary["error"] = f"Inbox folder does not exist: {root}"
        return summary

    known_slugs = {s.slug for s in list_series(session, channel)}

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        summary["scanned"] += 1

        parsed = parse_filename(path.name)
        # A subfolder (relative to the inbox) names the series explicitly.
        rel_parent = path.parent.relative_to(root)
        if rel_parent.parts:
            series_title = _clean_title(rel_parent.parts[0])
            series_type = "series" if parsed["season"] is not None else parsed["type"]
        else:
            series_title = parsed["series_title"]
            series_type = parsed["type"]

        slug = slugify(series_title)
        if slug not in known_slugs:
            summary["new_series"] += 1
            known_slugs.add(slug)
        series = ensure_series(
            session, channel, title=series_title, slug=slug, type=series_type, commit=False
        )
        session.flush()  # ensure series.id is available for the episode FK

        duration = probe_duration(str(path))
        _episode, created = register_episode(
            session,
            series,
            file_path=str(path),
            season=parsed["season"],
            episode=parsed["episode"],
            part_index=parsed["part_index"],
            title=parsed["episode_title"],
            duration_seconds=duration,
            commit=False,
        )
        if created:
            summary["new_episodes"] += 1

    if commit:
        session.commit()
    log.info("inbox_scanned", **{k: v for k, v in summary.items() if k != "inbox"})
    return summary


# ----------------------------------------------------------------------------
# Segmentation (pure) + ledger
# ----------------------------------------------------------------------------

def plan_segments(
    duration_seconds: float | None,
    *,
    target_short_seconds: float = 52.0,
    overlap_seconds: float = 0.0,
    min_short_seconds: float = 40.0,
    min_count: int = 1,
    max_count: int = 60,
) -> list[tuple[float, float]]:
    """Plan an ordered list of [start, end] windows covering an episode.

    Deterministic: the same inputs always yield the same windows, so the dedupe
    ledger can match windows by start time across runs. A trailing window
    shorter than ``min_short_seconds`` is dropped (unless it is the only window).
    """
    if not duration_seconds or duration_seconds <= 0:
        return []

    step = max(1.0, float(target_short_seconds) - max(0.0, float(overlap_seconds)))
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_seconds and len(windows) < max_count:
        end = min(start + target_short_seconds, duration_seconds)
        if (end - start) >= min_short_seconds or not windows:
            windows.append((round(start, 2), round(end, 2)))
        if end >= duration_seconds:
            break
        start = round(start + step, 2)

    # Honor a minimum count when the episode is long enough to subdivide further.
    if len(windows) < min_count and duration_seconds > min_short_seconds * min_count:
        return plan_segments(
            duration_seconds,
            target_short_seconds=max(min_short_seconds, duration_seconds / min_count),
            overlap_seconds=overlap_seconds,
            min_short_seconds=min_short_seconds,
            min_count=1,
            max_count=max_count,
        )
    return windows


def existing_segment_windows(session: Session, episode: MediaEpisode) -> list[tuple[float, float]]:
    rows = (
        session.query(RecapSegment.start_seconds, RecapSegment.end_seconds)
        .filter_by(episode_id=episode.id)
        .all()
    )
    return [(float(s), float(e)) for s, e in rows]


def pending_windows(
    planned: list[tuple[float, float]],
    existing: list[tuple[float, float]],
    tolerance: float = _WINDOW_MATCH_TOLERANCE,
) -> list[tuple[float, float]]:
    """Filter out windows already present in the ledger (matched by start time)."""
    done_starts = [s for s, _ in existing]

    def is_done(start: float) -> bool:
        return any(abs(start - ds) <= tolerance for ds in done_starts)

    return [(s, e) for (s, e) in planned if not is_done(s)]


def record_segment(
    session: Session,
    episode: MediaEpisode,
    *,
    channel_id: str | None,
    start_seconds: float,
    end_seconds: float,
    story_id: str | None = None,
    render_job_id: str | None = None,
    status: str = "rendered",
    commit: bool = True,
) -> RecapSegment:
    """Write a ledger row so this window is never re-cut, regardless of outcome."""
    segment = RecapSegment(
        id=str(uuid.uuid4()),
        episode_id=episode.id,
        channel_id=channel_id,
        start_seconds=round(float(start_seconds), 2),
        end_seconds=round(float(end_seconds), 2),
        story_id=story_id,
        render_job_id=render_job_id,
        status=status,
    )
    session.add(segment)
    if commit:
        session.commit()
    return segment


def episode_is_exhausted(session: Session, episode: MediaEpisode, cfg: dict) -> bool:
    """True when every planned window for the episode has a ledger row."""
    planned = plan_segments(
        episode.duration_seconds,
        target_short_seconds=cfg.get("target_short_seconds", 52),
        overlap_seconds=cfg.get("overlap_seconds", 0),
        min_short_seconds=cfg.get("min_short_seconds", 40),
        min_count=cfg.get("min_shorts_per_episode", 1),
        max_count=cfg.get("max_shorts_per_episode", 60),
    )
    if not planned:
        return True
    return not pending_windows(planned, existing_segment_windows(session, episode))


# ----------------------------------------------------------------------------
# FFmpeg / ffprobe helpers
# ----------------------------------------------------------------------------

def probe_duration(file_path: str) -> float | None:
    """Return a media file's duration in seconds via ffprobe, or None on failure."""
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception as exc:  # ffprobe missing / unreadable file
        log.warning("probe_duration_failed", path=file_path, error=str(exc))
    return None


def cut_clip(
    file_path: str,
    start_seconds: float,
    duration_seconds: float,
    output_dir: str = "./data/sources/clips",
    *,
    dry_run: bool = False,
    keep_audio: bool = False,
) -> str:
    """Cut a clip [start, start+duration] from the source file with FFmpeg.

    Returns the output path. In dry-run (or when the source is missing) a small
    placeholder file is written so the rest of the pipeline can proceed without
    invoking FFmpeg.

    By default the source audio is dropped (``-an``): in ``tts_narration`` mode
    the recap narration is the master clock. When ``keep_audio`` is True the
    clip's original audio is preserved and encoded (``original_audio`` mode).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    out_path = os.path.join(
        output_dir, f"clip_{uuid.uuid4().hex[:10]}_{int(start_seconds)}.mp4"
    )

    if dry_run or not os.path.exists(file_path):
        with open(out_path, "wb") as f:
            f.write(b"DRY_RUN_CLIP_PLACEHOLDER")
        return out_path

    audio_args = (
        ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
        if keep_audio
        else ["-an"]  # drop the source audio; the recap narration is the master clock
    )
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(round(float(start_seconds), 2)),
        "-i", file_path,
        "-t", str(round(float(duration_seconds), 2)),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        *audio_args,
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg clip cut failed: {result.stderr[-400:]}")
    return out_path


def rebase_words_to_montage(
    words: list[dict],
    cut_list: list[list[float]],
) -> list[dict]:
    """Map episode-time word timings onto a montage's stitched timeline (pure).

    For each cut ``[s, e]`` taken in order, words falling inside it are kept and
    their times shifted so they line up with the concatenated montage clip
    (subtitles stay in sync without a second transcription pass). Words outside
    every cut are dropped. Iterates cuts in the SAME order the montage stitches
    them, so reordered (hook-first) cuts still sync.
    """
    out: list[dict] = []
    offset = 0.0
    for cut in cut_list or []:
        try:
            s, e = float(cut[0]), float(cut[1])
        except (TypeError, ValueError, IndexError):
            continue
        length = e - s
        if length <= 0:
            continue
        for w in words or []:
            ws, we = float(w["start"]), float(w["end"])
            if ws >= s and ws < e:
                new_start = (ws - s) + offset
                new_end = min((we - s), length) + offset
                out.append({
                    "word": w["word"],
                    "start": round(new_start, 3),
                    "end": round(max(new_end, new_start + 0.05), 3),
                    "confidence": w.get("confidence"),
                })
        offset += length
    out.sort(key=lambda w: w["start"])
    return out


def build_montage_clip(
    file_path: str,
    cut_list: list[list[float]],
    output_dir: str = "./data/sources/clips",
    *,
    dry_run: bool = False,
) -> str:
    """Cut the montage sub-spans (with audio) and concatenate them into one clip.

    A single-cut list is just :func:`cut_clip`. Multiple cuts are each cut (with
    original audio, consistent codecs) then joined with the concat demuxer. In
    dry-run or when the source is missing, writes a placeholder. Returns the
    montage clip path.
    """
    cuts = [c for c in (cut_list or []) if len(c) == 2 and float(c[1]) > float(c[0])]
    if not cuts:
        raise ValueError("build_montage_clip requires at least one valid cut")

    if dry_run or not os.path.exists(file_path):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        out_path = os.path.join(output_dir, f"montage_{uuid.uuid4().hex[:10]}.mp4")
        with open(out_path, "wb") as f:
            f.write(b"DRY_RUN_CLIP_PLACEHOLDER")
        return out_path

    if len(cuts) == 1:
        s, e = float(cuts[0][0]), float(cuts[0][1])
        return cut_clip(file_path, s, e - s, output_dir, dry_run=dry_run, keep_audio=True)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    sub_paths: list[str] = []
    for s, e in cuts:
        sub_paths.append(
            cut_clip(file_path, float(s), float(e) - float(s), output_dir, keep_audio=True)
        )

    out_path = os.path.join(output_dir, f"montage_{uuid.uuid4().hex[:10]}.mp4")
    concat_list = os.path.join(output_dir, f"montage_{uuid.uuid4().hex[:8]}.txt")
    try:
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in sub_paths:
                f.write(f"file '{os.path.abspath(p)}'\n")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c", "copy", "-movflags", "+faststart", out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(f"FFmpeg montage concat failed: {result.stderr[-400:]}")
    finally:
        for p in sub_paths:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.remove(concat_list)
        except OSError:
            pass
    return out_path
