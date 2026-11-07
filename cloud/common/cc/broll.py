"""Stock b-roll for a moment of narration: Pexels/Pixabay video search on a few queries, preview frames checked
by the vision provider ("does this fit as background for these words?"), stock photos as the fallback."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from sof import footage, images, vision

log = logging.getLogger("cc.broll")

BROLL_PROMPT = (
    "These {n} images are preview frames of stock video clips, candidates for atmospheric b-roll under a narrated "
    "{kind}. At this moment the narrator says: \"{summary}\". The footage search was: \"{query}\".\n"
    "Label each tile \"keep\" when it works as background for that moment: a place, weather, object, texture or "
    "ordinary action that matches the mood and setting; natural-looking, cinematic, nothing that contradicts the "
    "words. Label \"reject\" when it shows readable text, logos, watermarks, a person looking into the camera, "
    "a talking head, a product shot, animation or cartoon, a screen recording, a different and distracting "
    "subject, or a stock-photo cliché (handshakes, people pointing at laptops, forced smiles).\n"
    'Return JSON {{"keep": [1-based indices], "reject": [...]}} and nothing else.'
)


def label_broll(vis: vision.Vision, paths: list[str], *, query: str, summary: str, kind: str = "video") -> dict[str, str]:
    """path → "subject" (keep) | "reject" | "unknown", the shape ``sof.footage.gather`` expects. Without an active
    vision provider every preview is kept: a wrong clip beats a dead run."""
    if not paths:
        return {}
    if not vis.active:
        return {p: "subject" for p in paths}
    labels = {p: "reject" for p in paths}
    with tempfile.TemporaryDirectory(prefix="cc-sheet-") as td:
        for i in range(0, len(paths), 12):
            group = paths[i:i + 12]
            sheet = str(Path(td) / f"sheet{i:04d}.jpg")
            kept = vision.contact_sheet(group, sheet)
            if not kept:
                continue
            data = vis.ask(BROLL_PROMPT.format(n=len(kept), kind=kind, summary=summary[:300].replace('"', "'"),
                                               query=query.replace('"', "'")), sheet)
            if data is None:                            # nobody could look: keep them, as sof does
                for p in kept:
                    labels[p] = "subject"
                continue
            keep = {int(k) - 1 for k in (data.get("keep") or []) if str(k).lstrip("-").isdigit()}
            for j, p in enumerate(kept):
                labels[p] = "subject" if j in keep else "reject"
    return labels


def footage_for(*, queries: list[str], summary: str, out_dir: str, vis: vision.Vision, pexels_key: str, pixabay_key: str,
                want: int, candidates: int, budget: float, exclude_ids: set[str] | None = None, kind: str = "video") -> list[str]:
    """Vision-approved clip paths for one moment (``footage.json`` beside them, resumable); ``[]`` when nothing fits."""
    out = Path(out_dir)
    done = out / "footage.json"
    if done.exists():
        rows = json.loads(done.read_text(encoding="utf-8"))
        paths = [str((out / r["file"]).resolve()) for r in rows if (out / r["file"]).exists()]
        if paths:
            return paths
    queries = [q for q in dict.fromkeys(q.strip() for q in queries) if q]
    if not queries:
        return []
    try:
        cands = footage.search(queries, pexels_key=pexels_key, pixabay_key=pixabay_key, limit=candidates)
    except Exception as exc:  # noqa: BLE001
        log.warning("footage search failed for %s: %s", queries, exc)
        cands = []
    cands = [c for c in cands if c["id"] not in (exclude_ids or set())]
    if not cands:
        return []
    return footage.gather(cands, str(out), classify_fn=lambda ps: label_broll(vis, ps, query=queries[0], summary=summary, kind=kind),
                          want=want, budget_seconds=budget)


def used_ids(out_dir: str) -> set[str]:
    f = Path(out_dir) / "footage.json"
    if not f.exists():
        return set()
    try:
        return {str(r.get("id")) for r in json.loads(f.read_text(encoding="utf-8"))}
    except (ValueError, OSError):
        return set()


def photos_for(*, queries: list[str], out_dir: str, pexels_key: str, pixabay_key: str, want: int) -> list[str]:
    """Stock photos (Ken Burns stills) when no footage fits; ``[]`` when the search finds nothing."""
    out = Path(out_dir)
    existing = sorted(p for p in out.glob("*.jpg")) if out.exists() else []
    if existing:
        return [str(p) for p in existing]
    queries = [q for q in dict.fromkeys(q.strip() for q in queries) if q]
    if not queries:
        return []
    try:
        urls = images.collect_urls(queries, target=want * 2, pexels_key=pexels_key, pixabay_key=pixabay_key)
        return images.download_all(urls, str(out), want=want)
    except Exception as exc:  # noqa: BLE001
        log.warning("photo fallback failed for %s: %s", queries, exc)
        return []
