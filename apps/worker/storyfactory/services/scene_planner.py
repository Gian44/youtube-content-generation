"""Content-aware scene planning for recap Shorts (``scene`` segmentation).

Pipeline: transcribe the episode → detect candidate scenes → an LLM scores each
for plot-importance against a FIXED rubric (temperature 0) and, for kept scenes,
returns a hook-first montage cut-list plus optimizer metadata (curiosity title,
2-second hook, comment-bait, post-specific tags, music mood). Scenes scoring at
or above the threshold are kept, so the Short COUNT is dynamic — it equals the
number of plot-integral scenes. The plan is persisted as frozen
:class:`MediaScene` rows so every re-run is byte-identical and the dedupe ledger
keeps working.

Pure helpers (``shape_cut_list``, ``choose_kept_indices``) carry the constraint
math and unit-test without I/O; the LLM call and transcription degrade
gracefully so the caller can fall back to time-based segmentation.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy.exc import IntegrityError

from storyfactory.db.models import MediaScene
from storyfactory.logger import get_logger
from storyfactory.services import scene_detector
from storyfactory.services.ai_provider import AIProvider, generate_text
from storyfactory.services.episode_transcriber import transcribe_episode

log = get_logger("scene_planner")

_MAX_CANDIDATES_SCORED = 60       # guardrail on prompt size for very long episodes
_TEXT_SNIPPET_CHARS = 320         # transcript chars per candidate sent to the LLM


# ---------------------------------------------------------------------------
# Pure constraint helpers (unit-tested)
# ---------------------------------------------------------------------------

def shape_cut_list(
    scene_start: float,
    scene_end: float,
    raw_cuts: list,
    *,
    target_seconds: float,
    max_seconds: float,
    min_seconds: float,
    max_cuts: int,
) -> list[list[float]]:
    """Clamp/trim a proposed montage cut-list to the optimizer length window.

    Cuts are clamped within ``[scene_start, scene_end]``, micro-cuts (<0.5s)
    dropped, capped to ``max_cuts`` (order preserved — the LLM front-loads the
    hook), then trimmed from the tail so the total never exceeds ``max_seconds``,
    and the last cut extended toward the scene end if the total is under
    ``min_seconds``. With no usable cuts it falls back to a single ``target``
    window from the scene start.
    """
    scene_start, scene_end = float(scene_start), float(scene_end)
    cuts: list[list[float]] = []
    for c in raw_cuts or []:
        try:
            s = max(scene_start, float(c[0]))
            e = min(scene_end, float(c[1]))
        except (TypeError, ValueError, IndexError):
            continue
        if e - s >= 0.5:
            cuts.append([round(s, 3), round(e, 3)])

    if not cuts:
        end = min(scene_end, scene_start + target_seconds)
        cuts = [[round(scene_start, 3), round(end, 3)]]

    cuts = cuts[: max(1, max_cuts)]

    total = sum(e - s for s, e in cuts)
    if total > max_seconds:
        budget = max_seconds
        trimmed: list[list[float]] = []
        for s, e in cuts:
            if budget <= 0:
                break
            seg = min(e - s, budget)
            trimmed.append([round(s, 3), round(s + seg, 3)])
            budget -= seg
        cuts = trimmed
        total = sum(e - s for s, e in cuts)

    if total < min_seconds and cuts:
        deficit = min_seconds - total
        s, e = cuts[-1]
        cuts[-1] = [s, round(min(scene_end, e + deficit), 3)]
    return cuts


def choose_kept_indices(
    scored: list[dict],
    *,
    threshold: float,
    min_count: int,
    max_count: int,
) -> set[int]:
    """Decide which candidate indices become Shorts (dynamic count, bounded).

    Keeps scenes flagged ``keep`` with ``importance >= threshold``; if more than
    ``max_count`` qualify, keeps the highest-scoring; if fewer than ``min_count``,
    tops up with the next-highest scorers. Returns the set of kept indices.
    """
    kept = [
        s for s in scored
        if s.get("keep") and float(s.get("importance", 0.0)) >= threshold
    ]
    if len(kept) > max_count:
        kept = sorted(kept, key=lambda s: float(s.get("importance", 0.0)), reverse=True)[:max_count]
    if len(kept) < min_count:
        kept_ids = {s["index"] for s in kept}
        rest = sorted(
            (s for s in scored if s["index"] not in kept_ids),
            key=lambda s: float(s.get("importance", 0.0)),
            reverse=True,
        )
        for s in rest:
            if len(kept) >= min_count:
                break
            kept.append(s)
    return {s["index"] for s in kept}


# ---------------------------------------------------------------------------
# LLM scoring
# ---------------------------------------------------------------------------

_RUBRIC = (
    "Score each scene 0.0-1.0 for how INTEGRAL it is to the episode's plot, using "
    "this fixed rubric: 1.0 = a turning point, major reveal, climax, or iconic "
    "moment the plot depends on; 0.7 = meaningful development (key conflict, "
    "decision, or emotional beat); 0.4 = minor/connective; 0.1 = filler, "
    "transitions, or small talk. Be strict — most scenes are NOT integral."
)


# Candidates per scoring call. Scoring is split into batches with a tiny
# per-scene payload so each JSON response stays well under the token cap — the
# previous single rich call over all 58 candidates truncated and failed to parse.
_SCORE_BATCH = 30


def _loads(raw: str) -> dict:
    """Tolerant JSON parse: strip code fences, salvage the outermost object."""
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        nl = text.find("\n")
        if nl != -1 and text[:nl].strip().lower() in ("json", ""):
            text = text[nl + 1:]
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        start, end = text.find("{"), text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except (ValueError, TypeError):
                return {}
        return {}


def _scene_lines(scenes: list[dict]) -> str:
    return "\n".join(
        f'#{c["index"]} [{c["start"]:.1f}-{c["end"]:.1f}s]: '
        + (c.get("text") or "")[:_TEXT_SNIPPET_CHARS].replace("\n", " ")
        for c in scenes
    )


def _score_prompt(series_title: str, batch: list[dict]) -> str:
    return (
        f'Rate scenes from "{series_title}" by plot-importance.\n\n{_RUBRIC}\n\n'
        f"Scenes:\n{_scene_lines(batch)}\n\n"
        'Return STRICT JSON only: {"scenes":[{"index":int,"importance":float,'
        '"keep":bool}]}. Include EVERY index; keep=true only for integral scenes.'
    )


def _score_candidates(series_title: str, candidates: list[dict], cfg: dict) -> dict[int, dict]:
    """Pass 1 — lean importance + keep for ALL candidates, in small batches.

    Returns ``{index: {"importance","keep"}}``; indices the model omits default to
    filler downstream. Batching keeps each response parseable.
    """
    out: dict[int, dict] = {}
    pool = candidates[:_MAX_CANDIDATES_SCORED]
    for i in range(0, len(pool), _SCORE_BATCH):
        batch = pool[i:i + _SCORE_BATCH]
        try:
            raw = generate_text(
                prompt=_score_prompt(series_title, batch),
                provider=AIProvider.OPENAI,
                temperature=0.0,  # deterministic scoring
                max_tokens=2500,
                response_format="json",
            )
            for s in _loads(raw).get("scenes", []):
                try:
                    out[int(s["index"])] = {
                        "importance": float(s.get("importance", 0.0) or 0.0),
                        "keep": bool(s.get("keep", False)),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
        except Exception as exc:  # noqa: BLE001 — one batch failing isn't fatal
            log.warning("scene_scoring_batch_failed", offset=i, error=str(exc))
    return out


def _enrich_prompt(series_title: str, kept: list[dict], cfg: dict) -> str:
    target = cfg.get("montage_target_seconds", 18)
    max_seconds = cfg.get("montage_max_seconds", 20)
    max_cuts = cfg.get("montage_max_cuts", 6)
    return (
        f'Build recap-Short montages for these key scenes from "{series_title}".\n\n'
        f"For each scene pick 1-{max_cuts} sub-spans (absolute seconds within the "
        "scene's range) that capture the beat, dropping filler/dead-air, with the "
        "most scroll-stopping moment FIRST (it must hook in 2 seconds). The kept "
        f"sub-spans total about {target}s and never exceed {max_seconds}s.\n"
        "Also write a curiosity-driven TITLE (a natural keyword, NO hashtags), a "
        "one-line HOOK, a COMMENT_BAIT question, 3-4 post-specific TAGS (literal "
        "content), and a 2-4 word music MOOD.\n\n"
        f"Scenes:\n{_scene_lines(kept)}\n\n"
        'Return STRICT JSON only: {"scenes":[{"index":int,"title":str,"hook":str,'
        '"comment_bait":str,"tags":[str],"mood":str,"cuts":[[start,end]],'
        '"reason":str}]}.'
    )


def _enrich_scenes(series_title: str, kept: list[dict], cfg: dict) -> dict[int, dict]:
    """Pass 2 — rich montage metadata (cuts/title/hook/tags/mood) for KEPT scenes.

    Only the small set of kept scenes is enriched, so the response stays small.
    Returns ``{index: enrichment}`` (empty on failure → cut-list falls back).
    """
    if not kept:
        return {}
    out: dict[int, dict] = {}
    try:
        raw = generate_text(
            prompt=_enrich_prompt(series_title, kept, cfg),
            provider=AIProvider.OPENAI,
            temperature=0.0,
            max_tokens=4000,
            response_format="json",
        )
        for s in _loads(raw).get("scenes", []):
            try:
                out[int(s["index"])] = s
            except (KeyError, TypeError, ValueError):
                continue
    except Exception as exc:  # noqa: BLE001 — enrichment is best-effort
        log.warning("scene_enrich_failed", error=str(exc))
    return out


# ---------------------------------------------------------------------------
# Orchestration + persistence
# ---------------------------------------------------------------------------

def existing_plan(session, episode_id: str) -> list[MediaScene]:
    return (
        session.query(MediaScene)
        .filter_by(episode_id=episode_id)
        .order_by(MediaScene.scene_index.asc())
        .all()
    )


def plan_episode_scenes(session, episode, channel, cfg: dict, *, dry_run: bool = False) -> list[MediaScene]:
    """Compute (or load) the frozen scene plan for an episode.

    Returns the KEPT scenes ordered by start time. Returns ``[]`` when no
    transcript is available (no key / dry-run / failure) so the caller can fall
    back to time-based segmentation. The plan is computed once and frozen.
    """
    frozen = existing_plan(session, episode.id)
    if frozen:
        return [s for s in frozen if s.keep]

    words = transcribe_episode(
        episode.id, episode.file_path, episode.duration_seconds, dry_run=dry_run
    )
    if not words:
        log.info("scene_plan_no_transcript", episode_id=episode.id)
        return []

    boundaries = scene_detector.detect_shot_boundaries(
        episode.file_path,
        threshold=float(cfg.get("scene_detect_threshold", 0.4)),
        dry_run=dry_run,
    )
    candidates = scene_detector.build_candidate_scenes(
        words,
        boundaries,
        episode.duration_seconds or 0.0,
        min_gap=float(cfg.get("scene_min_gap_seconds", 1.2)),
    )
    if not candidates:
        return []

    series_title = getattr(episode.series, "title", None) or "this show"

    # Pass 1: lean importance scoring for every candidate.
    scores = _score_candidates(series_title, candidates, cfg)
    if not scores:
        # Scoring failed entirely → don't fabricate a random plan from all-zero
        # scores; let the caller fall back to time-based windows.
        log.warning("scene_scoring_empty", episode_id=episode.id, candidates=len(candidates))
        return []

    scored_view = [
        {
            "index": c["index"],
            "importance": float(scores.get(c["index"], {}).get("importance", 0.0) or 0.0),
            "keep": bool(scores.get(c["index"], {}).get("keep", False)),
        }
        for c in candidates
    ]

    kept_ids = choose_kept_indices(
        scored_view,
        threshold=float(cfg.get("scene_importance_threshold", 0.6)),
        min_count=int(cfg.get("min_shorts_per_episode", 1)),
        max_count=int(cfg.get("max_shorts_per_episode", 20)),
    )

    # Pass 2: rich montage metadata for the (few) kept scenes only.
    enrich = _enrich_scenes(
        series_title, [c for c in candidates if c["index"] in kept_ids], cfg
    )

    target = float(cfg.get("montage_target_seconds", 18))
    max_seconds = float(cfg.get("montage_max_seconds", 20))
    min_seconds = float(cfg.get("montage_min_seconds", 8))
    max_cuts = int(cfg.get("montage_max_cuts", 6))

    rows: list[MediaScene] = []
    for c in candidates:
        keep = c["index"] in kept_ids
        e = enrich.get(c["index"], {}) if keep else {}
        cut_list = shape_cut_list(
            c["start"], c["end"], e.get("cuts") if keep else None,
            target_seconds=target, max_seconds=max_seconds,
            min_seconds=min_seconds, max_cuts=max_cuts,
        ) if keep else []
        rows.append(MediaScene(
            id=str(uuid.uuid4()),
            episode_id=episode.id,
            channel_id=channel.id,
            scene_index=c["index"],
            start_seconds=c["start"],
            end_seconds=c["end"],
            importance=float(scores.get(c["index"], {}).get("importance", 0.0) or 0.0),
            keep=keep,
            cut_list=cut_list,
            title=(e.get("title") or "")[:200] or None,
            hook=e.get("hook") or None,
            comment_bait=e.get("comment_bait") or None,
            tags=[str(t) for t in (e.get("tags") or [])][:6],
            mood=(e.get("mood") or "")[:80] or None,
            reason=e.get("reason") or None,
        ))

    session.add_all(rows)
    try:
        session.commit()
    except IntegrityError:
        # A concurrent run already froze this episode's plan (unique
        # (episode_id, scene_index) violated) — use theirs, don't duplicate.
        session.rollback()
        log.info("scene_plan_race_lost", episode_id=episode.id)
        return [s for s in existing_plan(session, episode.id) if s.keep]
    kept = [r for r in rows if r.keep]
    log.info(
        "scene_plan_frozen",
        episode_id=episode.id,
        candidates=len(candidates),
        kept=len(kept),
    )
    return sorted(kept, key=lambda r: r.start_seconds)
