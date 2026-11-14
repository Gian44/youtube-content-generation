"""Finder: watch the creators in watchlist.yml, read each new upload's transcript, and ask Claude for the
moments worth clipping. Runs on Gian's PC (video downloads from cloud runners are often blocked)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORK, OUT, LEDGER = ROOT / "work", ROOT / "out", ROOT / "ledger.json"
YTDLP = [sys.executable, "-m", "yt_dlp", "--no-warnings"]
LONG_VIDEO_S = 45 * 60          # above this only the most-replayed windows go to the model


def sh(cmd, timeout=900, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw)


def load_config() -> dict:
    return yaml.safe_load((ROOT / "watchlist.yml").read_text(encoding="utf-8"))


def load_ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {"videos": {}}


def save_ledger(ledger: dict) -> None:
    LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")


def latest_videos(url: str, n: int = 5) -> tuple[str, list[dict]]:
    r = sh(YTDLP + ["--flat-playlist", "--playlist-end", str(n), "-J", url.rstrip("/") + "/videos"], timeout=120)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"could not list {url}: {(r.stderr or '')[-200:]}")
    data = json.loads(r.stdout)
    vids = [{"id": e["id"], "title": e.get("title") or "", "duration": e.get("duration") or 0}
            for e in data.get("entries") or [] if e and e.get("id")]
    return data.get("channel") or data.get("title") or url, vids


def fetch_transcript(video_id: str) -> tuple[dict, list[dict]]:
    """Returns (info, words). words = [{"t": start_s, "e": end_s, "w": text}] from YouTube's own captions."""
    d = WORK / video_id
    d.mkdir(parents=True, exist_ok=True)
    # One caption track per request, with a pause: asking for four tracks at once drew HTTP 429 from YouTube
    # (2026-11-14), which also stopped the metadata from being written.
    info_p = d / "src.info.json"
    r = None
    for lang in ("en-orig", "en"):
        r = sh(YTDLP + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", lang,
                        "--sub-format", "json3", "--write-info-json", "--sleep-subtitles", "2", "--sleep-requests", "1",
                        "--retries", "3", "--extractor-retries", "3", "-o", str(d / "src.%(ext)s"),
                        f"https://www.youtube.com/watch?v={video_id}"], timeout=300)
        if list(d.glob("src.*.json3")):
            break
        if "429" in (r.stderr or ""):
            time.sleep(20)
    if not info_p.exists():
        raise RuntimeError(f"no metadata for {video_id}: {(r.stderr or '')[-200:]}")
    info = json.loads(info_p.read_text(encoding="utf-8"))
    subs = sorted(d.glob("src.*.json3"), key=lambda p: ("orig" not in p.name, p.name))
    return info, (parse_json3(json.loads(subs[0].read_text(encoding="utf-8"))) if subs else [])


def parse_json3(data: dict) -> list[dict]:
    words: list[dict] = []
    for ev in data.get("events") or []:
        t0, dur = ev.get("tStartMs", 0) / 1000, (ev.get("dDurationMs") or 0) / 1000
        segs = [s for s in ev.get("segs") or [] if (s.get("utf8") or "").strip()]
        starts = [t0 + s.get("tOffsetMs", 0) / 1000 for s in segs]
        for k, s in enumerate(segs):
            toks = [t for t in re.sub(r"\[[^\]]*\]|>>", " ", s["utf8"]).split() if t]
            if not toks:
                continue
            # a seg can hold several words: spread them up to the next seg (or the event end), never past it
            start = starts[k]
            span = (starts[k + 1] - start) if k + 1 < len(segs) else max((t0 + (dur or 2.0)) - start, 0.2)
            step = min(max(span, 0.0) / len(toks), 0.45)
            for i, tok in enumerate(toks):
                words.append({"t": round(start + i * step, 3), "w": tok})
    words.sort(key=lambda w: w["t"])
    for i, w in enumerate(words):
        nxt = words[i + 1]["t"] if i + 1 < len(words) else w["t"] + 0.6
        w["e"] = round(min(max(nxt, w["t"] + 0.12), w["t"] + 1.5), 3)
    return words


