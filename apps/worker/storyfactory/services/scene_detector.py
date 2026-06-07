"""Deterministic candidate-scene detection for an episode.

Two reproducible signals are combined into candidate scenes:

* **Shot boundaries** — FFmpeg's scene-change filter (visual cut detection).
* **Dialogue spans** — the cached word transcript grouped on silence gaps.

A candidate scene is a dialogue span whose boundaries are snapped to the nearest
shot change, so a montage cut lands on a real visual cut rather than mid-shot.
The parsing/grouping logic is pure and unit-tested; only the FFmpeg call touches
the filesystem and degrades to ``[]`` on any failure.
"""

from __future__ import annotations

import re
import subprocess

from storyfactory.logger import get_logger

log = get_logger("scene_detector")

_PTS_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")


def _parse_showinfo_times(stderr: str) -> list[float]:
    """Extract ``pts_time`` values from FFmpeg ``showinfo`` stderr (pure)."""
    times = sorted({round(float(m), 3) for m in _PTS_RE.findall(stderr or "")})
    return times


def detect_shot_boundaries(
    file_path: str,
    *,
    threshold: float = 0.4,
    dry_run: bool = False,
) -> list[float]:
    """Return sorted shot-change timestamps (seconds) via FFmpeg scene detection.

    Returns ``[]`` on dry-run or any failure — callers must treat shot boundaries
    as an optional refinement, not a requirement.
    """
    if dry_run or not file_path:
        return []
    cmd = [
        "ffmpeg", "-i", file_path,
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        # showinfo writes to stderr regardless of return code; parse what we got.
        return _parse_showinfo_times(result.stderr)
    except Exception as exc:  # noqa: BLE001 — shot detection is best-effort
        log.warning("shot_detection_failed", error=str(exc))
        return []


def group_dialogue_scenes(
    words: list[dict],
    *,
    min_gap: float = 1.2,
    max_scene_seconds: float = 90.0,
    min_scene_seconds: float = 6.0,
) -> list[dict]:
    """Group word timings into dialogue scenes, split on silence gaps (pure).

    A new scene starts when the silence between consecutive words exceeds
    ``min_gap`` or the current scene would exceed ``max_scene_seconds``. Scenes
    shorter than ``min_scene_seconds`` are merged forward when possible. Each
    scene is ``{"start","end","text","word_count"}``.
    """
    if not words:
        return []

    scenes: list[dict] = []
    cur: list[dict] = [words[0]]
    for prev, w in zip(words, words[1:]):
        gap = float(w["start"]) - float(prev["end"])
        span = float(w["end"]) - float(cur[0]["start"])
        if gap > min_gap or span > max_scene_seconds:
            scenes.append(_scene_from_words(cur))
            cur = [w]
        else:
            cur.append(w)
    scenes.append(_scene_from_words(cur))

    # Merge too-short scenes into the previous one (keeps cuts meaningful).
    merged: list[dict] = []
    for sc in scenes:
        if merged and (sc["end"] - sc["start"]) < min_scene_seconds:
            prev = merged[-1]
            prev["end"] = sc["end"]
            prev["text"] = (prev["text"] + " " + sc["text"]).strip()
            prev["word_count"] += sc["word_count"]
        else:
            merged.append(sc)

    # A too-short LEADING scene has no previous to merge into — fold it forward
    # into the next so we never emit an orphan one-word "scene".
    if len(merged) >= 2 and (merged[0]["end"] - merged[0]["start"]) < min_scene_seconds:
        first = merged.pop(0)
        nxt = merged[0]
        nxt["start"] = first["start"]
        nxt["text"] = (first["text"] + " " + nxt["text"]).strip()
        nxt["word_count"] += first["word_count"]
    return merged


def _scene_from_words(group: list[dict]) -> dict:
    return {
        "start": round(float(group[0]["start"]), 3),
        "end": round(float(group[-1]["end"]), 3),
        "text": " ".join(str(w["word"]).strip() for w in group).strip(),
        "word_count": len(group),
    }


def _snap(value: float, boundaries: list[float], *, max_dist: float = 2.5) -> float:
    """Snap a time to the nearest shot boundary within ``max_dist`` seconds."""
    if not boundaries:
        return value
    nearest = min(boundaries, key=lambda b: abs(b - value))
    return round(nearest, 3) if abs(nearest - value) <= max_dist else value


def build_candidate_scenes(
    words: list[dict],
    shot_boundaries: list[float],
    duration: float,
    *,
    min_gap: float = 1.2,
    max_scene_seconds: float = 90.0,
    min_scene_seconds: float = 6.0,
) -> list[dict]:
    """Build ordered candidate scenes with text, snapping bounds to shot cuts (pure).

    Returns ``[{"index","start","end","text","word_count"}, ...]``. Boundaries
    are clamped to ``[0, duration]``; degenerate (non-positive length) scenes are
    dropped.
    """
    scenes = group_dialogue_scenes(
        words,
        min_gap=min_gap,
        max_scene_seconds=max_scene_seconds,
        min_scene_seconds=min_scene_seconds,
    )
    candidates: list[dict] = []
    for sc in scenes:
        start = max(0.0, _snap(sc["start"], shot_boundaries))
        end = _snap(sc["end"], shot_boundaries)
        if duration > 0:
            end = min(end, duration)
        if end - start <= 0:
            continue
        candidates.append({
            "index": len(candidates),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": sc["text"],
            "word_count": sc["word_count"],
        })
    return candidates
