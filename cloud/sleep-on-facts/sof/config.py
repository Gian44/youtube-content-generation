"""All tunables in one place. Secrets come from the environment (GitHub Actions secrets)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from dataclasses import dataclass, field

WORDS_PER_MINUTE = 150
KOKORO_WORDS_PER_MINUTE = 135   # Kokoro at speed 0.88 (smoke 2026-10-25); the Pocket TTS designed voice measured ~137
SMOKE_MAX_MINUTES = 10  # at/below this the run is a smoke test: always unlisted


@dataclass
class Config:
    minutes: int = 150                  # 2026-10-25: ~2.5 h (the niche's leaders run 2h15–2h50; was 180)
    topic: str | None = None
    privacy: str = "public"
    skip_upload: bool = False
    work_dir: str = "work"
    topics_path: str = "topics.yml"
    ledger_path: str = "ledger.json"
    # script — writer does the 30 prose movements; fast model does topic/outline/metadata.
    script_model: str = "gpt-5.6-terra"     # writer (≈ $0.75 per 3-h script at $2/$12 per 1M tokens)
    fast_model: str = "gpt-5.6-luna"        # planner/metadata (≈ $0.03)
    # 2026-10-25 (Gian): Claude writes the scripts on his Claude subscription via the Claude Code CLI
    # (CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`); OpenAI/Gemini above are the fallbacks.
    claude_enabled: bool = False            # on when CLAUDE_CODE_OAUTH_TOKEN is set (from_env)
    claude_writer_model: str = "sonnet"     # the long narration movements
    claude_fast_model: str = "haiku"        # topic, outline, metadata, rewrites, Shorts
    research_linked_articles: int = 15      # linked Wikipedia articles pulled into the corpus
    gemini_models: list[str] = field(
        default_factory=lambda: ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    )
    num_movements: int = 16
    slop_rounds: int = 2                    # noslop: targeted rewrites of flagged paragraphs per movement (0 = lint only)
    segment_max_tokens: int = 3200
    max_segments: int = 40
    fill_ratio: float = 0.9
    max_words_ratio: float = 1.3
    # tts — which voice: "kokoro" (free, open-weights, runs on the runner; default since 2026-10-25 when the
    # OpenAI keys ran out of credits) or "openai" (gpt-4o-mini-tts echo below). Env TTS_BACKEND overrides.
    tts_backend: str = "pocket"               # 2026-10-25 evening: our designed voice "C · Velvet low London"
    pocket_voice: str = str(Path(__file__).resolve().parents[1] / "brand" / "voice" / "narrator.wav")
    pocket_workers: int = 2                   # processes; Pocket TTS uses ~2 cores each (runner has 4)
    pocket_threads: int = 2
    # Kokoro voice = our own blend of its British male voices (Gian picked sample 3, George 60 % + Lewis 40 %).
    kokoro_voice: str = "bm_george:0.6,bm_lewis:0.4"
    kokoro_lang: str = "en-gb"
    kokoro_speed: float = 0.88                 # slower than default: bedtime pace
    kokoro_sentence_pause: float = 0.55        # seconds between sentences
    kokoro_clause_pause: float = 0.2
    kokoro_short_speed: float = 0.97           # Shorts teasers: a little brighter
    kokoro_short_sentence_pause: float = 0.3
    kokoro_model_path: str = ""                # default ~/.cache/kokoro/kokoro-v1.0.onnx (env KOKORO_DIR)
    kokoro_voices_path: str = ""
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "echo"         # chosen by Gian from gpt-4o-mini-tts samples (2026-10-06); British host delivery 2026-10-25
    tts_speed: float = 0.9          # tts-1 only; gpt-4o-mini-tts takes pace from tts_instructions
    tts_instructions: str = (
        "You are the host of a late-night science show, speaking to one listener who is drifting off to sleep. "
        "A middle-aged man with a soft, educated British accent. Warm, low and unhurried, with natural pauses "
        "between sentences. Friendly and personal in the welcome, then calm and even through the facts. "
        "No excitement, no emphasis spikes, no sing-song."
    )
    tts_chunk_chars: int = 2000     # gpt-4o-mini-tts input cap is 2,000 chars (tts-1 allows 4,096)
    # music: synthesised ambient pad looped under the voice (see sof/music.py)
    music_enabled: bool = True
    music_lufs: float = -34.0       # ≈16 dB under the −18 LUFS narration: present, never competing
    # images
    images_target: int = 150
    images_min: int = 100
    images_fail_below: int = 30
    images_vision_check: bool = True            # a vision model labels each photo subject/setting/reject
    images_vision_model: str = "gpt-4o-mini"    # the OpenAI provider's model
    # Who looks at the photos (sof/vision.py), tried in order per call (Gian 2026-10-25: Codex, Claude as
    # fallback). "codex" needs CODEX_HOME with his ChatGPT login (the workflow writes it from CODEX_AUTH_JSON),
    # "claude" the CLAUDE_CODE_OAUTH_TOKEN, "openai" credits. Env VISION_PROVIDERS="codex,claude" overrides.
    vision_providers: tuple = ("codex", "claude", "openai")
    codex_model: str = ""                       # "" = the account's default Codex model
    codex_effort: str = "low"
    claude_vision_model: str = "haiku"
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
    video_dim: float = 0.95         # 2026-10-25: brighter, closer to the vivid look of the niche's leaders (was 0.75)
    # moving footage between the photos (sof/footage.py): Pexels + Pixabay video, vision-checked preview frames
    footage_enabled: bool = True
    footage_every: int = 3              # every 3rd filler slot (no named thing on screen) shows a clip
    footage_want: int = 30              # clips per video (each used at most 3 times, never close together)
    footage_candidates: int = 80
    footage_slow: float = 1.5           # clips play at 1/1.5 speed: drifting, not busy
    footage_budget: float = 600.0       # seconds for searching, checking and downloading clips
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
    # Shorts teasers (sof/shorts.py): ~50 s vertical clips per long video; the first an hour after it goes
    # live, the rest one a day after that (2026-10-19: Shorts out-reach the long videos ~6×)
    shorts_enabled: bool = True
    shorts_count: int = 3
    shorts_spacing_hours: float = 24.0
    shorts_words: int = 115            # ≈ 45–50 s at the narrator's pace, before the fixed closing line
    shorts_photos: int = 6
    shorts_width: int = 1080
    shorts_height: int = 1920
    shorts_fps: int = 30
    shorts_delay_minutes: int = 60     # the Short's link must never point at a still-private video
    shorts_brand: str = "Sleep On Facts"
    shorts_tts_instructions: str = (
        "You are the host of a late-night science show, recording a short teaser. A middle-aged man with a soft, "
        "educated British accent. Warm and low, but awake and curious — a little brighter than at bedtime. "
        "Clear diction, natural pauses, no hype."
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
        return self.minutes * (KOKORO_WORDS_PER_MINUTE if self.tts_backend in ("kokoro", "pocket") else WORDS_PER_MINUTE)

    @property
    def is_smoke(self) -> bool:
        return self.minutes <= SMOKE_MAX_MINUTES

    def make_llm(self):
        from sof.llm import LLM

        return LLM(self.openai_api_key, self.gemini_api_key, self.script_model, self.gemini_models,
                   claude=self.claude_enabled, claude_writer=self.claude_writer_model, claude_fast=self.claude_fast_model)

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
            claude_enabled=bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")) or os.environ.get("SOF_LLM") == "claude",
        )
        cfg.claude_writer_model = os.environ.get("CLAUDE_WRITER_MODEL") or cfg.claude_writer_model
        cfg.claude_fast_model = os.environ.get("CLAUDE_FAST_MODEL") or cfg.claude_fast_model
        cfg.tts_backend = (os.environ.get("TTS_BACKEND") or cfg.tts_backend).strip().lower()
        kdir = Path(os.environ.get("KOKORO_DIR") or Path.home() / ".cache" / "kokoro")
        cfg.kokoro_model_path = os.environ.get("KOKORO_MODEL") or str(kdir / "kokoro-v1.0.onnx")
        cfg.kokoro_voices_path = os.environ.get("KOKORO_VOICES") or str(kdir / "voices-v1.0.bin")
        cfg.kokoro_voice = os.environ.get("KOKORO_VOICE") or cfg.kokoro_voice
        cfg.pocket_voice = os.environ.get("POCKET_VOICE") or cfg.pocket_voice
        if os.environ.get("POCKET_WORKERS"):
            cfg.pocket_workers = int(os.environ["POCKET_WORKERS"])
        if os.environ.get("VISION_PROVIDERS"):
            cfg.vision_providers = tuple(x.strip().lower() for x in os.environ["VISION_PROVIDERS"].split(",") if x.strip())
        cfg.codex_model = os.environ.get("CODEX_MODEL", cfg.codex_model)
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        if cfg.is_smoke:
            cfg.privacy = "unlisted"
        return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sleep On Facts cloud pipeline")
    p.add_argument("--minutes", type=int, default=150, help="target narration length (<=10 = smoke, unlisted)")
    p.add_argument("--topic", default=None, help="force a topic by name")
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default=None)
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work-dir", default="work")
    p.add_argument("--no-shorts", action="store_true", help="skip the Shorts teaser for this run")
    return p.parse_args(argv)
