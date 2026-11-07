"""All tunables in one place. Secrets come from the environment (GitHub Actions secrets).

The voice, the writer and every API key live in a ``sof.config.Config`` (``cfg.sof``) so the Sleep On Facts
modules work unchanged; this file only adds what a recap needs.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

from sof.config import Config as SofConfig

WORDS_PER_MINUTE = 135          # voice F (Pocket TTS) measured ~137 wpm on Sleep On Facts; Kokoro 135
SMOKE_MAX_MINUTES = 3           # at/below this the run is a smoke test: always unlisted, ledger untouched
DEFAULT_TTS_BACKEND = "pocket"  # Gian 2026-11-07: fallback voice F for this channel (Sleep On Facts owns the
                                # free Gemini TTS quota); env TTS_BACKEND=gemini + GEMINI_API_KEY_CINYBE flips it


@dataclass
class Config:
    minutes: int = 10                   # long recap target
    film: str | None = None             # force a film (with --year); otherwise the next one in films.yml
    year: int | None = None
    privacy: str = "public"
    skip_upload: bool = False
    work_dir: str = "work"
    films_path: str = "films.yml"
    plots_dir: str = "plots"
    ledger_path: str = "ledger.json"
    gap_hours: float = 36.0             # one film every 2nd day: a daily cron is a no-op inside this gap
    max_tries: int = 3                  # films tried per run when plots are thin
    min_plot_words: int = 250
    max_plot_chars: int = 14000         # ~2,300 words; longer plots are cut at a paragraph (the model needs the end)
    shorts_per_film: int = 3
    short_max_words: int = 55           # ≈ 25 s at voice F's pace
    noslop_rounds: int = 1
    # render
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crossfade: float = 0.6
    card_seconds: float = 2.5           # act title card at the top of each section
    clip_max_seconds: float = 12.0      # one appearance of a clip; clips are cycled and re-entered at new offsets
    clip_slow: float = 1.0              # stock footage at normal speed (not the sleep channel's drift)
    video_dim: float = 0.85
    shorts_width: int = 1080
    shorts_height: int = 1920
    shorts_fps: int = 30
    shorts_clip_seconds: float = 6.0
    footage_per_act: int = 3            # vision-approved clips per act
    footage_candidates: int = 10        # search results per act before the vision check
    footage_budget: float = 75.0        # seconds per act for search + check + download
    photos_per_act: int = 4             # stock photos when an act has no usable footage
    music_enabled: bool = True
    music_lufs: float = -32.0
    brand: str = "CinybeShorts"
    shorts_delay_minutes: int = 60      # Short 1 an hour after the long video, then one a day
    shorts_spacing_hours: float = 24.0
    upload_check_seconds: int = 300
    sof: SofConfig = field(default_factory=SofConfig)

    @property
    def target_words(self) -> int:
        return int(self.minutes * WORDS_PER_MINUTE)

    @property
    def is_smoke(self) -> bool:
        return self.minutes <= SMOKE_MAX_MINUTES

    @property
    def acts_range(self) -> tuple[int, int]:
        """10 min → 9–13 acts (~100–150 words each); a 3-minute smoke → 4–6."""
        hi = max(5, min(14, round(self.minutes * 1.3)))
        return max(4, hi - 4), hi

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        cfg = cls()
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        if os.environ.get("GEMINI_API_KEY_CINYBE"):             # this channel's own Gemini key (TTS quota)
            os.environ["GEMINI_API_KEY"] = os.environ["GEMINI_API_KEY_CINYBE"]
        if os.environ.get("YT_REFRESH_TOKEN_CINYBE"):
            os.environ["YT_REFRESH_TOKEN"] = os.environ["YT_REFRESH_TOKEN_CINYBE"]
        os.environ.setdefault("TTS_BACKEND", DEFAULT_TTS_BACKEND)
        cfg.sof = SofConfig.from_env(minutes=cfg.minutes, skip_upload=cfg.skip_upload, work_dir=cfg.work_dir)
        cfg.sof.shorts_brand = cfg.brand
        cfg.sof.images_vision_check = True
        if cfg.is_smoke:
            cfg.privacy = "unlisted"
        return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CinybeShorts cloud pipeline: narrated movie recaps")
    p.add_argument("--minutes", type=int, default=10, help="target recap length (<=3 = smoke, unlisted)")
    p.add_argument("--film", default=None, help="force a film title (with --year)")
    p.add_argument("--year", type=int, default=None)
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default=None)
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work-dir", default="work")
    p.add_argument("--no-shorts", action="store_true", help="skip the Shorts for this run")
    return p.parse_args(argv)
