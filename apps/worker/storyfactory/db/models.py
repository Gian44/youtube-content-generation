"""SQLAlchemy database models for StoryFactory."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================
# Settings
# ============================================

class SettingsModel(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


# ============================================
# Channels (primary unit of multi-channel config & operation)
# ============================================

class Channel(Base):
    __tablename__ = "channels"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    slug = Column(String(80), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="active")  # active | paused
    niche = Column(Text, nullable=True)
    content_style = Column(Text, nullable=True)
    # Per-channel content config overrides (quotas, weights, ratios, privacy,
    # disclosure, caption style, ...). Anything absent inherits app-level defaults.
    config = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    integrations = relationship(
        "ChannelIntegration", back_populates="channel", cascade="all, delete-orphan"
    )


class ChannelIntegration(Base):
    __tablename__ = "channel_integrations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=False, index=True)
    provider_key = Column(String(50), nullable=False)  # e.g. text.openai, youtube
    enabled = Column(Boolean, default=False)
    config = Column(JSON, default=dict)  # non-secret (model name, voice map, bucket, ...)
    secrets_encrypted = Column(Text, nullable=True)  # Fernet ciphertext of a JSON blob
    status = Column(String(20), nullable=False, default="unknown")  # unknown|ok|error|missing
    status_detail = Column(Text, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    channel = relationship("Channel", back_populates="integrations")

    __table_args__ = (
        Index("ix_channel_integrations_unique", "channel_id", "provider_key", unique=True),
    )


# ============================================
# Daily Batches
# ============================================

class DailyBatch(Base):
    __tablename__ = "daily_batches"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=True, index=True)
    date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    category = Column(String(50), nullable=False)
    status = Column(String(30), nullable=False, default="pending")
    shorts_count = Column(Integer, default=0)
    long_form_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)

    # Relationships
    stories = relationship("Story", back_populates="batch", cascade="all, delete-orphan")
    render_jobs = relationship("RenderJob", back_populates="batch", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_daily_batches_date_status", "date", "status"),)


# ============================================
# Stories
# ============================================

class Story(Base):
    __tablename__ = "stories"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    batch_id = Column(String(36), ForeignKey("daily_batches.id"), nullable=False, index=True)
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=True, index=True)
    category = Column(String(50), nullable=False)
    type = Column(String(20), nullable=False)  # 'short' or 'long_form_extra'
    title = Column(String(200), nullable=False)
    hook = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    comment_bait = Column(Text, nullable=True)
    word_count = Column(Integer, nullable=False)
    estimated_duration_seconds = Column(Float, nullable=False)
    voice_persona = Column(String(30), nullable=False)
    originality_hash = Column(String(64), nullable=False, unique=True)
    novelty_score = Column(Float, default=0.0)
    prompt_used = Column(Text, nullable=False)
    raw_output = Column(Text, nullable=False)
    policy_status = Column(String(20), nullable=False, default="clean")
    policy_flags = Column(JSON, default=list)
    order_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    batch = relationship("DailyBatch", back_populates="stories")
    tts_jobs = relationship("TTSJob", back_populates="story", cascade="all, delete-orphan")
    caption_jobs = relationship("CaptionJob", back_populates="story", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_stories_category_type", "category", "type"),
        Index("ix_stories_policy_status", "policy_status"),
    )


# ============================================
# Story Segments (for long-form composition)
# ============================================

class StorySegment(Base):
    __tablename__ = "story_segments"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    story_id = Column(String(36), ForeignKey("stories.id"), nullable=False, index=True)
    segment_type = Column(String(30), nullable=False)  # intro, body, escalation, twist, outro, transition
    content = Column(Text, nullable=False)
    order_index = Column(Integer, default=0)
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utcnow)


# ============================================
# TTS Jobs
# ============================================

class TTSJob(Base):
    __tablename__ = "tts_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    story_id = Column(String(36), ForeignKey("stories.id"), nullable=False, index=True)
    provider = Column(String(20), nullable=False)  # openai or gemini
    voice = Column(String(30), nullable=False)
    persona = Column(String(30), nullable=False)
    status = Column(String(20), nullable=False, default="queued")
    input_text = Column(Text, nullable=False)
    output_path = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    loudness_lufs = Column(Float, nullable=True)
    cache_key = Column(String(64), nullable=True, index=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    story = relationship("Story", back_populates="tts_jobs")


# ============================================
# Caption Jobs
# ============================================

class CaptionJob(Base):
    __tablename__ = "caption_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    story_id = Column(String(36), ForeignKey("stories.id"), nullable=False, index=True)
    tts_job_id = Column(String(36), ForeignKey("tts_jobs.id"), nullable=True)
    style = Column(String(20), nullable=False, default="word_highlight")
    status = Column(String(20), nullable=False, default="queued")
    output_path = Column(Text, nullable=True)  # .ass subtitle file
    word_count = Column(Integer, nullable=True)
    font_size = Column(Integer, default=48)
    stroke_width = Column(Integer, default=3)
    shadow_depth = Column(Integer, default=2)
    max_words_per_line = Column(Integer, default=3)
    alignment_method = Column(String(30), nullable=True)  # local, openai_transcription, approximate
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    story = relationship("Story", back_populates="caption_jobs")
    words = relationship("CaptionWord", back_populates="caption_job", cascade="all, delete-orphan")


# ============================================
# Caption Words
# ============================================

class CaptionWord(Base):
    __tablename__ = "caption_words"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    caption_job_id = Column(String(36), ForeignKey("caption_jobs.id"), nullable=False, index=True)
    word = Column(String(100), nullable=False)
    start_time = Column(Float, nullable=False)  # seconds
    end_time = Column(Float, nullable=False)
    confidence = Column(Float, nullable=True)
    order_index = Column(Integer, nullable=False)

    # Relationships
    caption_job = relationship("CaptionJob", back_populates="words")


# ============================================
# Assets
# ============================================

class Asset(Base):
    __tablename__ = "assets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=True, index=True)
    provider = Column(String(20), nullable=False)
    type = Column(String(10), nullable=False)  # video, image, audio
    original_url = Column(Text, nullable=False)
    creator = Column(String(255), nullable=True)
    license = Column(String(100), nullable=True)
    attribution = Column(Text, nullable=True)
    source_query = Column(String(255), nullable=True)
    checksum = Column(String(64), nullable=True, index=True)
    duration_seconds = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    local_path = Column(Text, nullable=True)
    usage_count = Column(Integer, default=0)
    policy_status = Column(String(20), nullable=False, default="pending")
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_assets_provider_type", "provider", "type"),
        Index("ix_assets_policy_status", "policy_status"),
    )


# ============================================
# Render Jobs
# ============================================

class RenderJob(Base):
    __tablename__ = "render_jobs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    batch_id = Column(String(36), ForeignKey("daily_batches.id"), nullable=False, index=True)
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=True, index=True)
    type = Column(String(20), nullable=False)  # short or long_form
    status = Column(String(20), nullable=False, default="queued")
    story_ids = Column(JSON, default=list)
    asset_ids = Column(JSON, default=list)
    output_path = Column(Text, nullable=True)
    thumbnail_path = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    fps = Column(Integer, default=30)
    render_config = Column(JSON, default=dict)
    render_started_at = Column(DateTime, nullable=True)
    render_completed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.status is None:
            self.status = "queued"

    # Relationships
    batch = relationship("DailyBatch", back_populates="render_jobs")
    youtube_upload = relationship("YouTubeUpload", back_populates="render_job", uselist=False)


# ============================================
# Videos (rendered output tracking)
# ============================================

class Video(Base):
    __tablename__ = "videos"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    render_job_id = Column(String(36), ForeignKey("render_jobs.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    file_path = Column(Text, nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    format = Column(String(10), default="mp4")
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=utcnow)


# ============================================
# YouTube Uploads
# ============================================

class YouTubeUpload(Base):
    __tablename__ = "youtube_uploads"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    render_job_id = Column(String(36), ForeignKey("render_jobs.id"), nullable=False, index=True)
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=True, index=True)
    youtube_video_id = Column(String(20), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False)
    tags = Column(JSON, default=list)
    category_id = Column(String(5), default="24")  # Entertainment
    requested_privacy = Column(String(10), nullable=False, default="public")
    actual_privacy = Column(String(10), nullable=True)
    thumbnail_path = Column(Text, nullable=True)
    playlist_id = Column(String(50), nullable=True)
    made_for_kids = Column(Boolean, default=False)
    contains_synthetic_media = Column(Boolean, default=True)
    status = Column(String(20), nullable=False, default="queued")
    quota_used = Column(Integer, default=0)
    uploaded_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Relationships
    render_job = relationship("RenderJob", back_populates="youtube_upload")
    analytics_snapshots = relationship(
        "AnalyticsSnapshot", back_populates="youtube_upload", cascade="all, delete-orphan"
    )


# ============================================
# Analytics Snapshots
# ============================================

class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    youtube_upload_id = Column(
        String(36), ForeignKey("youtube_uploads.id"), nullable=False, index=True
    )
    youtube_video_id = Column(String(20), nullable=False)
    snapshot_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    views = Column(Integer, default=0)
    likes = Column(Integer, default=0)
    comments = Column(Integer, default=0)
    watch_time_minutes = Column(Float, nullable=True)
    avg_view_duration_seconds = Column(Float, nullable=True)
    subscribers_gained = Column(Integer, nullable=True)
    impressions = Column(Integer, nullable=True)
    ctr = Column(Float, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    # Relationships
    youtube_upload = relationship("YouTubeUpload", back_populates="analytics_snapshots")

    __table_args__ = (
        Index("ix_analytics_snapshot_date", "youtube_upload_id", "snapshot_date", unique=True),
    )


# ============================================
# API Usage Logs
# ============================================

class ApiUsageLog(Base):
    __tablename__ = "api_usage_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=True, index=True)
    provider = Column(String(30), nullable=False, index=True)
    endpoint = Column(String(255), nullable=False)
    tokens_used = Column(Integer, nullable=True)
    cost_estimate = Column(Float, nullable=True)
    status_code = Column(Integer, nullable=True)
    request_data = Column(JSON, nullable=True)
    response_summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_api_usage_created", "provider", "created_at"),)


# ============================================
# Policy Flags
# ============================================

class PolicyFlagRecord(Base):
    __tablename__ = "policy_flags"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=True, index=True)
    story_id = Column(String(36), ForeignKey("stories.id"), nullable=True, index=True)
    asset_id = Column(String(36), ForeignKey("assets.id"), nullable=True, index=True)
    flag_type = Column(String(50), nullable=False)
    severity = Column(String(10), nullable=False)  # block, rewrite, warn, info
    description = Column(Text, nullable=True)
    auto_resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)


# ============================================
# Prompt Templates
# ============================================

class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    # NULL channel_id = global default; a row scoped to a channel overrides it.
    channel_id = Column(String(36), ForeignKey("channels.id"), nullable=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    template = Column(Text, nullable=False)
    variables = Column(JSON, default=list)
    category = Column(String(30), nullable=False)
    version = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_prompt_templates_category", "category"),
        Index("ix_prompt_templates_channel_name", "channel_id", "name", unique=True),
    )
