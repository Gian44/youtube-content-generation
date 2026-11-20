"""Photo and footage credits: on the credits page, in the description, or on screen.

Wikimedia Commons photos and clips under CC BY / CC BY-SA (and the Commons "Attribution" licence) must credit
their author. Since 2026-11-07 the credit lives on the video's own page (sof/credits_site.py, on Cloudflare —
nothing there leads to the code, which was the 2026-10-11 objection to a credits page) and the description
carries one line with the link; the frame stays clean. Without that page (secrets missing) the credits go in
the description (Felix, Puello Media 2026-11: much less text on screen), and only the ones that do not fit
there keep a small, dim caption while their photo or clip is on screen (``credit_line``) — every licence is
still honoured. With the page configured but Cloudflare down for the run, the video is already clean, so the
authors are listed in the description as far as they fit. Public-domain, CC0, NASA, Pexels and Pixabay material
needs no credit.
"""

from __future__ import annotations

import re
from pathlib import Path

_ATTRIBUTION = re.compile(r"\bCC[ -]?BY\b|^Attribution\b", re.I)

# 2026-11: the footage now comes from NASA and Wikimedia Commons too (sof/footage.py), not only stock sites.
SOURCES = "Photos and footage: Wikimedia Commons contributors, NASA, Pexels and Pixabay."
LICENCES = "Licences: https://creativecommons.org/licenses/"
DESCRIPTION_LINE = f"{SOURCES} {LICENCES}"
ON_SCREEN = "; the rest are credited on screen"      # the credits that did not fit in the description
AND_OTHERS = "; and others"


def description_line(urls: list[str] | str | None = None) -> str:
    """The description's credits line: with credits-page URL(s) it points there; without, the sources alone."""
    urls = [u for u in ([urls] if isinstance(urls, str) else (urls or [])) if u]
    if not urls:
        return DESCRIPTION_LINE
    if len(urls) == 1:
        return f"{SOURCES} Photo and footage credits (authors and licences): {urls[0]}"
    return f"{SOURCES} Photo and footage credits for each part: " + " · ".join(urls)


def credit_part(row: dict) -> str:
    """One description entry: ``Author (licence)``."""
    return f"{_short(_clean(row.get('artist')), 60) or 'Unknown author'} ({str(row.get('license') or '').strip()})"


def credit_line(rows: list[dict], *, budget: int, rest: str = AND_OTHERS) -> tuple[str, set[str]]:
    """``(line, parts in it)``: the description line naming every author that must be credited (one entry per
    author + licence, ``credit_part``), within ``budget`` UTF-8 bytes. When not all fit it ends with ``rest``
    rather than a cut name; the caller shows the left-out ones on screen (``ON_SCREEN``) or accepts the loss."""
    parts: list[str] = []
    for r in rows:
        if needs_credit(r) and credit_part(r) not in parts:
            parts.append(credit_part(r))
    if not parts:
        return DESCRIPTION_LINE, set()
    head, tail = f"{SOURCES} Credits (Wikimedia Commons): ", f". {LICENCES}"
    body, kept = "", []

    def size(text: str) -> int:
        return len(text.encode("utf-8"))

    for p in parts:
        trial = body + ("; " if body else "") + p
        if size(head + trial + rest + tail) > budget:
            break
        body = trial
        kept.append(p)
    if len(kept) < len(parts):
        body = body + rest if body else rest.removeprefix("; ")
    return head + body + tail, set(kept)


def inline_credits(rows: list[dict], *, budget: int = 1800) -> str:
    """The description line naming every author that must be credited, cut with "and others" past ``budget``."""
    return credit_line(rows, budget=budget)[0]


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
    """One short line: ``Photo: <author> · <licence> · Wikimedia Commons`` (``Video:`` for a footage clip)."""
    artist = _short(_drawable(_clean(row.get("artist"))), 60) or "Unknown author"
    kind = "Video" if row.get("kind") == "clip" else "Photo"
    return f"{kind}: {artist} · {str(row.get('license') or '').strip()} · Wikimedia Commons"


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
