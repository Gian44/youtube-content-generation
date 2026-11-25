"""Scene frames + narration → mp4. Each scene lasts as long as its narration plus a short breath; inside it the
three boil variants cycle at 8 fps and each reveal state starts at its ``data-at`` fraction."""

from __future__ import annotations

import subprocess
from pathlib import Path

BOIL_FPS = 8


def _run(cmd: list[str], timeout: int = 3600) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed: {(r.stderr or '')[-600:]}")


def frame_plan(states: list[tuple[float, list[str]]], length: float, *, fps: int = BOIL_FPS) -> list[tuple[str, float]]:
    """[(png, seconds)] for one scene of ``length`` seconds."""
    n = max(1, round(length * fps))
    step = length / n
    out: list[tuple[str, float]] = []
    for k in range(n):
        t = k * step
        st = states[0][1]
        for at, pngs in states:
            if at * length <= t + 1e-6:
                st = pngs
        out.append((st[k % len(st)], step))
    return out


def concat_file(plan: list[tuple[str, float]], path: str) -> str:
    lines = ["ffconcat version 1.0"]
    for png, dur in plan:
        lines.append(f"file '{Path(png).resolve()}'")
        lines.append(f"duration {dur:.4f}")
    lines.append(f"file '{Path(plan[-1][0]).resolve()}'")       # the concat demuxer drops the last duration otherwise
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def pad_audio(mp3: str, out: str, length: float) -> str:
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3, "-af", f"apad=whole_dur={length:.3f}", "-t", f"{length:.3f}",
          "-ar", "48000", "-ac", "2", out], 600)
    return out


def assemble(*, frames_list: str, audio_list: str, out: str, width: int, height: int, fps: int = 30,
             loudness: float = -14.0, pre_filter: str = "") -> str:
    af = ",".join(f for f in (pre_filter, f"loudnorm=I={loudness}:TP=-1.5:LRA=11") if f)
    _run(["ffmpeg", "-y", "-loglevel", "error",
          "-f", "concat", "-safe", "0", "-i", frames_list,
          "-f", "concat", "-safe", "0", "-i", audio_list,
          "-vf", f"fps={fps},scale={width}:{height}:flags=lanczos,format=yuv420p",
          "-af", af, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-tune", "animation",
          "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", out])
    return out
