"""Re-package videos that are already live: a curiosity-first title, a new thumbnail with a hook line and
plain-English chapter titles — the narration is untouched.

Why (2026-10-25, Studio analytics): 76 % of long-form watch time came from *Suggested*, where only 1.4 %
of 8,167 thumbnail impressions were clicked. The flops (Indus 0.7 %, Paper 0.7 %, Honey Bees 0.9 %) were
shown MORE than the hits and skipped; the Honey Bees thumbnail showed flowers. The new packaging is the
same one the daily pipeline now writes (seo.build_title + thumbnail.compose with ``hook``).

Two ways to use it (repackage.py):
- ``--apply``: update title + description (renamed chapters, thumbnail credit) and set the thumbnail.
- without it: only write ``out/<video_id>/`` (thumb.jpg, meta.json) — the B arm for a YouTube Studio
  "Test & compare" (A/B by watch time), which the Data API cannot create.

Photos come fresh from the same sources as a daily run (Commons first, NASA for space, name-gated stock),
labelled by the vision model, and the thumbnail must show the subject.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sof import images, seo, thumbnail, upload
from sof.prompts import REPACKAGE_PROMPT

log = logging.getLogger("sof.repackage")

CHAPTER_LINE = re.compile(r"^(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$")
THUMB_CREDIT_PREFIX = "Thumbnail photo:"


def chapter_rows(description: str) -> list[tuple[str, str]]:
    """[(timestamp, title)] of the description's chapter block ('Chapters' header, then 'M:SS Title' lines)."""
    out: list[tuple[str, str]] = []
    lines = str(description or "").splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().lower() == "chapters") + 1
    except StopIteration:
        return out
    for line in lines[start:]:
        m = CHAPTER_LINE.match(line.strip())
        if not m:
            break
        out.append((m.group(1), m.group(2).strip()))
    return out


def _plain_chapter(title: str) -> str:
    t = re.sub(r"\s+", " ", seo._clean(title)).strip(" .:;,-–—")
    t = re.sub(r"^\d+[.)]\s*", "", t)                      # no "3." numbering
    return t[:70]


def rewrite_description(description: str, *, chapters: list[str] | None = None, thumb_credit: str | None = None) -> str:
    """Rename the chapter titles in place (timestamps kept) and swap the thumbnail-credit line.

    ``chapters`` must match the existing chapter count or it is ignored. ``thumb_credit`` = None keeps the
    current line; '' removes it; text replaces it (placed just before the hashtag line).
    """
    lines = str(description or "").splitlines()
    rows = chapter_rows(description)
    if chapters and rows and len(chapters) == len(rows):
        start = next(i for i, l in enumerate(lines) if l.strip().lower() == "chapters") + 1
        for k, ((ts, old), new) in enumerate(zip(rows, chapters)):
            lines[start + k] = f"{ts} {_plain_chapter(new) or _plain_chapter(old)}"
    if thumb_credit is not None:
        kept = [l for l in lines if not l.startswith(THUMB_CREDIT_PREFIX)]
        # collapse the blank-line pair a removed paragraph leaves behind
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip("\n")
        if thumb_credit:
            paras = text.split("\n\n")
            at = len(paras) - 1 if paras and paras[-1].lstrip().startswith("#") else len(paras)
            paras.insert(at, thumb_credit)
            text = "\n\n".join(paras)
        lines = text.splitlines()
    return seo._clean("\n".join(lines)).encode("utf-8")[:4900].decode("utf-8", "ignore")


def summary_for_prompt(description: str, *, chars: int = 1600) -> str:
    """The part of a description the model needs: opening + blurb + chapters (no sources, no credits)."""
    keep = []
    for para in str(description or "").split("\n\n"):
        p = para.strip()
        if p.startswith(("Facts in this video", "Sources", "Photos:", THUMB_CREDIT_PREFIX, "#")):
            break
        keep.append(p)
    return "\n\n".join(keep)[:chars]


def write_packaging(llm, *, topic: str, description: str, model: str | None) -> dict:
    """{"title_hook", "thumb_text", "chapters": [...]} from the model (chapters may be [] if none exist)."""
    rows = chapter_rows(description)
    prompt = (REPACKAGE_PROMPT.replace("{{topic}}", topic).replace("{{description}}", summary_for_prompt(description))
              .replace("{{n}}", str(len(rows)))
              .replace("{{chapters}}", "\n".join(f"{i + 1}. {t}" for i, (_, t) in enumerate(rows)) or "(none)"))
    data = llm.generate_json(prompt, max_tokens=1200, model=model)
    chapters = [str(c) for c in (data.get("chapters") or [])]
    return {"title_hook": seo.clean_hook(str(data.get("title_hook") or "")),
            "thumb_text": thumbnail.clean_thumb_text(str(data.get("thumb_text") or ""), topic),
            "chapters": chapters if rows and len(chapters) == len(rows) else []}


