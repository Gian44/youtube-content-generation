# Smart Montage Recaps + YouTube Shorts Optimizer + AI-flag removal

**Date:** 2026-06-06
**Status:** Implemented
**Pipelines:** `recap_shorts` (CinybeShorts) + all Shorts channels (metadata/cadence)

## Problem

Three issues with the generated CinybeShorts recaps (and Shorts generally):

1. **Recaps missed the highlights.** Segmentation was purely time-based
   (`plan_segments` sliced the episode into back-to-back ~52s windows from 0:00),
   so a "Short" was "minute N→N+1", not a key moment.
2. **Fixed ~5 Shorts.** Count was `duration ÷ 52` capped at 60, rendered 5/run —
   not tied to how much of the episode is actually plot-integral.
3. **Content flagged as AI** ("altered or synthetic content" label) — the
   uploader hardcoded `status.containsSyntheticMedia = True`.

Plus: bake the YouTube Shorts optimizer playbook into the app so Shorts are
algorithm-friendly (titles, tags, hashtags, length, upload cadence).

## Design

### 1. Smart montage recaps — `segmentation_mode: "scene"`

A new content-aware segmentation mode (CinybeShorts) replaces clock-slicing:

```
episode → transcribe once (whisper-1, chunked, cached)
        → detect candidate scenes (ffmpeg scene-change + dialogue grouping)
        → LLM scores each 0–1 for plot-importance (temp 0, fixed rubric)
          + builds a hook-first montage cut-list + optimizer metadata
        → freeze plan as media_scenes rows (deterministic)
        → one edited montage Short per kept scene: original audio + quiet music
          + dialogue subtitles sliced from the cached transcript
```

**Dynamic count** = number of scenes scoring ≥ `scene_importance_threshold`
(bounded by `min/max_shorts_per_episode`).

**Determinism** (req #2): candidate detection is signal-based; LLM scoring is
`temperature 0` against a fixed rubric; the plan is **frozen** in `media_scenes`
on first computation, so re-runs are identical and the start-time dedupe ledger
(`recap_segments`) keeps working.

New modules:
- `services/episode_transcriber.py` — chunked whisper-1, word timestamps stitched
  to episode time, cached to `data/cache/transcripts/{episode_id}.json`.
- `services/scene_detector.py` — ffmpeg `showinfo` shot boundaries + dialogue
  grouping on silence gaps → candidate scenes (boundaries snapped to shots).
- `services/scene_planner.py` — LLM scoring, `shape_cut_list` (clamp/trim to the
  ≤20s window, cap cuts, hook-first), `choose_kept_indices` (dynamic count),
  freezes `MediaScene` rows.
- `recap_service.build_montage_clip` (cut sub-spans + concat) and
  `rebase_words_to_montage` (slice/rebase transcript onto the montage timeline).
- `db.MediaScene` model + migration `0006_media_scenes`.

Reuses the existing `original_audio` path: `render_recap_short`,
`build_dialogue_captions`, `music_service`, the segment ledger, and the uploader.

Graceful degradation: no OpenAI key / dry-run / ffmpeg failure → falls back to
time-based windows; subtitles/music each degrade independently. The pipeline
never crashes for lack of any optional input.

### 2. YouTube Shorts optimizer (all Shorts) — `services/shorts_seo.py`

From the optimizer playbook (view-to-swipe ratio is the metric that matters):

- **Title** — curiosity + natural keyword, **zero hashtags** (`sanitize_title`).
- **Description** — exactly ~3 hashtags + disclosure/attribution tail.
- **Tags** — 3-tier formula (post-specific / niche / broad), 9–12 total
  (`build_tags`).
- **Length** — montage targets `montage_target_seconds=18`, hard ceiling 20s.
- **Cadence** — `max_short_uploads_per_day` (default **1**) throttles Short
  uploads per channel per UTC day in `pipeline/upload.py`; surplus Shorts queue
  and drain oldest-first. Long-form is never throttled.

Wired into both the recap metadata builder and the fiction-short metadata builder
in `youtube_uploader.py`.

### 3. AI-flag removal (req #3, all channels)

- `declare_synthetic_media` config (default **False**); the uploader sets
  `containsSyntheticMedia` only when True. Metadata builders no longer hardcode it.
- `-map_metadata -1` on every ffmpeg encode (recap / short / long-form) strips
  provenance/C2PA tags.
- The copyright/recap disclosure line stays (fair-use posture, not an AI
  admission). Owner remains responsible for YouTube policy compliance.

## Config (defaults)

`RECAP_DEFAULTS`: `segmentation_mode="time"`, `scene_importance_threshold=0.6`,
`scene_detect_threshold=0.4`, `scene_min_gap_seconds=1.2`,
`montage_target_seconds=18`, `montage_max_seconds=20`, `montage_min_seconds=8`,
`montage_max_cuts=6`.
`Settings`: `declare_synthetic_media=False`, `max_short_uploads_per_day=1`.

CinybeShorts is set to `segmentation_mode=scene`, `audio_mode=original_audio`,
`max_shorts_per_run=25`.

## Testing

All pure logic unit-tested (`shorts_seo`, `scene_detector`, `scene_planner`,
`rebase_words_to_montage`, `build_montage_clip`, throttle, synthetic-flag,
metadata strip) + a dry-run scene-mode pipeline integration test. Full worker
suite: 199 + new tests passing.

## Fresh start (one-time, done)

Before the build, on owner request: deleted all 10 live CinybeShorts YouTube
videos and purged the channel's DB output (stories/renders/uploads/ledger/assets,
on-disk artifacts), reset its episode to `pending`. Other channels untouched.
