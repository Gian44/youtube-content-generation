"""Canonical content defaults shared across the worker.

These were previously duplicated as module-level constants in
``pipeline/topic_selector.py`` and ``pipeline/story_generator.py``. They are
centralized here so channel-config resolution, the migration backfill, and the
pipeline all agree on the same baseline. A channel's stored ``config`` may
override ``category_weights`` / ``category_hints`` / ``voice_personas``.
"""

from __future__ import annotations

STORY_CATEGORIES: list[str] = [
    "aita", "relationships", "cheating", "revenge", "workplace_drama",
    "entitled_parents", "family_drama", "confessions", "scary_stories",
    "creepy_encounters", "wholesome", "mysteries", "customer_service_drama",
]

DEFAULT_CATEGORY_WEIGHTS: dict[str, int] = {
    "aita": 15, "relationships": 12, "cheating": 10, "revenge": 12,
    "workplace_drama": 10, "entitled_parents": 8, "family_drama": 8,
    "confessions": 5, "scary_stories": 5, "creepy_encounters": 3,
    "wholesome": 5, "mysteries": 3, "customer_service_drama": 4,
}

DEFAULT_CATEGORY_HINTS: dict[str, str] = {
    "aita": "moral dilemmas, family conflicts, social etiquette, boundary-setting",
    "relationships": "dating drama, long-term relationship issues, breakups, trust",
    "cheating": "infidelity discovery, betrayal, confrontation, aftermath",
    "revenge": "creative revenge, karma, justice served, petty retribution",
    "workplace_drama": "toxic bosses, coworker conflicts, unfair policies, quitting stories",
    "entitled_parents": "outrageous parenting, public meltdowns, unreasonable demands",
    "family_drama": "inheritance disputes, favoritism, toxic relatives, holiday disasters",
    "confessions": "secret reveals, guilty consciences, hidden truths, cathartic admissions",
    "scary_stories": "supernatural encounters, unexplained events, eerie discoveries",
    "creepy_encounters": "stalkers, strange strangers, unsettling experiences",
    "wholesome": "kindness from strangers, heartwarming surprises, found family",
    "mysteries": "unexplained disappearances, cold cases, strange coincidences",
    "customer_service_drama": "Karens, impossible demands, retail horror stories",
}

DEFAULT_VOICE_PERSONAS: list[str] = [
    "calm", "dramatic", "sarcastic", "confession", "horror", "warm",
]


# ============================================================
# Recap shorts (CinybeShorts) — pipeline_mode="recap_shorts"
# ============================================================
# User drops local video files into an inbox; the pipeline cuts many Shorts
# from each episode. These are the merged defaults for ``channel.config["recap"]``;
# a channel overrides individual keys. See docs/multi-channel.md.
# Mode-appropriate upload disclosures. The fiction default ("original fictional
# stories") lives on Settings.DEFAULT_DISCLOSURE_LINE; these are used when a
# recap/sleep channel is created without an explicit disclosure_line override.
RECAP_DISCLOSURE_LINE = (
    "This video contains transformative recap/commentary for entertainment. "
    "All footage belongs to its respective owners."
)
SLEEP_FACTS_DISCLOSURE_LINE = (
    "Facts are drawn from publicly available sources. "
    "Narration is AI-generated and intended for relaxation."
)

RECAP_DEFAULTS: dict = {
    "source_mode": "user_supplied",       # only mode in v1 (no stream rippers)
    "inbox_path": "./data/sources/inbox",  # where the user drops episode files
    "active_series_slug": None,            # which series to process now (None = first pending)
    "target_short_seconds": 52,            # aim for a ~45-59s Short (time mode only)
    "min_short_seconds": 40,               # never plan a window shorter than this (time mode)
    "overlap_seconds": 0,                  # overlap between consecutive windows (time mode)
    "min_shorts_per_episode": 1,           # always plan at least this many windows
    "max_shorts_per_episode": 60,          # hard cap of windows per episode
    "max_shorts_per_run": 5,               # render at most this many Shorts per daily run
    "footage_mode": "with_source_video",   # "with_source_video" | "stock_metaphor"
    "voice_persona": "dramatic",           # narration voice persona (tts_narration mode)
    # ----------------------------------------------------------------
    # Segmentation: HOW windows are chosen for an episode.
    #   time  — fixed back-to-back windows from 0:00 (legacy; content-blind).
    #   scene — content-aware: transcribe the episode → detect candidate scenes
    #           → an LLM scores each for plot-importance with a fixed rubric →
    #           one edited montage Short per scene scoring >= the threshold. The
    #           Short COUNT is therefore dynamic (= number of integral scenes),
    #           and each Short is a tight recap of a key beat, not a raw slice.
    #           The computed plan is frozen in the DB so re-runs are identical.
    # ----------------------------------------------------------------
    "segmentation_mode": "time",            # "time" | "scene"
    # scene-mode knobs (ignored in time mode):
    "scene_importance_threshold": 0.6,      # keep scenes scoring >= this (0..1)
    "scene_detect_threshold": 0.4,          # ffmpeg scene-change sensitivity (0..1)
    "scene_min_gap_seconds": 1.2,           # silence gap that separates dialogue scenes
    # Montage shaping — the optimizer playbook: Shorts <=20s win the
    # view-to-swipe ratio, so each montage is edited DOWN to the key beats.
    "montage_target_seconds": 18,           # aim ~18s (optimizer: <=20s)
    "montage_max_seconds": 20,              # hard ceiling per Short
    "montage_min_seconds": 8,               # don't ship a Short shorter than this
    "montage_max_cuts": 6,                  # max sub-clips stitched into one montage
    # Audio mode selects the soundtrack of each Short:
    #   tts_narration — LLM recap script voiced by TTS (the default; source audio dropped)
    #   original_audio — keep the clip's own audio, add a quiet music bed + dialogue subtitles
    "audio_mode": "tts_narration",
    # original_audio mode knobs (ignored in tts_narration mode):
    "music_enabled": True,                  # mix a background music bed under the clip
    "music_provider": "jamendo",            # only provider in v1
    "music_volume": 0.10,                   # ~10% — subtle, never competes with dialogue
    "music_mood_fallback": "cinematic ambient",  # used when scene mood can't be inferred
    "music_allow_noncommercial": False,     # exclude CC-NC/ND tracks (safe to monetize)
    "subtitles_from_dialogue": True,        # transcribe the clip's real audio → burned captions
}

