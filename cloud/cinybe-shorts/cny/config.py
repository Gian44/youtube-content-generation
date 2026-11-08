"""All tunables in one place. Secrets come from the environment (GitHub Actions secrets).

The voice, the writer and every API key live in a ``sof.config.Config`` (``cfg.sof``) so the Sleep On Facts
modules work unchanged; this file only adds what a recap needs.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

from sof.config import Config as SofConfig

NATURAL_WPM = 162               # voice F on recap text: 219 wpm measured at ×1.35 (review run 42456841529, 2026-11-08)
PAUSE_GAIN = 1.30               # capping its pauses at ~0.24 s (VOICE_TIGHTEN) alone makes it 30 % faster: 295 wpm
                                # measured at ×1.4 (review run 42465054249) — too fast to follow
VOICE_TEMPO = 1.15              # Gian 2026-11-08 "make it faster to retain audience": ×1.15 on top → ~242 wpm, quicker
                                # than the fastest sample (226), less time-stretch than ×1.4
VOICE_TIGHTEN = ("silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.05:"
                 "stop_periods=-1:stop_duration=0.12:stop_threshold=-42dB:stop_silence=0.1")
WORDS_PER_MINUTE = round(NATURAL_WPM * VOICE_TEMPO * PAUSE_GAIN)
SMOKE_MAX_MINUTES = 3           # at/below this the run is a smoke test: always unlisted, ledger untouched
DEFAULT_TTS_BACKEND = "pocket"  # Gian 2026-11-07: fallback voice F for this channel (Sleep On Facts owns the
                                # free Gemini TTS quota); env TTS_BACKEND=gemini + GEMINI_API_KEY_CINYBE flips it


@dataclass
class Config:
    minutes: int = 17                   # long recap target: ~20 min (Gian 2026-11-08), told faster → the same story in ~17
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
    max_candidates: int = 500           # queued films checked per run: the whole queue, so the archive theme at the end is reached
    min_plot_words: int = 250           # a Wikipedia plot shorter than this is not offered as reference
    max_plot_chars: int = 14000         # ~2,300 words; longer plots are cut at a paragraph (the model needs the end)
    shorts_per_film: int = 3
    short_max_words: int = 110          # ≈ 34 s at the sped-up pace
    noslop_rounds: int = 1
    # render
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crossfade: float = 0.0              # hard cuts, like the recap channels
    clip_slow: float = 1.0
    video_dim: float = 1.0              # film shots are shown as they are
    shorts_width: int = 1080
    shorts_height: int = 1920
    shorts_fps: int = 30
    shorts_clip_seconds: float = 3.5    # longest one shot plays in a Short (fast cuts hold attention)
    music_enabled: bool = True          # the synthetic pad: only when the film has no soundtrack to lay under the voice
    music_lufs: float = -32.0
    voice_tempo: float = VOICE_TEMPO
    voice_tighten: str = VOICE_TIGHTEN
    film_audio: bool = False            # Gian 2026-11-08 (2nd review): footage muted, the voice carries it, over the quiet
                                        # ambient bed — 4 of the 6 auto-captioned samples run a music bed, 2 let film sound
                                        # through at moments, 1 is voice only. True = the film's sound ducked under the voice
    film_lufs: float = -24.0            # the film bed's level in the gaps; ducked ~9 dB further while the voice speaks
    shorts_film_lufs: float = -22.0
    brand: str = "CinybeShorts"
    shorts_delay_minutes: int = 60      # Short 1 an hour after the long video, then one a day
    shorts_spacing_hours: float = 24.0
    upload_check_seconds: int = 300
    # the film itself (Gian, 2026-11-08: "use clips from the movies/shows" — and "if it is not from the movie itself, do
    # not post anything"): files he drops in the Drive folder (or an R2 bucket), or public-domain prints the run
    # fetches from the Internet Archive; the .srt (supplied, OpenSubtitles, or whisper) is the transcript the
    # script is written from. No stock fallback.
    gdrive_service_account_json: str = ""   # the service account key file's content (GDRIVE_SERVICE_ACCOUNT_JSON)
    gdrive_folder_id: str = ""              # GDRIVE_FOLDER_ID, or the folder is found by name:
    gdrive_folder: str = "cinybe-films"
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "cinybe-films"
    shot_seconds: float = 3.0            # one shot per this much narration (shots_wanted); faster than the samples' 3-6 s
    shot_min_seconds: float = 2.0        # scenes shorter than this are not used
    shot_max_seconds: float = 5.0        # longest one shot plays in the long recap
    shot_min_play: float = 1.2
    scene_threshold: float = 0.3         # ffmpeg scene-change score (0.3 = clear cuts only)
    whisper_model: str = "small"         # faster-whisper, CPU int8, only when there is no .srt
    min_cues: int = 120                  # fewer dialogue cues than this = not a usable transcript (nothing posted)
    transcript_max_chars: int = 90000    # ≈ 22k tokens of "[mm:ss] dialogue" lines for the acts call
    film_dim: float = 1.0
    delete_after: bool = True            # the store is a queue: a published film's file is removed
    # films the pipeline fetches itself (Gian, 2026-11-08): public-domain prints on the Internet Archive, one
    # `archive: <item identifier>` per film in films.yml; subtitles from OpenSubtitles (hash match first) when the
    # item ships none; faster-whisper last
    archive_max_bytes: int = 5_000_000_000      # the runner disk: ~14 GB free minus deps, models, shots
    opensubtitles_api_key: str = ""      # OPENSUBTITLES_API_KEY (+ USERNAME / PASSWORD: downloads need a login)
    opensubtitles_username: str = ""
    opensubtitles_password: str = ""
    opensubtitles_languages: str = "en"
    sof: SofConfig = field(default_factory=SofConfig)

    @property
    def target_words(self) -> int:
        return int(self.minutes * self.words_per_minute)

    @property
    def is_smoke(self) -> bool:
        return self.minutes <= SMOKE_MAX_MINUTES

    @property
    def words_per_minute(self) -> float:
        """Spoken pace of the finished narration: voice F's natural pace × tempo × the pause cap's gain."""
        return NATURAL_WPM * self.voice_tempo * (PAUSE_GAIN if self.voice_tighten else 1.0)

    @property
    def acts_range(self) -> tuple[int, int]:
        """17 min → 13–19 acts (~210–310 words each at ~240 wpm); 10 min → 8–11; a 3-minute smoke → 4–5."""
        hi = max(5, min(24, round(self.minutes * 1.1)))
        return max(4, hi - min(6, max(1, hi // 3))), hi

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
        cfg.gdrive_service_account_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "")
        cfg.gdrive_folder_id = os.environ.get("GDRIVE_FOLDER_ID", "")
        cfg.gdrive_folder = os.environ.get("GDRIVE_FOLDER") or cfg.gdrive_folder
        cfg.r2_account_id = os.environ.get("R2_ACCOUNT_ID", cfg.r2_account_id)
        cfg.r2_access_key_id = os.environ.get("R2_ACCESS_KEY_ID", cfg.r2_access_key_id)
        cfg.r2_secret_access_key = os.environ.get("R2_SECRET_ACCESS_KEY", cfg.r2_secret_access_key)
        cfg.r2_bucket = os.environ.get("R2_BUCKET") or cfg.r2_bucket
        cfg.opensubtitles_api_key = os.environ.get("OPENSUBTITLES_API_KEY", "")
        cfg.opensubtitles_username = os.environ.get("OPENSUBTITLES_USERNAME", "")
        cfg.opensubtitles_password = os.environ.get("OPENSUBTITLES_PASSWORD", "")
        cfg.sof = SofConfig.from_env(minutes=cfg.minutes, skip_upload=cfg.skip_upload, work_dir=cfg.work_dir)
        cfg.sof.shorts_brand = cfg.brand
        if cfg.is_smoke:
            cfg.privacy = "unlisted"
        return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CinybeShorts cloud pipeline: narrated movie recaps")
    p.add_argument("--minutes", type=int, default=17, help="target recap length (<=3 = smoke, unlisted)")
    p.add_argument("--film", default=None, help="force a film title (with --year)")
    p.add_argument("--year", type=int, default=None)
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default=None)
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work-dir", default="work")
    p.add_argument("--no-shorts", action="store_true", help="skip the Shorts for this run")
    return p.parse_args(argv)