def hot_windows(info: dict, pad: float = 75.0, top: int = 6) -> list[tuple[float, float]]:
    heat = [h for h in info.get("heatmap") or [] if h.get("start_time", 0) > 60]
    peaks = sorted(heat, key=lambda h: -h.get("value", 0))[:top]
    spans = sorted((max(0, p["start_time"] - pad), p["end_time"] + pad) for p in peaks)
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def transcript_lines(words: list[dict], windows: list[tuple[float, float]] | None, line_s: float = 8.0) -> str:
    out, cur, cur_t, last_win = [], [], None, None
    for w in words:
        win = next((i for i, (a, b) in enumerate(windows) if a <= w["t"] <= b), None) if windows else 0
        if win is None:
            continue
        if cur and (w["t"] - cur_t >= line_s or win != last_win):
            out.append(f"[{cur_t:.0f}] " + " ".join(cur))
            if win != last_win:
                out.append("...")
            cur, cur_t = [], None
        if cur_t is None:
            cur_t = w["t"]
        cur.append(w["w"])
        last_win = win
    if cur:
        out.append(f"[{cur_t:.0f}] " + " ".join(cur))
    return "\n".join(out)[:120_000]


PROMPT = """You pick moments from a creator's video to post as vertical short clips on a clip channel.

Creator: {creator}
Video title: {title}
Video length: {duration:.0f} seconds
{heat}
Transcript (each line starts with its time in seconds; "..." marks skipped parts):
{transcript}

Pick up to {n} moments, best first. Pick none if nothing is strong. A good moment:
- makes sense to someone who has never seen this creator and did not watch the rest
- opens on the interesting line itself, with only the setup it needs
- is funny, awkward, shocking, heated, or moving, with a clear payoff before it ends
- runs 18 to 55 seconds (style B may run up to 90)

For each moment choose a style:
- "A": funny, awkward, absurd or reaction moments. Headline is a short reaction that reframes the clip, the way a
  friend would caption it (examples of the register: "Bro Couldn't Do ONE Simple Task", "This has to be ragebait").
- "B": emotional, sincere or story moments. Headline is two short lines separated by " | ", the second line the turn.

Headline rules: 3 to 8 words (style B: each line 3 to 6 words), no hashtags, no creator name unless needed, nothing
the clip does not show, no insults aimed at a real person's looks, race, gender or disability. Give three different
headlines per moment, strongest first. "emojis" is 2 or 3 emoji that fit style A (empty string for style B).
"title" is the upload title: a second, different punchline, under 60 characters.

Use only times that appear in the transcript. Return JSON only, in this shape:
{{"clips": [{{"start": 123, "end": 151, "style": "A", "headlines": ["", "", ""], "emojis": "", "title": "", "why": ""}}]}}"""

SYSTEM = "You are a short-form video editor. Answer with one JSON object and nothing else."


def ask_claude(prompt: str, model: str = "sonnet", timeout: int = 600) -> dict:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH")
    cmd = [exe, "-p", "--output-format", "json", "--model", model, "--tools", "", "--strict-mcp-config",
           "--disable-slash-commands", "--no-session-persistence", "--max-turns", "2", "--system-prompt", SYSTEM]
    with tempfile.TemporaryDirectory() as tmp:  # neutral cwd: no project CLAUDE.md, no repo context
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=tmp)
    try:
        data = json.loads((r.stdout or "").strip() or "{}")
    except json.JSONDecodeError:
        data = {}
    if r.returncode != 0 or data.get("is_error") or not data.get("result"):
        raise RuntimeError(f"claude exit {r.returncode}: {(data.get('result') or r.stderr or r.stdout or '')[-300:]}")
    text = str(data["result"])
    a, b = text.find("{"), text.rfind("}")
    return json.loads(text[a:b + 1]) if a >= 0 and b > a else {}


