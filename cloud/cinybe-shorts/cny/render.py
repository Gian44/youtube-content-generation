"""Composition: act title cards + the act's b-roll under each section's narration (long recap, 16:9) and the
vertical Shorts. The segment planning and ffmpeg chains live in the shared ``cc.segments``."""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from cc import segments as seg
from sof import shorts as sof_shorts
from sof import tts

log = logging.getLogger("cny.render")

# re-exported for the tests and the pipeline
Segment, plan_segments = seg.Segment, seg.plan_segments
still_cmd, clip_window_cmd, xfade_cmd = seg.still_cmd, seg.clip_window_cmd, seg.xfade_cmd


def _font(size: int, bold: bool = True):
    return sof_shorts._font(size, bold=bold)


def act_card_png(out: str, *, act_title: str, index: int, count: int, film: str, width: int, height: int) -> str:
    """Dark card: ACT n OF m · the act's title · the film, small."""
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (width, height), (12, 12, 16))
    d = ImageDraw.Draw(im)
    for y in range(height):                          # a soft vertical gradient, no flat black
        v = 12 + int(18 * (1 - abs(y - height * 0.45) / (height * 0.55)))
        d.line([(0, y), (width, y)], fill=(v, v, v + 6))
    small, big, foot = _font(round(height * 0.032), bold=False), _font(round(height * 0.074)), _font(round(height * 0.026), bold=False)
    tag = f"ACT {index + 1} OF {count}"
    d.text(((width - d.textlength(tag, font=small)) / 2, height * 0.33), tag, font=small, fill=(220, 190, 90))
    lines = sof_shorts._wrap(d, act_title.upper(), big, int(width * 0.8))[:2]
    y = height * 0.41
    for line in lines:
        d.text(((width - d.textlength(line, font=big)) / 2, y), line, font=big, fill=(245, 245, 245))
        y += round(height * 0.074 * 1.15)
    d.text(((width - d.textlength(film, font=foot)) / 2, height * 0.80), film, font=foot, fill=(170, 170, 180))
    im.save(out, "PNG")
    return out


def build_long(cfg, *, work: Path, sections: list[dict], section_seconds: list[float], visuals: dict[int, dict], film: str) -> str:
    """One act video per section (card + its b-roll), concatenated → ``work/reel.mp4``."""
    tmp = work / "long_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    cache = work / "cover_169"
    acts: list[str] = []
    for i, (sec, dur) in enumerate(zip(sections, section_seconds)):
        out = str(tmp / f"act_{i:02d}.mp4")
        if not os.path.exists(out):
            v = visuals.get(int(sec.get("act_index", i)), {})
            clips = [seg.precut(c, cache, width=cfg.width, height=cfg.height, fps=cfg.fps, dim=cfg.video_dim, slow=cfg.clip_slow)
                     for c in v.get("clips") or []]
            card = act_card_png(str(tmp / f"card_{i:02d}.png"), act_title=sec.get("heading") or f"Act {i + 1}", index=i,
                                count=len(sections), film=film, width=cfg.width, height=cfg.height)
            plan = seg.plan_segments(dur, clips, list(v.get("photos") or []), card=card, card_seconds=cfg.card_seconds,
                                     clip_max=cfg.clip_max_seconds, crossfade=cfg.crossfade)
            seg.render_segments(plan, tmp / f"act_{i:02d}", width=cfg.width, height=cfg.height, fps=cfg.fps,
                                crossfade=cfg.crossfade, total=dur, out=out, dim=cfg.video_dim)
        acts.append(out)
    return seg.concat_videos(acts, str(work / "reel.mp4"), str(tmp / "acts.txt"))


def build_short(cfg, *, work: Path, name: str, text: str, title: str, audio: str, clips: list[str], photos: list[str]) -> str:
    return seg.build_vertical_short(work=work, name=name, text=text, title=title, brand=cfg.brand, audio=audio, clips=clips,
                                    photos=photos, width=cfg.shorts_width, height=cfg.shorts_height, fps=cfg.shorts_fps,
                                    clip_seconds=cfg.shorts_clip_seconds, slow=cfg.clip_slow)


def chapter_rows(sections: list[dict], starts: list[float]) -> list[dict]:
    return [{"start": round(st, 2), "title": str(s.get("heading") or f"Act {i + 1}")} for i, (st, s) in enumerate(zip(starts, sections))]


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


def frames_to_check(duration: float, n: int = 10) -> list[float]:
    step = duration / max(1, n)
    return [round(step * (i + 0.5), 2) for i in range(int(math.floor(duration / step)))] if step > 0 else []
