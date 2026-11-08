"""Shots from the film per act. There is no stock fallback (Gian, 2026-11-08): an act with no shots of its own borrows
its neighbours' shots, and a film that yields none fails the run."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from cny import align, cut

log = logging.getLogger("cny.visuals")


def film_visuals(cfg, *, acts: list[dict], work: Path, llm, title: str, video: str, srt: str | None,
                 vertical_for: set[int] = frozenset()) -> tuple[dict[int, dict], dict]:
    """Shots from the film per act (resumable via ``visuals.json``): windows from the .srt (writer call), from
    faster-whisper when there is no .srt, else proportional; then scene-cut shots inside each window.
    Returns (plan, info)."""
    plan_path = work / "visuals.json"
    if plan_path.exists():
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        if saved.get("_source") == "film" and all(Path(p).exists() for k, v in saved.items() if k != "_source"
                                                  for p in v.get("clips", []) + v.get("clips_v", [])):
            return {int(k): v for k, v in saved.items() if k != "_source"}, {"how": "cached"}
    t0 = time.monotonic()
    duration = cut.duration_seconds(video)
    cues: list[align.Cue] = []
    how = "proportional"
    if srt and Path(srt).exists():
        cues = align.parse_srt(Path(srt).read_text(encoding="utf-8", errors="replace"))
        how = "srt"
    if not cues:
        cues = align.transcribe(video, work, model=cfg.whisper_model)
        how = "whisper" if cues else "proportional"
    windows = align.align_acts(llm, title=title, acts=acts, cues=cues, duration=duration, min_s=cfg.film_window_min,
                               max_s=cfg.film_window_max)
    if windows and windows[0].get("how") == "proportional":
        how = "proportional"
    (work / "windows.json").write_text(json.dumps({"how": how, "duration": duration, "windows": windows}, indent=1), encoding="utf-8")
    out: dict[int, dict] = {}
    for w in windows:
        i = int(w["act_index"])
        clips, shots = cut.act_shots(video, str(work / "shots"), act_index=i, window=w, want=cfg.shots_per_act,
                                     min_len=cfg.shot_min_seconds, max_len=cfg.shot_max_seconds, threshold=cfg.scene_threshold,
                                     width=cfg.width, height=cfg.height, fps=cfg.fps)
        out[i] = {"clips": clips, "shots": shots, "film": True, "window": [w["start_seconds"], w["end_seconds"]]}
        if i in vertical_for:                     # the Shorts' acts: the same shots, centre-cropped to 9:16
            out[i]["clips_v"] = cut.extract_shots(video, shots, str(work / "shots_v"), prefix=f"act{i:02d}", width=cfg.shorts_width,
                                                  height=cfg.shorts_height, fps=cfg.shorts_fps, fit="crop")
        print(f"act {i + 1}: {len(clips)} shots from {w['start_seconds']:.0f}-{w['end_seconds']:.0f} s", flush=True)
    for i, v in out.items():                     # an act with no shots borrows its neighbours'
        if not v["clips"]:
            pool = [c for j in sorted(out, key=lambda j: abs(j - i)) for c in out[j]["clips"]]
            v["clips"], v["borrowed"] = pool[:cfg.shots_per_act], True
        if i in vertical_for and not v.get("clips_v"):
            v["clips_v"] = [c for j in sorted(out, key=lambda j: abs(j - i)) for c in out[j].get("clips_v") or []][:cfg.shots_per_act]
    plan_path.write_text(json.dumps({"_source": "film", **{str(k): v for k, v in out.items()}}, indent=1), encoding="utf-8")
    log.info("film visuals for %d acts (%s) in %.0f s", len(acts), how, time.monotonic() - t0)
    return out, {"how": how, "duration": duration, "cues": len(cues)}
