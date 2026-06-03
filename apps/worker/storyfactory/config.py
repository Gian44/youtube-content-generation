"""Application configuration with environment variable loading and validation."""

import os
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """StoryFactory configuration loaded from environment variables."""

    # Database
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    sqlite_path: str = Field(default="./data/storyfactory.db", alias="SQLITE_PATH")

    # AI Providers
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    openai_text_model: str = Field(default="gpt-4o", alias="OPENAI_TEXT_MODEL")
    gemini_text_model: str = Field(default="gemini-2.0-flash", alias="GEMINI_TEXT_MODEL")

    # YouTube OAuth
    youtube_client_id: str = Field(default="", alias="YOUTUBE_CLIENT_ID")
    youtube_client_secret: str = Field(default="", alias="YOUTUBE_CLIENT_SECRET")
    youtube_redirect_uri: str = Field(
        default="http://localhost:3000/api/auth/youtube/callback",
        alias="YOUTUBE_REDIRECT_URI",
    )
    youtube_refresh_token: str = Field(default="", alias="YOUTUBE_REFRESH_TOKEN")

    # Asset Providers
    pexels_api_key: str = Field(default="", alias="PEXELS_API_KEY")
    pixabay_api_key: str = Field(default="", alias="PIXABAY_API_KEY")

    # Storage
    storage_mode: Literal["local", "r2"] = Field(default="local", alias="STORAGE_MODE")
    local_storage_path: str = Field(default="./data/storage", alias="LOCAL_STORAGE_PATH")
    r2_account_id: str = Field(default="", alias="R2_ACCOUNT_ID")
    r2_access_key_id: str = Field(default="", alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: str = Field(default="", alias="R2_SECRET_ACCESS_KEY")
    r2_bucket: str = Field(default="storyfactory", alias="R2_BUCKET")
    r2_public_base_url: str = Field(default="", alias="R2_PUBLIC_BASE_URL")

    # Automation
    auto_mode: bool = Field(default=True, alias="AUTO_MODE")
    require_approval: bool = Field(default=False, alias="REQUIRE_APPROVAL")
    upload_privacy_mode: Literal["public", "unlisted", "private"] = Field(
        default="public", alias="UPLOAD_PRIVACY_MODE"
    )
    allow_private_fallback: bool = Field(default=True, alias="ALLOW_PRIVATE_FALLBACK")

    # Content Quotas
    shorts_per_day_min: int = Field(default=3, alias="SHORTS_PER_DAY_MIN")
    shorts_per_day_max: int = Field(default=5, alias="SHORTS_PER_DAY_MAX")
    long_form_per_day: int = Field(default=1, alias="LONG_FORM_PER_DAY")
    long_form_target_minutes: int = Field(default=10, alias="LONG_FORM_TARGET_MINUTES")

    # Content outputs (per-channel composable shape; app-level defaults here).
    # A channel toggles which outputs it produces. At least one must be enabled.
    enable_shorts: bool = Field(default=True, alias="ENABLE_SHORTS")
    enable_long_form: bool = Field(default=True, alias="ENABLE_LONG_FORM")
    # Number of long-form stories composed into one long-form video (replaces the
    # old hardcoded random.randint(2, 5)).
    long_form_segments_min: int = Field(default=2, alias="LONG_FORM_SEGMENTS_MIN")
    long_form_segments_max: int = Field(default=5, alias="LONG_FORM_SEGMENTS_MAX")
    # Whether approved Shorts are also folded into the long-form compilation.
    long_form_includes_shorts: bool = Field(default=True, alias="LONG_FORM_INCLUDES_SHORTS")

    # Topic Selection
    daily_topic_mode: Literal["weighted_random", "round_robin", "manual"] = Field(
        default="weighted_random", alias="DAILY_TOPIC_MODE"
    )
    topic_cooldown_days: int = Field(default=2)

    # TTS
    tts_openai_ratio: float = Field(default=0.8, alias="TTS_OPENAI_RATIO")
    tts_gemini_ratio: float = Field(default=0.2, alias="TTS_GEMINI_RATIO")

    # Safety
    fail_safe_on_policy_flag: bool = Field(default=True, alias="FAIL_SAFE_ON_POLICY_FLAG")
    fail_safe_on_license_unknown: bool = Field(default=True, alias="FAIL_SAFE_ON_LICENSE_UNKNOWN")

    # Worker
    worker_port: int = Field(default=8787, alias="WORKER_PORT")
    worker_log_level: str = Field(default="INFO", alias="WORKER_LOG_LEVEL")

    # Dry Run
    dry_run: bool = Field(default=False, alias="DRY_RUN")

    # Content Policy
    disclosure_line: str = Field(default="")
    max_same_hook_per_week: int = Field(default=2)
    caption_style: str = Field(default="word_highlight", alias="CAPTION_STYLE")

    # Secret encryption (app-level master key for per-channel credentials)
    storyfactory_secret_key: str = Field(default="", alias="STORYFACTORY_SECRET_KEY")
    storyfactory_key_file: str = Field(default="", alias="STORYFACTORY_KEY_FILE")

    # Default channel identity (used when backfilling existing single-channel installs)
    default_channel_slug: str = Field(default="default", alias="CHANNEL_SLUG")
    default_channel_name: str = Field(default="Main Channel", alias="CHANNEL_NAME")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @property
    def effective_database_url(self) -> str:
        """Return the active database URL, preferring PostgreSQL over SQLite."""
        if self.database_url:
            return self.database_url
        # Ensure SQLite directory exists
        sqlite_path = Path(self.sqlite_path)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{sqlite_path.resolve()}"

    @property
    def is_sqlite(self) -> bool:
        return self.effective_database_url.startswith("sqlite")

    def validate_api_keys(self) -> dict[str, bool]:
        """Check which API keys are configured."""
        return {
            "openai": bool(self.openai_api_key and self.openai_api_key != "sk-your-openai-api-key"),
            "gemini": bool(self.gemini_api_key and self.gemini_api_key != "your-gemini-api-key"),
            "youtube": bool(self.youtube_client_id and self.youtube_client_secret),
            "pexels": bool(self.pexels_api_key and self.pexels_api_key != "your-pexels-api-key"),
            "pixabay": bool(self.pixabay_api_key and self.pixabay_api_key != "your-pixabay-api-key"),
        }

    def get_missing_keys(self) -> list[str]:
        """Return list of missing but required API keys."""
        keys = self.validate_api_keys()
        missing = []
        if not keys["openai"]:
            missing.append("OPENAI_API_KEY")
        # Others are optional but noted
        return missing

    DEFAULT_DISCLOSURE_LINE: ClassVar[str] = (
        "These are original fictional stories created for entertainment."
    )

    def content_config_defaults(self) -> dict:
        """Per-channel content config derived from the global environment.

        These are the app-level defaults that a channel's stored ``config``
        overrides field-by-field. Used to backfill the default channel during
        migration so existing single-channel installs keep their behavior.
        ``category_weights`` / ``category_hints`` are added by the migration
        from the worker's canonical defaults to avoid an import cycle.
        """
        return {
            "shorts_per_day_min": self.shorts_per_day_min,
            "shorts_per_day_max": self.shorts_per_day_max,
            "long_form_per_day": self.long_form_per_day,
            "long_form_target_minutes": self.long_form_target_minutes,
            "enable_shorts": self.enable_shorts,
            "enable_long_form": self.enable_long_form,
            "long_form_segments_min": self.long_form_segments_min,
            "long_form_segments_max": self.long_form_segments_max,
            "long_form_includes_shorts": self.long_form_includes_shorts,
            "daily_topic_mode": self.daily_topic_mode,
            "topic_cooldown_days": self.topic_cooldown_days,
            "max_same_hook_per_week": self.max_same_hook_per_week,
            "tts_openai_ratio": self.tts_openai_ratio,
            "tts_gemini_ratio": self.tts_gemini_ratio,
            "upload_privacy_mode": self.upload_privacy_mode,
            "allow_private_fallback": self.allow_private_fallback,
            "fail_safe_on_policy_flag": self.fail_safe_on_policy_flag,
            "fail_safe_on_license_unknown": self.fail_safe_on_license_unknown,
            "caption_style": self.caption_style,
            "disclosure_line": self.disclosure_line or self.DEFAULT_DISCLOSURE_LINE,
        }


# Global settings instance
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
