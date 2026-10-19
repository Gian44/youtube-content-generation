"""Shorts teasers: three ~50-second vertical clips per long video, pointing at it, one a day.

Why: a week-old channel gets almost no impressions on 3-hour long-form — YouTube has no watch history
to recommend it on. Shorts are the one surface where a new channel is shown to strangers, so every long
video now ships with a teaser: the single most surprising concrete fact from its own script, read by the
same narrator (a touch brighter), over 4–6 of the run's own photos, with big burned-in captions (Shorts
are often watched muted) and a spoken + written pointer to the full video.

2026-10-19: the one Short per video out-reached the long videos ~6× (Rice Short 1.3K views vs 39 on the
long video), so each video now ships ``shorts_count`` (3) Shorts, each a different fact from a different
chapter, published ``shorts_spacing_hours`` (24 h) apart so the channel posts every day.

Pipeline: pick fact (fast model, JSON) → TTS (~750 chars, ≈ $0.01) → captions timed by character share
→ 1080×1920 clips on a blurred fill (whole photo visible) with a slow push-in → xfade → caption/credit/
title overlays → mux → upload (public, scheduled ``shorts_delay_minutes`` after the long video is live).
Every step is best-effort from the pipeline's point of view: a failed Short never fails the long video.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sof import captions as cap_mod
from sof import render, seo, tts
from sof.llm import LLM

log = logging.getLogger("sof.shorts")

SHORT_PROMPT = """You write 50-second YouTube Shorts for a calm "facts to fall asleep to" channel.
Each Short teases ONE long bedtime video by telling its single most surprising, concrete, true fact.

Subject of the long video: {{topic}}
What the long video covers, chapter by chapter (index: heading — how it opens):
{{chapters}}

Write the Short's narration: {{words}} words at most, plain spoken English, present tense where natural.
Rules:
- Line 1 is the hook: a specific, curious statement about the subject that makes a stranger stop
  scrolling (a number, a scale, a strange detail). Not a question, not "did you know", no hype words
  (insane, shocking, mind-blowing, secret), no exclamation marks, no emojis.
- Then 3-5 short sentences that unfold that ONE fact with concrete detail taken from the chapters above.
  Stay on one thread; do not list unrelated facts.
- Calm but awake: this is the channel's narrator speaking a little brighter than at bedtime.
- Do NOT write a sign-off or mention the long video; that line is added separately.
- Every sentence must be a complete sentence ending in a period.

Return JSON:
{"section": <index of the chapter the fact comes from>,
 "hook_title": "<3-8 words, Title Case, the fact as a short title for the Short, e.g. 'Venice Stands on Ten Million Logs'; no clickbait words, no question marks, no numbers spelled as digits is fine>",
 "narration": "<the sentences, one paragraph>"}"""

SHORTS_PROMPT = """You write 50-second YouTube Shorts for a calm "facts to fall asleep to" channel.
Each Short teases ONE long bedtime video by telling one surprising, concrete, true fact from it.
Write {{count}} different Shorts for the same long video.

Subject of the long video: {{topic}}
What the long video covers, chapter by chapter (index: heading — how it opens):
{{chapters}}

Each Short's narration: {{words}} words at most, plain spoken English, present tense where natural.
Rules for every Short:
- Line 1 is the hook: a specific, curious statement about the subject that makes a stranger stop
  scrolling (a number, a scale, a strange detail). Not a question, not "did you know", no hype words
  (insane, shocking, mind-blowing, secret), no exclamation marks, no emojis.
- Then 3-5 short sentences that unfold that ONE fact with concrete detail taken from the chapters above.
  Stay on one thread; do not list unrelated facts.
- Calm but awake: this is the channel's narrator speaking a little brighter than at bedtime.
- Do NOT write a sign-off or mention the long video; that line is added separately.
- Every sentence must be a complete sentence ending in a period.
Across the {{count}} Shorts: each comes from a DIFFERENT chapter and tells a DIFFERENT fact with a
different hook shape (e.g. a number, a place, a person, a process). Best Short first.

