"""Film-side helpers: scene-change detection, shot picking inside a time window, bars burned into the file, and the
filter that frames a shot (16:9 blurred fill or 9:16 crop). ``cny.beats`` renders each narration beat with them."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("cny.cut")
_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def scene_cmd(video: str, *, start: float, seconds: float, threshold: float) -> list[str]:
    """Scene scores on a 480-px copy of the frames: the same cuts, a fraction of the work."""
    return ["ffmpeg", "-v", "info", "-nostats", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}", "-i", video, "-an",
            "-vf", f"scale=480:-2,select='gt(scene,{threshold:g})',showinfo", "-f", "null", "-"]


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
    """Pure: ``want`` (start, seconds) shots spread across the window, in screen order. Shots begin just after a
    scene cut (capped at ``max_len``); too-short scenes are skipped — so a sliver of the previous scene before a cut
    never opens a beat. With more scenes than wanted, an even spread of them; with fewer (one long take included),
    the longest scenes are split into several shots (different moments of the same scene) so nothing is replayed;
    only when no scene is long enough is the window split evenly."""
    want = max(1, want)
    bounds = [start] + [c for c in cuts if start < c < end] + [end]
    scenes = [(a + pad, b - a - pad) for a, b in zip(bounds, bounds[1:]) if b - a - pad >= min_len]
    if not scenes:                              # nothing but tiny cuts (or none inside a short window): split it evenly
        n = max(1, min(want, int((end - start) // max(0.5, min_len))))
        step = (end - start) / n
        return [(round(start + i * step + pad, 3), round(min(max_len, max(1.0, step - 2 * pad)), 3)) for i in range(n)]
    if len(scenes) > want:
        picked = []
        for i in range(want):                   # evenly spread picks, no repeats
            j = round(i * (len(scenes) - 1) / max(1, want - 1))
            if not picked or j != picked[-1]:
                picked.append(j)
        return [(round(scenes[j][0], 3), round(min(max_len, scenes[j][1]), 3)) for j in picked]
    pieces = [1] * len(scenes)
    while sum(pieces) < want:                   # split the scene with the most room per piece
        i = max(range(len(scenes)), key=lambda k: scenes[k][1] / (pieces[k] + 1))
        if scenes[i][1] / (pieces[i] + 1) < min_len:
            break
        pieces[i] += 1
    out: list[tuple[float, float]] = []
    for (a, length), n in zip(scenes, pieces):
        seg = length / n
        for k in range(n):
            out.append((round(a + k * seg, 3), round(min(max_len, seg - (0.1 if n > 1 else 0.0)), 3)))
    return out


_CROP = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")


def video_size(video: str) -> tuple[int, int]:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
                       capture_output=True, text=True, timeout=60)
    try:
        w, h = (int(x) for x in r.stdout.strip().split(",")[:2])
        return w, h
    except ValueError:
        return 0, 0


def union_crop(found: list[tuple[int, int, int, int]], width: int, height: int, *, keep: float = 0.98) -> str | None:
    """Pure: the picture area inside bars burned into the file, from cropdetect results at several points of the
    film. Dark scenes make cropdetect cut into the picture, so the largest area seen wins (bars never move); centred,
    even sizes. None when there are no real bars (the area is ≥ ``keep`` of the frame both ways)."""
    if not found or width <= 0 or height <= 0:
        return None
    w = max(f[0] for f in found)
    h = max(f[1] for f in found)
    if w >= width * keep and h >= height * keep:
        return None
    w, h = min(width, w) // 2 * 2, min(height, h) // 2 * 2
    return f"{w}:{h}:{(width - w) // 2}:{(height - h) // 2}"


def detect_crop(video: str, duration: float, *, points: int = 8) -> str | None:
    """``w:h:x:y`` of the picture inside black bars burned into the file (many Archive prints are 4:3 films in a
    16:9 file), or None."""
    width, height = video_size(video)
    found = []
    for k in range(points):
        at = duration * (0.1 + 0.8 * k / max(1, points - 1))
        r = subprocess.run(["ffmpeg", "-v", "info", "-nostats", "-ss", f"{at:.1f}", "-i", video, "-frames:v", "25", "-an",
                            "-vf", "cropdetect=limit=24:round=2:reset=0", "-f", "null", "-"], capture_output=True, text=True, timeout=120)
        m = _CROP.findall(r.stderr or "")
        if m:
            found.append(tuple(int(x) for x in m[-1]))
    crop = union_crop(found, width, height)
    log.info("crop detect %s (%dx%d, %d samples): %s", Path(video).name, width, height, len(found), crop or "none")
    return crop


def shot_vf(*, width: int, height: int, fps: int, fit: str = "blur", crop: str | None = None) -> str:
    """``fit="blur"`` keeps the whole frame and fills the rest with the same frame blurred and darkened (a 4:3 print
    in the 16:9 recap, Gian 2026-11-08); ``"pad"`` fills with black; ``"crop"`` fills the frame from the centre
    (the 9:16 Shorts)."""
    pre = f"crop={crop}," if crop else ""                 # bars burned into the file come off first
    if fit == "crop":
        vf = f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height}"
    elif fit == "pad":
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
              f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black")
    else:
        bw, bh = max(2, width // 4 // 2 * 2), max(2, height // 4 // 2 * 2)
        vf = (f"split=2[bg][fg];[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},boxblur=12:2,"
              f"eq=brightness=-0.07:saturation=0.9,scale={width}:{height}[bgb];"
              f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    return pre + vf + f",fps={fps},setsar=1,format=yuv420p"


def has_audio(video: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", video],
                       capture_output=True, text=True, timeout=60)
    return bool(r.stdout.strip())


def duration_seconds(video: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
                       capture_output=True, text=True, timeout=120)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0
