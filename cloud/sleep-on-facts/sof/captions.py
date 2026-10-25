"""On-screen photo credits.

Wikimedia Commons photos under CC BY / CC BY-SA (and the Commons "Attribution" licence) must credit
their author. The channel does not link an external credits page (Gian, 2026-10-11: no link that leads
to the code), so each such photo carries a small, dim caption while it is on screen, and every
description carries one licence line. Public-domain, CC0, NASA, Pexels and Pixabay photos need no credit.
"""

from __future__ import annotations

import re
from pathlib import Path

_ATTRIBUTION = re.compile(r"\bCC[ -]?BY\b|^Attribution\b", re.I)

DESCRIPTION_LINE = ("Photos: Wikimedia Commons contributors, NASA, Pexels and Pixabay; footage: Pexels and Pixabay. "
                    "Photos that need a credit show their author and licence on screen. "
                    "Licences: https://creativecommons.org/licenses/")


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
