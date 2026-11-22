"""All tunables in one place. Secrets come from the environment (GitHub Actions secrets). The voice, the writer and
every API key live in a ``sof.config.Config`` (``cfg.sof``) so the Sleep On Facts modules work unchanged."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

from sof.config import Config as SofConfig

from bqd import voice

WORDS_PER_MINUTE = 180          # Kokoro af_heart at speed 1.05 (voice F was ~130)


@dataclass
class Config:
    count: int = 3                      # Shorts per day (Gian 2026-11-07: 3, 8 h apart)
    spacing_hours: float = 8.0
    privacy: str = "public"
    smoke: bool = False                 # unlisted, ledger untouched
    skip_upload: bool = False
    work_dir: str = "work"
    topics_path: str = "topics.yml"
    ledger_path: str = "ledger.json"
    category: str | None = None         # force one category for every story of the run (all slots become stories)
    format: str | None = None           # force one format for every slot: story | would_you_rather | guess_the_game | pick_one
    # Gian 2026-11-19: one story + two interactive Shorts a day; after two weeks the views decide the mix. The order
    # of the day's three is shuffled by the date so each format takes each time slot in turn.
    mix: tuple = ("story", "interactive", "interactive")
    hold_seconds: int = 3               # the countdown after each interactive question
    words_min: int = 100
    words_max: int = 150                # ≈ 65 s at voice F's pace
    words_target: int = 125
    avoid_recent_days: int = 2          # categories used in the last N days are not drawn again
    recent_hooks: int = 40              # hooks the writer is told to avoid
    noslop_rounds: int = 1
    # render
    width: int = 1080
    height: int = 1920
    fps: int = 30
    # Gian 2026-11-22: full subtitles on every Short, in the middle of the frame ("people would just read them and
    # not really focus on the gameplay"). Story Shorts: dead centre. Interactive Shorts: just under the panel and the
    # countdown circle (56 %), which sit in the upper half. Short chunks so each cue is one or two lines.
    captions: bool = True
    caption_y_story: float = 0.50
    caption_y_interactive: float = 0.68
    caption_max_chars: int = 40
    caption_scale: float = 0.036
    clip_seconds: float = 6.0
    clip_slow: float = 1.0
    footage_per_story: int = 4
    footage_candidates: int = 12
    footage_budget: float = 90.0
    photos_per_story: int = 4
    brand: str = "Block Quest Daily"
    # Roblox channel (Gian 2026-11-17/18): stories over Creative Commons Roblox gameplay listed in gameplay.yml.
    # "stock" brings back Pexels/Pixabay b-roll; it is also what a run falls back to when the manifest lists no clip.
    background: str = "gameplay"
    gameplay_path: str = "gameplay.yml"
    gameplay_avoid_recent: int = 30     # clips used in the last N Shorts are not drawn again
    upload_check_seconds: int = 240
    sof: SofConfig = field(default_factory=SofConfig)

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        cfg = cls()
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        # This channel's own secrets win over the shared ones: its YouTube OAuth client + refresh token (each
        # StoryFactory channel was authorised with a different Google Cloud OAuth client), and optionally its own
        # Gemini key (a free TTS quota of its own).
        for name in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "GEMINI_API_KEY"):
            if os.environ.get(f"{name}_BLOCKQUEST"):
                os.environ[name] = os.environ[f"{name}_BLOCKQUEST"]
        cfg.sof = SofConfig.from_env(minutes=150, skip_upload=cfg.skip_upload, work_dir=cfg.work_dir)
        # 2026-11-19 (Gian: "Use a more suitable voice. You decide."): Kokoro af_heart, whatever TTS_BACKEND says —
        # the shared env must not hand this channel Sleep On Facts' Gemini quota or the old bedtime voice F.
        voice.apply(cfg.sof)
        cfg.sof.shorts_brand = cfg.brand
        cfg.sof.images_vision_check = True
        if cfg.smoke:
            cfg.privacy = "unlisted"
        return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Block Quest Daily cloud pipeline: Roblox story and interactive Shorts")
    p.add_argument("--count", type=int, default=3, help="Shorts to make today")
    p.add_argument("--category", default=None, help="force a story category (see topics.yml); every Short is a story")
    p.add_argument("--format", default=None, help="force one format: story | would_you_rather | guess_the_game | pick_one")
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default=None)
    p.add_argument("--smoke", action="store_true", help="unlisted, ledger untouched")
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work-dir", default="work")
    return p.parse_args(argv)
