"""All tunables in one place. Secrets come from the environment (GitHub Actions secrets). The voice, the writer and
every API key live in a ``sof.config.Config`` (``cfg.sof``) so the Sleep On Facts modules work unchanged."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

from sof.config import Config as SofConfig

WORDS_PER_MINUTE = 135          # voice F (Pocket TTS) pace
DEFAULT_TTS_BACKEND = "pocket"  # Gian 2026-11-07: fallback voice F; TTS_BACKEND=gemini + GEMINI_API_KEY_BLOCKQUEST flips it


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
    category: str | None = None         # force one category for every story of the run
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
    clip_seconds: float = 6.0
    clip_slow: float = 1.0
    footage_per_story: int = 4
    footage_candidates: int = 12
    footage_budget: float = 90.0
    photos_per_story: int = 4
    brand: str = "Block Quest Daily"
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
        os.environ.setdefault("TTS_BACKEND", DEFAULT_TTS_BACKEND)
        cfg.sof = SofConfig.from_env(minutes=150, skip_upload=cfg.skip_upload, work_dir=cfg.work_dir)
        cfg.sof.shorts_brand = cfg.brand
        cfg.sof.images_vision_check = True
        if cfg.smoke:
            cfg.privacy = "unlisted"
        return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Block Quest Daily cloud pipeline: story Shorts")
    p.add_argument("--count", type=int, default=3, help="Shorts to make today")
    p.add_argument("--category", default=None, help="force a category (see topics.yml)")
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default=None)
    p.add_argument("--smoke", action="store_true", help="unlisted, ledger untouched")
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work-dir", default="work")
    return p.parse_args(argv)
