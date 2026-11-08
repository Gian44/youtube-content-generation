"""Shots from the film for one act: ffmpeg scene-change detection inside the act's window, then a few 3–8 s shots
spread across it, each extracted as a silent normalised clip that the renderer treats like any stock clip."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("cny.cut")
_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def scene_cmd(video: str, *, start: float, seconds: float, threshold: float) -> list[str]:
    return ["ffmpeg", "-v", "info", "-nostats", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", video, "-an",
            "-vf", f"select='gt(scene,{threshold:g})',showinfo", "-f", "null", "-"]


def scene_cuts(video: str, *, start: float, end: float, threshold: float = 0.3, timeout: int = 600) -> list[float]:
    """Absolute times (s) of scene changes inside [start, end]."""
    try:
        r = subprocess.run(scene_cmd(video, start=start, seconds=end - start, threshold=threshold), capture_output=True,
                           text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("scene detection failed: %s", exc)
        return []
    return sorted(round(start + float(t), 3) for t in _PTS.findall(r.stderr or ""))


def pick_shots(start: float, end: float, cuts: list[float], *, want: int, min_len: float = 3.0, max_len: float = 8.0,
               pad: float = 0.15) -> list[tuple[float, float]]:
    """Pure: ``want`` (start, seconds) shots spread across the window. Shots begin just after a scene cut and run
    to the next cut (capped at ``max_len``); too-short scenes are skipped; with too few scenes the window is
    split evenly instead."""
    want = max(1, want)
    bounds = [start] + [c for c in cuts if start < c < end] + [end]
    scenes = [(a + pad, min(b, a + pad + max_len) - (a + pad)) for a, b in zip(bounds, bounds[1:]) if b - a - pad >= min_len]
    if len(scenes) < max(2, want // 2):
        n = want
        step = (end - start) / n
        return [(round(start + i * step + pad, 3), round(min(max_len, max(1.0, step - 2 * pad)), 3)) for i in range(n)]
    if len(scenes) <= want:
        return [(round(a, 3), round(d, 3)) for a, d in scenes]
    picked = []
    for i in range(want):                       # evenly spread picks, no repeats
        j = round(i * (len(scenes) - 1) / max(1, want - 1))
        if not picked or j != picked[-1]:
            picked.append(j)
    return [(round(scenes[j][0], 3), round(scenes[j][1], 3)) for j in picked]


def shot_cmd(video: str, out: str, *, start: float, seconds: float, width: int, height: int, fps: int, fit: str = "pad") -> list[str]:
    """``fit="pad"`` keeps the whole frame (a 2.39:1 film is letterboxed inside 16:9); ``"crop"`` fills the frame
    from the centre (the 9:16 Shorts)."""
    if fit == "crop":
        vf = f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}"
    else:
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    vf += f",fps={fps},setsar=1,format=yuv420p"
    return ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", video, "-t", f"{seconds:.3f}", "-an", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-map_metadata", "-1", out]


def extract_shots(video: str, shots: list[tuple[float, float]], out_dir: str, *, prefix: str, width: int, height: int,
                  fps: int, fit: str = "pad") -> list[str]:
    """Cut each (start, seconds) shot → ``<out_dir>/<prefix>_<k>.mp4`` (resumable: existing files are kept)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for k, (st, secs) in enumerate(shots):
        p = str(d / f"{prefix}_{k:02d}.mp4")
        if Path(p).exists():
            out.append(p)
            continue
        try:
            subprocess.run(shot_cmd(video, p, start=st, seconds=secs, width=width, height=height, fps=fps, fit=fit), check=True,
                           capture_output=True, timeout=600)
            out.append(p)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("shot %d (%s) failed: %s", k, prefix, exc)
    return out


def act_shots(video: str, out_dir: str, *, act_index: int, window: dict, want: int, min_len: float, max_len: float,
              threshold: float, width: int, height: int, fps: int) -> tuple[list[str], list[tuple[float, float]]]:
    """The act's shots: (clip paths, the (start, seconds) list they were cut from)."""
    s, e = float(window["start_seconds"]), float(window["end_seconds"])
    cuts = scene_cuts(video, start=s, end=e, threshold=threshold)
    shots = pick_shots(s, e, cuts, want=want, min_len=min_len, max_len=max_len)
    out = extract_shots(video, shots, out_dir, prefix=f"act{act_index:02d}", width=width, height=height, fps=fps, fit="pad")
    log.info("act %d: %d shots from %d scene cuts in %.0f-%.0f s", act_index, len(out), len(cuts), s, e)
    return out, shots


def duration_seconds(video: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                       capture_output=True, text=True, timeout=120)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0
