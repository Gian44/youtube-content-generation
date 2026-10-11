"""Per-video photo credits page, published on the channel's GitHub Pages site.

Wikimedia Commons photos under CC BY / CC BY-SA must be attributed (title, author, source, licence);
a 3-hour video can use 100+ of them, far more than a YouTube description holds, so each video's
description links to one page listing every photo it shows. NASA, Pexels and Pixabay are credited
too, although their licences do not require it.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

SOURCE_LABELS = [("commons", "Wikimedia Commons"), ("nasa", "NASA Image and Video Library"),
                 ("pexels", "Pexels"), ("pixabay", "Pixabay")]


def _title_of(item: dict) -> str:
    if str(item.get("title") or "").strip():
        return str(item["title"]).strip()[:120]
    desc = str(item.get("desc") or "").strip()
    if item.get("source") == "commons":
        desc = re.sub(r"\.(jpe?g|tiff?)\b.*$", "", desc, flags=re.I)        # "File name.jpg <description>" → "File name"
    return (desc or item.get("query") or "Photo")[:120]


def page_html(*, title: str, video_url: str, items: list[dict]) -> str:
    esc = html.escape
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>Photo credits — {esc(title)}</title><link rel=\"stylesheet\" href=\"../_style.css\"></head><body><main>",
        '<span class="badge">SLEEP ON FACTS</span>',
        f"<h1>Photo credits</h1><p class=\"muted\">{esc(title)}</p>",
    ]
    if video_url:
        parts.append(f'<nav><a href="{esc(video_url)}">Watch the video</a><a href="../index.html">About the channel</a></nav>')
    for key, label in SOURCE_LABELS:
        rows = [i for i in items if i.get("source") == key]
        if not rows:
            continue
        parts.append(f"<h2>{esc(label)} ({len(rows)})</h2><ul>")
        for it in rows:
            link = str(it.get("page") or it.get("url") or "")
            parts.append(f'<li><a href="{esc(link)}">{esc(_title_of(it))}</a> — {esc(str(it.get("artist") or "unknown"))}'
                         f' — {esc(str(it.get("license") or ""))}</li>')
        parts.append("</ul>")
    other = [i for i in items if i.get("source") not in {k for k, _ in SOURCE_LABELS}]
    if other:
        parts.append("<h2>Other</h2><ul>" + "".join(f"<li>{esc(str(i.get('credit') or i.get('url')))}</li>" for i in other) + "</ul>")
    parts.append('<footer>Photos are shown unaltered except for cropping, slow zoom and dimming. '
                 'Licences: <a href="https://creativecommons.org/licenses/">Creative Commons</a> · '
                 '<a href="https://www.nasa.gov/nasa-brand-center/images-and-media/">NASA media guidelines</a>.</footer>')
    parts.append("</main></body></html>\n")
    return "\n".join(parts)


def write_page(path: str, *, title: str, video_url: str, items: list[dict]) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(page_html(title=title, video_url=video_url, items=items), encoding="utf-8")
    return str(p)
