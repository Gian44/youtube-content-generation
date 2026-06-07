"""YouTube upload service with OAuth, quota tracking, and privacy fallback."""

import os
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from storyfactory.channel_context import (
    effective_config,
    get_current_channel_id,
    youtube_credentials,
)
from storyfactory.config import get_settings
from storyfactory.db.engine import get_session
from storyfactory.db.models import YouTubeUpload, RenderJob, Story
from storyfactory.logger import get_logger
from storyfactory.services import shorts_seo
from storyfactory.services.api_tracker import track_api_call, check_youtube_quota

log = get_logger("youtube_uploader")

LEGACY_LONG_FORM_TITLE_PREFIX = "Reddit Stories That Will Keep You Up At Night"
YOUTUBE_TITLE_LIMIT = 100

LONG_FORM_TITLE_TEMPLATES = {
    "family_drama": "Reddit Family Drama Stories That Get Worse Every Minute",
    "relationships": "Reddit Relationship Stories With Endings You Won't See Coming",
    "confessions": "These Reddit Confessions Started Small And Got Out Of Control",
    "scary_stories": "Scary Reddit Stories You Should Not Listen To Alone",
    "revenge": "Reddit Revenge Stories Where The Payback Went Too Far",
    "workplace_drama": "Reddit Workplace Drama Stories That Escalated Fast",
    "aita": "Reddit AITA Stories Where Everyone Picked The Wrong Side",
    "entitled_parents": "Reddit Entitled Parent Stories That Are Hard To Believe",
}


def upload_video(render_job: RenderJob, metadata: dict) -> YouTubeUpload:
    """Upload a rendered video to YouTube.

    Args:
        render_job: The completed render job
        metadata: dict with title, description, tags, etc.

    Returns:
        YouTubeUpload record
    """
    settings = get_settings()
    session = get_session()

    upload = YouTubeUpload(
        id=str(uuid.uuid4()),
        render_job_id=render_job.id,
        channel_id=getattr(render_job, "channel_id", None) or get_current_channel_id(),
        title=metadata.get("title", "Untitled"),
        description=_build_description(metadata),
        tags=metadata.get("tags", []),
        category_id=metadata.get("category_id", "24"),
        requested_privacy=effective_config("upload_privacy_mode", settings.upload_privacy_mode),
        made_for_kids=False,
        # Self-declaring synthetic media is what produced the "Made with AI" /
        # "altered or synthetic content" label. Resolve from per-channel config
        # (default OFF) unless a metadata builder explicitly overrides it.
        contains_synthetic_media=metadata.get(
            "contains_synthetic_media",
            bool(effective_config("declare_synthetic_media", settings.declare_synthetic_media)),
        ),
        status="queued",
    )

    try:
        if settings.dry_run:
            upload.status = "completed"
            upload.youtube_video_id = f"DRY_RUN_{upload.id[:8]}"
            upload.actual_privacy = upload.requested_privacy
            upload.uploaded_at = datetime.now(timezone.utc)
            log.info("dry_run_upload", title=upload.title, privacy=upload.actual_privacy)

        else:
            # Validate prerequisites
            _validate_upload_prerequisites(settings, render_job)

            # Check quota
            if not check_youtube_quota():
                raise RuntimeError("YouTube API daily quota exceeded. Try again tomorrow.")

            # Perform upload
            youtube_video_id = _perform_upload(settings, render_job, upload)
            upload.youtube_video_id = youtube_video_id
            upload.status = "completed"
            upload.uploaded_at = datetime.now(timezone.utc)
            upload.quota_used = 1600  # videos.insert costs

            # Track quota usage
            track_api_call(
                provider="youtube",
                endpoint="videos.insert",
                tokens_used=1600,
            )

            # Upload thumbnail if available (skip for Shorts since YouTube doesn't show custom thumbnails on Shorts feed)
            if render_job.type != "short" and render_job.thumbnail_path and os.path.exists(render_job.thumbnail_path):
                try:
                    _upload_thumbnail(settings, youtube_video_id, render_job.thumbnail_path)
                    track_api_call(
                        provider="youtube",
                        endpoint="thumbnails.set",
                        tokens_used=50,
                    )
                except Exception as thumb_err:
                    log.warning(
                        "thumbnail_upload_failed",
                        video_id=youtube_video_id,
                        error=str(thumb_err),
                    )

            log.info(
                "video_uploaded",
                youtube_id=youtube_video_id,
                title=upload.title,
                privacy=upload.actual_privacy,
            )

    except Exception as e:
        error_msg = str(e)
        log.error("upload_failed", error=error_msg)

        # Try private fallback if configured
        if effective_config("allow_private_fallback", settings.allow_private_fallback) and "forbidden" in error_msg.lower():
            log.info("attempting_private_fallback")
            upload.requested_privacy = "public"
            upload.actual_privacy = "private"
            try:
                if not settings.dry_run:
                    youtube_video_id = _perform_upload(
                        settings, render_job, upload, privacy_override="private"
                    )
                    upload.youtube_video_id = youtube_video_id
                    upload.status = "completed"
                    upload.uploaded_at = datetime.now(timezone.utc)
                    log.info("private_fallback_success", youtube_id=youtube_video_id)
            except Exception as fallback_error:
                upload.status = "failed"
                upload.error = f"Original: {error_msg} | Fallback: {str(fallback_error)}"
        else:
            upload.status = "failed"
            upload.error = error_msg

    session.add(upload)
    session.commit()
    session.close()
    return upload


