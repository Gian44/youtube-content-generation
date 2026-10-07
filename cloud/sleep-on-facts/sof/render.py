"""Image-slideshow renderer (ported from StoryFactory slideshow_renderer).

1. One Ken Burns clip per image (thread pool).
2. Crossfade clips into partial reels (batched), then the partials into ONE reel — encoded once.
3. Loop the reel under the narration with ``-c:v copy`` so the 3-hour assembly never re-encodes video.
"""

from __future__ import annotations

import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

log = logging.getLogger("sof.render")


def _fmt(v: float) -> str:
    return f"{v:g}"


def run(cmd: list[str], timeout: int) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({cmd[-1]}): {r.stderr[-400:]}")


# ---------------------------------------------------------------- command builders (pure)

ZOOM_END = 1.06     # very slow push-in: 1.00 → 1.06 over the clip (sleep audience; smaller steps too)
CANVAS = 4          # pre-cover at 4× the output so zoompan's whole-pixel rounding is ¼ of an output pixel


def cover_image(src: str, dst: str, *, width: int, height: int, scale: float = CANVAS) -> str:
    """Resize+centre-crop ``src`` once (Pillow, Lanczos) to ``scale``× the output frame.

    Done once per image, not per frame inside ffmpeg, so a 150-clip reel stays affordable.
    """
    from PIL import Image, ImageOps

    tw, th = int(width * scale), int(height * scale)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        ImageOps.fit(im, (tw, th), method=Image.LANCZOS, centering=(0.5, 0.5)).save(dst, "JPEG", quality=94, subsampling=0)
    return dst


def ken_burns_cmd(image: str, out: str, *, dwell: float, crossfade: float, width: int, height: int, fps: int) -> list[str]:
    """Slow, smooth push-in from ONE pre-covered 4× frame (see ``cover_image``).

    Why this shape (measured, not guessed — see tests/test_render_ffmpeg.py):
    * scale(t)+crop quantises to whole output pixels → the picture holds still then jumps ~0.4 px
      every few frames: the "shaking". ``zoompan`` on a 1× frame has the same problem plus blur.
    * ``zoompan`` on a 4× frame rounds its crop window to ¼ output pixel: 5× lower frame-to-frame
      step variance. The 4× canvas is an intermediate (Lanczos up, bicubic down); net, a 2560-px
      photo is still shown below its own resolution. Faster too: one decoded frame feeds every
      output frame instead of ``-loop 1`` re-decoding and re-scaling the JPEG per frame.
    """
    frames = max(1, int(round((dwell + crossfade) * fps)))
    vf = (
        f"zoompan=z='1+{ZOOM_END - 1:g}*on/{max(1, frames - 1)}':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps},setsar=1,format=yuv420p"
    )
    return [
        "ffmpeg", "-y", "-i", image, "-vf", vf, "-frames:v", str(frames), "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-an",
        "-map_metadata", "-1", out,
    ]


def xfade_chain_cmd(clips: list[str], durations: list[float], out: str, *, crossfade: float, fps: int) -> list[str]:
    if len(clips) < 2:
        raise ValueError("xfade chain needs at least two clips")
    cmd: list[str] = ["ffmpeg", "-y"]
    for c in clips:
        cmd += ["-i", c]
    filters, prev, acc = [], "[0]", durations[0]
    for k in range(1, len(clips)):
        label = f"[vx{k}]"
        filters.append(f"{prev}[{k}]xfade=transition=fade:duration={_fmt(crossfade)}:offset={_fmt(acc - crossfade)}{label}")
        prev = label
        acc = acc + durations[k] - crossfade
    cmd += ["-filter_complex", ";".join(filters), "-map", prev, "-r", str(fps), "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p", "-an", "-map_metadata", "-1", out]
    return cmd


def loop_assembly_cmd(reel: str, audio: str, out: str, *, audio_duration: float | None, fps: int) -> list[str]:
    cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", reel, "-i", audio, "-map", "0:v", "-map", "1:a",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]
    cmd += ["-t", str(audio_duration)] if audio_duration and audio_duration > 0 else ["-shortest"]
    cmd += ["-movflags", "+faststart", "-map_metadata", "-1", out]
    return cmd


def plan_batches(n: int, size: int) -> list[tuple[int, int]]:
    return [(s, min(s + size, n)) for s in range(0, n, size)]


# ---------------------------------------------------------------- orchestration

