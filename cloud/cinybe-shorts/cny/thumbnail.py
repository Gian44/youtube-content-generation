"""1280×720 thumbnail in the recap-channel style: a frame of the film, the hook in big gold caps, a red "MOVIE RECAP" tag.
No film name, no poster."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sof import shorts as sof_shorts


def grab_frame(video: str, out: str, *, at: float = 2.0) -> str | None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", video, "-frames:v", "1", "-q:v", "2", out],
                       capture_output=True, text=True, timeout=120)
    return out if r.returncode == 0 and Path(out).exists() else None


def trim_bars(im, *, threshold: int = 18, keep: float = 0.5):
    """Crop the letterbox / pillarbox bars off a frame (the shots are padded to 16:9, a 2.39:1 film has black bands);
    never crops more than half the frame away."""
    from PIL import ImageStat

    g = im.convert("L")
    w, h = g.size

    def dark_rows(rng):
        n = 0
        for y in rng:
            if ImageStat.Stat(g.crop((0, y, w, y + 1))).mean[0] > threshold:
                break
            n += 1
        return n

    def dark_cols(rng):
        n = 0
        for x in rng:
            if ImageStat.Stat(g.crop((x, 0, x + 1, h))).mean[0] > threshold:
                break
            n += 1
        return n

    top, bottom = dark_rows(range(h)), dark_rows(range(h - 1, -1, -1))
    left, right = dark_cols(range(w)), dark_cols(range(w - 1, -1, -1))
    if top + bottom > h * keep:
        top = bottom = 0
    if left + right > w * keep:
        left = right = 0
    return im.crop((left, top, w - right, h - bottom)) if (top or bottom or left or right) else im


def compose(*, base: str | None, out: str, hook: str, tag: str = "MOVIE RECAP", width: int = 1280, height: int = 720) -> str:
    """The film's own frame, a dark band at the bottom, the hook in big gold caps (3-5 words), a small tag. No film name
    (the sample channels never show it)."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageOps

    if base:
        with Image.open(base) as im:
            bg = ImageOps.fit(trim_bars(im.convert("RGB")), (width, height), method=Image.LANCZOS)
        bg = ImageEnhance.Contrast(ImageEnhance.Brightness(bg).enhance(0.9)).enhance(1.15)
    else:
        bg = Image.new("RGB", (width, height), (18, 18, 24))
    d = ImageDraw.Draw(bg, "RGBA")
    gold = sof_shorts._font(round(height * 0.125))
    lines = sof_shorts._wrap(d, (hook or tag).upper(), gold, int(width * 0.92))[:3]
    lh = round(height * 0.125 * 1.12)
    band_top = height - lh * len(lines) - round(height * 0.14)
    d.rectangle([0, band_top, width, height], fill=(0, 0, 0, 150))
    y = band_top + round(height * 0.04)
    for line in lines:
        x = (width - d.textlength(line, font=gold)) / 2
        for dx, dy in ((-4, 0), (4, 0), (0, -4), (0, 4), (4, 4), (-4, -4)):
            d.text((x + dx, y + dy), line, font=gold, fill=(0, 0, 0, 255))
        d.text((x, y), line, font=gold, fill=(255, 205, 60, 255))
        y += lh
    small = sof_shorts._font(round(height * 0.045))
    d.rectangle([round(width * 0.03), round(height * 0.04), round(width * 0.03) + d.textlength(tag, font=small) + 28, round(height * 0.04) + round(height * 0.075)],
                fill=(200, 30, 30, 230))
    d.text((round(width * 0.03) + 14, round(height * 0.05)), tag, font=small, fill=(255, 255, 255, 255))
    bg.save(out, "JPEG", quality=90)
    return out
