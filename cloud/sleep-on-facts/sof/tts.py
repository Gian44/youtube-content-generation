"""OpenAI text-to-speech in ≤4,000-char chunks, concatenated and loudness-normalized for bedtime."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

log = logging.getLogger("sof.tts")
URL = "https://api.openai.com/v1/audio/speech"
PRICE_PER_M_CHARS = {"tts-1": 15.0, "tts-1-hd": 30.0}
_SENT = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    cur = ""
    for sent in _SENT.split(text.replace("\n", " ").strip()):
        sent = sent.strip()
        if not sent:
            continue
        if cur and len(cur) + len(sent) + 1 > limit:
            chunks.append(cur)
            cur = ""
        while len(sent) > limit:  # pathological run-on sentence
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(sent[:limit])
            sent = sent[limit:]
        cur = f"{cur} {sent}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def synthesize(chunks: list[str], out_dir: str, *, api_key: str, model: str, voice: str, speed: float,
               transport=None, retries: int = 4, sleep=time.sleep) -> list[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    with httpx.Client(timeout=180.0, transport=transport, headers={"Authorization": f"Bearer {api_key}"}) as c:
        for i, chunk in enumerate(chunks):
            p = Path(out_dir) / f"{i:04d}.mp3"
            paths.append(str(p))
            if p.exists() and p.stat().st_size > 0:
                continue
            delay = 2.0
            for attempt in range(retries + 1):
                r = c.post(URL, json={"model": model, "voice": voice, "speed": speed, "input": chunk,
                                      "response_format": "mp3"})
                if r.status_code == 200 and r.content:
                    p.write_bytes(r.content)
                    break
                if attempt == retries or r.status_code not in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"TTS chunk {i} failed: HTTP {r.status_code} {r.text[:200]}")
                sleep(delay)
                delay *= 2
            log.info("tts chunk %d/%d", i + 1, len(chunks))
    return paths


def concat_cmd(list_file: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out]


def loudnorm_cmd(inp: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-i", inp, "-af", "loudnorm=I=-18:TP=-2:LRA=11", "-ar", "44100", "-b:a", "128k", out]


def _run(cmd: list[str], timeout: int) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-400:]}")


def assemble(paths: list[str], out_dir: str) -> str:
    list_file = os.path.join(out_dir, "concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    raw = os.path.join(out_dir, "narration_raw.mp3")
    final = os.path.join(out_dir, "narration.mp3")
    _run(concat_cmd(list_file, raw), 1800)
    _run(loudnorm_cmd(raw, final), 3600)
    return final


def estimate_cost_usd(chars: int, model: str) -> float:
    return chars / 1_000_000 * PRICE_PER_M_CHARS.get(model, 15.0)


def duration_seconds(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0
