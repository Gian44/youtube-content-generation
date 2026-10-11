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
CONTAIN_BELOW = 1.3  # narrower than this (e.g. portrait/square) → shown whole, not cropped


def cover_image(src: str, dst: str, *, width: int, height: int, scale: float = CANVAS, dim: float = 1.0,
                contain_below: float = CONTAIN_BELOW) -> str:
    """Prepare one photo at ``scale``× the output frame (Pillow, Lanczos) — done once per image, not
    per frame inside ffmpeg, so a 150-clip reel stays affordable.

    * Landscape photos (aspect ≥ ``contain_below``) fill the frame (centre crop).
    * Portrait/square photos (statues, seals, tall buildings) are shown WHOLE on a blurred, darkened
      fill of themselves, documentary-style, instead of losing their top and bottom to the crop.
    * ``dim`` < 1 darkens the frame (night-friendly: a phone shouldn't light up the bedroom).
    """
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    tw, th = int(width * scale), int(height * scale)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        if im.width / max(1, im.height) >= contain_below:
            out = ImageOps.fit(im, (tw, th), method=Image.LANCZOS, centering=(0.5, 0.5))
        else:
            small = ImageOps.fit(im, (max(16, tw // 16), max(9, th // 16)), method=Image.BILINEAR)
            bg = small.filter(ImageFilter.GaussianBlur(4)).resize((tw, th), Image.BILINEAR)
            out = ImageEnhance.Brightness(bg).enhance(0.45)
            fg = ImageOps.contain(im, (tw, th), method=Image.LANCZOS)
            out.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
        if dim < 1.0:
            out = ImageEnhance.Brightness(out).enhance(max(0.2, dim))
        out.save(dst, "JPEG", quality=94, subsampling=0)
    return dst


CAPTION_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                 "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf")


def caption_png(text: str, out: str, *, width: int, height: int, alpha: int = 120) -> str:
    """A transparent full-frame PNG with one small, dim credit line at the bottom left.

    Overlaid AFTER the Ken Burns zoom, so the text stays put while the photo moves. ~2 % of the frame
    height and ~47 % opacity: legible when someone looks for it, invisible to a drowsy viewer.
    """
    from PIL import Image, ImageDraw, ImageFont

    size = max(12, round(height * 0.02))
    font = None
    for f in CAPTION_FONTS:
        try:
            font = ImageFont.truetype(f, size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default(size=size)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    margin = round(height * 0.033)
    d.text((margin + 1, height - margin + 1), text, font=font, fill=(0, 0, 0, min(255, alpha)), anchor="ls")
    d.text((margin, height - margin), text, font=font, fill=(255, 255, 255, alpha), anchor="ls")
    im.save(out, "PNG")
    return out


def ken_burns_cmd(image: str, out: str, *, dwell: float, crossfade: float, width: int, height: int, fps: int,
                  caption: str | None = None) -> list[str]:
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
    enc = ["-frames:v", str(frames), "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-pix_fmt", "yuv420p", "-an", "-map_metadata", "-1", out]
    if caption:                       # fixed credit line on top of the moving photo
        return ["ffmpeg", "-y", "-i", image, "-i", caption, "-filter_complex",
                f"[0:v]{vf}[bg];[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]", "-map", "[v]", *enc]
    return ["ffmpeg", "-y", "-i", image, "-vf", vf, *enc]


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

def _render_clips(images: list[str], tmp: Path, *, dwell, crossfade, width, height, fps, workers: int | None,
                  dim: float = 1.0, captions: dict[str, str] | None = None) -> list[str]:
    n = max(1, min(workers or max(2, (os.cpu_count() or 2) - 1), len(images)))
    results: list[str | None] = [None] * len(images)

    def one(idx: int, img: str):
        clip = str(tmp / f"clip_{idx:04d}.mp4")
        try:
            covered = cover_image(img, str(tmp / f"cover_{idx:04d}.jpg"), width=width, height=height, dim=dim)
        except Exception as exc:  # noqa: BLE001 — a corrupt download must not kill the reel
            return idx, None, f"cover failed: {exc}"
        cap = None
        if (captions or {}).get(img):
            cap = caption_png(captions[img], str(tmp / f"caption_{idx:04d}.png"), width=width, height=height)
        r = subprocess.run(ken_burns_cmd(covered, clip, dwell=dwell, crossfade=crossfade, width=width, height=height, fps=fps,
                                         caption=cap),
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
               batch: int, workers: int | None = None, dim: float = 1.0, captions: dict[str, str] | None = None) -> str:
    """Return the path of one crossfaded reel built from ``images`` (hard-cut concat as fallback)."""
    tmp = Path(tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    clip_dur = dwell + crossfade
    clips = _render_clips(images, tmp, dwell=dwell, crossfade=crossfade, width=width, height=height, fps=fps, workers=workers,
                          dim=dim, captions=captions)
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


def sample_frames(video: str, out_dir: str, times: list[float], *, width: int = 960) -> list[str]:
    """Small JPEG stills at ``times`` (seconds) — kept as run artifacts so a run can be checked by eye
    (on-screen credits, which photo shows when) without downloading a 2 GB video."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out = []
    for t in times:
        f = str(Path(out_dir) / f"t{int(t):05d}.jpg")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", video, "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-q:v", "4", f], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(f):
            out.append(f)
    return out