def _render_clips(images: list[str], tmp: Path, *, dwell, crossfade, width, height, fps, workers: int | None) -> list[str]:
    n = max(1, min(workers or max(2, (os.cpu_count() or 2) - 1), len(images)))
    results: list[str | None] = [None] * len(images)

    def one(idx: int, img: str):
        clip = str(tmp / f"clip_{idx:04d}.mp4")
        try:
            covered = cover_image(img, str(tmp / f"cover_{idx:04d}.jpg"), width=width, height=height)
        except Exception as exc:  # noqa: BLE001 — a corrupt download must not kill the reel
            return idx, None, f"cover failed: {exc}"
        r = subprocess.run(ken_burns_cmd(covered, clip, dwell=dwell, crossfade=crossfade, width=width, height=height, fps=fps),
                           capture_output=True, text=True, timeout=600)
        return idx, clip if (r.returncode == 0 and os.path.exists(clip)) else None, r.stderr[-200:]

    log.info("rendering %d Ken Burns clips with %d workers", len(images), n)
    with ThreadPoolExecutor(max_workers=n) as pool:
        for fut in as_completed([pool.submit(one, i, img) for i, img in enumerate(images)]):
            idx, clip, err = fut.result()
            if clip:
                results[idx] = clip
            else:
                log.warning("clip %d failed: %s", idx, err)
    return [c for c in results if c]


def _concat_copy(clips: list[str], tmp: Path, name: str) -> str:
    lst = tmp / f"{name}.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    out = str(tmp / f"{name}.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", out], 1800)
    return out


def build_reel(images: list[str], tmp_dir: str, *, dwell: float, crossfade: float, width: int, height: int, fps: int,
               batch: int, workers: int | None = None) -> str:
    """Return the path of one crossfaded reel built from ``images`` (hard-cut concat as fallback)."""
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    clip_dur = dwell + crossfade
    clips = _render_clips(images, tmp, dwell=dwell, crossfade=crossfade, width=width, height=height, fps=fps, workers=workers)
    if not clips:
        raise RuntimeError("no slideshow clips could be rendered")
    if len(clips) == 1:
        return clips[0]
    try:
        partials, durs = [], []
        for bi, (s, e) in enumerate(plan_batches(len(clips), batch)):
            group = clips[s:e]
            if len(group) == 1:
                partials.append(group[0]); durs.append(clip_dur); continue
            out = str(tmp / f"partial_{bi:03d}.mp4")
            run(xfade_chain_cmd(group, [clip_dur] * len(group), out, crossfade=crossfade, fps=fps), 3600)
            partials.append(out); durs.append(len(group) * clip_dur - (len(group) - 1) * crossfade)
        if len(partials) == 1:
            return partials[0]
        final = str(tmp / "reel.mp4")
        run(xfade_chain_cmd(partials, durs, final, crossfade=crossfade, fps=fps), 7200)
        return final
    except Exception as exc:  # noqa: BLE001 — degrade to hard cuts rather than fail the video
        log.warning("xfade reel failed, falling back to concat: %s", exc)
        return _concat_copy(clips, tmp, "reel_concat")


def assemble(reel: str, narration: str, out: str, *, audio_duration: float, fps: int) -> str:
    run(loop_assembly_cmd(reel, narration, out, audio_duration=audio_duration, fps=fps), 7200)
    return out


def thumbnail(image_path: str, title: str, out: str) -> str:
    """1280×720 JPEG: the image darkened, the title bottom-left in a large serif."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont

    with Image.open(image_path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = max(1280 / w, 720 / h)
        im = im.resize((int(w * scale) + 1, int(h * scale) + 1))
        left, top = (im.width - 1280) // 2, (im.height - 720) // 2
        im = im.crop((left, top, left + 1280, top + 720))
        im = ImageEnhance.Brightness(im).enhance(0.6)
        draw = ImageDraw.Draw(im)
        font = None
        for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
                     "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"):
            if os.path.exists(path):
                font = ImageFont.truetype(path, 72)
                break
        font = font or ImageFont.load_default()
        words, lines, cur = title.split(), [], ""
        for wd in words:
            test = f"{cur} {wd}".strip()
            if draw.textlength(test, font=font) > 1180 and cur:
                lines.append(cur); cur = wd
            else:
                cur = test
        if cur:
            lines.append(cur)
        y = 720 - 60 - 84 * len(lines[:3])
        for line in lines[:3]:
            draw.text((60, y), line, font=font, fill=(245, 240, 230))
            y += 84
        im.save(out, "JPEG", quality=88)
    return out