def pick_moments(creator: str, info: dict, words: list[dict], n: int = 2) -> list[dict]:
    duration = float(info.get("duration") or (words[-1]["e"] if words else 0))
    windows = hot_windows(info) if duration > LONG_VIDEO_S else None
    if duration > LONG_VIDEO_S and not windows:
        windows = [(60.0, LONG_VIDEO_S)]
    peaks = sorted((h for h in info.get("heatmap") or [] if h.get("start_time", 0) > 60), key=lambda h: -h.get("value", 0))[:5]
    heat = ("Most replayed points (seconds): " + ", ".join(f"{p['start_time']:.0f}" for p in sorted(peaks, key=lambda h: h["start_time"])) + "\n") if peaks else ""
    prompt = PROMPT.format(creator=creator, title=info.get("title") or "", duration=duration, heat=heat,
                           transcript=transcript_lines(words, windows), n=n)
    clips = []
    for c in (ask_claude(prompt).get("clips") or [])[:n]:
        try:
            s, e = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        heads = [h.strip() for h in c.get("headlines") or [] if isinstance(h, str) and h.strip()][:3]
        if not heads or e - s < 8 or e - s > 120 or s < 0 or e > duration + 2:
            continue
        clips.append({"start": s, "end": e, "style": "B" if str(c.get("style", "A")).upper() == "B" else "A",
                      "headlines": heads, "emojis": str(c.get("emojis") or ""), "title": str(c.get("title") or "")[:95],
                      "why": str(c.get("why") or "")})
    return clips


MULTI_RANK_PROMPT = """You plan a "ranking" Short: five very short clips on ONE narrow theme, each taken from a DIFFERENT
source video, shown under a title with a numbered list that fills in as each clip plays.

Themes already used (pick a different one): {used}

Source videos. Each transcript line starts with its time in seconds; "..." marks skipped parts.

{videos}

Rules:
- The theme is one specific, comparable kind of moment that really happens in at least five of these videos, such
  as the moment the prize is won, the worst fail, the biggest reaction, the most expensive thing. Never a vague
  theme like "best moments" or "craziest moments".
- Pick exactly five moments, each from a different video. If you cannot, return {{"theme": ""}}.
- Each moment is 7 to 11 seconds: one sentence of setup so a stranger knows what is happening, the key line, then
  the reaction to it. Start at the beginning of a sentence and end after a sentence or the reaction has finished,
  never in the middle of one.
- "rank": 1 is the most extreme example of the theme and 5 the mildest. Rank by the theme itself (bigger prize,
  harder fail), not by the order of the videos.
- "label": 1 to 3 words plus exactly one emoji at the end. If the theme ranks something measurable (money, size,
  speed, a count), the label is that number as said in the clip, for example "$250,000 💰". Otherwise it is what a
  friend would blurt out, for example "Wiped out 💀". The label must match what the clip itself shows or says.
- A higher rank must visibly beat the one below it (bigger number, more extreme moment). Avoid ties: with more
  than five candidate videos, drop the ones that would tie.
- "title_plain" and "title_accent" form the on-screen title, for example "Ranking MrBeast's" and "Biggest Wins".
  Together under 32 characters.
- "upload_title": under 60 characters, says what is ranked and may tease number 1.

Return JSON only:
{{"theme": "", "title_plain": "", "title_accent": "", "upload_title": "", "items": [{{"rank": 1, "video": "V1", "start": 0, "end": 0, "label": ""}}]}}"""


def _video_text(info: dict, words: list[dict], cap: int = 60_000) -> str:
    """Transcript for the ranking prompt: whole video, or for long ones the most-replayed parts plus the ending."""
    duration = float(info.get("duration") or 0)
    wins = hot_windows(info, pad=75, top=8) if duration > LONG_VIDEO_S else []
    if not wins:
        return transcript_lines(words, None, 10)[:cap]
    merged: list[tuple[float, float]] = []
    for a, b in sorted(wins + [(max(0.0, duration - 240), duration + 5)]):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return transcript_lines(words, merged, 10)[:cap]


