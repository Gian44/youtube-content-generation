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
    # script — writer does the 30 prose movements; fast model does topic/outline/metadata.
    script_model: str = "gpt-5.6-terra"     # writer (≈ $0.75 per 3-h script at $2/$12 per 1M tokens)
    fast_model: str = "gpt-5.6-luna"        # planner/metadata (≈ $0.03)
    research_linked_articles: int = 15      # linked Wikipedia articles pulled into the corpus
    gemini_models: list[str] = field(
        default_factory=lambda: ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    )
    num_movements: int = 16
    segment_max_tokens: int = 3200
    max_segments: int = 40
    fill_ratio: float = 0.9
    max_words_ratio: float = 1.3
    # tts
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "echo"         # chosen by Gian from gpt-4o-mini-tts samples (2026-10-06)
    tts_speed: float = 0.9          # tts-1 only; gpt-4o-mini-tts takes pace from tts_instructions
    tts_instructions: str = (
        "You are narrating a gentle bedtime video. Speak slowly and softly, in a warm, low, unhurried voice, "
        "with long natural pauses between sentences. No excitement, no emphasis spikes. An even, soothing "
        "cadence, like telling a quiet story to someone who is drifting off to sleep."
    )
    tts_chunk_chars: int = 2000     # gpt-4o-mini-tts input cap is 2,000 chars (tts-1 allows 4,096)
    # music: synthesised ambient pad looped under the voice (see sof/music.py)
    music_enabled: bool = True
    music_lufs: float = -34.0       # ≈16 dB under the −18 LUFS narration: present, never competing
    # images
    images_target: int = 150
    images_min: int = 100
    images_fail_below: int = 30
    images_vision_check: bool = True            # gpt-4o-mini looks at each photo and drops off-topic ones (~$0.02/video)
    images_vision_model: str = "gpt-4o-mini"
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
