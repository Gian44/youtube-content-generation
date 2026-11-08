"""Composition in the recap-channel style: the film's shots, in screen order, hard-cut under each act's narration;
no title cards. Vertical Shorts from the same shots. Segment planning and ffmpeg chains live in ``cc.segments``."""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from cc import segments as seg
from sof import shorts as sof_shorts
from sof import tts

log = logging.getLogger("cny.render")

# re-exported for the tests
Segment, plan_segments, plan_sequence = seg.Segment, seg.plan_segments, seg.plan_sequence
still_cmd, clip_window_cmd, xfade_cmd = seg.still_cmd, seg.clip_window_cmd, seg.xfade_cmd


def build_long(cfg, *, work: Path, sections: list[dict], section_seconds: list[float], visuals: dict[int, dict]) -> str:
    """One video per section (its act's shots in order, cut to the narration's length), concatenated → ``work/reel.mp4``."""
    tmp = work / "long_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    cache = work / "cover_169"
    parts: list[str] = []
    for i, (sec, dur) in enumerate(zip(sections, section_seconds)):
        out = str(tmp / f"act_{i:02d}.mp4")
        if not os.path.exists(out):
            v = visuals.get(int(sec.get("act_index", i)), {})
            clips = [seg.precut(c, cache, width=cfg.width, height=cfg.height, fps=cfg.fps, dim=cfg.video_dim, slow=cfg.clip_slow)
                     for c in v.get("clips") or []]
            plan = seg.plan_sequence(dur, clips, crossfade=cfg.crossfade, min_seconds=cfg.shot_min_play, max_seconds=cfg.shot_max_seconds)
            if not plan:
                raise RuntimeError(f"act {i + 1} has no shots")
            seg.render_segments(plan, tmp / f"act_{i:02d}", width=cfg.width, height=cfg.height, fps=cfg.fps,
                                crossfade=cfg.crossfade, total=dur, out=out, dim=cfg.video_dim)
        parts.append(out)
    return seg.concat_videos(parts, str(work / "reel.mp4"), str(tmp / "acts.txt"))


def build_short(cfg, *, work: Path, name: str, text: str, title: str, audio: str, clips: list[str]) -> str:
    """Vertical Short: the act's shots in order under the Short's narration, captions and the hook as title band."""
    tmp = work / f"{name}_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    duration = tts.duration_seconds(audio)
    if duration <= 0:
        raise RuntimeError("short narration has no duration")
    w, h, fps = cfg.shorts_width, cfg.shorts_height, cfg.shorts_fps
    cuts = [seg.precut(c, work / "cover_916", width=w, height=h, fps=fps, dim=1.0, slow=cfg.clip_slow) for c in clips]
    plan = seg.plan_sequence(duration, cuts, crossfade=cfg.crossfade, min_seconds=cfg.shot_min_play, max_seconds=cfg.shorts_clip_seconds)
    if not plan:
        raise RuntimeError("short has no shots")
    reel = seg.render_segments(plan, tmp, width=w, height=h, fps=fps, crossfade=cfg.crossfade, total=duration, out=str(tmp / "reel.mp4"))
    cues = sof_shorts.caption_cues(text, duration)
    pngs = [sof_shorts.caption_png(t, str(tmp / f"cue_{i:03d}.png"), width=w, height=h) for i, (_, _, t) in enumerate(cues)]
    tpng = sof_shorts.title_png(title, cfg.brand, str(tmp / "title.png"), width=w, height=h)
    out = str(work / f"{name}.mp4")
    seg.sof_render.run(sof_shorts.overlay_cmd(reel, audio, out, cues=cues, cue_pngs=pngs, title_png_path=tpng, credit_png_path=None,
                                              duration=duration, fps=fps), 900)
    return out


def chapter_rows(sections: list[dict], starts: list[float]) -> list[dict]:
    return [{"start": round(st, 2), "title": str(s.get("heading") or f"Part {i + 1}")} for i, (st, s) in enumerate(zip(starts, sections))]


def scaled_durations(chunk_paths: list[str], owner: list[int], n_sections: int, total: float) -> list[float]:
    """Per-section narration seconds from the chunk files, scaled so they sum to the assembled narration."""
    durs = [tts.duration_seconds(p) for p in chunk_paths]
    per = [0.0] * n_sections
    for d, si in zip(durs, owner):
        per[si] += d
    s = sum(per)
    scale = total / s if s > 0 else 1.0
    out = [round(d * scale, 3) for d in per]
    if out:
        out[-1] = round(max(0.0, total - sum(out[:-1])), 3)      # rounding lands on the last section
    return out


def section_starts(durations: list[float]) -> list[float]:
    out, t = [], 0.0
    for d in durations:
        out.append(round(t, 3))
        t += d
    return out


def frames_to_check(duration: float, n: int = 12) -> list[float]:
    step = duration / max(1, n)
    return [round(step * (i + 0.5), 2) for i in range(int(math.floor(duration / step)))] if step > 0 else []
