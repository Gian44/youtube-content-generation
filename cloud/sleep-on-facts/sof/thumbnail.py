"""Channel-branded thumbnail (1280×720) from the run's own photos — Pillow only, deterministic.

Layout: the photo that shows the subject most clearly, cover-cropped; a dark gradient from the
left and bottom so type always reads; the topic in big bold caps (auto-fit, ≤2 lines); under it the
video's hook in gold caps (``hook`` — the metadata model's 2-4 word ``thumb_text``; the old serif
tagline only when there is none); a "N HOURS" pill top-right; a small crescent-moon mark + wordmark
bottom-right. Every video gets the same frame, so the channel page reads as one series.

2026-10-25: the frame was too dim for where it is seen — a 168-px tile in YouTube's dark sidebar at
night — and the generic tagline gave no reason to click (1.4 % CTR on Suggested). The photo now
keeps its colour (lighter night tint, gentle lift for dark photos) and the hook replaces the tagline.
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

def _cover(im, w: int, h: int, *, subject_side: str = ""):
    """Wide photos fill the frame. Square/portrait ones (a planet's full disk, a statue) are shown whole
    on the RIGHT, over a blurred, darkened fill of themselves, leaving the left for the title.

    ``subject_side="left"`` (from the vision check): the title would sit on the subject (2026-10-25: the
    Honey Bees bee was under "HONEY BEES"), so the photo is mirrored. The vision check has already
    confirmed it carries no text, so nothing reads backwards."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    im = im.convert("RGB")
    if subject_side == "left":
        im = ImageOps.mirror(im)
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
    """A light night tint so the series shares a palette, without dulling the photo: dark photos are
    lifted and colour is kept (a dim, grey tile disappears in YouTube's dark sidebar)."""
    from PIL import Image, ImageEnhance, ImageStat

    mean = ImageStat.Stat(im.convert("L").resize((64, 36))).mean[0]
    if mean < 90:
        im = ImageEnhance.Brightness(im).enhance(min(1.35, 90 / max(mean, 30)))
    im = ImageEnhance.Contrast(im).enhance(1.08)
    im = ImageEnhance.Color(im).enhance(1.1)
    tint = Image.new("RGB", im.size, (28, 42, 78))
    return Image.blend(im, tint, 0.05)


def _scrim(im):
    """Multiply a left→right and bottom→top darkening so type reads on any photo."""
    from PIL import Image

    w, h = im.size
    horiz = Image.linear_gradient("L").rotate(90, expand=True).resize((w, h))     # dark left → light right
    vert = Image.linear_gradient("L").resize((w, h))                             # dark top → light bottom
    vert = vert.transpose(Image.FLIP_TOP_BOTTOM)                                 # now dark bottom
    # Map gradients to multipliers: left edge ×0.40 → right ×1.0; bottom ×0.55 → top ×1.0.
    hp = horiz.point(lambda v: int(255 * (0.40 + 0.60 * v / 255)))
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


def _hook_font(draw, text: str, max_w: int, start: int = 64, min_size: int = 40):
    for size in range(start, min_size - 1, -4):
        f = _font("DejaVuSans-Bold.ttf", size)
        if draw.textlength(text, font=f) <= max_w:
            return f
    return _font("DejaVuSans-Bold.ttf", min_size)


def clean_thumb_text(text: str, topic: str = "") -> str:
    """The hook line as drawn: capitals, 2-5 words, ≤26 characters, no hype/fear/question; '' if unusable."""
    import re

    from sof.seo import _HOOK_BANNED

    t = re.sub(r"\s+", " ", str(text or "")).strip(" .,:;-–—'‘’\"").upper()
    if not t or not 1 <= len(t.split()) <= 5 or len(t) > 26 or _HOOK_BANNED.search(t):
        return ""                                # (a single question mark is allowed only at the very end)
    if topic and t == topic.strip().upper():
        return ""
    return t


def compose(photo: str, *, topic: str, hours: float | int, out: str, tagline: str = "Calm facts to fall asleep to",
            brand: str = "SLEEP ON FACTS", hook: str = "", subject_side: str = "") -> str:
    from PIL import Image, ImageDraw

    with Image.open(photo) as src:
        base = _scrim(_cool(_cover(src, W, H, subject_side=subject_side)))
    frame = base.copy()
    d = ImageDraw.Draw(frame)
    hook = clean_thumb_text(hook, topic)

    # Topic — big, bold, cream; thin gold rule above it.
    f_topic, lines = _fit_lines(d, topic.upper(), "DejaVuSans-Bold.ttf", max_w=760, start=150)
    line_h = int(f_topic.size * 1.02)
    block_h = line_h * len(lines)
    f_hook = _hook_font(d, hook, 760) if hook else None
    under = (f_hook.size + 18) if f_hook else 54
    y = H - 84 - under - block_h        # above the hook/tagline, above the bottom margin
    d.rectangle((64, y - 26, 64 + 110, y - 18), fill=GOLD)
    for ln in lines:
        d.text((60, y), ln, font=f_topic, fill=CREAM)
        y += line_h
    if f_hook:
        # The hook — bold gold caps with a soft shadow, so it reads on a 168-px tile.
        d.text((66 + 3, y + 10 + 3), hook, font=f_hook, fill=(0, 0, 0))
        d.text((66, y + 10), hook, font=f_hook, fill=GOLD)
    else:
        # Tagline — serif, muted.
        d.text((66, y + 6), tagline, font=_font("DejaVuSerif-Italic.ttf", 40), fill=MUTED)

    # Hours pill — top right.
    from sof.seo import fmt_hours

    h = fmt_hours(hours)
    label = f"{h} HOUR{'' if h == '1' else 'S'}"
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


def make(image_paths: list[str], *, topic: str, hours: float | int, out: str, candidates: list[str] | None = None,
         choose=None, max_choices: int = 16, hook: str = "", confirm=None, attempts: int = 4) -> str:
    """Compose the thumbnail from the best photo.

    ``candidates`` — photos verified to show the subject itself (vision-labelled); the thumbnail is
    never taken from scenery when those exist. ``choose(paths) -> path | None`` picks the most iconic
    one (a vision call) among up to ``max_choices`` (wide first, then square/portrait, which
    ``_cover`` shows whole); the mood/contrast heuristic over wide photos is the fallback.
    ``confirm(path) -> {"ok", "where"} | bool | None`` double-checks the pick (subject large, no printed
    text); not ok drops that photo and asks again, up to ``attempts`` times (2026-10-25: an Indus pick
    had a scanned caption); "where" = left moves the subject out from under the title.
    """
    pool = [p for p in (candidates or []) if p] or list(image_paths)
    wide = [p for p in pool if _aspect(p) >= 1.3]
    # Offer both shapes (wide first): the most recognisable view of a planet or a statue is often square.
    offer = (wide + [p for p in pool if p not in wide])[:max_choices]
    best, side = None, ""
    for _ in range(max(1, attempts)):
        pick = None
        if choose is not None and offer:
            try:
                pick = choose(offer)
            except Exception as exc:  # noqa: BLE001
                log.warning("thumbnail chooser failed: %s", exc)
        if not pick:
            break
        verdict = confirm(pick) if confirm is not None else None
        ok = verdict.get("ok") if isinstance(verdict, dict) else verdict
        if ok is False:
            offer = [p for p in offer if p != pick]
            continue
        best, side = pick, (verdict.get("where", "") if isinstance(verdict, dict) else "")
        break
    if not best:
        rest = [p for p in (wide or pool) if p in offer] or offer or wide or pool
        best = pick_photo(rest)
    log.info("thumbnail photo: %s (%d subject candidates)", Path(best).name, len(pool))
    result = compose(best, topic=topic, hours=hours, out=out, hook=hook, subject_side=side)
    Path(_source_file(out)).write_text(str(best), encoding="utf-8")      # for the description's photo credit
    return result


def _source_file(out: str) -> str:
    return str(Path(out).with_suffix(".source.txt"))


def source_of(out: str) -> str | None:
    """Path of the photo the thumbnail at ``out`` was made from (None if unknown)."""
    f = Path(_source_file(out))
    return f.read_text(encoding="utf-8").strip() or None if f.exists() else None


def compose_marathon(photos: list[str], *, label: str, names: list[str], hours: int, out: str,
                     brand: str = "SLEEP ON FACTS") -> str:
    """Marathon thumbnail: the segments' own thumbnail photos as side-by-side panels (it reads as a
    compilation at a glance), the length big ("9 HOURS"), the category under it and the subjects in gold."""
    from PIL import Image, ImageDraw

    photos = [p for p in photos if p][:4] or []
    if not photos:
        raise ValueError("no photos for the marathon thumbnail")
    k = len(photos)
    base = Image.new("RGB", (W, H), (10, 14, 28))
    pw = W // k
    for i, p in enumerate(photos):
        with Image.open(p) as src:
            from PIL import ImageOps
            panel = ImageOps.fit(src.convert("RGB"), (pw, H), method=Image.LANCZOS, centering=(0.5, 0.45))
        base.paste(panel, (i * pw, 0))
    base = _scrim(_cool(base))
    d = ImageDraw.Draw(base)
    for i in range(1, k):                                       # thin dark seams between panels
        d.rectangle((i * pw - 3, 0, i * pw + 3, H), fill=(10, 14, 28))
    frame = base
    big = f"{max(1, int(hours))} HOURS"
    f_big = _hook_font(d, big, 1000, start=170, min_size=90)
    f_label = _hook_font(d, label.upper(), 1000, start=72, min_size=40)
    line = " · ".join(n.upper() for n in names)
    f_names = _hook_font(d, line, 1100, start=50, min_size=26)
    y = H - 84 - f_names.size - 22 - f_label.size - 14 - int(f_big.size * 1.0)
    d.rectangle((64, y - 26, 64 + 110, y - 18), fill=GOLD)
    d.text((60, y), big, font=f_big, fill=CREAM)
    y += int(f_big.size * 1.0) + 14
    d.text((64, y), label.upper(), font=f_label, fill=CREAM)
    y += f_label.size + 22
    d.text((66 + 3, y + 3), line, font=f_names, fill=(0, 0, 0))
    d.text((66, y), line, font=f_names, fill=GOLD)
    f_brand = _font("DejaVuSans-Bold.ttf", 30)
    bw = d.textlength(brand, font=f_brand)
    d.text((int(W - 60 - bw), 48), brand, font=f_brand, fill=CREAM)
    frame.save(out, "JPEG", quality=90, optimize=True)
    return out
