"""Clean, simple icons instead of words (Felix, Puello Media, 2026-11: replace pop-up text cards with footage plus
clean, simple icons). An icon is one colour emoji drawn as an image: Segoe UI Emoji on Windows scales freely; Noto
Color Emoji (the runners' ``fonts-noto-color-emoji``) is a bitmap font that only loads at size 109, so it is drawn
there and resized. Shared by Block Quest Daily (panels) and sylusnix (ranking list)."""

from __future__ import annotations

FONTS_EMOJI = [r"C:\Windows\Fonts\seguiemj.ttf", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"]


def emoji_image(text: str, target_h: int = 96):
    """``text`` (one or a few emoji) as an RGBA image ``target_h`` pixels tall, or None when no emoji font can draw
    it (callers then simply leave the icon out)."""
    from PIL import Image, ImageDraw, ImageFont

    text = "".join(ch for ch in str(text or "") if not ch.isspace())
    if not text:
        return None
    for p in FONTS_EMOJI:
        for size in (target_h, 109):
            try:
                f = ImageFont.truetype(p, size)
                im = Image.new("RGBA", (int(f.getlength(text)) + 16, int(size * 1.5)), (0, 0, 0, 0))
                ImageDraw.Draw(im).text((8, 4), text, font=f, embedded_color=True)
            except Exception:  # noqa: BLE001 — a font that is missing or refuses the size: try the next
                continue
            box = im.getbbox()
            if not box:
                continue
            im = im.crop(box)
            s = target_h / im.height
            return im.resize((max(1, int(im.width * s)), target_h), Image.LANCZOS)
    return None


def is_emoji(ch: str) -> bool:
    """True for characters drawn from the emoji font (the same split the ranking labels use)."""
    return ord(ch) >= 0x2190 and ord(ch) not in (0x200D, 0xFE0F)
