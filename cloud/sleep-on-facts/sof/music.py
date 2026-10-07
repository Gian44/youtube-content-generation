"""Procedural ambient bed: a soft, slowly breathing chord pad with a whisper of filtered noise.

Synthesised with numpy at run time — no licensed asset in a public repo, nothing for Content ID to
match, and it is not generative AI (so it needs no synthetic-media disclosure either). The bed is
rendered as one seamless loop (its length is a whole number of chord cycles and it ends where it
starts), then ffmpeg loops it under the narration far below speech level.
"""

from __future__ import annotations

import logging
import subprocess
import wave

import numpy as np

log = logging.getLogger("sof.music")

SR = 44_100
# Two chords a sleepy pad can drift between, in Hz (A2-based: Amin9 → Fmaj7). Low, open, no thirds
# fighting the voice's fundamentals.
CHORDS = [
    (110.00, 164.81, 220.00, 246.94, 329.63),   # A2 E3 A3 B3 E4   (A sus/9)
    (87.31, 130.81, 174.61, 220.00, 261.63),    # F2 C3 F3 A3 C4   (F maj7-ish)
]
CHORD_SECONDS = 24.0       # one chord held this long; 2 chords = one 48 s cycle
LOOP_CYCLES = 2            # loop length 96 s


def _env(n: int, attack: float, release: float) -> np.ndarray:
    t = np.linspace(0, 1, n, endpoint=False)
    e = np.ones(n)
    a = int(attack * n)
    r = int(release * n)
    if a:
        e[:a] = 0.5 - 0.5 * np.cos(np.pi * t[:a] / attack)
    if r:
        e[-r:] = 0.5 + 0.5 * np.cos(np.pi * (t[-r:] - (1 - release)) / release)
    return e


def _pad_note(freq: float, n: int, rng: np.random.Generator) -> np.ndarray:
    """Three slightly detuned sines + a quiet octave, with slow random vibrato — a cheap analog pad."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    for det in (-0.35, 0.0, 0.35):          # cents-ish detune in Hz
        f = freq + det
        vib = 0.15 * np.sin(2 * np.pi * (0.07 + 0.03 * rng.random()) * t + rng.random() * 6.28)
        out += np.sin(2 * np.pi * f * t + vib)
    out += 0.25 * np.sin(2 * np.pi * 2 * freq * t)
    return out / 3.25


def render_loop(out_wav: str, *, seed: int = 7) -> str:
    rng = np.random.default_rng(seed)
    chord_n = int(CHORD_SECONDS * SR)
    pieces = []
    for _ in range(LOOP_CYCLES):
        for chord in CHORDS:
            block = np.zeros(chord_n)
            for k, f in enumerate(chord):
                block += _pad_note(f, chord_n, rng) * (1.0 if k else 1.4)   # bass a touch louder
            # Long cross-faded swells so chord changes breathe instead of cutting.
            block *= _env(chord_n, attack=0.35, release=0.35)
            pieces.append(block)
    pad = np.concatenate(pieces)
    # Overlap successive chords by half their fade so the pad never drops to silence.
    half = int(0.35 * chord_n)
    mixed = np.zeros(len(pad) - half * (len(pieces) - 1))
    pos = 0
    for p in pieces:
        mixed[pos:pos + chord_n] += p
        pos += chord_n - half
    # Whisper of brown-ish noise (white shaped by 1/f in the frequency domain — vectorised, instant).
    white = rng.standard_normal(len(mixed))
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(len(mixed), 1 / SR)
    spec[1:] /= np.maximum(freqs[1:], 20.0)
    spec[0] = 0
    noise = np.fft.irfft(spec, n=len(mixed))
    noise /= max(1e-9, np.abs(noise).max())
    sig = mixed / max(1e-9, np.abs(mixed).max()) * 0.8 + noise * 0.06
    # Make the loop seamless: short equal-power crossfade of the tail into the head.
    xf = int(2.0 * SR)
    fade_in = np.sin(np.linspace(0, np.pi / 2, xf)) ** 2
    sig[:xf] = sig[:xf] * fade_in + sig[-xf:] * (1 - fade_in)
    sig = sig[:-xf]
    sig = np.clip(sig / max(1e-9, np.abs(sig).max()) * 0.9, -1, 1)
    pcm = (sig * 32767).astype("<i2")
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    log.info("ambient loop %.0f s → %s", len(sig) / SR, out_wav)
    return out_wav


def normalize_cmd(bed: str, out: str, *, bed_lufs: float) -> list[str]:
    """Level the short loop once (cheap, and avoids running loudnorm over three hours of audio)."""
    return ["ffmpeg", "-y", "-i", bed, "-af", f"lowpass=f=1800,loudnorm=I={bed_lufs}:TP=-8:LRA=5", "-ar", str(SR), out]


def mix_cmd(narration: str, bed: str, out: str) -> list[str]:
    """Loop the levelled bed under the narration; the mix ends with the voice.

    ``normalize=0`` keeps amix from halving both inputs, so speech level is unchanged; the bed sits
    wherever ``normalize_cmd`` put it (default −34 LUFS, i.e. ~16 dB under a −18 LUFS voice).
    """
    fc = (
        "[1:a]aloop=loop=-1:size=2e9[bed];"
        "[0:a][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[mix]"
    )
    return ["ffmpeg", "-y", "-i", narration, "-i", bed, "-filter_complex", fc, "-map", "[mix]",
            "-ar", "44100", "-ac", "2", "-b:a", "160k", out]


def _run(cmd: list[str], timeout: int) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({cmd[-1]}): {r.stderr[-400:]}")


def mix(narration: str, out: str, *, work_dir: str, bed_lufs: float = -34.0) -> str:
    """narration.mp3 + synthesised bed → ``out`` (mp3)."""
    import os

    loop = os.path.join(work_dir, "bed_loop.wav")
    levelled = os.path.join(work_dir, "bed.wav")
    if not os.path.exists(levelled):
        render_loop(loop)
        _run(normalize_cmd(loop, levelled, bed_lufs=bed_lufs), 600)
    _run(mix_cmd(narration, levelled, out), 3600)
    return out