def _validate_upload_prerequisites(settings, render_job: RenderJob):
    """Validate that all prerequisites for upload are met."""
    creds = youtube_credentials()

    if not creds["client_id"]:
        raise RuntimeError(
            "YouTube Client ID not configured.\n"
            "Set the shared YOUTUBE_CLIENT_ID in .env (or override it on the channel).\n"
            "See docs/youtube-oauth-setup.md for instructions."
        )

    if not creds["client_secret"]:
        raise RuntimeError(
            "YouTube Client Secret not configured.\n"
            "Set the shared YOUTUBE_CLIENT_SECRET in .env (or override it on the channel)."
        )

    if not creds["refresh_token"]:
        raise RuntimeError(
            "YouTube refresh token not configured for this channel.\n"
            "Connect the channel's YouTube account from the dashboard.\n"
            "See docs/youtube-oauth-setup.md for instructions."
        )

    if not render_job.output_path or not os.path.exists(render_job.output_path):
        raise RuntimeError(
            f"Rendered video file not found: {render_job.output_path}"
        )

    # Check if the file is a dry-run placeholder
    try:
        if os.path.getsize(render_job.output_path) < 1024:
            with open(render_job.output_path, "rb") as f:
                content = f.read(100)
                if b"DRY_RUN_VIDEO_PLACEHOLDER" in content:
                    raise RuntimeError(
                        f"Cannot upload dry-run placeholder video: {render_job.output_path}. "
                        "Please run the pipeline without --dry-run to render real video files."
                    )
    except Exception as e:
        if "Cannot upload dry-run" in str(e):
            raise


def _build_youtube_client():
    """Build an authenticated YouTube Data API client for the active channel."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = youtube_credentials()
    credentials = Credentials(
        token=None,
        refresh_token=creds["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )

    return build("youtube", "v3", credentials=credentials)



def _perform_upload(settings, render_job: RenderJob, upload: YouTubeUpload, privacy_override: str | None = None) -> str:
    """Perform the actual YouTube upload using the Data API."""
    from googleapiclient.http import MediaFileUpload

    youtube = _build_youtube_client()

    privacy = privacy_override or upload.requested_privacy

    body = {
        "snippet": {
            "title": upload.title[:100],  # YouTube max title length
            "description": upload.description[:5000],
            "tags": upload.tags[:500],  # Limit tags
            "categoryId": upload.category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "madeForKids": upload.made_for_kids,
            "selfDeclaredMadeForKids": upload.made_for_kids,
        },
    }

    # Add synthetic media disclosure if configured
    if upload.contains_synthetic_media:
        body["status"]["containsSyntheticMedia"] = True

    media = MediaFileUpload(
        render_job.output_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 10,  # 10MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("upload_progress", progress=f"{int(status.progress() * 100)}%")

    upload.actual_privacy = privacy
    youtube_video_id = response.get("id", "")

    log.info("youtube_upload_complete", video_id=youtube_video_id, privacy=privacy)

    # Add to playlist if configured
    if upload.playlist_id:
        _add_to_playlist(youtube, youtube_video_id, upload.playlist_id)

    return youtube_video_id


def _upload_thumbnail(settings, youtube_video_id: str, thumbnail_path: str):
    """Upload a custom thumbnail for a YouTube video."""
    from googleapiclient.http import MediaFileUpload

    youtube = _build_youtube_client()

    media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")

    youtube.thumbnails().set(
        videoId=youtube_video_id,
        media_body=media,
    ).execute()

    log.info("thumbnail_uploaded", video_id=youtube_video_id)


def _add_to_playlist(youtube, video_id: str, playlist_id: str):
    """Add a video to a YouTube playlist."""
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                },
            },
        ).execute()
        log.info("added_to_playlist", video_id=video_id, playlist_id=playlist_id)
    except Exception as e:
        log.warning("playlist_add_failed", error=str(e))


def _build_description(metadata: dict) -> str:
    """Build the YouTube video description with the channel's disclosure line."""
    settings = get_settings()
    disclosure = effective_config(
        "disclosure_line", settings.disclosure_line or get_settings().DEFAULT_DISCLOSURE_LINE
    )

    description = metadata.get("description", "")

    # Ensure disclosure line is included
    if disclosure and disclosure not in description:
        description += f"\n\n{disclosure}"

    # Add timestamps for long-form
    timestamps = metadata.get("timestamps")
    if timestamps:
        description += "\n\nTimestamps:\n"
        for ts in timestamps:
            description += f"{ts['time']} - {ts['title']}\n"

    return description


