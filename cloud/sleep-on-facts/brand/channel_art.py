"""Generator for the channel art in this folder (rendered 2026-10-11 with Poppins + Lora, both SIL OFL;
run: python channel_art.py <out_dir> on a machine where GF points at those fonts).

Sleep On Facts channel art: profile picture (800x800) and banner (2560x1440, safe area 1546x423).

Palette and motifs match the video thumbnails: deep night navy, gold crescent, cream wordmark.
"""
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
GF = "/usr/share/fonts/truetype/google-fonts/"
NAVY_TOP, NAVY_MID, NAVY_LOW = (7, 10, 22), (14, 21, 42), (28, 40, 72)
GOLD, GOLD_SOFT = (233, 196, 106), (246, 214, 140)
CREAM, MUTED = (246, 240, 228), (176, 184, 202)
SS = 3  # supersampling for smooth curves


def vgradient(w, h, stops):
    """Vertical gradient through (position 0..1, rgb) stops."""
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                k = (t - p0) / max(1e-6, p1 - p0)
                k = k * k * (3 - 2 * k)
                px[0, y] = tuple(round(a + (b - a) * k) for a, b in zip(c0, c1))
                break
    return col.resize((w, h))


def radial_glow(w, h, cx, cy, r, color, strength):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).ellipse((cx - r, cy - r, cx + r, cy + r), fill=int(255 * strength))
    m = m.filter(ImageFilter.GaussianBlur(r * 0.6))
    layer = Image.new("RGB", (w, h), color)
    return layer, m


def crescent_mask(size, r, cx, cy, cut=0.62, tilt=-0.55):
    """Crescent = disc minus an offset disc (offset up-right so it opens toward the upper right)."""
    m = Image.new("L", (size[0] * SS, size[1] * SS), 0)
    d = ImageDraw.Draw(m)
    R, X, Y = r * SS, cx * SS, cy * SS
    d.ellipse((X - R, Y - R, X + R, Y + R), fill=255)
    off = R * cut
    ox, oy = X + off * math.cos(tilt), Y + off * math.sin(tilt)
    R2 = R * 0.86
    d.ellipse((ox - R2, oy - R2, ox + R2, oy + R2), fill=0)
    return m.resize(size, Image.LANCZOS)


