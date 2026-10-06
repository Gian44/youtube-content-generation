"""All tunables in one place. Secrets come from the environment (GitHub Actions secrets)."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

WORDS_PER_MINUTE = 150
SMOKE_MAX_MINUTES = 10  # at/below this the run is a smoke test: always unlisted


@dataclass
class Config:
    minutes: int = 180
    topic: str | None = None
    privacy: str = "public"
    skip_upload: bool = False
    work_dir: str = "work"
    topics_path: str = "topics.yml"
    ledger_path: str = "ledger.json"
    # script
    script_model: str = "gpt-4o-mini"
    gemini_models: list[str] = field(
        default_factory=lambda: ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    )
    num_movements: int = 16
    segment_max_tokens: int = 2800
    max_segments: int = 40
    fill_ratio: float = 0.9
    max_words_ratio: float = 1.3
    # tts
    tts_model: str = "tts-1"
    tts_voice: str = "onyx"
    tts_speed: float = 0.9
    tts_chunk_chars: int = 4000
    # images
    images_target: int = 150
    images_min: int = 100
    images_fail_below: int = 30
    # render
    width: int = 1920
    height: int = 1080
    fps: int = 24
    dwell_seconds: float = 20.0
    crossfade_seconds: float = 1.5
    xfade_batch: int = 20
    # secrets
    openai_api_key: str = ""
    gemini_api_key: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    yt_client_id: str = ""
    yt_client_secret: str = ""
    yt_refresh_token: str = ""

    @property
    def target_words(self) -> int:
        return self.minutes * WORDS_PER_MINUTE

    @property
    def is_smoke(self) -> bool:
        return self.minutes <= SMOKE_MAX_MINUTES

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        cfg = cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            pexels_api_key=os.environ.get("PEXELS_API_KEY", ""),
            pixabay_api_key=os.environ.get("PIXABAY_API_KEY", ""),
            yt_client_id=os.environ.get("YT_CLIENT_ID", ""),
            yt_client_secret=os.environ.get("YT_CLIENT_SECRET", ""),
            yt_refresh_token=os.environ.get("YT_REFRESH_TOKEN", ""),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        if cfg.is_smoke:
            cfg.privacy = "unlisted"
        return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sleep On Facts cloud pipeline")
    p.add_argument("--minutes", type=int, default=180, help="target narration length (<=10 = smoke, unlisted)")
    p.add_argument("--topic", default=None, help="force a topic by name")
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default=None)
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work-dir", default="work")
    return p.parse_args(argv)