Return JSON:
{"shorts": [{"section": <index of the chapter the fact comes from>,
             "hook_title": "<3-8 words, Title Case, the fact as a short title, e.g. 'Venice Stands on Ten Million Logs'; no clickbait words, no question marks>",
             "narration": "<the sentences, one paragraph>"}, ...]}"""

CTA_LINE = "The full three-hour version is on the channel. Put it on tonight, and sleep well."
CTA_LINES = (CTA_LINE,
             "There is a three-hour version of this on the channel, made for falling asleep to.",
             "The long, slow version is on the channel. Three hours of it, for when you want to drift off.")
FALLBACK_TITLES = ("One Strange Fact About {s}", "A Quiet Fact About {s}", "What Few People Know About {s}")
HOOK_BANNED = re.compile(r"[!?#@\"“”]|\b(shocking|secrets?|insane|unbelievable|incredible|mind|blow\w*|you won't)\b", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+")


# ---------------------------------------------------------------- 1. the fact (LLM)

def chapter_digest(sections: list[dict], *, chars: int = 500) -> str:
    rows = []
    for i, s in enumerate(sections):
        body = re.sub(r"\s+", " ", str(s.get("body") or "")).strip()
        rows.append(f"{i}: {str(s.get('heading') or '').strip()} — {body[:chars]}")
    return "\n".join(rows)


def clean_narration(text: str, *, max_words: int) -> str:
    """One paragraph of complete sentences, trimmed on a sentence boundary to ``max_words``."""
    sents = [s.strip() for s in _SENT.split(re.sub(r"\s+", " ", str(text or "")).strip()) if s.strip()]
    out, n = [], 0
    for s in sents:
        if not re.search(r"[.!?]$", s):
            s += "."
        s = s.replace("!", ".")
        w = len(s.split())
        if out and n + w > max_words:
            break
        out.append(s)
        n += w
    return " ".join(out)


def clean_hook_title(title: str, topic: str, *, fallback: int = 0) -> str:
    t = re.sub(r"\s+", " ", seo._clean(title)).strip(" .,:;-–—")
    if not 3 <= len(t.split()) <= 9 or len(t) > 60 or HOOK_BANNED.search(t):
        return FALLBACK_TITLES[fallback % len(FALLBACK_TITLES)].format(s=seo.subject_phrase(topic))
    return t


def write_short(llm: LLM, *, topic: str, sections: list[dict], words: int, model: str) -> dict:
    """``{"section", "hook_title", "narration"}`` — narration ends with CTA_LINE."""
    prompt = (SHORT_PROMPT.replace("{{topic}}", topic).replace("{{words}}", str(words))
              .replace("{{chapters}}", chapter_digest(sections)))
    data = llm.generate_json(prompt, max_tokens=800, temperature=0.8, model=model)
    body = clean_narration(data.get("narration", ""), max_words=words)
    if len(body.split()) < 20:
        raise RuntimeError("short narration too thin")
    try:
        section = int(data.get("section", 0))
    except (TypeError, ValueError):
        section = 0
    return {"section": max(0, min(section, max(0, len(sections) - 1))),
            "hook_title": clean_hook_title(str(data.get("hook_title") or ""), topic),
            "narration": body + " " + CTA_LINE}


def write_shorts(llm: LLM, *, topic: str, sections: list[dict], words: int, model: str, count: int) -> list[dict]:
    """Up to ``count`` Shorts from one call, each from a different chapter with a different title; each
    narration ends with its own closing line (``CTA_LINES`` in turn). Thin or repeated items are dropped;
    raises only when none survive."""
    if count <= 1:
        return [write_short(llm, topic=topic, sections=sections, words=words, model=model)]
    prompt = (SHORTS_PROMPT.replace("{{topic}}", topic).replace("{{words}}", str(words)).replace("{{count}}", str(count))
              .replace("{{chapters}}", chapter_digest(sections)))
    data = llm.generate_json(prompt, max_tokens=700 * count, temperature=0.8, model=model)
    raw = data.get("shorts") if isinstance(data, dict) else data
    out: list[dict] = []
    seen_sections: set[int] = set()
    seen_titles: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        body = clean_narration(item.get("narration", ""), max_words=words)
        if len(body.split()) < 20:
            continue
        try:
            section = max(0, min(int(item.get("section", 0)), max(0, len(sections) - 1)))
        except (TypeError, ValueError):
            section = 0
        title = clean_hook_title(str(item.get("hook_title") or ""), topic, fallback=len(out))
        if section in seen_sections or title.lower() in seen_titles:
            continue
        seen_sections.add(section)
        seen_titles.add(title.lower())
        out.append({"section": section, "hook_title": title,
                    "narration": body + " " + CTA_LINES[len(out) % len(CTA_LINES)]})
        if len(out) >= count:
            break
    if not out:
        raise RuntimeError("no usable shorts in the model's answer")
    return out


# ---------------------------------------------------------------- 2. photos

def choose_photos(*, section: int, plan: list[dict] | None, reel: list[str], labels: dict[str, str],
                  want: int = 6, avoid: set[str] | frozenset = frozenset()) -> list[str]:
    """Photos for the Short, best first: the timeline's named-thing photos from the fact's chapter,
    then that chapter's other photos, then subject-labelled reel photos. Distinct files, ``want`` at most.
    Photos in ``avoid`` (used by the video's earlier Shorts) only fill in when nothing else is left."""
    out: list[str] = []

    def add(p):
        if p and os.path.isfile(p) and p not in out:
            out.append(p)

    for r in plan or []:
        if int(r.get("section", -1)) == section and r.get("entity"):
            add(r.get("image"))
    for r in plan or []:
        if int(r.get("section", -1)) == section:
            add(r.get("image"))
    for p in reel:
        if labels.get(p) == "subject":
            add(p)
    for p in reel:
        add(p)
    fresh = [p for p in out if p not in avoid]
    return (fresh + [p for p in out if p in avoid])[:want]


# ---------------------------------------------------------------- 3. captions (timed by character share)

def caption_cues(narration: str, duration: float, *, max_chars: int = 70) -> list[tuple[float, float, str]]:
    """``(start, end, text)`` per cue. TTS pace is close to uniform per character, so each sentence
    gets its share of the audio by character count; long sentences are split at clause boundaries."""
    sents = [s.strip() for s in _SENT.split(narration.strip()) if s.strip()]
    cues: list[str] = []
    for s in sents:
        if len(s) <= max_chars:
            cues.append(s)
            continue
        parts, cur = [], ""
        for piece in re.split(r"(?<=[,;:—])\s+", s):
            if cur and len(cur) + len(piece) + 1 > max_chars:
                parts.append(cur)
                cur = ""
            cur = f"{cur} {piece}".strip()
        if cur:
            parts.append(cur)
        cues.extend(parts)
    total = sum(len(c) for c in cues) or 1
    out, t = [], 0.0
    for c in cues:
        d = duration * len(c) / total
        out.append((round(t, 3), round(t + d, 3), c))
        t += d
    return out


def _font(size: int, bold: bool = True):
    from PIL import ImageFont
    paths = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
             "C:/Windows/Fonts/arialbd.ttf") if bold else render.CAPTION_FONTS
    return render._load_font(paths, size) or ImageFont.load_default(size=size)


