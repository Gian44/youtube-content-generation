"""Narration and footage in step, beat by beat (Gian, 2026-11-08: "the video is showing something and the narration
is explaining something different").

The writer splits every act into beats of 1-3 sentences and gives each the film timestamp of the moment it tells
(it reads the act's dialogue with a timestamp on every line). A beat that quotes a line is snapped to the exact
subtitle of that line. Each beat is voiced on its own, so its spoken length is known to the frame; the footage under
it is cut from the moment it tells, starting a breath before, in screen order, ~3-second cuts, exactly as long as the
beat is spoken. One ffmpeg call per beat: segments read straight from the film, bars cropped, 16:9 blurred fill or
9:16 crop, encoded once.
"""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path

from cc import segments as seg

from cny import cut

log = logging.getLogger("cny.beats")
_TS = re.compile(r"^\[?\s*(?:(\d+):)?(\d{1,3}):(\d{2})(?:\.\d+)?\s*\]?$")
_QUOTE = re.compile(r"[\"“]([^\"”]{12,200})[\"”]")


# ---------------------------------------------------------------- timestamps (pure)

def parse_at(v) -> float | None:
    """``95`` / ``"95.5"`` / ``"01:35"`` / ``"1:02:10"`` / ``"[12:04]"`` → seconds; None when unreadable."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if v >= 0 else None
    s = str(v or "").strip()
    try:
        f = float(s)
        return f if f >= 0 else None
    except ValueError:
        pass
    m = _TS.match(s)
    if not m:
        return None
    h, mm, ss = m.groups()
    return (int(h) * 3600 if h else 0) + int(mm) * 60 + int(ss)


def normalize(beats: list[dict], *, lo: float, hi: float) -> list[dict]:
    """Pure: every beat gets a float ``at`` inside [lo, hi], in screen order. Unreadable times are interpolated from
    their neighbours; a time that runs backwards is lifted to the previous one (the narration never jumps back)."""
    out = [dict(b, at=parse_at(b.get("at"))) for b in beats]
    known = [i for i, b in enumerate(out) if b["at"] is not None]
    if not known:
        n = len(out)
        for i, b in enumerate(out):
            b["at"] = lo + (hi - lo) * i / max(1, n)
    else:
        for i, b in enumerate(out):
            if b["at"] is None:
                prev = max((j for j in known if j < i), default=None)
                nxt = min((j for j in known if j > i), default=None)
                a = out[prev]["at"] if prev is not None else lo
                z = out[nxt]["at"] if nxt is not None else hi
                p0 = prev if prev is not None else -1
                p1 = nxt if nxt is not None else len(out)
                b["at"] = a + (z - a) * (i - p0) / max(1, p1 - p0)
    prev = lo
    for b in out:
        b["at"] = round(min(hi, max(lo, prev, float(b["at"]))), 2)
        prev = b["at"]
    return out


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def snap_quotes(beats: list[dict], cues, *, lo: float, hi: float, slack: float = 90.0) -> int:
    """Beats that quote a line word for word move to the subtitle of that line (exact time); the beats around them
    are kept in order. Returns how many were snapped. ``cues``: objects with .start/.text."""
    snapped = 0
    pool = [(float(c.start), " ".join(_words(c.text))) for c in cues if lo - slack <= float(c.start) <= hi + slack]
    anchors = [False] * len(beats)
    for i, b in enumerate(beats):
        for q in _QUOTE.findall(str(b.get("text") or "")):
            key = " ".join(_words(q)[:6])
            if len(key.split()) < 3:
                continue
            hits = [t for t, txt in pool if key in txt]
            if hits:
                at = float(b.get("at") or lo)
                t = min(hits, key=lambda x: abs(x - at))
                if abs(t - at) <= slack:
                    b["at"] = round(max(lo, min(hi, t - 0.5)), 2)
                    anchors[i] = True
                    snapped += 1
                    break
    for i in range(len(beats) - 2, -1, -1):          # keep order: unsnapped beats never sit after a later anchor
        if not anchors[i] and beats[i]["at"] > beats[i + 1]["at"]:
            beats[i]["at"] = beats[i + 1]["at"]
    for i in range(1, len(beats)):
        if not anchors[i] and beats[i]["at"] < beats[i - 1]["at"]:
            beats[i]["at"] = beats[i - 1]["at"]
    return snapped


# ---------------------------------------------------------------- which footage, for how long (pure)

def windows(ats: list[float], durs: list[float], *, end: float, film_end: float, lead: float = 0.5, stretch: float = 1.3,
            extra: float = 1.5, max_ahead: float = 3.0, reach: float = 1.5) -> list[tuple[float, float]]:
    """Film window per beat: from a breath before the moment it tells, long enough to fill the beat with fresh
    shots (``dur × stretch + extra``), up to ``reach``× that when the next beat is further away (so the footage stays
    on the moment, not a minute later); never re-showing footage the previous beat used unless the moment itself is
    earlier, and never starting more than ``max_ahead`` past it."""
    out: list[tuple[float, float]] = []
    prev_end = -1.0
    for i, (at, d) in enumerate(zip(ats, durs)):
        s = max(0.0, at - lead)
        s = max(s, min(prev_end, at + max_ahead))
        need = d * stretch + extra
        nxt = ats[i + 1] if i + 1 < len(ats) else end
        e = s + max(need, min(max(0.0, nxt - s), need * reach))
        e = min(film_end, e)
        s = max(0.0, min(s, e - need))
        out.append((round(s, 3), round(e, 3)))
        prev_end = e
    return out


def shots_for(window: tuple[float, float], cuts: list[float], *, dur: float, shot_seconds: float, min_len: float,
              max_len: float) -> list[tuple[float, float]]:
    """Pure: shots inside the window, in order, enough to carry ``dur`` seconds; the last one is held longer when the
    window's scenes come up short (never a replay)."""
    s, e = window
    want = max(1, math.ceil(dur / max(0.5, shot_seconds)))
    shots = cut.pick_shots(s, e, cuts, want=want, min_len=min_len, max_len=max_len)
    total = sum(d for _, d in shots)
    if shots and total < dur:
        a, d = shots[-1]
        shots[-1] = (a, round(d + (dur - total) + 0.2, 3))
    return shots