def _category_label(category: str) -> str:
    """Return a display label for a story category."""
    return str(category or "stories").replace("_", " ").title()


def build_long_form_title(stories: list, category: str) -> str:
    """Build a specific, category-aware title for long-form uploads."""
    category_key = str(category or "stories")
    story_count = len(stories)
    title = LONG_FORM_TITLE_TEMPLATES.get(category_key)

    if title is None:
        label = _category_label(category_key)
        if story_count > 1:
            title = f"{story_count} Reddit {label} Stories That Escalate Way Too Fast"
        else:
            title = f"Reddit {label} Story With An Ending You Won't See Coming"

    return title[:YOUTUBE_TITLE_LIMIT]


def _category_from_legacy_title(title: str) -> str:
    """Extract a category key from the legacy long-form title suffix."""
    if "|" not in str(title):
        return "stories"
    suffix = str(title).split("|", 1)[1].strip()
    return suffix.lower().replace(" ", "_") or "stories"


def _is_legacy_long_form_upload(upload: YouTubeUpload) -> bool:
    """Return True when an upload is safe for the legacy retitle command."""
    render_job = getattr(upload, "render_job", None)
    video_id = getattr(upload, "youtube_video_id", None)
    title = str(getattr(upload, "title", "") or "")

    return (
        getattr(upload, "status", None) == "completed"
        and getattr(render_job, "type", None) == "long_form"
        and bool(video_id)
        and not str(video_id).startswith("DRY_RUN_")
        and title.startswith(LEGACY_LONG_FORM_TITLE_PREFIX)
    )


def _stories_for_render_job(session, render_job: RenderJob) -> list[Story]:
    """Load stories used by a render job in stored order when possible."""
    story_ids = list(getattr(render_job, "story_ids", None) or [])
    if not story_ids:
        return []

    stories = session.query(Story).filter(Story.id.in_(story_ids)).all()
    stories_by_id = {story.id: story for story in stories}
    return [stories_by_id[story_id] for story_id in story_ids if story_id in stories_by_id]