def pick_ranking_multi(sources: list[dict], used_themes=()) -> dict | None:
    """One theme and five ranked moments, each from a different source video. sources = [{key, id, creator, info,
    words}]. Returns None when no theme covers five different videos."""
    blocks = [f"=== {s['key']} | {s['creator']} | {s['info'].get('title') or ''} | "
              f"{float(s['info'].get('duration') or 0):.0f}s\n{_video_text(s['info'], s['words'])}" for s in sources]
    data = ask_claude(MULTI_RANK_PROMPT.format(videos="\n\n".join(blocks), used=", ".join(t for t in used_themes if t) or "none"),
                      timeout=900)
    by_key = {s["key"]: s for s in sources}
    items: dict[int, dict] = {}
    for it in data.get("items") or []:
        try:
            rank, key, s, e = int(it["rank"]), str(it["video"]).strip().upper(), float(it["start"]), float(it["end"])
        except (KeyError, TypeError, ValueError):
            continue
        src, label = by_key.get(key), str(it.get("label") or "").strip()[:26]
        if not src or not label or not 1 <= rank <= 5:
            continue
        if s < 0 or e - s < 2 or e > float(src["info"].get("duration") or 0) + 2:
            continue
        items[rank] = {"rank": rank, "key": key, "video_id": src["id"], "creator": src["creator"],
                       "start": s, "end": e, "label": label}
    theme = str(data.get("theme") or "").strip()[:40]
    if not theme or len(items) != 5 or len({it["video_id"] for it in items.values()}) < 5:
        return None
    plain, accent = str(data.get("title_plain") or "Ranking").strip()[:24], str(data.get("title_accent") or theme).strip()[:24]
    # the first pass only sees a timestamp every 10 s, so its cut points are rough: a second pass sets them
    # sentence by sentence
    return {"theme": theme, "title_plain": plain, "title_accent": accent,
            "upload_title": str(data.get("upload_title") or f"Ranking {theme}").strip()[:95],
            "items": refine_spans(f"{plain} {accent}", [items[r] for r in sorted(items)], {s["id"]: s["words"] for s in sources})}


def sentences(words: list[dict]) -> list[tuple[float, float, str]]:
    """(start, end, text) per sentence. A sentence ends on . ? ! or before a pause of 1.2 s or more."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        long_run = w["t"] - cur[0]["t"] >= 6.0 and nxt is not None and nxt["t"] - w["t"] >= 0.5   # unpunctuated captions
        if nxt is None or re.search(r"[.?!][\"')]*$", w["w"]) or nxt["t"] - w["t"] >= 1.2 or long_run:
            out.append((cur[0]["t"], min(cur[-1]["e"], cur[-1]["t"] + 1.2), " ".join(x["w"] for x in cur)))
            cur = []
    return out


def widen_span(words: list[dict], s: float, e: float, target: float = 9.0, max_len: float = 13.0) -> tuple[float, float]:
    """Grow a picked span to whole sentences, then add the sentence before and after until it reaches `target`
    seconds. Clips that stop mid-sentence or start without their setup were the main complaint about the first
    rankings (Gian, 2026-11-14). Never shrinks the span and never exceeds max_len."""
    sents = sentences(words)
    hit = [i for i, (a, b, _) in enumerate(sents) if b > s and a < e]
    if not hit:
        return s, e
    i, j = hit[0], hit[-1]
    a, b = min(s, sents[i][0]), max(e, sents[j][1])
    if b - a > max_len:                     # a run-on with no punctuation: keep the pick, only let it finish
        return s, max(e, min(b, s + max_len))
    while b - a < target:
        grew = False
        if i > 0 and a - sents[i - 1][1] < 2.0 and b - sents[i - 1][0] <= max_len:
            i -= 1
            a, grew = sents[i][0], True
        if b - a < target and j + 1 < len(sents) and sents[j + 1][0] - b < 2.0 and sents[j + 1][1] - a <= max_len:
            j += 1
            b, grew = sents[j][1], True
        if not grew:
            break
    return max(0.0, a - 0.06), b - 0.03     # a sentence's end time is the next word's onset: stop just short of it


REFINE_PROMPT = """These clips are entries in a ranking Short titled "{title}". Each was picked roughly. Set exact start and
end times so every clip makes sense alone and does not stop abruptly.