def segments(shots: list[tuple[float, float]], dur: float, *, min_play: float, max_len: float) -> list[tuple[float, float]]:
    """Pure: (film start, seconds) pieces that fill exactly ``dur``: each shot once, in order, equal shares."""
    clips = [(str(i), d) for i, (_, d) in enumerate(shots)]
    plan = seg.plan_sequence(dur, clips, min_seconds=min_play, max_seconds=max(max_len, max((d for _, d in shots), default=0)))
    return [(round(shots[int(p.src)][0] + p.offset, 3), round(p.seconds, 3)) for p in plan]


# ---------------------------------------------------------------- one ffmpeg call per beat

def render_cmd(video: str, segs: list[tuple[float, float]], out: str, *, dur: float, width: int, height: int, fps: int,
               fit: str, crop: str | None, audio: bool = False) -> list[str]:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for s, d in segs:
        cmd += ["-ss", f"{s:.3f}", "-t", f"{d + 0.2:.3f}", "-i", video]
    n = len(segs)
    parts = []
    for k, (_, d) in enumerate(segs):
        parts.append(f"[{k}:v]trim=duration={d:.3f},setpts=PTS-STARTPTS[v{k}]")
        if audio:
            parts.append(f"[{k}:a]atrim=duration={d:.3f},asetpts=PTS-STARTPTS[a{k}]")
    ins = "".join(f"[v{k}][a{k}]" if audio else f"[v{k}]" for k in range(n))
    parts.append(f"{ins}concat=n={n}:v=1:a={1 if audio else 0}[cat]" + ("[acat]" if audio else ""))
    parts.append(f"[cat]{cut.shot_vf(width=width, height=height, fps=fps, fit=fit, crop=crop)},"
                 f"tpad=stop_mode=clone:stop_duration=3[vout]")
    if audio:
        parts.append(f"[acat]apad,atrim=duration={dur:.3f}[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    cmd += (["-map", "[aout]", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"] if audio else ["-an"])
    cmd += ["-t", f"{dur:.3f}", "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-map_metadata", "-1", out]
    return cmd


def render(cfg, *, video: str, beats: list[dict], durs: list[float], cuts: list[float], out_dir: Path, prefix: str,
           end: float, film_end: float, width: int, height: int, fps: int, fit: str, crop: str | None,
           audio: bool = False, max_len: float | None = None) -> tuple[list[str], list[dict]]:
    """Render each beat of one act (or one Short) → ``out_dir/<prefix>_<k>.mp4`` (resumable). Returns the files and,
    per beat, the window and pieces used (for the run log / checks)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    max_len = max_len or cfg.shot_max_seconds
    wins = windows([float(b["at"]) for b in beats], durs, end=end, film_end=film_end)
    files, info = [], []
    for k, (b, d, w) in enumerate(zip(beats, durs, wins)):
        p = out_dir / f"{prefix}_{k:03d}.mp4"
        shots = shots_for(w, cuts, dur=d, shot_seconds=cfg.shot_seconds, min_len=cfg.shot_min_seconds, max_len=max_len)
        segs = segments(shots, d, min_play=cfg.shot_min_play, max_len=max_len)
        if not p.exists():
            seg.sof_render.run(render_cmd(video, segs, str(p), dur=d, width=width, height=height, fps=fps, fit=fit, crop=crop,
                                          audio=audio), 900)
        files.append(str(p))
        info.append({"at": b["at"], "seconds": round(d, 2), "window": list(w), "pieces": [list(x) for x in segs]})
    return files, info


def concat(files: list[str], out: str, list_file: str) -> str:
    with open(list_file, "w", encoding="utf-8") as f:
        for p in files:
            f.write(f"file '{Path(p).resolve()}'\n")
    seg.sof_render.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out], 1800)
    return out
