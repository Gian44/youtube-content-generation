"""Narration: text in chunks → one loudness-normalised MP3 for bedtime.

Voices (``speak`` picks by ``cfg.tts_backend``):
- ``pocket`` (default since 2026-10-25, evening): the channel's own designed voice (brand/voice/narrator.wav,
  see its README) spoken by Kyutai's Pocket TTS on the runner's CPU, two worker processes. Falls back to
  Kokoro for the whole video if Pocket TTS cannot load (e.g. the Hugging Face gate/token).
- ``kokoro`` (default since 2026-10-25): Kokoro-82M, an open-weights TTS model (Apache-2.0, free for
  monetised use) run on the GitHub runner's CPU through kokoro-onnx. The voice is our own: a weighted blend of
  Kokoro's British "George" and "Lewis" voices (Gian picked sample 3), so no other channel sounds the same.
  No API, no credits; costs ~0.3–0.6 s of CPU per second of audio.
- ``openai``: gpt-4o-mini-tts "echo" with British host directions (needs OpenAI credits).
"""

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
# gpt-4o-mini-tts is billed per audio token (~$0.015/min ≈ 900 chars/min at narration pace) → ≈ $17/M chars.
PRICE_PER_M_CHARS = {"tts-1": 15.0, "tts-1-hd": 30.0, "gpt-4o-mini-tts": 17.0}
_SENT = re.compile(r"(?<=[.!?])\s+")


def request_body(chunk: str, *, model: str, voice: str, speed: float, instructions: str | None) -> dict:
    """``speed`` only exists on tts-1/tts-1-hd; ``instructions`` (delivery direction) only on gpt-4o-*-tts."""
    body = {"model": model, "voice": voice, "input": chunk, "response_format": "mp3"}
    if model.startswith("gpt-4o"):
        if instructions:
            body["instructions"] = instructions
    else:
        body["speed"] = speed
    return body


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


def chunk_sections(sections: list[dict], limit: int) -> tuple[list[str], list[int]]:
    """Chunk each movement separately so chunk boundaries coincide with movement boundaries.
    Returns (chunks, owner) where ``owner[i]`` is the section index of chunk i."""
    chunks: list[str] = []
    owner: list[int] = []
    for si, sec in enumerate(sections):
        for ch in chunk_text(str(sec.get("body") or ""), limit):
            chunks.append(ch)
            owner.append(si)
    return chunks, owner


def section_starts(chunk_paths: list[str], owner: list[int], n_sections: int, *, duration=None) -> list[float]:
    """Start time (s) of each section in the concatenated narration, from the chunk durations.
    Sections that produced no chunk inherit the next start (they are dropped as chapters later)."""
    duration = duration or duration_seconds
    starts: list[float | None] = [None] * n_sections
    t = 0.0
    for path, si in zip(chunk_paths, owner):
        if starts[si] is None:
            starts[si] = t
        t += duration(path)
    out: list[float] = []
    nxt = t
    for v in reversed(starts):
        nxt = v if v is not None else nxt
        out.append(nxt)
    return list(reversed(out))


def synthesize(chunks: list[str], out_dir: str, *, api_key: str, model: str, voice: str, speed: float,
               instructions: str | None = None, transport=None, retries: int = 4, sleep=time.sleep) -> list[str]:
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
                try:
                    r = c.post(URL, json=request_body(chunk, model=model, voice=voice, speed=speed, instructions=instructions))
                except httpx.TransportError as exc:  # read timeout, dropped connection: retry, don't kill a 3-h run
                    if attempt == retries:
                        raise RuntimeError(f"TTS chunk {i} failed after {retries + 1} attempts: {exc!r}") from exc
                    log.warning("tts chunk %d: %r, retrying in %.0f s", i + 1, exc, delay)
                    sleep(delay)
                    delay *= 2
                    continue
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


def loudnorm_cmd(inp: str, out: str, pre: str = "") -> list[str]:
    """``pre``: filters before loudnorm (Kokoro gets a little low-end warmth and a soft top)."""
    af = (pre.rstrip(",") + "," if pre else "") + "loudnorm=I=-18:TP=-2:LRA=11"
    return ["ffmpeg", "-y", "-i", inp, "-af", af, "-ar", "44100", "-b:a", "128k", out]


def _run(cmd: list[str], timeout: int) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-400:]}")