def stars(img, n, seed, *, region=None, max_r=2.2, avoid=None):
    rnd = random.Random(seed)
    w, h = img.size
    x0, y0, x1, y1 = region or (0, 0, w, h)
    layer = Image.new("L", (w * SS, h * SS), 0)
    d = ImageDraw.Draw(layer)
    for _ in range(n):
        x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
        if avoid and avoid(x, y):
            continue
        r = rnd.choice([0.6, 0.8, 1.0, 1.0, 1.3, 1.6, max_r]) * SS
        a = rnd.randint(70, 230)
        d.ellipse((x * SS - r, y * SS - r, x * SS + r, y * SS + r), fill=a)
        if r > 1.5 * SS and rnd.random() < 0.5:                       # a few soft glints
            g = r * 3.2
            d.line((x * SS - g, y * SS, x * SS + g, y * SS), fill=a // 3, width=max(1, SS // 2))
            d.line((x * SS, y * SS - g, x * SS, y * SS + g), fill=a // 3, width=max(1, SS // 2))
    layer = layer.resize((w, h), Image.LANCZOS)
    tint = Image.new("RGB", (w, h), (236, 232, 222))
    return Image.composite(tint, img, layer)


def paste_crescent(img, cx, cy, r, *, glow=0.55, haze=0.45):
    w, h = img.size
    if haze > 0:                                   # wide moonlight haze (gold over navy turns grey if overdone)
        layer, m = radial_glow(w, h, cx, cy, int(r * 1.7), GOLD, glow * haze)
        img = Image.composite(layer, img, m)
    cm = crescent_mask((w, h), r, cx, cy)
    halo = cm.filter(ImageFilter.GaussianBlur(r * 0.18)).point(lambda v: int(v * glow))
    img = Image.composite(Image.new("RGB", (w, h), GOLD_SOFT), img, halo)
    grad = vgradient(w, h, [(0, GOLD_SOFT), (1, GOLD)])
    return Image.composite(grad, img, cm)


def tracked(draw, xy, text, font, fill, tracking, anchor_center=True):
    widths = [draw.textlength(ch, font=font) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x, y = xy
    if anchor_center:
        x -= total / 2
    for ch, wch in zip(text, widths):
        draw.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += wch + tracking
    return total


def profile(out):
    S = 800
    img = vgradient(S, S, [(0, (9, 13, 30)), (1, (17, 26, 54))])
    layer, m = radial_glow(S, S, 400, 400, 300, (30, 46, 92), 0.75)          # deep blue halo, not grey
    img = Image.composite(layer, img, m)
    rnd = random.Random(5)
    pts = [(505, 300, 3.2), (560, 395, 2.2), (470, 230, 1.6), (590, 300, 1.4), (530, 470, 1.5)]   # stars in the crescent's arms
    sl = Image.new("L", (S * SS, S * SS), 0)
    d = ImageDraw.Draw(sl)
    for x, y, r in pts:
        d.ellipse(((x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS), fill=230)
        if r > 2:
            g = r * 4
            d.line(((x - g) * SS, y * SS, (x + g) * SS, y * SS), fill=110, width=SS)
            d.line((x * SS, (y - g) * SS, x * SS, (y + g) * SS), fill=110, width=SS)
    sl = sl.resize((S, S), Image.LANCZOS)
    img = Image.composite(Image.new("RGB", (S, S), (240, 236, 226)), img, sl)
    img = stars(img, 26, seed=7, max_r=1.6, avoid=lambda x, y: (x - 400) ** 2 + (y - 400) ** 2 < 290 ** 2
                or (x - 400) ** 2 + (y - 400) ** 2 > 370 ** 2)
    img = paste_crescent(img, 385, 405, 205, glow=0.65, haze=0.08)
    img.save(out, "PNG", optimize=True)
    return out


def banner(out):
    W, H = 2560, 1440
    img = vgradient(W, H, [(0, NAVY_TOP), (0.42, NAVY_MID), (0.66, (24, 36, 66)), (0.7, (40, 56, 92)), (1, (9, 13, 26))])
    # Horizon glow + calm sea, visible on TVs (full 2560x1440); desktop shows only the middle band.
    layer, m = radial_glow(W, H, 1280, 1010, 900, (60, 80, 128), 0.5)
    img = Image.composite(layer, img, m)
    sea = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sea).rectangle((0, 1010, W, H), fill=255)
    img = Image.composite(vgradient(W, H, [(0, (16, 24, 46)), (0.7, (16, 24, 46)), (1, (6, 9, 18))]), img, sea)
    # moon-path shimmer on the water under the crescent
    shimmer = Image.new("L", (W * SS, H * SS), 0)
    d = ImageDraw.Draw(shimmer)
    rnd = random.Random(3)
    for i in range(70):
        y = 1016 + i * 6 + rnd.uniform(-2, 2)
        half = (30 + i * 3.2) * rnd.uniform(0.35, 1.0)
        x = 1280 + rnd.uniform(-1, 1) * i * 1.8
        a = int(150 * (1 - i / 75))
        d.rounded_rectangle(((x - half) * SS, y * SS, (x + half) * SS, (y + 2.2) * SS), radius=SS, fill=a)
    shimmer = shimmer.resize((W, H), Image.LANCZOS).filter(ImageFilter.GaussianBlur(2.2)).point(lambda v: int(v * 0.8))
    img = Image.composite(Image.new("RGB", (W, H), GOLD_SOFT), img, shimmer)
    img = stars(img, 520, seed=11, region=(0, 0, W, 990), max_r=2.4,
                avoid=lambda x, y: 520 < x < 2040 and 560 < y < 900)            # keep the text block clean
    img = paste_crescent(img, 1280, 606, 52, glow=0.7)
    d = ImageDraw.Draw(img)
    word = ImageFont.truetype(GF + "Poppins-Medium.ttf", 96)
    tracked(d, (1280, 768), "SLEEP ON FACTS", word, CREAM, tracking=24)
    tag = ImageFont.truetype(GF + "Lora-Italic-Variable.ttf", 48)
    d.text((1280, 840), "Calm, true facts to fall asleep to", font=tag, fill=MUTED, anchor="ms")
    small = ImageFont.truetype(GF + "Poppins-Medium.ttf", 24)
    tracked(d, (1280, 893), "A NEW LONG, GENTLE VIDEO EVERY NIGHT", small, GOLD, tracking=7)
    img.save(out, "PNG", optimize=True)
    return out


def previews(banner_path, out_dir):
    """What desktop (2560x423 band) and mobile (1546x423 safe area) actually show."""
    im = Image.open(banner_path)
    desk = im.crop((0, 508, 2560, 931))
    mob = im.crop((507, 508, 2053, 931))
    desk.save(out_dir / "preview-desktop.png"); mob.save(out_dir / "preview-mobile.png")
    small = Image.open(OUT / "sleep-on-facts-profile.png").resize((98, 98), Image.LANCZOS)
    circ = Image.new("L", (98, 98), 0); ImageDraw.Draw(circ).ellipse((0, 0, 97, 97), fill=255)
    bg = Image.new("RGB", (98, 98), (255, 255, 255)); bg.paste(small, (0, 0), circ); bg.save(out_dir / "preview-avatar-98.png")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    p = profile(OUT / "sleep-on-facts-profile.png")
    b = banner(OUT / "sleep-on-facts-banner.png")
    previews(b, OUT)
    print(p, b, Path(p).stat().st_size, Path(b).stat().st_size)
