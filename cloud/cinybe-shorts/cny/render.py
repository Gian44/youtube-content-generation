"""Composition in the recap-channel style, beat by beat: under every narration beat, the film from the moment that beat
tells, cut in screen order and exactly as long as the beat is spoken (``cny.beats``); no title cards. Vertical Shorts the
same way, with captions timed per beat. The footage is muted by default (Gian 2026-11-08); ``film_audio`` lays the film's
own sound — cut with the very same pieces — under the voice instead."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from sof import shorts as sof_shorts
from sof import tts
from sof import render as sof_render

from cny import beats as beatmod

log = logging.getLogger("cny.render")


# ---------------------------------------------------------------- the long recap

def build_long(cfg, *, work: Path, video: str, sections: list[dict], beat_seconds: list[list[float]], cuts: dict[int, list[float]],
               crop: str | None, film_end: float, acts: list[dict], audio: bool = False) -> tuple[str, list[dict]]:
    """Every section's beats → ``work/long_beats/aNN_KKK.mp4`` → ``work/reel.mp4``. Returns the reel and the per-beat
    log (moment, window, pieces) that is kept as ``beats.json``."""
    out_dir = work / "long_beats"
    by_idx = {int(a["index"]): a for a in acts}
    files: list[str] = []
    log_rows: list[dict] = []
    for sec, durs in zip(sections, beat_seconds):
        ai = int(sec["act_index"])
        act = by_idx.get(ai, {})
        f, info = beatmod.render(cfg, video=video, beats=sec["beats"], durs=durs, cuts=cuts.get(ai, []), out_dir=out_dir,
                                 prefix=f"a{ai:02d}", end=float(act.get("end_seconds", film_end)), film_end=film_end,
                                 width=cfg.width, height=cfg.height, fps=cfg.fps, fit="blur", crop=crop, audio=audio)
        files += f
        log_rows += [dict(r, act=ai, beat=k, text=b["text"][:120]) for k, (r, b) in enumerate(zip(info, sec["beats"]))]
    reel = beatmod.concat(files, str(work / "reel.mp4"), str(out_dir / "list.txt"))
    return reel, log_rows


# ---------------------------------------------------------------- Shorts

def beat_cues(beats: list[dict], durs: list[float]) -> list[tuple[float, float, str]]:
    """Captions per beat, each beat's words inside that beat's own time (so they stay in step with the voice)."""
    out, t = [], 0.0
    for b, d in zip(beats, durs):
        for s, e, txt in sof_shorts.caption_cues(b["text"], d):
            out.append((round(t + s, 3), round(t + e, 3), txt))
        t += d
    return out


def build_short(cfg, *, work: Path, name: str, video: str, short: dict, durs: list[float], cuts: list[float], crop: str | None,
                end: float, film_end: float, audio: str, film_audio: bool = False) -> str:
    """Vertical Short: each beat's moment, 9:16, short cuts, under the Short's own voice; captions per beat and the hook
    as title band."""
    tmp = work / f"{name}_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    duration = tts.duration_seconds(audio)
    if duration <= 0:
        raise RuntimeError("short narration has no duration")
    w, h, fps = cfg.shorts_width, cfg.shorts_height, cfg.shorts_fps
    files, info = beatmod.render(cfg, video=video, beats=short["beats"], durs=durs, cuts=cuts, out_dir=tmp / "beats", prefix="b",
                                 end=end, film_end=film_end, width=w, height=h, fps=fps, fit="crop", crop=crop,
                                 audio=film_audio, max_len=cfg.shorts_clip_seconds)
    (tmp / "beats.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    reel = beatmod.concat(files, str(tmp / "reel.mp4"), str(tmp / "list.txt"))
    if film_audio:
        try:
            bed, mixed = str(tmp / "bed.wav"), str(tmp / "voice_film.wav")
            sof_render.run(["ffmpeg", "-y", "-v", "error", "-i", reel, "-vn", "-ac", "2", "-ar", "48000", bed], 600)
            sof_render.run(mix_film_cmd(audio, bed, mixed, bed_lufs=cfg.shorts_film_lufs), 600)
            audio = mixed
        except Exception as exc:  # noqa: BLE001 — the Short still goes out with the voice alone
            log.warning("%s: film sound skipped (%s)", name, exc)
    cues = beat_cues(short["beats"], durs)
    pngs = [sof_shorts.caption_png(t, str(tmp / f"cue_{i:03d}.png"), width=w, height=h) for i, (_, _, t) in enumerate(cues)]
    tpng = sof_shorts.title_png(str(short["title"]), cfg.brand, str(tmp / "title.png"), width=w, height=h)
    out = str(work / f"{name}.mp4")
    sof_render.run(sof_shorts.overlay_cmd(reel, audio, out, cues=cues, cue_pngs=pngs, title_png_path=tpng, credit_png_path=None,
                                          duration=duration, fps=fps), 900)
    return out


# ---------------------------------------------------------------- the film's sound under the voice (when enabled)

def mix_film_cmd(voice: str, bed: str, out: str, *, bed_lufs: float = -24.0) -> list[str]:
    """Voice on top, the film's sound under it: the bed is levelled to ``bed_lufs`` and ducked hard while the voice
    speaks (sidechain, ~12.5 dB measured), so music, screams and gunshots come up in the gaps. Lasts as long as the voice."""
    fc = (f"[1:a]aformat=channel_layouts=stereo,loudnorm=I={bed_lufs:g}:TP=-4:LRA=18,aresample=48000[bed];"
          "[0:a]aformat=channel_layouts=stereo,aresample=48000,asplit=2[v1][v2];"
          "[bed][v1]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=450[duck];"
          "[v2][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[m]")
    return ["ffmpeg", "-y", "-v", "error", "-i", voice, "-i", bed, "-filter_complex", fc, "-map", "[m]", "-ar", "48000",
            "-c:a", "pcm_s16le", out]


# ---------------------------------------------------------------- timing helpers

def beat_durations(paths: list[str], total: float) -> list[float]:
    """Each beat's spoken seconds, scaled so they sum exactly to the assembled narration (mp3 framing adds a few ms
    per file; scaling spreads that instead of letting it pile up at the end)."""
    durs = [max(0.05, tts.duration_seconds(p)) for p in paths]
    s = sum(durs)
    scale = total / s if s > 0 else 1.0
    out = [round(d * scale, 3) for d in durs]
    if out:
        out[-1] = round(max(0.05, total - sum(out[:-1])), 3)
    return out


def split_by(values: list[float], counts: list[int]) -> list[list[float]]:
    out, i = [], 0
    for n in counts:
        out.append(values[i:i + n])
        i += n
    return out


def chapter_rows(sections: list[dict], starts: list[float]) -> list[dict]:
    return [{"start": round(st, 2), "title": str(s.get("heading") or f"Part {i + 1}")} for i, (st, s) in enumerate(zip(starts, sections))]


def section_starts(durations: list[float]) -> list[float]:
    out, t = [], 0.0
    for d in durations:
        out.append(round(t, 3))
        t += d
    return out


def frames_to_check(duration: float, n: int = 12) -> list[float]:
    step = duration / max(1, n)
    return [round(step * (i + 0.5), 2) for i in range(int(math.floor(duration / step)))] if step > 0 else []
