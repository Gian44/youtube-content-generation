"""1280×720 thumbnail: a darkened frame of the film's own b-roll, the title in big caps, the hook in gold.
No poster, no stills from the film."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sof import shorts as sof_shorts


def grab_frame(video: str, out: str, *, at: float = 2.0) -> str | None:
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", video, "-frames:v", "1", "-q:v", "2", out],
                       capture_output=True, text=True, timeout=120)
    return out if r.returncode == 0 and Path(out).exists() else None


def compose(*, base: str | None, out: str, film: str, year: int | None, hook: str, width: int = 1280, height: int = 720) -> str:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

    if base:
        with Image.open(base) as im:
            bg = ImageOps.fit(im.convert("RGB"), (width, height), method=Image.LANCZOS)
        bg = ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(1.2))).enhance(0.55)
    else:
        bg = Image.new("RGB", (width, height), (18, 18, 24))
    d = ImageDraw.Draw(bg, "RGBA")
    d.rectangle([0, int(height * 0.55), width, height], fill=(0, 0, 0, 120))
    big = sof_shorts._font(round(height * 0.14))
    lines = sof_shorts._wrap(d, film.upper(), big, int(width * 0.9))[:2]
    y = round(height * 0.08)
    for line in lines:
        x = (width - d.textlength(line, font=big)) / 2
        for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (3, 3)):
            d.text((x + dx, y + dy), line, font=big, fill=(0, 0, 0, 255))
        d.text((x, y), line, font=big, fill=(255, 255, 255, 255))
        y += round(height * 0.14 * 1.1)
    tag = f"FULL RECAP{' · ' + str(year) if year else ''}"
    small = sof_shorts._font(round(height * 0.05), bold=False)
    d.text(((width - d.textlength(tag, font=small)) / 2, y + 6), tag, font=small, fill=(230, 230, 230, 255))
    if hook:
        gold = sof_shorts._font(round(height * 0.095))
        hl = sof_shorts._wrap(d, hook.upper(), gold, int(width * 0.9))[:2]
        yy = round(height * 0.66)
        for line in hl:
            x = (width - d.textlength(line, font=gold)) / 2
            for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3)):
                d.text((x + dx, yy + dy), line, font=gold, fill=(0, 0, 0, 255))
            d.text((x, yy), line, font=gold, fill=(255, 205, 60, 255))
            yy += round(height * 0.095 * 1.15)
    bg.save(out, "JPEG", quality=90)
    return out