# ============================================================
# Sleep On Facts — pipeline_mode="sleep_facts"
# ============================================================
# One calm, single-topic long-form video per run. ``channel.config["sleep_facts"]``
# overrides these defaults; ``topic_rotation`` is the per-channel topic cursor list.
#
# v2: ~3-hour "themed fact compilation" sleep videos — a single calm narrator
# (onyx) over a slideshow of 100+ topic-matched stock IMAGES. No blue title card,
# no captions, no music. See docs/superpowers/specs/2026-06-07-sleep-facts-3hour-slideshow-design.md
SLEEP_FACTS_DEFAULTS: dict = {
    "topic_rotation": [],                  # falls back to DEFAULT_SLEEP_TOPICS when empty
    "enable_wikipedia_grounding": True,    # keyless Wikipedia/Wikidata fact grounding
    # --- length (the actual duration is measured post-TTS; this is a target) ---
    "long_form_target_minutes": 180,       # ~3 hours of calm narration
    "narration_wpm": 150,                  # word target = minutes x wpm (approximate)
    # --- segmented script generation (avoids the single-call token ceiling) ---
    "gen_num_movements": 16,               # outline "movements" per outline call
    "gen_segment_max_tokens": 2800,        # per-movement expansion ceiling (~1.5-2k words)
    "gen_max_segments": 80,                # hard cap on expansion calls; the generator
                                           # loops (requesting more movements) until it
                                           # nears the word target, since the model
                                           # under-writes a fixed count for ~3h
    # --- narration voice (single, deterministic, calm) ---
    "voice_persona": "calm",
    "tts_voice": "onyx",                   # deep, low-energy voice best for sleep
    "tts_model": "tts-1-hd",               # deterministic; gpt-4o-mini-tts is the steerable alt
    "tts_speed": 0.9,                      # slightly slower than normal for bedtime pacing
    "tts_instructions": None,              # only honored by gpt-4o-mini-tts (steerable)
    # --- visuals: image slideshow ---
    "asset_type": "image",                 # IMAGES (slideshow), not background video
    "images_target": 150,                  # collect ~150 topic-matched stock images (>=100)
    "slideshow_dwell_seconds": 20,         # how long each image holds (before crossfade)
    "crossfade_seconds": 2,                # gentle crossfade between images
    "slideshow_fps": 24,                   # stills don't need 30fps; smaller + faster
    "slideshow_clip_workers": None,        # concurrent Ken Burns encodes (None = auto: cores-1)
    "ken_burns": True,                     # subtle slow zoom on each still
    # --- explicitly off for sleep ---
    "captions_enabled": False,             # no on-screen text
    "music_enabled": False,                # voice only
    # legacy key kept for back-compat with any existing channel config / dashboard
    "assets_per_video": 150,
}

# A broad, all-domain default rotation (history, animals, space, science, geography,
# culture...). Single topic per video — never mixed. Channels edit this list freely.
DEFAULT_SLEEP_TOPICS: list[str] = [
    "Ancient Egypt",
    "The Deep Sea",
    "Outer Space",
    "The Roman Empire",
    "Volcanoes",
    "The Human Brain",
    "Whales",
    "The Sahara Desert",
    "Ancient Greece",
    "The Solar System",
    "Rainforests",
    "The Ocean Floor",
    "Dinosaurs",
    "The Northern Lights",
    "Bees",
    "The Great Wall of China",
    "Antarctica",
    "The Moon",
    "Coral Reefs",
    "The Vikings",
]