def _wrap(d, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if cur and d.textlength(trial, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def caption_png(text: str, out: str, *, width: int, height: int) -> str:
    """Big centred captions under the photo (around 79 % down, above YouTube's Shorts bottom UI),
    white on a soft dark pill, with a thin outline."""
    from PIL import Image, ImageDraw

    size = max(40, round(height * 0.034))
    font = _font(size)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lines = _wrap(d, text, font, int(width * 0.82))
    lh = round(size * 1.25)
    block_h = lh * len(lines)
    y0 = round(height * 0.79) - block_h // 2
    pad = round(size * 0.6)
    widest = max(d.textlength(l, font=font) for l in lines) if lines else 0
    d.rounded_rectangle([(width - widest) / 2 - pad, y0 - pad * 0.6, (width + widest) / 2 + pad, y0 + block_h + pad * 0.4],
                        radius=pad, fill=(0, 0, 0, 120))
    for i, line in enumerate(lines):
        x = (width - d.textlength(line, font=font)) / 2
        y = y0 + i * lh
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
            d.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 230))
        d.text((x, y), line, font=font, fill=(255, 255, 255, 255))
    im.save(out, "PNG")
    return out


def title_png(title: str, brand: str, out: str, *, width: int, height: int) -> str:
    """Persistent top band: the Short's title plus the channel name, under the Shorts' own top UI."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    size = max(36, round(height * 0.03))
    font, small = _font(size), _font(max(24, round(height * 0.018)), bold=False)
    lines = _wrap(d, title.upper(), font, int(width * 0.84))[:2]
    y = round(height * 0.12)
    for line in lines:
        x = (width - d.textlength(line, font=font)) / 2
        d.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))
        d.text((x, y), line, font=font, fill=(255, 255, 255, 245))
        y += round(size * 1.2)
    x = (width - d.textlength(brand, font=small)) / 2
    d.text((x, y + 6), brand, font=small, fill=(255, 255, 255, 170))
    im.save(out, "PNG")
    return out


def credit_png(text: str, out: str, *, width: int, height: int) -> str:
    """Small credit line above the bottom Shorts UI (bottom-left of the safe area)."""
    from PIL import Image, ImageDraw

    size = max(20, round(height * 0.014))
    font = _font(size, bold=False)
    im = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x, y = round(width * 0.05), round(height * 0.885)
    d.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 160))
    d.text((x, y), text, font=font, fill=(255, 255, 255, 150))
    im.save(out, "PNG")
    return out


# ---------------------------------------------------------------- 4. video (pure command builders + orchestration)

def cover_vertical(src: str, dst: str, *, width: int, height: int, scale: float = 2.0) -> str:
    """Whole photo centred on a blurred, darkened fill of itself, at ``scale``× the frame (for zoompan)."""
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    tw, th = int(width * scale), int(height * scale)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        small = ImageOps.fit(im, (max(16, tw // 12), max(16, th // 12)), method=Image.BILINEAR)
        bg = ImageEnhance.Brightness(small.filter(ImageFilter.GaussianBlur(3)).resize((tw, th), Image.BILINEAR)).enhance(0.5)
        fg = ImageOps.contain(im, (int(tw * 0.96), int(th * 0.52)), method=Image.LANCZOS)
        bg.paste(fg, ((tw - fg.width) // 2, int(th * 0.44) - fg.height // 2))     # photo sits above the captions
        bg.save(dst, "JPEG", quality=92, subsampling=0)
    return dst


def clip_cmd(image: str, out: str, *, seconds: float, width: int, height: int, fps: int, zoom_end: float = 1.08) -> list[str]:
    frames = max(1, int(round(seconds * fps)))
    vf = (f"zoompan=z='1+{zoom_end - 1:g}*on/{max(1, frames - 1)}':d={frames}:"
          f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps},setsar=1,format=yuv420p")
    return ["ffmpeg", "-y", "-i", image, "-vf", vf, "-frames:v", str(frames), "-r", str(fps), "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an", "-map_metadata", "-1", out]


def overlay_cmd(reel: str, audio: str, out: str, *, cues: list[tuple[float, float, str]], cue_pngs: list[str],
                title_png_path: str | None, credit_png_path: str | None, duration: float, fps: int) -> list[str]:
    """Reel + timed caption PNGs (``enable=between``) + persistent title/credit PNGs + narration → MP4."""
    cmd = ["ffmpeg", "-y", "-i", reel]
    overlays = [p for p in (title_png_path, credit_png_path) if p] + list(cue_pngs)
    for p in overlays:
        cmd += ["-i", p]
    cmd += ["-i", audio]
    filters, prev, k = [], "[0:v]", 1
    for p in (title_png_path, credit_png_path):
        if p:
            filters.append(f"{prev}[{k}:v]overlay=0:0:format=auto[v{k}]")
            prev, k = f"[v{k}]", k + 1
    for (s, e, _), _p in zip(cues, cue_pngs):
        filters.append(f"{prev}[{k}:v]overlay=0:0:format=auto:enable='between(t,{s:.3f},{e:.3f})'[v{k}]")
        prev, k = f"[v{k}]", k + 1
    filters.append(f"{prev}format=yuv420p[vout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", f"{k}:a", "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", "-t", f"{duration:.3f}",
            "-movflags", "+faststart", "-map_metadata", "-1", out]
    return cmd


def loudnorm_cmd(inp: str, out: str) -> list[str]:
    """Shorts are watched on a phone speaker in daylight: louder than the bedtime narration."""
    return ["ffmpeg", "-y", "-i", inp, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-b:a", "160k", out]


def build_video(*, photos: list[str], narration: str, audio: str, work: Path, title: str, brand: str,
                credits: dict[str, str], width: int, height: int, fps: int, crossfade: float = 0.5,
                name: str = "short") -> str:
    """``work/<name>.mp4``: clips of equal length over the audio, crossfaded, captions and overlays burned in."""
    tmp = work / f"{name}_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    duration = tts.duration_seconds(audio)
    if duration <= 0:
        raise RuntimeError("short narration has no duration")
    n = max(1, len(photos))
    per = (duration + crossfade * (n - 1)) / n
    clips = []
    for i, p in enumerate(photos):
        covered = cover_vertical(p, str(tmp / f"cover_{i:02d}.jpg"), width=width, height=height)
        clip = str(tmp / f"clip_{i:02d}.mp4")
        render.run(clip_cmd(covered, clip, seconds=per, width=width, height=height, fps=fps), 300)
        clips.append(clip)
    if len(clips) == 1:
        reel = clips[0]
    else:
        reel = str(tmp / "reel.mp4")
        render.run(render.xfade_chain_cmd(clips, [per] * len(clips), reel, crossfade=crossfade, fps=fps), 900)
    cues = caption_cues(narration, duration)
    pngs = [caption_png(text, str(tmp / f"cue_{i:03d}.png"), width=width, height=height) for i, (_, _, text) in enumerate(cues)]
    tpng = title_png(title, brand, str(tmp / "title.png"), width=width, height=height)
    lines = sorted({credits[p] for p in photos if credits.get(p)})
    cpng = credit_png(" · ".join(lines)[:140], str(tmp / "credit.png"), width=width, height=height) if lines else None
    out = str(work / f"{name}.mp4")
    render.run(overlay_cmd(reel, audio, out, cues=cues, cue_pngs=pngs, title_png_path=tpng, credit_png_path=cpng,
                           duration=duration, fps=fps), 900)
    return out


# ---------------------------------------------------------------- 5. metadata

def short_title(hook_title: str, topic: str) -> str:
    t = f"{hook_title} #Shorts"
    if len(t) > 100:
        t = f"{seo._clean(topic)[:80]} #Shorts"
    return t


def short_description(*, long_video_id: str, topic: str, category: str, hours: int, credit_lines: list[str]) -> str:
    p = seo.profile(category)
    h = max(1, int(hours))
    lines = [f"Full video — {h} hour{'s' if h != 1 else ''} of calm {p.phrase.lower()} about {seo._clean(topic)} to fall asleep to:",
             f"https://youtu.be/{long_video_id}", "",
             "Sleep On Facts: long, gentle, true narrations to drift off to. New long video several nights a week."]
    if credit_lines:
        lines += ["", "Photos: " + "; ".join(credit_lines)[:600] + ". Licences: https://creativecommons.org/licenses/"]
    lines += ["", " ".join(["#Shorts", *seo.hashtags(topic, category)])]
    return seo._clean("\n".join(lines))[:4900]


def short_tags(topic: str, category: str) -> list[str]:
    return seo.build_tags(topic, category, ["shorts", "facts", "did you know", f"{topic.lower()} shorts"])


def short_publish_at(long_publish_at: str | None, *, delay_minutes: int, index: int = 0, spacing_hours: float = 24.0,
                     now: datetime | None = None) -> str | None:
    """Short ``index`` (0-based) goes live ``delay_minutes`` + ``index`` × ``spacing_hours`` after the long
    video, so its link never points at a private video and the channel posts one Short a day.
    Unscheduled long video (published at once) → the first Short publishes at once, the rest
    ``index`` × ``spacing_hours`` from ``now``."""
    step = timedelta(hours=max(0.0, float(spacing_hours)) * max(0, int(index)))
    if not long_publish_at:
        if index <= 0 or not step:
            return None
        base = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
        return seo.rfc3339(base + step)
    base = datetime.strptime(long_publish_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return seo.rfc3339(base + timedelta(minutes=max(0, int(delay_minutes))) + step)


# ---------------------------------------------------------------- 6. orchestration

def file_stem(index: int) -> str:
    """``short`` for the first Short (the name runs before 2026-10-19 used), then ``short2``, ``short3``…"""
    return "short" if index <= 0 else f"short{index + 1}"


def make(cfg, *, llm: LLM, work: Path, topic: str, sections: list[dict], plan: list[dict] | None, reel: list[str],
         labels: dict[str, str], credits: dict[str, str], index: int = 0, item: dict | None = None,
         avoid: set[str] | frozenset = frozenset()) -> dict:
    """Write (unless ``item`` is given), voice and render one Short. Resumable: ``<stem>.json`` /
    ``<stem>.mp3`` / ``<stem>.mp4`` are reused when present (stem from :func:`file_stem`).
    Returns ``{"path", "narration", "hook_title", "section", "seconds", "photos", "index"}``."""
    stem = file_stem(index)
    meta_path = work / f"{stem}.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    else:
        meta = dict(item) if item else write_short(llm, topic=topic, sections=sections, words=cfg.shorts_words,
                                                   model=cfg.fast_model)
        meta_path.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    audio = work / f"{stem}.mp3"
    if not audio.exists():
        parts = tts.synthesize([meta["narration"]], str(work / f"{stem}_tts"), api_key=cfg.openai_api_key, model=cfg.tts_model,
                               voice=cfg.tts_voice, speed=min(1.0, cfg.tts_speed + 0.1), instructions=cfg.shorts_tts_instructions)
        render.run(loudnorm_cmd(parts[0], str(audio)), 300)
    photos = choose_photos(section=int(meta.get("section", 0)), plan=plan, reel=reel, labels=labels,
                           want=cfg.shorts_photos, avoid=avoid)
    if not photos:
        raise RuntimeError("no photos for the short")
    out = work / f"{stem}.mp4"
    if not out.exists():
        build_video(photos=photos, narration=meta["narration"], audio=str(audio), work=work, title=meta["hook_title"],
                    brand=cfg.shorts_brand, credits=credits, width=cfg.shorts_width, height=cfg.shorts_height,
                    fps=cfg.shorts_fps, name=stem)
    seconds = tts.duration_seconds(str(audio))
    meta.update({"path": str(out), "seconds": round(seconds, 1), "photos": photos, "index": index})
    return meta


def make_all(cfg, *, llm: LLM, work: Path, topic: str, sections: list[dict], plan: list[dict] | None, reel: list[str],
             labels: dict[str, str], credits: dict[str, str], count: int) -> list[dict]:
    """``count`` Shorts for one long video: one model call writes them all (kept in ``shorts.json`` so a
    rerun neither pays for nor changes them), then each is voiced and rendered on its own. A Short that
    fails is skipped; raises only when none rendered."""
    plan_path = work / "shorts.json"
    if plan_path.exists():
        items = json.loads(plan_path.read_text(encoding="utf-8"))
    else:
        items = write_shorts(llm, topic=topic, sections=sections, words=cfg.shorts_words, model=cfg.fast_model,
                             count=max(1, int(count)))
        plan_path.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    metas: list[dict] = []
    used: set[str] = set()
    for i, item in enumerate(items[:max(1, int(count))]):
        try:
            meta = make(cfg, llm=llm, work=work, topic=topic, sections=sections, plan=plan, reel=reel, labels=labels,
                        credits=credits, index=i, item=item, avoid=frozenset(used))
        except Exception as exc:  # noqa: BLE001 — one bad Short must not cost the others
            log.exception("short %d failed", i + 1)
            print(f"! short {i + 1} skipped ({exc})", flush=True)
            continue
        used.update(meta["photos"])
        metas.append(meta)
    if not metas:
        raise RuntimeError("no short rendered")
    return metas


def upload_short(yt, cfg, *, meta: dict, long_video_id: str, long_publish_at: str | None, topic: str, category: str,
                 hours: int, credit_rows: list[dict], index: int = 0) -> tuple[str, str, str | None]:
    """Upload the rendered Short; returns ``(video_id, title, publish_at)``."""
    from sof import upload

    title = short_title(meta["hook_title"], topic)
    credit_lines = sorted({cap_mod.caption_text(r).removeprefix("Photo: ") for r in credit_rows if cap_mod.needs_credit(r)})
    desc = short_description(long_video_id=long_video_id, topic=topic, category=category, hours=hours, credit_lines=credit_lines)
    publish_at = (short_publish_at(long_publish_at, delay_minutes=cfg.shorts_delay_minutes, index=index,
                                   spacing_hours=cfg.shorts_spacing_hours) if cfg.privacy == "public" else None)
    body = upload.video_body(title=title, description=desc, tags=short_tags(topic, category), privacy=cfg.privacy,
                             publish_at=publish_at)
    vid = upload.upload_video(yt, meta["path"], body, None)
    return vid, title, publish_at
