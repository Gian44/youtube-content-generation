"""Composition in the recap-channel style: the film's shots, in screen order, hard-cut under each act's narration;
no title cards; the film's own soundtrack cut to the same moments and ducked under the voice (Gian 2026-11-08, like
the sample channels). Vertical Shorts from the same shots. Segment planning and ffmpeg chains live in ``cc.segments``."""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from cc import segments as seg
from sof import shorts as sof_shorts
from sof import tts

log = logging.getLogger("cny.render")

# re-exported for the tests
Segment, plan_segments, plan_sequence = seg.Segment, seg.plan_segments, seg.plan_sequence
still_cmd, clip_window_cmd, xfade_cmd = seg.still_cmd, seg.clip_window_cmd, seg.xfade_cmd
PRECUT_SKIP = 0.5            # cc.segments.precut drops the first 0.5 s of every clip (sof cover_clip_cmd skip)


# ---------------------------------------------------------------- the film's sound under the voice

def bed_pieces(plan: list, origin: dict[str, float]) -> list[tuple[float, float]]:
    """Pure: the film-time (start, seconds) of each planned segment. ``origin`` maps a precut clip to the film second
    its first frame shows; a segment from an unknown clip is silent (start -1)."""
    out = []
    for sg in plan:
        o = origin.get(sg.src)
        out.append((round(o + sg.offset, 3), round(sg.seconds, 3)) if o is not None else (-1.0, round(sg.seconds, 3)))
    return out


