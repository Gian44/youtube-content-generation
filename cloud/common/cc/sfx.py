"""Quiet, varied sound effects (Felix, Puello Media, 2026-11: "the pings were too loud", the whoosh/static transition
"reads as AI slop" — use different, quieter effects, and not on every cut).

Every effect is synthesised with ffmpeg (nothing to license) from a few soft, natural-sounding shapes — a woodblock
tick, a muted pluck, a low felt thump — never a pure sine beep, never a whoosh or static. Each video gets its own
variant (pitch, length and tone drawn from a seed), so the same sound does not repeat channel-wide. Levels are fixed
well under speech: a normalised voice peaks near -1.5 dBFS; these peak at ``PEAK_DB`` (-24 dBFS by default), ~20 dB
quieter, before any final loudness normalisation (which moves both by the same amount).

``on_cuts`` decides which cuts get an effect at all (a few, never every one); ``loudness`` measures a file or a window
of it, for the checks after a render.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess

PEAK_DB = -24.0           # effect peak in dBFS before the final mix (voice peaks near -1.5 dBFS)

# name → (frequency range Hz, decay rate range 1/s, body length range s, noise share range)
SHAPES: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "tick": ((1400.0, 2100.0), (55.0, 80.0), (0.05, 0.07), (0.25, 0.4)),     # a soft woodblock / clock tick
    "tock": ((700.0, 1000.0), (30.0, 45.0), (0.09, 0.12), (0.2, 0.3)),       # the last count: lower, a touch longer
    "pluck": ((330.0, 520.0), (14.0, 22.0), (0.22, 0.3), (0.05, 0.12)),     # a muted string pluck for an entry landing
    "thump": ((90.0, 140.0), (18.0, 26.0), (0.18, 0.25), (0.1, 0.2)),       # a low felt thump for a reveal
}


def variant(kind: str, seed: str | int = 0) -> dict:
    """The parameters of one effect: the same ``seed`` always gives the same sound, another seed a different one."""
    if kind not in SHAPES:
        raise ValueError(f"unknown effect {kind!r} (have {', '.join(SHAPES)})")
    rng = random.Random(int(hashlib.sha1(f"{kind}|{seed}".encode()).hexdigest(), 16))
    (f0, f1), (d0, d1), (l0, l1), (n0, n1) = SHAPES[kind]
    return {"freq": round(rng.uniform(f0, f1), 1), "decay": round(rng.uniform(d0, d1), 1),
            "seconds": round(rng.uniform(l0, l1), 3), "noise": round(rng.uniform(n0, n1), 3),
            "overtone": round(rng.uniform(2.3, 2.9), 3)}


def expr(v: dict) -> str:
    """aevalsrc expression: a damped tone with one inharmonic overtone (wood/string, not a beep) and a short noise
    transient at the start. Peak normalised to ~1 before the level is set."""
    f, k, o, nz = v["freq"], v["decay"], v["overtone"], v["noise"]
    tone = f"(0.75*sin(2*PI*{f}*t)+0.25*sin(2*PI*{f * o:.1f}*t))*exp(-{k}*t)"
    click = f"{nz}*(2*random(0)-1)*exp(-{k * 6:.1f}*t)"
    return f"({tone}+{click})"


def synth_cmd(kind: str, out: str, *, seed: str | int = 0, peak_db: float = PEAK_DB, pad_to: float | None = None,
              sample_rate: int = 44100, channels: int = 1) -> list[str]:
    """ffmpeg command writing one effect to ``out`` (WAV): a soft attack, a low-pass so nothing is shrill, the level
    set to ``peak_db``, then silence up to ``pad_to`` seconds when given (a countdown second). The filters take a
    little off: the file peaks ~1.5-6 dB under ``peak_db`` (the low thump the most; ``channels=2`` 3 dB more), so a
    caller that needs an exact level measures the file (``peak_db()``) and corrects."""
    v = variant(kind, seed)
    gain = 10 ** (peak_db / 20.0)
    af = (f"afade=t=in:d=0.004,lowpass=f={min(6000, int(v['freq'] * 3))},highpass=f=60,"
          f"volume={gain:.5f},afade=t=out:st={max(0.0, v['seconds'] - 0.02):.3f}:d=0.02")
    if pad_to:
        af += f",apad=whole_dur={pad_to:g}"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
           f"aevalsrc='{expr(v)}':d={v['seconds']:g}:s={sample_rate}", "-af", af]
    if pad_to:
        cmd += ["-t", f"{pad_to:g}"]
    return cmd + ["-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le", out]


def synth(kind: str, out: str, **kw) -> str:
    r = subprocess.run(synth_cmd(kind, out, **kw), capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"sfx {kind} failed: {r.stderr[-300:]}")
    return out


def on_cuts(n: int, *, seed: str | int = 0, share: float = 0.34, first: bool = False, last: bool = True) -> set[int]:
    """Which of ``n`` cuts (0-based) get an effect: about ``share`` of them, never two in a row, the ``last`` (the
    payoff) and optionally the ``first``; the rest stay clean."""
    if n <= 0:
        return set()
    out: set[int] = set()
    if last:
        out.add(n - 1)
    if first:
        out.add(0)
    want = max(len(out), round(n * share))
    rng = random.Random(int(hashlib.sha1(f"cuts|{seed}|{n}".encode()).hexdigest(), 16))
    pool = [i for i in range(n) if i not in out]
    rng.shuffle(pool)
    for i in pool:
        if len(out) >= want:
            break
        if i - 1 not in out and i + 1 not in out:
            out.add(i)
    return out


def _window(start: float | None, seconds: float | None) -> str:
    """An ``atrim`` that decodes from the start and keeps [start, start+seconds): an input ``-ss`` seek into MP3/AAC
    lands a few ms late and misses a tick's attack (read -25.6 dBFS where the true peak was -18.8, 2026-11-21)."""
    if start is None and seconds is None:
        return ""
    parts = [f"start={max(0.0, start or 0.0):.3f}"] + ([f"duration={seconds:.3f}"] if seconds is not None else [])
    return f"atrim={':'.join(parts)},asetpts=PTS-STARTPTS,"


def loudness(path: str, *, start: float | None = None, seconds: float | None = None) -> dict:
    """Integrated loudness (LUFS) and true peak (dBFS) of ``path`` (or a window of it), via ffmpeg's loudnorm
    analysis. ``{"I": float, "TP": float}``; values are -70/-99 for silence."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", _window(start, seconds) + "loudnorm=print_format=json",
           "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise RuntimeError(f"loudness: no measurement for {path}: {r.stderr[-200:]}")
    d = json.loads(m.group(0))

    def num(x: str, floor: float) -> float:
        try:
            return max(floor, float(x))
        except ValueError:
            return floor
    return {"I": num(d["input_i"], -70.0), "TP": num(d["input_tp"], -99.0)}


def peak_db(path: str, *, start: float | None = None, seconds: float | None = None) -> float:
    """Sample peak (dBFS) of ``path`` or a window of it (volumedetect)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", _window(start, seconds) + "volumedetect",
           "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    m = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", r.stderr)
    if not m:
        raise RuntimeError(f"peak: no measurement for {path}")
    return -99.0 if m.group(1) == "-inf" else float(m.group(1))