def assemble(paths: list[str], out_dir: str, pre: str = "") -> str:
    list_file = os.path.join(out_dir, "concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    raw = os.path.join(out_dir, "narration_raw.mp3")
    final = os.path.join(out_dir, "narration.mp3")
    _run(concat_cmd(list_file, raw), 1800)
    _run(loudnorm_cmd(raw, final, pre), 3600)
    return final


def estimate_cost_usd(chars: int, model: str) -> float:
    if model in ("kokoro", "pocket"):
        return 0.0
    return chars / 1_000_000 * PRICE_PER_M_CHARS.get(model, 15.0)


def duration_seconds(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, timeout=60)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ------------------------------------------------------------------ Kokoro (open-weights, runs on the CPU)

KOKORO_EQ = "equalizer=f=180:t=q:w=1:g=2,lowpass=f=11000"
_KOKORO_ENGINE = None


def parse_voice_mix(spec: str) -> list[tuple[str, float]]:
    """"bm_george:0.6,bm_lewis:0.4" → [("bm_george", 0.6), ("bm_lewis", 0.4)] (weights normalised to 1);
    a plain "bm_george" → [("bm_george", 1.0)]."""
    parts: list[tuple[str, float]] = []
    for item in str(spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        name, _, w = item.partition(":")
        try:
            weight = float(w) if w.strip() else 1.0
        except ValueError:
            raise ValueError(f"bad voice weight in {spec!r}") from None
        if weight > 0:
            parts.append((name.strip(), weight))
    if not parts:
        raise ValueError(f"no voice in {spec!r}")
    total = sum(w for _, w in parts)
    return [(n, w / total) for n, w in parts]


def kokoro_engine(model_path: str, voices_path: str):
    """One Kokoro session per process (loading the 310 MB model takes a few seconds)."""
    global _KOKORO_ENGINE
    if _KOKORO_ENGINE is None:
        from kokoro_onnx import Kokoro

        logging.getLogger("phonemizer").setLevel(logging.ERROR)     # "words count mismatch" on every chunk

        for f in (model_path, voices_path):
            if not Path(f).exists():
                raise RuntimeError(f"Kokoro model file missing: {f} (the workflow downloads it; see README)")
        _KOKORO_ENGINE = Kokoro(model_path, voices_path)
    return _KOKORO_ENGINE


def voice_style(engine, spec: str):
    import numpy as np

    mix = parse_voice_mix(spec)
    style = sum(w * np.asarray(engine.get_voice_style(name), dtype=np.float32) for name, w in mix)
    return np.asarray(style, dtype=np.float32)


def encode_mp3_cmd(wav: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame",
            "-b:a", "96k", out]


def synthesize_kokoro(chunks: list[str], out_dir: str, *, voice: str, speed: float = 0.88, lang: str = "en-gb",
                      sentence_pause: float = 0.55, clause_pause: float = 0.2, model_path: str = "",
                      voices_path: str = "", engine=None, write_wav=None, encode=None) -> list[str]:
    """Same contract as ``synthesize``: one ``NNNN.mp3`` per chunk (existing files are kept, so a resumed run
    only speaks what is missing). ``engine``/``write_wav``/``encode`` are injectable for tests."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    todo = [i for i in range(len(chunks)) if not ((Path(out_dir) / f"{i:04d}.mp3").exists()
                                                    and (Path(out_dir) / f"{i:04d}.mp3").stat().st_size > 0)]
    eng = None
    style = None
    if todo:
        eng = engine or kokoro_engine(model_path, voices_path)
        style = voice_style(eng, voice)
    if write_wav is None:
        def write_wav(path, samples, sr):
            import soundfile as sf

            sf.write(path, samples, sr)
    encode = encode or (lambda wav, mp3: _run(encode_mp3_cmd(wav, mp3), 600))
    t0 = time.monotonic()
    spoken = 0.0
    for i, chunk in enumerate(chunks):
        p = Path(out_dir) / f"{i:04d}.mp3"
        paths.append(str(p))
        if i not in todo:
            continue
        samples, sr = eng.create(chunk, voice=style, speed=speed, lang=lang, sentence_pause=sentence_pause,
                                 clause_pause=clause_pause)
        wav = Path(out_dir) / f"{i:04d}.wav"
        write_wav(str(wav), samples, sr)
        encode(str(wav), str(p))
        wav.unlink(missing_ok=True)
        spoken += len(samples) / float(sr or 1)
        log.info("tts chunk %d/%d (kokoro) · %.0f s audio so far in %.0f s", i + 1, len(chunks), spoken,
                 time.monotonic() - t0)
    return paths


# ------------------------------------------------------------------ Pocket TTS (our designed voice, CPU)

_POCKET = None


class PocketUnavailable(RuntimeError):
    pass


def pocket_engine(threads: int = 2):
    global _POCKET
    if _POCKET is None:
        try:
            import torch
            from pocket_tts import TTSModel

            torch.set_num_threads(max(1, int(threads)))
            _POCKET = TTSModel.load_model()
        except Exception as exc:  # noqa: BLE001 — missing package, gated weights, no token
            raise PocketUnavailable(f"Pocket TTS could not load: {str(exc)[:300]}") from exc
    return _POCKET


def _pocket_worker_init(voice_wav: str, threads: int):
    global _POCKET_STATE
    eng = pocket_engine(threads)
    _POCKET_STATE = eng.get_state_for_audio_prompt(voice_wav)


def _pocket_worker_task(args):
    text, wav_path = args
    import scipy.io.wavfile

    eng = pocket_engine()
    audio = eng.generate_audio(_POCKET_STATE, text)
    samples = audio.detach().cpu().numpy()
    scipy.io.wavfile.write(wav_path, eng.sample_rate, samples)
    return len(samples) / float(eng.sample_rate)


def synthesize_pocket(chunks: list[str], out_dir: str, *, voice_wav: str, workers: int = 2, threads: int = 2,
                      engine=None, write_wav=None, encode=None) -> list[str]:
    """Same contract as ``synthesize``: one ``NNNN.mp3`` per chunk, existing ones kept. ``workers`` > 1 runs
    that many processes (each loads the model and voice once; Pocket TTS uses ~2 cores each)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if not Path(voice_wav).exists():
        raise PocketUnavailable(f"voice reference missing: {voice_wav}")
    paths = [str(out / f"{i:04d}.mp3") for i in range(len(chunks))]
    todo = [i for i, p in enumerate(paths) if not (Path(p).exists() and Path(p).stat().st_size > 0)]
    encode = encode or (lambda wav, mp3: _run(encode_mp3_cmd(wav, mp3), 600))
    t0 = time.monotonic()
    spoken = 0.0
    if not todo:
        return paths
    if engine is not None or workers <= 1:
        eng = engine or pocket_engine(threads)
        state = eng.get_state_for_audio_prompt(voice_wav)
        if write_wav is None:
            def write_wav(path, samples, sr):
                import scipy.io.wavfile

                scipy.io.wavfile.write(path, sr, samples)
        for i in todo:
            audio = eng.generate_audio(state, chunks[i])
            samples = audio.detach().cpu().numpy() if hasattr(audio, "detach") else audio
            wav = out / f"{i:04d}.wav"
            write_wav(str(wav), samples, eng.sample_rate)
            encode(str(wav), paths[i])
            wav.unlink(missing_ok=True)
            spoken += len(samples) / float(eng.sample_rate)
            log.info("tts chunk %d/%d (pocket) · %.0f s audio so far in %.0f s", i + 1, len(chunks), spoken,
                     time.monotonic() - t0)
        return paths
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor

    try:
        pocket_engine(threads)          # fail fast here (gate/token) instead of inside every worker
    except PocketUnavailable:
        raise
    jobs = [(chunks[i], str(out / f"{i:04d}.wav")) for i in todo]
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"),
                             initializer=_pocket_worker_init, initargs=(voice_wav, threads)) as ex:
        for i, secs in zip(todo, ex.map(_pocket_worker_task, jobs)):
            wav = out / f"{i:04d}.wav"
            encode(str(wav), paths[i])
            wav.unlink(missing_ok=True)
            spoken += secs
            log.info("tts chunk %d/%d (pocket ×%d) · %.0f s audio so far in %.0f s", i + 1, len(chunks), workers,
                     spoken, time.monotonic() - t0)
    return paths


def _kokoro_speak(chunks, out_dir, cfg, short):
    return synthesize_kokoro(chunks, out_dir, voice=cfg.kokoro_voice,
                             speed=cfg.kokoro_short_speed if short else cfg.kokoro_speed, lang=cfg.kokoro_lang,
                             sentence_pause=cfg.kokoro_short_sentence_pause if short else cfg.kokoro_sentence_pause,
                             clause_pause=cfg.kokoro_clause_pause, model_path=cfg.kokoro_model_path,
                             voices_path=cfg.kokoro_voices_path)


def speak(chunks: list[str], out_dir: str, cfg, *, short: bool = False) -> list[str]:
    """Narrate ``chunks`` with the configured voice (``cfg.tts_backend``). If Pocket TTS cannot load, the whole
    narration switches to Kokoro (``cfg.tts_backend`` is changed so later steps match) — never a mix."""
    if cfg.tts_backend == "pocket":
        try:
            return synthesize_pocket(chunks, out_dir, voice_wav=cfg.pocket_voice, workers=1 if short else cfg.pocket_workers,
                                     threads=cfg.pocket_threads)
        except PocketUnavailable as exc:
            log.warning("%s — narrating with Kokoro instead", exc)
            for f in Path(out_dir).glob("*.mp3"):
                f.unlink()
            cfg.tts_backend = "kokoro"
    if cfg.tts_backend == "kokoro":
        return _kokoro_speak(chunks, out_dir, cfg, short)
    return synthesize(chunks, out_dir, api_key=cfg.openai_api_key, model=cfg.tts_model, voice=cfg.tts_voice,
                      speed=min(1.0, cfg.tts_speed + 0.1) if short else cfg.tts_speed,
                      instructions=cfg.shorts_tts_instructions if short else cfg.tts_instructions)


def post_filter(cfg) -> str:
    """Filters applied before loudnorm for the configured voice."""
    return KOKORO_EQ if cfg.tts_backend == "kokoro" else ""


def cost_model(cfg) -> str:
    return "kokoro" if cfg.tts_backend in ("kokoro", "pocket") else cfg.tts_model


def voice_label(cfg) -> str:
    if cfg.tts_backend == "pocket":
        return f"pocket {Path(cfg.pocket_voice).name}"
    if cfg.tts_backend == "kokoro":
        return f"kokoro {cfg.kokoro_voice}"
    return f"{cfg.tts_model} {cfg.tts_voice}"
