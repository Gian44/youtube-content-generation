"""Channel-branded thumbnail (1280×720) from the run's own photos — Pillow only, deterministic.

Layout: the most atmospheric photo, cover-cropped and cooled slightly; a dark gradient from the
left and bottom so type always reads; the topic in big bold caps (auto-fit, ≤2 lines); a serif
tagline; a "N HOURS" pill top-right; a small crescent-moon mark + wordmark bottom-right.
Every video gets the same frame, so the channel page reads as one series.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("sof.thumbnail")

W, H = 1280, 720
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
# Same roles on other systems (the backfill script runs on Windows); first hit wins.
FONT_FALLBACKS = {
    "DejaVuSans-Bold.ttf": ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "/Library/Fonts/Arial Bold.ttf",
                            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    "DejaVuSerif-Italic.ttf": ["C:/Windows/Fonts/georgiai.ttf", "C:/Windows/Fonts/timesi.ttf", "/Library/Fonts/Georgia Italic.ttf",
                               "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf"],
}
CREAM = (246, 240, 228)
GOLD = (233, 196, 106)
MUTED = (200, 204, 214)


def _font(name: str, size: int):
    from PIL import ImageFont

    for cand in [str(Path(FONT_DIR) / name), *FONT_FALLBACKS.get(name, [])]:
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    log.warning("no TrueType font found for %s; using Pillow default at size %d", name, size)
    try:
        return ImageFont.load_default(size=size)      # Pillow ≥ 10.1 scales its built-in font
    except TypeError:
        return ImageFont.load_default()


# ------------------------------------------------------------------ photo choice

def score_photo(path: str) -> float:
    """Higher = better thumbnail base: mid-dark, contrasty, darker on the left where the type goes.

    Rejects near-black and washed-out frames. Pure heuristic — no API call — so the daily run has
    one less thing that can fail.
    """
    from PIL import Image, ImageStat

    try:
        with Image.open(path) as im:
            g = im.convert("L").resize((160, 90))
            mean = ImageStat.Stat(g).mean[0]
            std = ImageStat.Stat(g).stddev[0]
            left = ImageStat.Stat(g.crop((0, 0, 70, 90))).mean[0]
            right = ImageStat.Stat(g.crop((90, 0, 160, 90))).mean[0]
    except Exception:  # noqa: BLE001
        return -1.0
    if mean < 25 or mean > 185:
        return 0.0
    mood = 1.0 - abs(mean - 95) / 95           # peak around a dusk-dark 95/255
    contrast = min(std, 70) / 70
    left_dark = max(0.0, min(1.0, (right - left) / 60 + 0.5))
    return 0.45 * mood + 0.35 * contrast + 0.20 * left_dark


def pick_photo(paths: list[str]) -> str:
    scored = sorted(((score_photo(p), p) for p in paths), reverse=True)
    best = scored[0][1] if scored else paths[0]
    log.info("thumbnail photo: %s (score %.2f of %d)", Path(best).name, scored[0][0] if scored else 0, len(paths))
    return best


# ------------------------------------------------------------------ composition

def _cover(im, w: int, h: int):
    """Wide photos fill the frame. Square/portrait ones (a planet's full disk, a statue) are shown whole
    on the RIGHT, over a blurred, darkened fill of themselves, leaving the left for the title."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    im = im.convert("RGB")
    if im.width / max(1, im.height) >= 1.3:
        return ImageOps.fit(im, (w, h), method=Image.LANCZOS, centering=(0.5, 0.45))
    bg = ImageOps.fit(im, (max(16, w // 16), max(9, h // 16)), method=Image.BILINEAR).filter(ImageFilter.GaussianBlur(3))
    bg = ImageEnhance.Brightness(bg.resize((w, h), Image.BILINEAR)).enhance(0.35)
    fg = ImageOps.contain(im, (int(w * 0.62), h), method=Image.LANCZOS)
    # feather the photo's left edge into the fill so there is no visible seam behind the title
    fade = max(1, int(fg.width * 0.18))
    mask = Image.new("L", fg.size, 255)
    ramp = Image.linear_gradient("L").rotate(90, expand=True).resize((fade, fg.height))   # 0 at left → 255
    mask.paste(ramp, (0, 0))
    bg.paste(fg, (w - fg.width, (h - fg.height) // 2), mask)
    return bg


def _cool(im):
    """Slight desaturation + cool shift so every thumbnail shares a night palette."""
    from PIL import Image, ImageEnhance

    im = ImageEnhance.Color(im).enhance(0.8)
    tint = Image.new("RGB", im.size, (28, 42, 78))
    return Image.blend(im, tint, 0.12)


def _scrim(im):
    """Multiply a left→right and bottom→top darkening so type reads on any photo."""
    from PIL import Image

    w, h = im.size
    horiz = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))     # dark left → light right
    vert = Image.linear_gradient("L").resize((w, h))                             # dark top → light bottom
    vert = vert.transpose(Image.FLIP_TOP_BOTTOM)                                 # now dark bottom
    # Map gradients to multipliers: left edge ×0.45 → right ×1.0; bottom ×0.55 → top ×1.0.
    hp = horiz.point(lambda v: int(255 * (0.45 + 0.55 * v / 255)))
    vp = vert.point(lambda v: int(255 * (0.55 + 0.45 * v / 255)))
    from PIL import ImageChops

    mask = ImageChops.multiply(hp, vp).convert("RGB")
    return ImageChops.multiply(im, mask)


def _fit_lines(draw, text: str, font_name: str, max_w: int, start: int, min_size: int = 56):
    """Largest font size at which `text` fits in ≤2 lines of `max_w`."""
    words = text.split()
    for size in range(start, min_size - 1, -6):
        f = _font(font_name, size)
        lines, cur = [], ""
        for w_ in words:
            t = f"{cur} {w_}".strip()
            if draw.textlength(t, font=f) <= max_w:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w_
        if cur:
            lines.append(cur)
        if len(lines) <= 2 and all(draw.textlength(l, font=f) <= max_w for l in lines):
            return f, lines
    f = _font(font_name, min_size)
    return f, [text]


def _moon(draw, cx: int, cy: int, r: int, color):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)


def _moon_cut(im, cx: int, cy: int, r: int):
    """Carve the crescent by pasting the underlying image back over an offset disc."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).ellipse((cx - r + int(r * 0.55), cy - r - int(r * 0.25), cx + r + int(r * 0.55), cy + r - int(r * 0.25)), fill=255)
    return mask


def compose(photo: str, *, topic: str, hours: int, out: str, tagline: str = "Calm facts to fall asleep to",
            brand: str = "SLEEP ON FACTS") -> str:
    from PIL import Image, ImageDraw

    with Image.open(photo) as src:
        base = _scrim(_cool(_cover(src, W, H)))
    frame = base.copy()
    d = ImageDraw.Draw(frame)

    # Topic — big, bold, cream; thin gold rule above it.
    f_topic, lines = _fit_lines(d, topic.upper(), "DejaVuSans-Bold.ttf", max_w=760, start=150)
    line_h = int(f_topic.size * 1.02)
    block_h = line_h * len(lines)
    y = H - 84 - 54 - block_h           # above tagline, above bottom margin
    d.rectangle((64, y - 26, 64 + 110, y - 18), fill=GOLD)
    for ln in lines:
        d.text((60, y), ln, font=f_topic, fill=CREAM)
        y += line_h
    # Tagline — serif, muted.
    d.text((66, y + 6), tagline, font=_font("DejaVuSerif-Italic.ttf", 40), fill=MUTED)

    # Hours pill — top right.
    label = f"{hours} HOUR{'S' if hours != 1 else ''}"
    f_pill = _font("DejaVuSans-Bold.ttf", 44)
    tw = d.textlength(label, font=f_pill)
    px2, py1 = W - 56, 48
    px1, py2 = int(px2 - tw - 56), py1 + 78
    d.rounded_rectangle((px1, py1, px2, py2), radius=39, fill=(10, 14, 28), outline=GOLD, width=3)
    d.text((px1 + 28, py1 + 14), label, font=f_pill, fill=GOLD)

    # Brand — crescent moon + wordmark, bottom right.
    f_brand = _font("DejaVuSans-Bold.ttf", 30)
    bw = d.textlength(brand, font=f_brand)
    bx = int(W - 60 - bw)
    by = H - 60 - 34
    r = 20
    mx, my = bx - 30, by + 16
    _moon(d, mx, my, r, CREAM)
    frame.paste(base.crop((mx - 2 * r, my - 2 * r, mx + 2 * r, my + 2 * r)), (mx - 2 * r, my - 2 * r),
                _moon_cut(frame, mx, my, r).crop((mx - 2 * r, my - 2 * r, mx + 2 * r, my + 2 * r)))
    d = ImageDraw.Draw(frame)
    d.text((bx, by), brand, font=f_brand, fill=CREAM)

    frame.save(out, "JPEG", quality=90, optimize=True)
    return out


def _aspect(path: str) -> float:
    from PIL import Image

    try:
        with Image.open(path) as im:
            return im.width / max(1, im.height)
    except Exception:  # noqa: BLE001
        return 0.0


def make(image_paths: list[str], *, topic: str, hours: int, out: str, candidates: list[str] | None = None,
         choose=None, max_choices: int = 16) -> str:
    """Compose the thumbnail from the best photo.

    ``candidates`` — photos verified to show the subject itself (vision-labelled); the thumbnail is
    never taken from scenery when those exist. ``choose(paths) -> path | None`` picks the most iconic
    one (a vision call) among up to ``max_choices`` (wide first, then square/portrait, which
    ``_cover`` shows whole); the mood/contrast heuristic over wide photos is the fallback.
    """
    pool = [p for p in (candidates or []) if p] or list(image_paths)
    wide = [p for p in pool if _aspect(p) >= 1.3]
    # Offer both shapes (wide first): the most recognisable view of a planet or a statue is often square.
    offer = (wide + [p for p in pool if p not in wide])[:max_choices]
    best = None
    if choose is not None:
        try:
            best = choose(offer)
        except Exception as exc:  # noqa: BLE001
            log.warning("thumbnail chooser failed: %s", exc)
    if not best:
        best = pick_photo(wide or pool)
    log.info("thumbnail photo: %s (%d subject candidates)", Path(best).name, len(pool))
    result = compose(best, topic=topic, hours=hours, out=out)
    Path(_source_file(out)).write_text(str(best), encoding="utf-8")      # for the description's photo credit
    return result


def _source_file(out: str) -> str:
    return str(Path(out).with_suffix(".source.txt"))


def source_of(out: str) -> str | None:
    """Path of the photo the thumbnail at ``out`` was made from (None if unknown)."""
    f = Path(_source_file(out))
    return f.read_text(encoding="utf-8").strip() or None if f.exists() else None
