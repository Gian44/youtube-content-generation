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
    max_tries: int = 3                  # films (with a file) tried per run when plots are thin
    max_candidates: int = 20            # queued films checked against the bucket per run
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
    video_dim: float = 1.0              # film shots are shown as they are
    shorts_width: int = 1080
    shorts_height: int = 1920
    shorts_fps: int = 30
    shorts_clip_seconds: float = 6.0
    music_enabled: bool = True
    music_lufs: float = -32.0
    brand: str = "CinybeShorts"
    shorts_delay_minutes: int = 60      # Short 1 an hour after the long video, then one a day
    shorts_spacing_hours: float = 24.0
    upload_check_seconds: int = 300
    # the film itself (Gian, 2026-11-08: "use clips from the movies/shows" — and "if it is not from the movie itself, do
    # not post anything"): files he supplies in a private R2 bucket; acts aligned via the .srt (writer call) or
    # faster-whisper on the runner; a few shots per act under the narration. No stock fallback.
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "cinybe-films"
    film_window_min: float = 45.0        # seconds of the film one act may draw from
    film_window_max: float = 180.0
    shots_per_act: int = 5
    shot_min_seconds: float = 3.0
    shot_max_seconds: float = 8.0
    scene_threshold: float = 0.3         # ffmpeg scene-change score (0.3 = clear cuts only)
    whisper_model: str = "small"         # faster-whisper, CPU int8, only when there is no .srt
    film_dim: float = 1.0
    r2_delete_after: bool = True         # the bucket is a queue: a published film's file is removed
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
        # This channel's own secrets win over the shared ones: its YouTube OAuth client + refresh token (each
        # StoryFactory channel was authorised with a different Google Cloud OAuth client), and optionally its own
        # Gemini key (a free TTS quota of its own).
        for name in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "GEMINI_API_KEY"):
            if os.environ.get(f"{name}_CINYBE"):
                os.environ[name] = os.environ[f"{name}_CINYBE"]
        os.environ.setdefault("TTS_BACKEND", DEFAULT_TTS_BACKEND)
        cfg.r2_account_id = os.environ.get("R2_ACCOUNT_ID", cfg.r2_account_id)
        cfg.r2_access_key_id = os.environ.get("R2_ACCESS_KEY_ID", cfg.r2_access_key_id)
        cfg.r2_secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY", cfg.r2_secret_access_key)
        cfg.r2_bucket = os.environ.get("R2_BUCKET") or cfg.r2_bucket
        cfg.sof = SofConfig.from_env(minutes=cfg.minutes, skip_upload=cfg.skip_upload, work_dir=cfg.work_dir)
        cfg.sof.shorts_brand = cfg.brand
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
