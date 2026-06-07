"""Whole-episode transcription with word timestamps, cached to disk.

Scene segmentation needs the *whole* episode's dialogue on one timeline (not the
per-clip pass that ``caption_service.transcribe_dialogue`` does). Whisper caps a
single request at 25 MB, so a 45-minute episode is transcribed in chunks: extract
a small mono audio track per chunk, transcribe each with ``whisper-1`` (word
timestamps), offset each chunk's timings by its start, and stitch into one
episode-relative word list.

The stitched transcript is cached as JSON keyed by episode id, so the
deterministic scene plan and any re-run never re-transcribe (and never re-pay).
Every failure path degrades to ``[]`` — the caller falls back to time-based
segmentation rather than crashing.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

from storyfactory.logger import get_logger
from storyfactory.services.caption_service import _openai_transcription

log = get_logger("episode_transcriber")

CACHE_DIR = "./data/cache/transcripts"
# Mono, low-bitrate chunks stay far under Whisper's 25 MB/request ceiling
# (~3-4 MB per 10 minutes at 48 kbps mono).
_CHUNK_SECONDS = 600
_AUDIO_BITRATE = "48k"
_AUDIO_RATE = "16000"


def _cache_path(episode_id: str) -> str:
    return os.path.join(CACHE_DIR, f"{episode_id}.json")


def load_cached_transcript(episode_id: str) -> list[dict] | None:
    """Return the cached word list for an episode, or ``None`` if not cached."""
    path = _cache_path(episode_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        words = data.get("words") if isinstance(data, dict) else data
        return words if isinstance(words, list) else None
    except (OSError, ValueError) as exc:
        log.warning("transcript_cache_read_failed", episode_id=episode_id, error=str(exc))
        return None


def _save_cache(episode_id: str, words: list[dict]) -> None:
    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)
    try:
        with open(_cache_path(episode_id), "w", encoding="utf-8") as f:
            json.dump({"episode_id": episode_id, "words": words}, f)
    except OSError as exc:
        log.warning("transcript_cache_write_failed", episode_id=episode_id, error=str(exc))


def _extract_audio_chunk(file_path: str, offset: float, duration: float, out_path: str) -> bool:
    """Extract a mono, low-bitrate audio chunk for transcription. False on failure."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(round(offset, 2)),
        "-t", str(round(duration, 2)),
        "-i", file_path,
        "-vn", "-ac", "1", "-ar", _AUDIO_RATE, "-b:a", _AUDIO_BITRATE,
        out_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return result.returncode == 0 and os.path.exists(out_path)
    except Exception as exc:  # noqa: BLE001 — extraction failures are non-fatal
        log.warning("audio_chunk_extract_failed", offset=offset, error=str(exc))
        return False


def _offset_words(words: list[dict], offset: float) -> list[dict]:
    """Shift a chunk's word timings onto the episode timeline."""
    shifted = []
    for w in words:
        shifted.append({
            "word": w["word"],
            "start": round(float(w["start"]) + offset, 3),
            "end": round(float(w["end"]) + offset, 3),
            "confidence": w.get("confidence"),
        })
    return shifted


def transcribe_episode(
    episode_id: str,
    file_path: str,
    duration_seconds: float | None,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> list[dict]:
    """Transcribe a whole episode to episode-relative word timings (cached).

    Returns ``[{"word","start","end","confidence"}, ...]`` ordered by start, or
    ``[]`` when transcription is unavailable (dry-run, no key, missing file, or
    failure). The result is cached per episode and reused unless ``force``.
    """
    if not force:
        cached = load_cached_transcript(episode_id)
        if cached is not None:
            log.info("transcript_cache_hit", episode_id=episode_id, words=len(cached))
            return cached

    if dry_run or not file_path or not os.path.exists(file_path):
        return []

    duration = duration_seconds or 0.0
    if duration <= 0:
        from storyfactory.services.recap_service import probe_duration

        duration = probe_duration(file_path) or 0.0
    if duration <= 0:
        log.warning("transcript_no_duration", episode_id=episode_id)
        return []

    tmp_dir = Path("./data/tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    all_words: list[dict] = []
    offset = 0.0
    chunk_idx = 0
    while offset < duration:
        chunk_len = min(_CHUNK_SECONDS, duration - offset)
        chunk_path = str(tmp_dir / f"epx_{episode_id[:8]}_{chunk_idx}_{uuid.uuid4().hex[:6]}.mp3")
        try:
            if not _extract_audio_chunk(file_path, offset, chunk_len, chunk_path):
                log.warning("transcript_chunk_skipped", episode_id=episode_id, offset=offset)
            else:
                try:
                    words = _openai_transcription(chunk_path) or []
                except Exception as exc:  # noqa: BLE001 — one bad chunk shouldn't kill all
                    log.warning("transcript_chunk_failed", offset=offset, error=str(exc))
                    words = []
                all_words.extend(_offset_words(words, offset))
        finally:
            if os.path.exists(chunk_path):
                try:
                    os.remove(chunk_path)
                except OSError:
                    pass
        offset += chunk_len
        chunk_idx += 1

    all_words.sort(key=lambda w: w["start"])
    if all_words:
        _save_cache(episode_id, all_words)
    log.info("episode_transcribed", episode_id=episode_id, words=len(all_words), chunks=chunk_idx)
    return all_words