def bed_cmd(film_audio: str, pieces: list[tuple[float, float]], total: float, out: str) -> list[str]:
    """The film's sound for one section: each piece cut from the soundtrack, joined in order, padded/cut to
    ``total`` seconds; unknown pieces (start < 0) are silence."""
    real = [(s, d) for s, d in pieces if s >= 0]
    lo = max(0.0, min((s for s, _ in real), default=0.0) - 1.0)
    hi = max((s + d for s, d in real), default=lo + 1.0) + 1.0
    n = len(pieces)
    parts = [f"[0:a]asplit={n}" + "".join(f"[s{k}]" for k in range(n)) if n > 1 else "[0:a]anull[s0]"]
    for k, (s, d) in enumerate(pieces):
        if s >= 0:
            parts.append(f"[s{k}]atrim=start={s - lo:.3f}:duration={d:.3f},asetpts=PTS-STARTPTS[a{k}]")
        else:
            parts.append(f"[s{k}]atrim=duration={d:.3f},asetpts=PTS-STARTPTS,volume=0[a{k}]")
    parts.append("".join(f"[a{k}]" for k in range(n)) + f"concat=n={n}:v=0:a=1,apad,atrim=duration={total:.3f}[out]")
    return ["ffmpeg", "-y", "-v", "error", "-ss", f"{lo:.3f}", "-t", f"{hi - lo:.3f}", "-i", film_audio,
            "-filter_complex", ";".join(parts), "-map", "[out]", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", out]


def mix_film_cmd(voice: str, bed: str, out: str, *, bed_lufs: float = -24.0) -> list[str]:
    """Voice on top, the film's sound under it: the bed is levelled to ``bed_lufs`` and ducked hard while the voice
    speaks (sidechain), so music, screams and gunshots come up in the gaps like on the recap channels. The mix lasts
    exactly as long as the voice."""
    fc = (f"[1:a]aformat=channel_layouts=stereo,loudnorm=I={bed_lufs:g}:TP=-4:LRA=18,aresample=48000[bed];"
          "[0:a]aformat=channel_layouts=stereo,aresample=48000,asplit=2[v1][v2];"
          "[bed][v1]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=450[duck];"
          "[v2][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[m]")
    return ["ffmpeg", "-y", "-v", "error", "-i", voice, "-i", bed, "-filter_complex", fc, "-map", "[m]", "-ar", "48000",
            "-c:a", "pcm_s16le", out]


def concat_audio(paths: list[str], out: str) -> str:
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for p in paths:
        cmd += ["-i", p]
    cmd += ["-filter_complex", "".join(f"[{i}:a]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=0:a=1[a]",
            "-map", "[a]", "-c:a", "pcm_s16le", out]
    seg.sof_render.run(cmd, 1800)
    return out


def _origin(precuts: dict[str, str], starts: dict[str, float]) -> dict[str, float]:
    """precut path → film second of its first frame (shot start + the precut's skip)."""
    return {dst: starts[src] + PRECUT_SKIP for dst, src in precuts.items() if src in starts}


def build_long(cfg, *, work: Path, sections: list[dict], section_seconds: list[float], visuals: dict[int, dict],
               film_audio: str | None = None, starts: dict[str, float] | None = None) -> str:
    """One video per section (its act's shots in order, cut to the narration's length), concatenated → ``work/reel.mp4``;
    with ``film_audio`` also ``work/film_bed.wav``: the film's sound at the very moments the shots show."""
    tmp = work / "long_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    cache = work / "cover_169"
    parts: list[str] = []
    beds: list[str] = []
    for i, (sec, dur) in enumerate(zip(sections, section_seconds)):
        out = str(tmp / f"act_{i:02d}.mp4")
        bed = str(tmp / f"act_{i:02d}.wav")
        need_bed = bool(film_audio and starts) and not os.path.exists(bed)
        if not os.path.exists(out) or need_bed:
            v = visuals.get(int(sec.get("act_index", i)), {})
            src = list(v.get("clips") or [])
            clips = [seg.precut(c, cache, width=cfg.width, height=cfg.height, fps=cfg.fps, dim=cfg.video_dim, slow=cfg.clip_slow)
                     for c in src]
            plan = seg.plan_sequence(dur, clips, crossfade=cfg.crossfade, min_seconds=cfg.shot_min_play, max_seconds=cfg.shot_max_seconds)
            if not plan:
                raise RuntimeError(f"act {i + 1} has no shots")
            if not os.path.exists(out):
                seg.render_segments(plan, tmp / f"act_{i:02d}", width=cfg.width, height=cfg.height, fps=cfg.fps,
                                    crossfade=cfg.crossfade, total=dur, out=out, dim=cfg.video_dim)
            if need_bed:
                origin = _origin({d: c for (d, _), c in zip(clips, src)}, starts or {})
                seg.sof_render.run(bed_cmd(film_audio, bed_pieces(plan, origin), dur, bed), 900)
        parts.append(out)
        if film_audio and starts:
            beds.append(bed)
    if beds:
        concat_audio(beds, str(work / "film_bed.wav"))
    return seg.concat_videos(parts, str(work / "reel.mp4"), str(tmp / "acts.txt"))


def build_short(cfg, *, work: Path, name: str, text: str, title: str, audio: str, clips: list[str],
                film_audio: str | None = None, starts: dict[str, float] | None = None) -> str:
    """Vertical Short: the act's shots in order under the Short's narration (with the film's sound ducked under it
    when ``film_audio`` is given), captions and the hook as title band."""
    tmp = work / f"{name}_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    duration = tts.duration_seconds(audio)
    if duration <= 0:
        raise RuntimeError("short narration has no duration")
    w, h, fps = cfg.shorts_width, cfg.shorts_height, cfg.shorts_fps
    cuts = [seg.precut(c, work / "cover_916", width=w, height=h, fps=fps, dim=1.0, slow=cfg.clip_slow) for c in clips]
    plan = seg.plan_sequence(duration, cuts, crossfade=cfg.crossfade, min_seconds=cfg.shot_min_play, max_seconds=cfg.shorts_clip_seconds)
    if not plan:
        raise RuntimeError("short has no shots")
    reel = seg.render_segments(plan, tmp, width=w, height=h, fps=fps, crossfade=cfg.crossfade, total=duration, out=str(tmp / "reel.mp4"))
    if film_audio and starts:
        try:
            origin = _origin({d: c for (d, _), c in zip(cuts, clips)}, starts)
            bed = str(tmp / "bed.wav")
            seg.sof_render.run(bed_cmd(film_audio, bed_pieces(plan, origin), duration, bed), 600)
            mixed = str(tmp / "voice_film.wav")
            seg.sof_render.run(mix_film_cmd(audio, bed, mixed, bed_lufs=cfg.shorts_film_lufs), 600)
            audio = mixed
        except Exception as exc:  # noqa: BLE001 — the Short still goes out with the voice alone
            log.warning("%s: film sound skipped (%s)", name, exc)
    cues = sof_shorts.caption_cues(text, duration)
    pngs = [sof_shorts.caption_png(t, str(tmp / f"cue_{i:03d}.png"), width=w, height=h) for i, (_, _, t) in enumerate(cues)]
    tpng = sof_shorts.title_png(title, cfg.brand, str(tmp / "title.png"), width=w, height=h)
    out = str(work / f"{name}.mp4")
    seg.sof_render.run(sof_shorts.overlay_cmd(reel, audio, out, cues=cues, cue_pngs=pngs, title_png_path=tpng, credit_png_path=None,
                                              duration=duration, fps=fps), 900)
    return out


def chapter_rows(sections: list[dict], starts: list[float]) -> list[dict]:
    return [{"start": round(st, 2), "title": str(s.get("heading") or f"Part {i + 1}")} for i, (st, s) in enumerate(zip(starts, sections))]


def scaled_durations(chunk_paths: list[str], owner: list[int], n_sections: int, total: float) -> list[float]:
    """Per-section narration seconds from the chunk files, scaled so they sum to the assembled narration."""
    durs = [tts.duration_seconds(p) for p in chunk_paths]
    per = [0.0] * n_sections
    for d, si in zip(durs, owner):
        per[si] += d
    s = sum(per)
    scale = total / s if s > 0 else 1.0
    out = [round(d * scale, 3) for d in per]
    if out:
        out[-1] = round(max(0.0, total - sum(out[:-1])), 3)      # rounding lands on the last section
    return out


def section_starts(durations: list[float]) -> list[float]:
    out, t = [], 0.0
    for d in durations:
        out.append(round(t, 3))
        t += d
    return out


def frames_to_check(duration: float, n: int = 12) -> list[float]:
    step = duration / max(1, n)
    return [round(step * (i + 0.5), 2) for i in range(int(math.floor(duration / step)))] if step > 0 else []