For each entry you get the sentences around the rough pick, each written as [start-end] text, in seconds.

{blocks}

Rules for each entry:
- Whole sentences only: start at a sentence's start time and end at a sentence's end time.
- Include one or two sentences of setup so a stranger knows what is happening, the line that makes the entry's
  label true, and the reaction right after it.
- 7 to 12 seconds long, never more than 14.
- The sentence that states the entry's point (the win, the number, the fail itself) must be inside the clip. If
  something has to go, shorten the reaction after it, never the setup or that sentence.
- Stop before the talk moves on to something else.

Return JSON only: {{"spans": [{{"rank": 1, "start": 0.0, "end": 0.0}}]}}"""


def refine_spans(title: str, items: list[dict], words_by_id: dict) -> list[dict]:
    """Second pass for rankings: exact cut points chosen from whole sentences around each rough pick. An entry the
    model answers badly for falls back to widen_span()."""
    blocks, local = [], {}
    for it in items:
        sents = [x for x in sentences(words_by_id.get(it["video_id"]) or []) if x[1] > it["start"] - 22 and x[0] < it["end"] + 22]
        local[it["rank"]] = sents
        blocks.append(f"Entry {it['rank']}: label \"{it['label']}\" (rough pick {it['start']:.0f}-{it['end']:.0f})\n"
                      + "\n".join(f"[{a:.1f}-{b:.1f}] {t}" for a, b, t in sents))
    try:
        data = ask_claude(REFINE_PROMPT.format(title=title, blocks="\n\n".join(blocks)), timeout=300)
    except Exception:
        data = {}
    spans = {}
    for sp in data.get("spans") or []:
        try:
            spans[int(sp["rank"])] = (float(sp["start"]), float(sp["end"]))
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    for it in items:
        sents, (s, e) = local[it["rank"]], spans.get(it["rank"], (None, None))
        ok = bool(sents) and s is not None and sents[0][0] - 0.5 <= s < e <= sents[-1][1] + 0.5
        if ok:  # snap to real sentence edges so a rounded number cannot cut a word
            s = min((x[0] for x in sents), key=lambda v: abs(v - s))
            e = min((x[1] for x in sents), key=lambda v: abs(v - e))
            ok = 4.0 <= e - s <= 14.5
        if ok:
            s, e = max(0.0, s - 0.06), e - 0.03
        else:
            s, e = widen_span(words_by_id.get(it["video_id"]) or [], it["start"], it["end"])
        out.append({**it, "start": round(s, 2), "end": round(e, 2), "refined": ok})
    return out


def cached_words(video_id: str) -> list[dict]:
    """Word timings from the captions already on disk for this video, fetched only if they are missing."""
    subs = sorted((WORK / video_id).glob("src.*.json3"), key=lambda p: ("orig" not in p.name, p.name))
    if subs:
        return parse_json3(json.loads(subs[0].read_text(encoding="utf-8")))
    return fetch_transcript(video_id)[1]


def label_said(label: str, text: str) -> bool:
    """True when a number in the label is actually spoken in the clip (labels without digits always pass)."""
    digits = re.sub(r"\D", "", label)
    if not digits:
        return True
    t = text.lower()
    nums = [re.sub(r"\D", "", n) for n in re.findall(r"\d[\d,]*", t)]
    if digits in nums:
        return True
    if digits.endswith("000000"):
        lead = digits[:-6]
        if "million" in t and (lead in nums or (lead == "1" and re.search(r"\b(a|one) million", t))):
            return True
    if digits.endswith("000") and digits[:-3] in nums and re.search(r"grand|thousand|\bk\b", t):
        return True
    return digits == "500000" and "half" in t and "million" in t
