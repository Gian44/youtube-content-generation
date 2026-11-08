"""Segment-based composition: a card, rotating stock clips (each appearance from a later offset) or Ken Burns
photos, crossfaded and cut to exactly the narration's length. Pure planning + ffmpeg command builders
(unit-tested without ffmpeg) + the runners."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from sof import render as sof_render
from sof import shorts as sof_shorts
from sof import tts

log = logging.getLogger("cc.segments")


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


@dataclass
class Segment:
    kind: str           # card | clip | photo
    src: str            # PNG (card), pre-cut clip (clip) or photo (photo)
    seconds: float      # rendered length (includes the crossfade overlap)
    offset: float = 0.0  # clip: where in the pre-cut clip this appearance starts


# ---------------------------------------------------------------- planning (pure)

def plan_segments(duration: float, clips: list[tuple[str, float]], photos: list[str], *, card: str | None,
                  card_seconds: float, clip_max: float, crossfade: float, min_seconds: float = 2.0) -> list[Segment]:
    """Segments whose crossfaded length covers ``duration``: the card, then the clips in rotation (each appearance
    from a later offset of the clip), or photos when there are no clips; ``clips`` carry their pre-cut lengths.
    With nothing at all the card is held for the whole section."""
    segs: list[Segment] = []
    covered = 0.0

    def add(seg: Segment) -> None:
        nonlocal covered
        segs.append(seg)
        covered += seg.seconds - (crossfade if len(segs) > 1 else 0.0)

    if card:
        add(Segment("card", card, max(card_seconds, min_seconds) + (crossfade if (clips or photos) else 0.0)))
    if not clips and not photos:
        if card:
            segs[0].seconds = max(duration, min_seconds)
        return segs
    k = 0
    uses: dict[str, int] = {}
    while covered < duration - 0.01:
        remaining = duration - covered
        want = min(clip_max, remaining) + crossfade
        if remaining - min(clip_max, remaining) < min_seconds:   # a sliver would follow: take it now
            want = remaining + crossfade
        if clips:
            src, avail = clips[k % len(clips)]
            n = uses.get(src, 0)
            offset = (n * clip_max) % max(avail, 1.0)
            if offset + want > avail and avail >= want:
                offset = 0.0
            uses[src] = n + 1
            add(Segment("clip", src, want, offset=round(offset, 3)))
        else:
            add(Segment("photo", photos[k % len(photos)], want))
        k += 1
        if k > 400:
            break
    return segs


# ---------------------------------------------------------------- ffmpeg commands (pure)

def still_cmd(image: str, out: str, *, seconds: float, width: int, height: int, fps: int) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", image, "-t", _fmt(seconds),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps={fps},setsar=1,format=yuv420p",
            "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
            "-map_metadata", "-1", out]


def clip_window_cmd(src: str, out: str, *, offset: float, seconds: float, fps: int) -> list[str]:
    """``seconds`` of a pre-cut clip from ``offset`` (looping the clip when it is shorter)."""
    return ["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", src, "-ss", _fmt(offset), "-t", _fmt(seconds),
            "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
            "-map_metadata", "-1", out]


def xfade_cmd(clips: list[str], durations: list[float], out: str, *, crossfade: float, fps: int, total: float | None) -> list[str]:
    """``sof.render.xfade_chain_cmd`` plus ``-t total`` so a section is exactly as long as its narration."""
    if len(clips) == 1:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", clips[0], "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "25", "-pix_fmt", "yuv420p", "-an", "-map_metadata", "-1"]
    else:
        cmd = sof_render.xfade_chain_cmd(clips, durations, out, crossfade=crossfade, fps=fps)[:-1]
    if total and total > 0:
        cmd += ["-t", _fmt(total)]
    return cmd + [out]


def concat_cmd(list_file: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out]


# ---------------------------------------------------------------- rendering

def precut(src: str, cache: Path, *, width: int, height: int, fps: int, dim: float, slow: float) -> tuple[str, float]:
    """Cover-crop and normalise one stock clip once per frame size; returns (path, seconds)."""
    cache.mkdir(parents=True, exist_ok=True)
    dst = cache / (hashlib.sha1(f"{src}|{width}x{height}".encode()).hexdigest()[:12] + ".mp4")
    if not dst.exists():
        sof_render.run(sof_render.cover_clip_cmd(src, str(dst), width=width, height=height, fps=fps, dim=dim, slow=slow,
                                                 max_seconds=45.0, skip=0.5), 900)
    return str(dst), max(1.0, tts.duration_seconds(str(dst)))


def render_segments(segs: list[Segment], tmp: Path, *, width: int, height: int, fps: int, crossfade: float, total: float,
                    out: str, dim: float = 1.0) -> str:
    """Render each segment, crossfade them and cut at ``total`` seconds → ``out``."""
    tmp.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    for i, s in enumerate(segs):
        p = str(tmp / f"seg_{i:03d}.mp4")
        if not os.path.exists(p):
            if s.kind == "card":
                sof_render.run(still_cmd(s.src, p, seconds=s.seconds, width=width, height=height, fps=fps), 600)
            elif s.kind == "clip":
                sof_render.run(clip_window_cmd(s.src, p, offset=s.offset, seconds=s.seconds, fps=fps), 900)
            else:
                covered = str(tmp / f"cover_{i:03d}.jpg")
                if width >= height:
                    sof_render.cover_image(s.src, covered, width=width, height=height, scale=3.0, dim=dim)
                else:
                    sof_shorts.cover_vertical(s.src, covered, width=width, height=height)
                sof_render.run(sof_render.ken_burns_cmd(covered, p, dwell=max(0.5, s.seconds - crossfade), crossfade=crossfade,
                                                        width=width, height=height, fps=fps), 900)
        parts.append(p)
    if not parts:
        raise RuntimeError("nothing to render")
    if crossfade <= 0:                                    # hard cuts: concat the normalised segments, trim to length
        joined = str(tmp / "joined.mp4")
        concat_videos(parts, joined, str(tmp / "joined.txt"))
        sof_render.run(["ffmpeg", "-y", "-v", "error", "-i", joined, "-t", _fmt(total), "-c", "copy", "-movflags", "+faststart", out], 1800)
        return out
    sof_render.run(xfade_cmd(parts, [s.seconds for s in segs], out, crossfade=crossfade, fps=fps, total=total), 3600)
    return out


def build_vertical_short(*, work: Path, name: str, text: str, title: str, brand: str, audio: str, clips: list[str], photos: list[str],
                         width: int = 1080, height: int = 1920, fps: int = 30, clip_seconds: float = 6.0, crossfade: float = 0.4,
                         slow: float = 1.0, hold_card: str | None = None) -> str:
    """A vertical Short: the clips (or photos) under the narration, captions and a title band → ``work/<name>.mp4``."""
    tmp = work / f"{name}_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    duration = tts.duration_seconds(audio)
    if duration <= 0:
        raise RuntimeError("short narration has no duration")
    cuts = [precut(c, work / "cover_916", width=width, height=height, fps=fps, dim=1.0, slow=slow) for c in clips]
    segs = plan_segments(duration, cuts, photos, card=hold_card, card_seconds=0, clip_max=clip_seconds, crossfade=crossfade)
    if not segs:
        raise RuntimeError("short has no visuals")
    reel = render_segments(segs, tmp, width=width, height=height, fps=fps, crossfade=crossfade, total=duration,
                           out=str(tmp / "reel.mp4"))
    cues = sof_shorts.caption_cues(text, duration)
    pngs = [sof_shorts.caption_png(t, str(tmp / f"cue_{i:03d}.png"), width=width, height=height) for i, (_, _, t) in enumerate(cues)]
    tpng = sof_shorts.title_png(title, brand, str(tmp / "title.png"), width=width, height=height)
    out = str(work / f"{name}.mp4")
    sof_render.run(sof_shorts.overlay_cmd(reel, audio, out, cues=cues, cue_pngs=pngs, title_png_path=tpng, credit_png_path=None,
                                          duration=duration, fps=fps), 900)
    return out


def concat_videos(paths: list[str], out: str, list_file: str) -> str:
    with open(list_file, "w", encoding="utf-8") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    sof_render.run(concat_cmd(list_file, out), 1800)
    return out


# ---------------------------------------------------------------- a plain card

def title_card_png(out: str, *, title: str, kicker: str = "", footer: str = "", width: int = 1080, height: int = 1920) -> str:
    """Dark gradient card with a kicker line, the title in big caps and a small footer; the hold-frame when a
    section has no footage and no photos."""
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (width, height), (12, 12, 16))
    d = ImageDraw.Draw(im)
    for y in range(height):
        v = 12 + int(18 * (1 - abs(y - height * 0.45) / (height * 0.55)))
        d.line([(0, y), (width, y)], fill=(v, v, v + 6))
    scale = min(width, height)
    small, big, foot = (sof_shorts._font(round(scale * 0.034), bold=False), sof_shorts._font(round(scale * 0.075)),
                        sof_shorts._font(round(scale * 0.028), bold=False))
    y = height * 0.36
    if kicker:
        d.text(((width - d.textlength(kicker, font=small)) / 2, y - round(scale * 0.08)), kicker, font=small, fill=(220, 190, 90))
    for line in sof_shorts._wrap(d, title.upper(), big, int(width * 0.84))[:3]:
        d.text(((width - d.textlength(line, font=big)) / 2, y), line, font=big, fill=(245, 245, 245))
        y += round(scale * 0.075 * 1.15)
    if footer:
        d.text(((width - d.textlength(footer, font=foot)) / 2, height * 0.8), footer, font=foot, fill=(170, 170, 180))
    im.save(out, "PNG")
    return out


# ---------------------------------------------------------------- continuous footage (recap channels)

def plan_sequence(duration: float, clips: list[tuple[str, float]], *, crossfade: float = 0.0, min_seconds: float = 1.5,
                  max_seconds: float = 8.0) -> list[Segment]:
    """Shots played ONCE each, in order, sized to cover ``duration`` exactly: every shot gets an equal share of the
    time (capped at its own length and ``max_seconds``); the time that short shots leave over goes to the longer
    ones; if the shots together are still too short, the list is cycled from the start. ``clips`` carry their
    lengths. The result is what the recap channels do: footage that moves forward with the narration."""
    if not clips or duration <= 0:
        return []
    fit = max(1, int((duration + crossfade) // (min_seconds + crossfade)))
    if len(clips) > fit:                                  # more shots than the time can hold at min_seconds each:
        step = len(clips) / fit                           # keep every k-th shot so the footage still moves forward
        clips = [clips[min(len(clips) - 1, int(i * step))] for i in range(fit)]
    n = len(clips)
    avail = [max(0.0, min(a, max_seconds)) for _, a in clips]
    total_avail = sum(avail)
    extra = crossfade * max(0, n - 1)
    need = duration + extra
    if total_avail <= need + 0.01:                        # not enough footage: play everything, then cycle
        segs = [Segment("clip", src, round(a, 3)) for (src, _), a in zip(clips, avail) if a >= min_seconds]
        covered = sum(s.seconds for s in segs) - crossfade * max(0, len(segs) - 1)
        k = 0
        while covered < duration - 0.01 and segs and k < 400:
            src, a = clips[k % n][0], avail[k % n]
            take = min(a, max(min_seconds, duration - covered + crossfade))
            segs.append(Segment("clip", src, round(take, 3), offset=0.0))
            covered += take - crossfade
            k += 1
        return segs
    share = need / n
    take = [min(a, share) for a in avail]                 # equal shares, capped by each shot's length
    short = need - sum(take)
    for _ in range(4):                                    # hand the shortfall to the shots that have room left
        if short <= 0.01:
            break
        room = [a - t for a, t in zip(avail, take)]
        room_total = sum(room)
        if room_total <= 0:
            break
        for i in range(n):
            give = min(room[i], short * room[i] / room_total)
            take[i] += give
        short = need - sum(take)
    segs = [Segment("clip", src, round(t, 3)) for (src, _), t in zip(clips, take) if t >= min_seconds]
    return segs
