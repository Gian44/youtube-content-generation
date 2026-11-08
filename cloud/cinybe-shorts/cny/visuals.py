"""Where the film cuts, per act: ffmpeg scene detection over each act's time range (± 30 s, so beats stamped just
outside it still find their cuts), computed once and cached in ``work/cuts.json``. The beats module picks each beat's
shots from these. No stock, no photos: everything on screen is the film (Gian, 2026-11-08)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from cny import cut

log = logging.getLogger("cny.visuals")
MARGIN = 30.0


def act_cuts(cfg, *, acts: list[dict], video: str, work: Path, film_end: float) -> dict[int, list[float]]:
    path = work / "cuts.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        return {int(k): v for k, v in saved.items()}
    t0 = time.monotonic()
    out: dict[int, list[float]] = {}
    for a in acts:
        s = max(0.0, float(a["start_seconds"]) - MARGIN)
        e = min(film_end, float(a["end_seconds"]) + MARGIN)
        out[int(a["index"])] = cut.scene_cuts(video, start=s, end=e, threshold=cfg.scene_threshold)
        print(f"act {int(a['index']) + 1}: {len(out[int(a['index'])])} scene cuts in {s:.0f}-{e:.0f} s", flush=True)
    path.write_text(json.dumps({str(k): v for k, v in out.items()}), encoding="utf-8")
    log.info("scene cuts for %d acts in %.0f s", len(acts), time.monotonic() - t0)
    return out
