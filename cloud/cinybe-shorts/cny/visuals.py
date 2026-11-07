"""Stock b-roll per act (shared ``cc.broll``: Pexels + Pixabay video, vision-checked), stock photos as the fallback
and the neighbours' clips as the last resort, so a run never dies on footage. No film footage, no posters."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from cc import broll
from sof import vision

from cny import align, cut

log = logging.getLogger("cny.visuals")


def film_visuals(cfg, *, acts: list[dict], work: Path, llm, title: str, video: str, srt: str | None) -> tuple[dict[int, dict], dict]:
    """Shots from the film per act (resumable via ``visuals.json``): windows from the .srt (writer call), from
    faster-whisper when there is no .srt, else proportional; then scene-cut shots inside each window.
    Returns (plan, info)."""
    plan_path = work / "visuals.json"
    if plan_path.exists():
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        if saved.get("_source") == "film" and all(Path(p).exists() for k, v in saved.items() if k != "_source" for p in v.get("clips", [])):
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
        clips = cut.act_shots(video, str(work / "shots"), act_index=i, window=w, want=cfg.shots_per_act,
                              min_len=cfg.shot_min_seconds, max_len=cfg.shot_max_seconds, threshold=cfg.scene_threshold,
                              width=cfg.width, height=cfg.height, fps=cfg.fps)
        out[i] = {"clips": clips, "photos": [], "film": True, "window": [w["start_seconds"], w["end_seconds"]]}
        print(f"act {i + 1}: {len(clips)} shots from {w['start_seconds']:.0f}-{w['end_seconds']:.0f} s", flush=True)
    for i, v in out.items():                     # an act with no shots borrows its neighbours'
        if not v["clips"]:
            pool = [c for j in sorted(out, key=lambda j: abs(j - i)) for c in out[j]["clips"]]
            v["clips"], v["borrowed"] = pool[:cfg.shots_per_act], True
    plan_path.write_text(json.dumps({"_source": "film", **{str(k): v for k, v in out.items()}}, indent=1), encoding="utf-8")
    log.info("film visuals for %d acts (%s) in %.0f s", len(acts), how, time.monotonic() - t0)
    return out, {"how": how, "duration": duration, "cues": len(cues)}


def gather_all(cfg, *, acts: list[dict], work: Path, vis: vision.Vision) -> dict[int, dict]:
    """Per act: ``{"clips": [...], "photos": [...], "queries": [...]}`` (resumable; written to ``visuals.json``)."""
    plan_path = work / "visuals.json"
    if plan_path.exists():
        saved = {int(k): v for k, v in json.loads(plan_path.read_text(encoding="utf-8")).items() if k != "_source"}
        if all(Path(p).exists() for v in saved.values() for p in v.get("clips", []) + v.get("photos", [])):
            return saved
    t0 = time.monotonic()
    out: dict[int, dict] = {}
    seen: set[str] = set()
    for a in acts:
        i = int(a["index"])
        queries = list(a.get("broll_queries") or [])[:3]
        d = work / "footage" / f"act{i:02d}"
        clips = broll.footage_for(queries=queries, summary=str(a.get("summary") or ""), out_dir=str(d), vis=vis,
                                  pexels_key=cfg.sof.pexels_api_key, pixabay_key=cfg.sof.pixabay_api_key,
                                  want=cfg.footage_per_act, candidates=cfg.footage_candidates, budget=cfg.footage_budget,
                                  exclude_ids=seen, kind="movie recap")
        seen |= broll.used_ids(str(d))
        photos: list[str] = []
        if not clips:
            photos = broll.photos_for(queries=queries, out_dir=str(work / "photos" / f"act{i:02d}"),
                                      pexels_key=cfg.sof.pexels_api_key, pixabay_key=cfg.sof.pixabay_api_key,
                                      want=cfg.photos_per_act)
        out[i] = {"clips": clips, "photos": photos, "queries": queries}
        print(f"act {i + 1}: {len(clips)} clips" + (f", {len(photos)} photos" if photos else "")
              + f" · {queries[0] if queries else '-'}", flush=True)
    for i, v in out.items():                     # an act with nothing borrows its neighbours' clips
        if not v["clips"] and not v["photos"]:
            pool = [c for j in sorted(out, key=lambda j: abs(j - i)) for c in out[j]["clips"]]
            v["clips"], v["borrowed"] = pool[:cfg.footage_per_act], True
    plan_path.write_text(json.dumps({str(k): v for k, v in out.items()}, indent=1), encoding="utf-8")
    log.info("visuals for %d acts in %.0f s", len(acts), time.monotonic() - t0)
    return out
