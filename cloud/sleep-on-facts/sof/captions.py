"""Photo credits: on screen, on the credits page, or in the description.

Wikimedia Commons photos under CC BY / CC BY-SA (and the Commons "Attribution" licence) must credit
their author. Since 2026-11-07 the credit lives on the video's own page (sof/credits_site.py, on
Cloudflare — nothing there leads to the code, which was the 2026-10-11 objection to a credits page) and
the description carries one line with the link; the frame stays clean. Without that page (secrets
missing, or Cloudflare down for the run) the older forms still apply: a small, dim caption while the
photo is on screen when the video is yet to be rendered, else the author list in the description.
Public-domain, CC0, NASA, Pexels and Pixabay photos need no credit.
"""

from __future__ import annotations

import re
from pathlib import Path

_ATTRIBUTION = re.compile(r"\bCC[ -]?BY\b|^Attribution\b", re.I)

SOURCES = "Photos: Wikimedia Commons contributors, NASA, Pexels and Pixabay; footage: Pexels and Pixabay."
LICENCES = "Licences: https://creativecommons.org/licenses/"
DESCRIPTION_LINE = f"{SOURCES} Photos that need a credit show their author and licence on screen. {LICENCES}"


def description_line(urls: list[str] | str | None = None) -> str:
    """The description's photo line: with credits-page URL(s) it points there; without, the on-screen form."""
    urls = [u for u in ([urls] if isinstance(urls, str) else (urls or [])) if u]
    if not urls:
        return DESCRIPTION_LINE
    if len(urls) == 1:
        return f"{SOURCES} Photo credits (authors and licences): {urls[0]}"
    return f"{SOURCES} Photo credits for each part: " + " · ".join(urls)


def inline_credits(rows: list[dict], *, budget: int = 1800) -> str:
    """Fallback description line naming every author that must be credited, within ``budget``
    characters (one entry per author + licence; cut with "and others" rather than mid-name)."""
    parts: list[str] = []
    for r in rows:
        if not needs_credit(r):
            continue
        part = f"{_short(_clean(r.get('artist')), 60) or 'Unknown author'} ({str(r.get('license') or '').strip()})"
        if part not in parts:
            parts.append(part)
    if not parts:
        return DESCRIPTION_LINE.replace(" Photos that need a credit show their author and licence on screen.", "")
    head, tail = f"{SOURCES} Photo credits (Wikimedia Commons): ", f". {LICENCES}"
    body, kept = "", 0
    for p in parts:
        trial = body + ("; " if body else "") + p
        if len(head) + len(trial) + len(tail) + len("; and others") > budget:
            break
        body, kept = trial, kept + 1
    if kept < len(parts):
        body += "; and others"
    return head + body + tail


def needs_credit(row: dict) -> bool:
    return str(row.get("source") or "") == "commons" and bool(_ATTRIBUTION.search(str(row.get("license") or "").strip()))


_ASSUMED = re.compile(r"No machine-readable author provided\.?\s*(.+?)\s+assumed\b.*", re.I | re.S)
# Commons user links "Name (talk · contribs)" lose their tags and come out as "Name ( talk · contribs )"
_TALK = re.compile(r"\(\s*talk\b[^()]*(?:\)|$)", re.I)


def _clean(s: object) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(s or ""))).strip(" .,;")
    m = _ASSUMED.match(t)                # Commons boilerplate: "No machine-readable author provided. X assumed (…)"
    if m:
        t = m.group(1).strip(" .,;")
    t = re.sub(r"\s+", " ", _TALK.sub(" ", t)).strip(" .,;")
    return re.sub(r"^No machine-readable author provided\.?$", "", t, flags=re.I).strip()


def _short(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;/-")
    return (cut or s[:n]) + "…"


# What the caption fonts can draw (DejaVu Sans + a CJK font, see render.caption_png); other scripts would
# be dropped at render time, so a name written only in them reads "Unknown author" instead of vanishing.
_DRAWABLE = re.compile("[\u0020-\u06ff\u10a0-\u10ff\u1e00-\u1fff\u2000-\u2bff\u3000-\u30ff\u3400-\u4dbf"
                       "\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")


def _drawable(s: str) -> str:
    t = re.sub(r"\s+", " ", "".join(ch for ch in s if _DRAWABLE.match(ch))).strip(" .,;")
    # drop only unbalanced edge brackets: "Jane Doe (photographer)" keeps its ")"
    while t.endswith(")") and t.count(")") > t.count("("):
        t = t[:-1].rstrip(" .,;")
    while t.startswith("(") and t.count("(") > t.count(")"):
        t = t[1:].lstrip(" .,;")
    if t.startswith("(") and t.endswith(")") and t.count("(") == 1:
        t = t[1:-1].strip(" .,;")
    return t


def caption_text(row: dict) -> str:
    """One short line: ``Photo: <author> · <licence> · Wikimedia Commons``."""
    artist = _short(_drawable(_clean(row.get("artist"))), 60) or "Unknown author"
    return f"Photo: {artist} · {str(row.get('license') or '').strip()} · Wikimedia Commons"


def captions_for(paths: list[str], rows: list[dict]) -> dict[str, str]:
    """``path → caption`` for the photos in ``paths`` that need a credit (rows from credits.json, by file name)."""
    by_file = {str(r.get("file") or ""): r for r in rows}
    out: dict[str, str] = {}
    for p in paths:
        row = by_file.get(Path(p).name)
        if row and needs_credit(row):
            out[p] = caption_text(row)
    return out


def thumbnail_line(row: dict | None) -> str:
    """Description line for a thumbnail photo that needs a credit ('' otherwise)."""
    if not row or not needs_credit(row):
        return ""
    page = str(row.get("page") or "").strip()
    return f"Thumbnail photo: {_short(_clean(row.get('artist')), 80) or 'Unknown author'}, " \
           f"{str(row.get('license') or '').strip()}, via Wikimedia Commons{(' — ' + page) if page else ''}"
