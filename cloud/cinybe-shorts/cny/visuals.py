"""Shots from the film per act: the act's own time range (written by the planner from the subtitles) → ffmpeg scene
detection → enough shots, in screen order, to carry that act's narration. No stock, no photos: an act with no
shots borrows its neighbours', a film that yields none fails the run (Gian, 2026-11-08)."""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

from cny import cut

log = logging.getLogger("cny.visuals")


def shots_wanted(words: int, *, words_per_minute: float, shot_seconds: float, extra: int = 2, floor: int = 4) -> int:
    """How many shots an act needs to carry ``words`` of narration at one shot per ``shot_seconds``."""
    est = words / max(60.0, words_per_minute) * 60.0
    return max(floor, int(math.ceil(est / shot_seconds)) + extra)


def film_visuals(cfg, *, acts: list[dict], sections: list[dict], work: Path, video: str,
                 vertical_for: set[int] = frozenset()) -> dict[int, dict]:
    """Per act: ``{"clips": [...], "shots": [(start, seconds)], "clips_v": [...]}`` (resumable via ``visuals.json``)."""
    plan_path = work / "visuals.json"
    if plan_path.exists():
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        rows = {int(k): v for k, v in saved.items() if k != "_source"}
        if saved.get("_source") == "film" and rows and all(Path(p).exists() for v in rows.values()
                                                            for p in v.get("clips", []) + v.get("clips_v", [])):
            return rows
    t0 = time.monotonic()
    words_by_act = {int(s["act_index"]): len(str(s.get("body") or "").split()) for s in sections}
    out: dict[int, dict] = {}
    for a in acts:
        i = int(a["index"])
        want = shots_wanted(words_by_act.get(i, 0), words_per_minute=cfg.words_per_minute, shot_seconds=cfg.shot_seconds)
        window = {"start_seconds": float(a["start_seconds"]), "end_seconds": float(a["end_seconds"])}
        clips, shots = cut.act_shots(video, str(work / "shots"), act_index=i, window=window, want=want,
                                     min_len=cfg.shot_min_seconds, max_len=cfg.shot_max_seconds, threshold=cfg.scene_threshold,
                                     width=cfg.width, height=cfg.height, fps=cfg.fps)
        out[i] = {"clips": clips, "shots": shots, "film": True, "window": [window["start_seconds"], window["end_seconds"]], "wanted": want}
        if i in vertical_for:                      # the Shorts' acts: the same shots, centre-cropped to 9:16
            out[i]["clips_v"] = cut.extract_shots(video, shots, str(work / "shots_v"), prefix=f"act{i:02d}", width=cfg.shorts_width,
                                                  height=cfg.shorts_height, fps=cfg.shorts_fps, fit="crop")
        print(f"act {i + 1}: {len(clips)} shots (wanted {want}) from {window['start_seconds']:.0f}-{window['end_seconds']:.0f} s", flush=True)
    for i, v in out.items():                        # an act with no shots borrows its neighbours'
        if not v["clips"]:
            pool = [c for j in sorted(out, key=lambda j: abs(j - i)) for c in out[j]["clips"]]
            v["clips"], v["borrowed"] = pool[:v["wanted"]], True
        if i in vertical_for and not v.get("clips_v"):
            v["clips_v"] = [c for j in sorted(out, key=lambda j: abs(j - i)) for c in out[j].get("clips_v") or []][:v["wanted"]]
    plan_path.write_text(json.dumps({"_source": "film", **{str(k): v for k, v in out.items()}}, indent=1), encoding="utf-8")
    log.info("film visuals for %d acts in %.0f s", len(acts), time.monotonic() - t0)
    return out
