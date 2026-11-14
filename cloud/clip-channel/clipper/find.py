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
