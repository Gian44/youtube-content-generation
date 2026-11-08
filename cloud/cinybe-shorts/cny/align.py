"""The film's dialogue as a timed transcript: the subtitles (``.srt`` Gian supplies) parsed into cues and folded into
"[mm:ss] text" lines for the planner, which writes each act's time range from them; faster-whisper on the runner's
CPU when there is no ``.srt``."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("cny.align")


@dataclass
class Cue:
    start: float
    end: float
    text: str


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


# ---------------------------------------------------------------- whisper fallback (no .srt)

def extract_audio_cmd(video: str, wav: str) -> list[str]:
    return ["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav]


def _wav_samples(wav: str):
    """16 kHz mono PCM16 → float32 numpy array in [-1, 1] (what faster-whisper takes instead of a path)."""
    import wave

    import numpy as np

    with wave.open(wav, "rb") as w:
        assert w.getnchannels() == 1 and w.getframerate() == 16000 and w.getsampwidth() == 2, "audio16k.wav is not 16 kHz mono PCM16"
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


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
    audio = _wav_samples(wav)                   # hand whisper the samples: its own decoder (PyAV) broke on the runner
    m = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads)
    segments, info = m.transcribe(audio, beam_size=1, vad_filter=True, language="en")
    cues = [Cue(round(s.start, 2), round(s.end, 2), s.text.strip()) for s in segments if s.text.strip()]
    cache.write_text(json.dumps([c.__dict__ for c in cues], ensure_ascii=False), encoding="utf-8")
    Path(wav).unlink(missing_ok=True)
    log.info("whisper: %d segments over %.0f s", len(cues), getattr(info, "duration", 0.0))
    return cues
