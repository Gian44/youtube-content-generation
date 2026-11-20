"""Composition in the recap-channel style, beat by beat: under every narration beat, the film from the moment that beat
tells, cut in screen order and exactly as long as the beat is spoken (``cny.beats``); no title cards, no words on the
long recap. Vertical Shorts the same way, with a few emphasis words (a key number, a main character's name) timed to the
beat that says them (Felix, Puello Media, 2026-11). The footage is muted by default (Gian 2026-11-08); ``film_audio``
lays the film's own sound — cut with the very same pieces — under the voice instead."""

from __future__ import annotations

import json
import logging
import math
import re
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
    """The Short's narration as timed cues, each beat's words inside that beat's own time (so they stay in step with the
    voice). Never drawn as captions: the emphasis picker reads them."""
    out, t = [], 0.0
    for b, d in zip(beats, durs):
        for s, e, txt in sof_shorts.caption_cues(b["text"], d):
            out.append((round(t + s, 3), round(t + e, 3), txt))
        t += d
    return out


_QUOTED = re.compile(r"[\"“][^\"”]*[\"”]?")
_EDGE = re.compile(r"^[\"'“”‘’(\[]+|[\"'“”‘’)\],.;:!?…]+$")
_TITLES = {"the", "mr", "mrs", "ms", "miss", "mister", "dr", "doctor", "judge", "sir", "lady", "lord", "uncle", "aunt",
           "father", "mother", "sister", "brother", "captain", "detective", "inspector", "sheriff", "officer", "professor",
           "old", "young", "big", "little"}


def name_tokens(names: list[str]) -> set[str]:
    """Pure: the lower-case words that make up the film's main people's names (the plan's ``people``), titles left out
    ("Judge Longstreet" → {"longstreet"})."""
    out: set[str] = set()
    for n in names or []:
        for w in re.findall(r"[^\W\d_][\w'’-]*", str(n or "")):
            w = w.lower()
            if len(w) >= 2 and w not in _TITLES:
                out.add(w)
    return out


def emphasis_cues(cues: list[tuple[float, float, str]], names: list[str]) -> list[tuple[float, float, str]]:
    """Pure: what the shared emphasis picker (``cc.textbudget``: the key number, then a capitalised name said inside a
    sentence) reads on a recap Short. Every character stays where it was (the words are timed by character share); only
    capitals change: quoted dialogue and every capitalised word that is not one of the film's main people's names is
    lower-cased, and a person's name keeps its capitals only at its first mention inside a sentence. So a Short shows a
    key number or a main character's name the first time it is said, never a quoted line's first word ("They're",
    "Don't"), a nationality or creed ("German", "Jew"), a place or a club; "Charles Rankin" never comes back as
    "Rankin". No names known → numbers only."""
    tokens = name_tokens(names)
    texts = [str(t or "").replace("\n", " ") for _, _, t in cues]
    texts = _QUOTED.sub(lambda m: m.group(0).lower(), "\n".join(texts)).split("\n")   # a quote may span two cues
    words: list[list] = []                                    # [cue index, part index, word] in narration order
    parts_by_cue: list[list[str]] = []
    for ci, text in enumerate(texts):
        parts = re.split(r"(\s+)", text)
        parts_by_cue.append(parts)
        words += [[ci, pi, p] for pi, p in enumerate(parts) if p and not p.isspace()]
    shown: set[str] = set()
    i = 0
    while i < len(words):
        bare = _EDGE.sub("", words[i][2])
        if not bare[:1].isupper():
            i += 1
            continue
        run = []                                              # a run of capitalised words, ended by punctuation
        while i < len(words):
            w = words[i][2]
            bare = _EDGE.sub("", w)
            if not bare[:1].isupper():
                break
            run.append(i)
            i += 1
            if w.rstrip("\"'”’)").endswith((".", ",", "!", "?", ";", ":", "…")):
                break
        first = run[0]
        at_start = first == 0 or bool(re.search(r"[.!?…:]$", words[first - 1][2].strip("\"'”’)")))
        keep = {k for k in run if _EDGE.sub("", words[k][2]).lower() in tokens}
        toks = {_EDGE.sub("", words[k][2]).lower() for k in keep}
        allowed = bool(keep) and not at_start and not (toks & shown) and keep == set(range(min(keep), max(keep) + 1))
        if allowed:
            shown |= toks
            keep = set(sorted(keep)[:3])                      # the picker shows at most three words at a time
        for k in run:
            if not (allowed and k in keep):
                words[k][2] = words[k][2].lower()
    for ci, pi, w in words:
        parts_by_cue[ci][pi] = w
    return [(s, e, "".join(parts_by_cue[ci])) for ci, (s, e, _) in enumerate(cues)]


def build_short(cfg, *, work: Path, name: str, video: str, short: dict, durs: list[float], cuts: list[float], crop: str | None,
                end: float, film_end: float, audio: str, film_audio: bool = False, names: list[str] | None = None) -> str:
    """Vertical Short: each beat's moment, 9:16, short cuts, under the Short's own voice; a few emphasis words timed per
    beat (a key number or one of ``names``, the film's main people, at its first mention; never the narration word for
    word). The hook as title band over the footage only when ``cfg.shorts_title_band`` (off: it covered the picture)."""
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
    # Felix (Puello Media) 2026-11: no full captions — only the key number or a main character's name at its first
    # mention, a few times a minute, timed to the beat that says it (sof.shorts.emphasis_overlays / cc.textbudget)
    cues, pngs = sof_shorts.emphasis_overlays(emphasis_cues(beat_cues(short["beats"], durs), names or []), tmp, width=w,
                                              height=h, duration=duration)
    tpng = (sof_shorts.title_png(str(short["title"]), cfg.brand, str(tmp / "title.png"), width=w, height=h)
            if getattr(cfg, "shorts_title_band", False) else None)
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
