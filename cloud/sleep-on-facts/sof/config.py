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
    images_vision_check: bool = True            # gpt-4o-mini labels each photo subject/setting/reject (~$0.01/video)
    images_vision_model: str = "gpt-4o-mini"
    images_commons: bool = True                 # Wikimedia Commons: the article's own photos + its Commons category
    images_nasa: str = "auto"                   # "auto" = space topics only, "on", "off"
    images_setting_share: float = 0.3           # at most this share of the reel may be setting/scenery shots
    # render
    width: int = 1920
    height: int = 1080
    fps: int = 24
    dwell_seconds: float = 20.0
    crossfade_seconds: float = 1.5
    xfade_batch: int = 20
    video_dim: float = 0.75         # photos shown at 75 % brightness — night-friendly (Gian, 2026-10-11)
    # discovery
    publish_tz: str = "America/New_York"   # scheduled publish slot: 8:30 PM US Eastern, all year (DST-aware)
    publish_hour: int = 20
    publish_minute: int = 30
    schedule_publish: bool = True   # upload private + publishAt; a run that is >12 h from the slot publishes at once
    playlists: bool = True          # add each video to its category playlist (created on first use)
    upload_check_seconds: int = 300  # after upload, poll up to this long for a rejection ("Video is too long", duplicate…)
    photo_captions: bool = True     # small on-screen credit on CC BY / BY-SA photos (no external credits page)
    # narration-matched photos (sof/visuals.py + sof/timeline.py): named things get their own photos when named
    photo_timeline: bool = True
    timeline_max_entities: int = 80     # things per video that get their own photos
    timeline_per_entity: int = 6        # vision-approved photos per thing
    timeline_lead: float = 5.0          # a photo may appear at most this many seconds before its thing is named
    timeline_hold: float = 75.0         # a named thing stays on screen up to this long unless something else is named
    timeline_recall: float = 150.0      # filler may recall a thing named this recently; later, neutral subject photos
    timeline_canvas: float = 3.0        # zoom canvas for the timeline renderer: 3× measured as smooth as 4× at ~0.7× CPU
    timeline_photo_budget: float = 900.0  # seconds for fetching per-thing photos; later things go without
    # Shorts teaser (sof/shorts.py): one ~50 s vertical clip per long video, scheduled just after it goes live
    shorts_enabled: bool = True
    shorts_words: int = 115            # ≈ 45–50 s at the narrator's pace, before the fixed closing line
    shorts_photos: int = 6
    shorts_width: int = 1080
    shorts_height: int = 1920
    shorts_fps: int = 30
    shorts_delay_minutes: int = 60     # the Short's link must never point at a still-private video
    shorts_brand: str = "Sleep On Facts"
    shorts_tts_instructions: str = (
        "You are the narrator of a calm facts channel, recording a short teaser. Warm, low, unhurried, but "
        "awake and curious — a little brighter than a bedtime read. Clear diction, natural pauses, no hype."
    )
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
    p.add_argument("--no-shorts", action="store_true", help="skip the Shorts teaser for this run")
    return p.parse_args(argv)
