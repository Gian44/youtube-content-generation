"""Stock b-roll per act (shared ``cc.broll``: Pexels + Pixabay video, vision-checked), stock photos as the fallback
and the neighbours' clips as the last resort, so a run never dies on footage. No film footage, no posters."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from cc import broll
from sof import vision

log = logging.getLogger("cny.visuals")


def gather_all(cfg, *, acts: list[dict], work: Path, vis: vision.Vision) -> dict[int, dict]:
    """Per act: ``{"clips": [...], "photos": [...], "queries": [...]}`` (resumable; written to ``visuals.json``)."""
    plan_path = work / "visuals.json"
    if plan_path.exists():
        saved = {int(k): v for k, v in json.loads(plan_path.read_text(encoding="utf-8")).items()}
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
