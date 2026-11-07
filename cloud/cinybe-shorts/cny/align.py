"""Where in the film each act happens: the subtitles (``.srt`` Gian supplies) as a timed transcript, one writer
call that picks a window per act, validated; faster-whisper on the runner's CPU when there is no ``.srt``;
proportional windows (act i = the i-th slice of the runtime) when nothing else works. Ported from the
StoryFactory branch's ``film_align.py`` onto ``sof.llm``."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sof.llm import LLM, LLMError

log = logging.getLogger("cny.align")


class AlignError(RuntimeError):
    pass


@dataclass
class Cue:
    start: float
    end: float
    text: str


ALIGN_SYSTEM = ("You map story acts to time ranges in a film's dialogue transcript. Use only the transcript "
                "timestamps given. Output strict JSON.")

ALIGN_PROMPT = """Film: {title} · runtime {duration} seconds.
Acts (JSON): {acts_json}

Below is the dialogue transcript as "[mm:ss] text" lines (one line per 10 seconds that has dialogue). For EACH
act pick one window of {window_min}-{window_max} seconds that best shows that act on screen: the scenes where
the act's events happen. Windows must be in act order, must not overlap, and must stay inside the runtime.
Skip opening titles and end credits.

Return JSON: {{"windows": [{{"act_index": <int>, "start_seconds": <number>, "end_seconds": <number>}}]}}

TRANSCRIPT:
{transcript}"""

_TS = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})")
_TAG = re.compile(r"<[^>]+>|\{\\an\d\}")


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[Cue]:
    """Tolerant .srt parser: BOM, CRLF, missing indices, HTML tags and ASS position tags stripped."""
    cues: list[Cue] = []
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        times = _TS.findall(lines[ti])
        if len(times) < 2:
            continue
        start, end = _secs(*times[0]), _secs(*times[1])
        body = " ".join(_TAG.sub("", l).strip() for l in lines[ti + 1:])
        body = re.sub(r"\s+", " ", body).strip()
        if body and end > start:
            cues.append(Cue(start, end, body))
    return cues


def transcript_lines(cues: list[Cue], *, bucket_seconds: int = 10, max_chars: int = 60000) -> str:
    """``[mm:ss] text`` per bucket that has dialogue; long transcripts are thinned to fit ``max_chars``."""
    buckets: dict[int, list[str]] = {}
    for c in cues:
        b = int(c.start // bucket_seconds) * bucket_seconds
        buckets.setdefault(b, []).append(c.text)
    lines = [f"[{b // 60:02d}:{b % 60:02d}] " + " ".join(buckets[b]) for b in sorted(buckets)]
    out = "\n".join(lines)
    if len(out) > max_chars and lines:                     # keep every bucket's timestamp, trim its words
        per = max(40, max_chars // len(lines) - 9)
        out = "\n".join(l[:per + 8].rstrip() for l in lines)
    return out


def proportional_windows(acts: list[dict], *, duration: float, min_s: float, max_s: float) -> list[dict]:
    """Act i covers the i-th slice of the runtime, skipping the first 3 % (titles) and the last 8 % (credits)."""
    n = max(1, len(acts))
    usable_start, usable_end = duration * 0.03, duration * 0.92
    slice_len = (usable_end - usable_start) / n
    win = max(min_s, min(max_s, slice_len * 0.6))
    out = []
    for a in acts:
        s = round(usable_start + int(a["index"]) * slice_len + max(0.0, (slice_len - win) / 2), 2)
        e = round(min(s + win, duration), 2)
        out.append({"act_index": int(a["index"]), "start_seconds": s, "end_seconds": e, "how": "proportional"})
    return out


def validate_windows(data, acts: list[dict], *, duration: float, min_s: float, max_s: float) -> list[dict]:
    rows = data.get("windows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise AlignError("windows must be a JSON array under \"windows\"")
    by_idx: dict[int, dict] = {}
    for w in rows:
        try:
            by_idx[int(w["act_index"])] = {"act_index": int(w["act_index"]), "start_seconds": float(w["start_seconds"]),
                                           "end_seconds": float(w["end_seconds"]), "how": "transcript"}
        except (KeyError, TypeError, ValueError) as exc:
            raise AlignError(f"malformed window {w!r}: {exc}")
    if set(by_idx) != {int(a["index"]) for a in acts}:
        raise AlignError("one window per act is required")
    ordered = [by_idx[int(a["index"])] for a in acts]
    prev_end = -1.0
    for w in ordered:
        length = w["end_seconds"] - w["start_seconds"]
        if w["start_seconds"] < 0 or w["end_seconds"] > duration + 0.01:
            raise AlignError(f"window for act {w['act_index']} is outside the runtime")
        if not (min_s - 0.01 <= length <= max_s + 0.01):
            raise AlignError(f"window for act {w['act_index']} is {length:.0f}s; must be {min_s:g}-{max_s:g}s")
        if w["start_seconds"] < prev_end:
            raise AlignError(f"window for act {w['act_index']} overlaps the previous act")
        prev_end = w["end_seconds"]
    return ordered


def align_acts(llm: LLM | None, *, title: str, acts: list[dict], cues: list[Cue], duration: float, min_s: float,
               max_s: float) -> list[dict]:
    """Transcript-based windows when they validate (one retry with the errors); else proportional."""
    if cues and llm is not None:
        prompt = ALIGN_PROMPT.format(title=title, duration=int(duration), acts_json=json.dumps(acts, ensure_ascii=False),
                                     window_min=int(min_s), window_max=int(max_s), transcript=transcript_lines(cues))
        for attempt in range(2):
            try:
                data = llm.generate_json(prompt, system=ALIGN_SYSTEM, temperature=0.0, max_tokens=2000)
                return validate_windows(data, acts, duration=duration, min_s=min_s, max_s=max_s)
            except (AlignError, LLMError) as exc:
                log.warning("alignment attempt %d failed: %s", attempt + 1, str(exc)[:200])
                prompt = f"{prompt}\n\nYour previous answer was rejected: {exc}\nReturn corrected JSON that satisfies every rule."
    return proportional_windows(acts, duration=duration, min_s=min_s, max_s=max_s)


# ---------------------------------------------------------------- whisper fallback (no .srt)

def extract_audio_cmd(video: str, wav: str) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]


def transcribe(video: str, work: Path, *, model: str = "small", threads: int = 4) -> list[Cue]:
    """faster-whisper on the CPU → cues; ``[]`` when the package or the model is unavailable."""
    cache = work / "transcript.json"
    if cache.exists():
        return [Cue(**c) for c in json.loads(cache.read_text(encoding="utf-8"))]
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001
        log.warning("faster-whisper unavailable: %s", exc)
        return []
    wav = str(work / "audio16k.wav")
    if not Path(wav).exists():
        subprocess.run(extract_audio_cmd(video, wav), check=True, timeout=1800)
    m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
    segments, info = m.transcribe(wav, beam_size=1, vad_filter=True, language="en")
    cues = [Cue(round(s.start, 2), round(s.end, 2), s.text.strip()) for s in segments if s.text.strip()]
    cache.write_text(json.dumps([c.__dict__ for c in cues], ensure_ascii=False), encoding="utf-8")
    Path(wav).unlink(missing_ok=True)
    log.info("whisper: %d segments over %.0f s", len(cues), getattr(info, "duration", 0.0))
    return cues
