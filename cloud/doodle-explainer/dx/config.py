"""Tunables. Secrets come from the environment (GitHub Actions secrets); the sylusnix channel's own YouTube
client and token (``*_SYLUSNIX``) win over the shared ones."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

from sof.config import Config as SofConfig

BRAND = "sylusnix"


@dataclass
class Config:
    kind: str = "short"                 # "short" | "long"
    count: int = 2                      # Shorts per run (Claude 2026-11-25: 2 a day, 8 h apart)
    spacing_hours: float = 8.0
    long_every_days: int = 2            # one long video every second day (from 2026-11-26)
    force: bool = False                 # make a long video even on an off day / again today
    privacy: str = "public"
    smoke: bool = False
    skip_upload: bool = False
    work_dir: str = "work"
    ledger_path: str = "ledger.json"
    topics_path: str = "topics.yml"
    question: str | None = None         # force the question
    long_words: tuple = (1350, 1700)    # ≈ 8-10 min at ~170 wpm
    short_words: tuple = (105, 140)     # ≈ 40-50 s
    batch_long: int = 7                 # scenes per drawing request
    batch_short: int = 9
    voice: str = "af_bella"             # Kokoro, graded A- by its makers; Block Quest Daily already uses af_heart
    speed_long: float = 1.0
    speed_short: float = 1.08
    gap_long: float = 0.35              # breath after each scene
    gap_short: float = 0.12
    upload_check_seconds: int = 240
    sof: SofConfig = field(default_factory=SofConfig)

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        cfg = cls()
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        for name in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
            if os.environ.get(f"{name}_SYLUSNIX"):
                os.environ[name] = os.environ[f"{name}_SYLUSNIX"]
        cfg.sof = SofConfig.from_env(minutes=150, skip_upload=cfg.skip_upload, work_dir=cfg.work_dir)
        cfg.sof.tts_backend = "kokoro"
        cfg.sof.kokoro_voice = cfg.voice
        cfg.sof.kokoro_lang = "en-us"
        cfg.sof.kokoro_speed = cfg.speed_long
        cfg.sof.kokoro_short_speed = cfg.speed_short
        cfg.sof.kokoro_sentence_pause = 0.3
        cfg.sof.kokoro_short_sentence_pause = 0.22
        cfg.sof.kokoro_clause_pause = 0.1
        if cfg.smoke:
            cfg.privacy = "unlisted"
        return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="sylusnix: stick-figure explainers (long videos and Shorts)")
    p.add_argument("--kind", choices=["short", "long"], default="short")
    p.add_argument("--count", type=int, default=None, help="Shorts to make (kind short)")
    p.add_argument("--question", default=None, help="force the question")
    p.add_argument("--force", action="store_true", help="long: ignore the every-second-day rule")
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default=None)
    p.add_argument("--smoke", action="store_true", help="unlisted, ledger untouched")
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work-dir", default="work")
    return p.parse_args(argv)