def subject_photos(cfg, *, topic: str, wiki_title: str, category: str, out_dir: Path, target: int = 24) -> tuple[list[str], list[str], list[dict]]:
    """(reel, subject shots, credit rows) — the daily run's image sources, sized for one thumbnail."""
    from sof import pipeline

    out_dir.mkdir(parents=True, exist_ok=True)
    space = images.is_space_topic(category, "")
    use_nasa = cfg.images_nasa == "on" or (cfg.images_nasa == "auto" and space)
    terms = images.name_terms(topic, wiki_title or "", [])
    urls = images.gather_urls(topic=topic, wiki_title=wiki_title or topic, terms=terms, queries=[topic, f"{topic} photo"],
                              setting_queries=[], target=target, pexels_key=cfg.pexels_api_key,
                              pixabay_key=cfg.pixabay_api_key, use_commons=cfg.images_commons, use_nasa=use_nasa,
                              generic_floor=6, category_limit=80, category_subcats=3, category_per_subcat=30)

    def label(paths):
        if not (cfg.images_vision_check and cfg.openai_api_key):
            return {p: "unknown" for p in paths}
        return images.classify(paths, subject=topic, api_key=cfg.openai_api_key, model=cfg.images_vision_model)

    reel, labels = images.gather_reel(urls, str(out_dir), target=target, floor=4, classify_fn=label, setting_share=0.2)
    subject = [p for p in reel if labels.get(p) == "subject"]
    return reel, subject, pipeline._used_credits(out_dir, reel)


def make_thumbnail(cfg, *, topic: str, wiki_title: str, category: str, hours: int, hook: str, work: Path) -> tuple[str, str]:
    """(thumb path, description credit line or '')."""
    from sof import captions

    reel, subject, used = subject_photos(cfg, topic=topic, wiki_title=wiki_title, category=category, out_dir=work / "images")
    if not reel:
        raise RuntimeError(f"no photos found for {topic!r}")
    chooser = confirmer = None
    if cfg.images_vision_check and cfg.openai_api_key:
        def chooser(paths):
            return images.pick_iconic(paths, subject=topic, api_key=cfg.openai_api_key, model=cfg.images_vision_model)

        def confirmer(path):
            return images.confirm_thumbnail(path, subject=topic, api_key=cfg.openai_api_key, model=cfg.images_vision_model)
    out = str(work / "thumb.jpg")
    thumbnail.make(reel, topic=topic, hours=hours, out=out, candidates=subject, choose=chooser, hook=hook,
                   confirm=confirmer)
    src = thumbnail.source_of(out)
    row = next((r for r in used if src and r.get("file") == Path(src).name), None)
    return out, captions.thumbnail_line(row)


def repackage_row(cfg, yt, llm, row: dict, *, index: int, work: Path, apply: bool) -> dict:
    """Build (and with ``apply`` publish) the new packaging for one ledger row. Returns a report dict."""
    vid = row["video_id"]
    items = yt.videos().list(part="snippet", id=vid).execute().get("items") or []
    if not items:
        raise RuntimeError(f"{vid} not found on YouTube")
    snip = items[0]["snippet"]
    topic = str(row.get("topic") or snip.get("title"))
    category = str(row.get("category") or "")
    hours = max(1, round(float(row.get("duration_seconds") or 10800) / 3600))
    pack = write_packaging(llm, topic=topic, description=snip.get("description") or "", model=cfg.fast_model)
    title = seo.build_title(topic, category, hours, hook=pack["title_hook"], variant=index)
    vdir = work / vid
    vdir.mkdir(parents=True, exist_ok=True)
    thumb, credit = make_thumbnail(cfg, topic=topic, wiki_title=str(row.get("wiki_title") or topic), category=category,
                                   hours=hours, hook=pack["thumb_text"], work=vdir)
    description = rewrite_description(snip.get("description") or "", chapters=pack["chapters"], thumb_credit=credit)
    report = {"video_id": vid, "topic": topic, "title_before": snip.get("title"), "title": title,
              "thumb_text": pack["thumb_text"], "title_hook": pack["title_hook"],
              "chapters_renamed": len(pack["chapters"]), "thumb": thumb, "thumb_credit": credit, "applied": False}
    (vdir / "description.txt").write_text(description, encoding="utf-8")
    if apply:
        upload.update_snippet(yt, vid, title=title, description=description, tags=list(snip.get("tags") or []))
        yt.thumbnails().set(videoId=vid, media_body=thumb).execute()
        report["applied"] = True
    (vdir / "meta.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return report


def apply_prepared(yt, vdir: Path) -> dict:
    """Publish a reviewed ``prepare`` result as-is (meta.json title, description.txt, thumb.jpg) — no new
    model call, so what goes live is exactly what was looked at."""
    meta = json.loads((vdir / "meta.json").read_text(encoding="utf-8"))
    vid = meta["video_id"]
    description = (vdir / "description.txt").read_text(encoding="utf-8")
    thumb = vdir / "thumb.jpg"
    items = yt.videos().list(part="snippet", id=vid).execute().get("items") or []
    if not items:
        raise RuntimeError(f"{vid} not found on YouTube")
    snip = items[0]["snippet"]
    upload.update_snippet(yt, vid, title=meta["title"], description=description, tags=list(snip.get("tags") or []))
    if thumb.exists():
        yt.thumbnails().set(videoId=vid, media_body=str(thumb)).execute()
    report = {**meta, "title_before": snip.get("title"), "thumb": str(thumb), "applied": True}
    (vdir / "meta.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return report


def mark_ledger(rows: list[dict], reports: list[dict], *, today: str | None = None) -> list[dict]:
    """Record applied repackages on their ledger rows (title now, and what it was)."""
    day = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    by_id = {r["video_id"]: r for r in reports if r.get("applied")}
    for row in rows:
        rep = by_id.get(row.get("video_id"))
        if rep:
            row["repackaged"] = {"date": day, "title_before": rep["title_before"], "thumb_text": rep["thumb_text"]}
            row["title"] = rep["title"]
    return rows