def retitle_legacy_long_form_uploads(
    dry_run: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """Retitle uploaded long-form videos that still use the legacy generic title."""
    session = get_session()
    results = []

    try:
        query = (
            session.query(YouTubeUpload)
            .join(RenderJob)
            .filter(
                YouTubeUpload.status == "completed",
                RenderJob.type == "long_form",
                YouTubeUpload.youtube_video_id.isnot(None),
                YouTubeUpload.title.startswith(LEGACY_LONG_FORM_TITLE_PREFIX),
            )
        )
        if limit is not None:
            query = query.limit(limit)

        settings = None
        for upload in query.all():
            if not _is_legacy_long_form_upload(upload):
                continue

            stories = _stories_for_render_job(session, upload.render_job)
            category = (
                stories[0].category
                if stories
                else _category_from_legacy_title(upload.title)
            )
            new_title = build_long_form_title(stories, category)
            result = {
                "upload_id": upload.id,
                "youtube_video_id": upload.youtube_video_id,
                "old_title": upload.title,
                "new_title": new_title,
            }

            if new_title == upload.title:
                result["status"] = "skipped"
                results.append(result)
                continue

            if dry_run:
                result["status"] = "dry_run"
                results.append(result)
                continue

            if settings is None:
                settings = get_settings()

            try:
                _update_youtube_video_title(settings, upload.youtube_video_id, new_title)
                upload.title = new_title
                session.commit()
                result["status"] = "updated"
            except Exception as exc:
                session.rollback()
                result["status"] = "failed"
                result["error"] = str(exc)

            results.append(result)

        return results
    finally:
        session.close()


def _update_youtube_video_title(settings, youtube_video_id: str, title: str):
    """Update a live YouTube video title."""
    youtube = _build_youtube_client()
    safe_title = title[:YOUTUBE_TITLE_LIMIT]

    response = youtube.videos().list(
        part="snippet",
        id=youtube_video_id,
    ).execute()
    track_api_call(
        provider="youtube",
        endpoint="videos.list",
        tokens_used=1,
    )

    items = response.get("items", [])
    if not items:
        raise RuntimeError(f"YouTube video not found: {youtube_video_id}")

    snippet = dict(items[0].get("snippet", {}))
    snippet["title"] = safe_title

    youtube.videos().update(
        part="snippet",
        body={
            "id": youtube_video_id,
            "snippet": snippet,
        },
    ).execute()
    track_api_call(
        provider="youtube",
        endpoint="videos.update",
        tokens_used=50,
    )

    log.info("youtube_title_updated", video_id=youtube_video_id, title=safe_title)


def _planner_tags(story) -> list[str]:
    """Post-specific tags the scene planner wrote into ``story.raw_output`` (if any)."""
    raw = getattr(story, "raw_output", None)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return []
    tags = data.get("tags") if isinstance(data, dict) else None
    return [str(t) for t in tags] if isinstance(tags, list) else []


def _recap_metadata(first_story, category: str, render_job=None) -> dict:
    """Upload metadata for a CinybeShorts recap Short (transformative commentary).

    Shaped by the Shorts optimizer playbook (curiosity title, no title hashtags,
    ~3 description hashtags, 3-tier tags). In original_audio mode the music
    track's attribution (recorded on the render job's ``render_config``) is
    appended below the hashtags so the CC credit appears in the description.
    """
    title = getattr(first_story, "title", None) or "Recap Short"
    hook = getattr(first_story, "hook", "") or ""
    label = _category_label(category)

    post_specific = _planner_tags(first_story) or (
        [label, f"{label} recap", f"{label} explained"]
        + shorts_seo.keyword_tags_from_title(title)
    )
    niche = ["recap", "tv recap", "movie recap", "explained"]
    hashtags = ["#shorts", "#recap", "#" + label.replace(" ", "")]

    music_attribution = ""
    if render_job is not None:
        music_attribution = (getattr(render_job, "render_config", None) or {}).get(
            "music_attribution"
        ) or ""

    return shorts_seo.build_short_metadata(
        title=title,
        hook=hook,
        post_specific_tags=post_specific,
        niche_tags=niche,
        hashtags=hashtags,
        extra_description=music_attribution or None,
    )


def _sleep_facts_metadata(first_story, category: str) -> dict:
    """Upload metadata for a Sleep On Facts long-form video (calm, single topic)."""
    title = getattr(first_story, "title", None) or "Calm Facts to Fall Asleep To"
    topic = _category_label(category)
    description = (
        f"Relax and drift off with calm, soothing facts about {topic}.\n\n"
        f"Put this on, dim the lights, and let the gentle narration carry you to sleep.\n\n"
        f"Like and subscribe for more calming facts to fall asleep to."
    )
    tags = [
        "sleep", "facts", "calm", "relaxation", "fall asleep", "bedtime",
        "sleep facts", "calming narration", topic,
    ]
    return {
        "title": title[:YOUTUBE_TITLE_LIMIT],
        "description": description,
        "tags": tags,
    }


def build_upload_metadata(
    render_job: RenderJob,
    stories: list,
    video_type: str = "short",
) -> dict:
    """Build metadata dict for YouTube upload.

    This generates title, description, tags using AI or templates. The shape is
    chosen by the active channel's ``pipeline_mode`` so recap Shorts and sleep
    videos get topic-appropriate titles/descriptions instead of the fiction
    "Reddit" framing.
    """
    settings = get_settings()

    if not stories:
        return {
            "title": "Story Time",
            "description": "",
            "tags": ["stories", "reddit stories"],
        }

    first_story = stories[0]
    category = first_story.category if hasattr(first_story, "category") else "stories"

    mode = effective_config("pipeline_mode", "fiction")
    if mode == "recap_shorts":
        return _recap_metadata(first_story, category, render_job)
    if mode == "sleep_facts":
        return _sleep_facts_metadata(first_story, category)

    if video_type == "short":
        # Optimizer-shaped metadata for fiction Shorts (curiosity title, no title
        # hashtags, ~3 description hashtags, 3-tier tags).
        cat_label = category.replace("_", " ")
        return shorts_seo.build_short_metadata(
            title=getattr(first_story, "title", None) or "Story Time",
            hook=getattr(first_story, "hook", "") or "",
            post_specific_tags=(
                _planner_tags(first_story)
                or [cat_label, f"{cat_label} stories"]
                + shorts_seo.keyword_tags_from_title(getattr(first_story, "title", "") or "")
            ),
            niche_tags=["storytime", "reddit stories", "drama"],
            hashtags=["#shorts", "#storytime", "#redditstories"],
        )
    else:
        title = build_long_form_title(stories, category)
        story_teasers = "\n".join(
            f"• {s.title}" for s in stories[:5] if hasattr(s, "title")
        )
        description = (
            f"Binge-worthy {category.replace('_', ' ')} stories!\n\n"
            f"Stories in this video:\n{story_teasers}\n\n"
            f"Like and subscribe for daily story content!"
        )
        tags = [
            "reddit stories", "storytime", category.replace("_", " "),
            "drama", "original stories", "story compilation",
            "best reddit stories", "aita", "relationship advice",
        ]

    return {
        "title": title,
        "description": description,
        "tags": tags,
    }
