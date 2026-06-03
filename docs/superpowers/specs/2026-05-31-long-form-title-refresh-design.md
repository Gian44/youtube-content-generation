# Long-Form Title Refresh Design

Date: 2026-05-31

## Context

Long-form uploads currently use a hard-coded title:

`Reddit Stories That Will Keep You Up At Night | {Category}`

This reads like a placeholder and gives every long-form video the same opening phrase, which weakens packaging for YouTube recommendations. The current upload path builds this title in `build_upload_metadata()` inside `apps/worker/storyfactory/services/youtube_uploader.py`. The dashboard displays the stored `youtube_uploads.title`; it does not generate titles itself.

There is no existing command or service for updating metadata on already uploaded YouTube videos. The uploader can insert videos and upload thumbnails, but not retitle an existing video.

## Goals

- Replace the generic long-form title with stronger, category-aware title generation for future uploads.
- Keep generated long-form titles under YouTube's 100-character title limit used by the uploader.
- Update only already uploaded long-form videos whose stored title starts with `Reddit Stories That Will Keep You Up At Night`.
- Update the live YouTube video title and the local `youtube_uploads.title` value so YouTube and the database stay in sync.
- Avoid touching Shorts, failed uploads, dry-run uploads, or long-form uploads that already have custom titles.

## Non-Goals

- Do not redesign thumbnails in this change.
- Do not add broad A/B testing or analytics-based title selection.
- Do not change story generation prompts.
- Do not retitle videos whose title does not start with the old generic phrase.

## Title Strategy

Long-form titles should lead with a specific emotional promise instead of a generic "keep you up" phrase. The generator will use the video category, number of stories, and available story titles or hooks to choose from a curated set of formats.

Example formats:

- `Reddit Family Drama Stories That Get Worse Every Minute`
- `3 Reddit Stories Where The Family Secret Finally Came Out`
- `Reddit Relationship Stories With Endings You Won't See Coming`
- `These Reddit Confessions Started Small And Got Out Of Control`
- `Reddit Stories About Betrayal, Revenge, And One Final Twist`
- `Late Night Reddit Drama That Escalates Way Too Fast`
- `Reddit Stories Where Everyone Picked The Wrong Side`
- `The Most Unhinged Reddit Family Drama Stories This Week`

The helper should be deterministic enough for repeatable tests, while still giving variety by category and story content. If content-specific wording is not available, it should fall back to a strong category-specific template.

## Architecture

Add a focused helper in `youtube_uploader.py`, such as `build_long_form_title(stories, category)`, and have `build_upload_metadata()` call it for non-short videos.

The helper will:

- normalize category labels,
- inspect the first few story titles and hooks for useful signals,
- choose a category-aware title template,
- enforce the 100-character YouTube title limit,
- avoid the old generic phrase entirely.

Add a metadata update service, such as `retitle_legacy_long_form_uploads(dry_run=False, limit=None)`, that finds completed long-form uploads matching the legacy title prefix.

The update service will:

- query `YouTubeUpload` joined to `RenderJob`,
- require `RenderJob.type == "long_form"`,
- require `YouTubeUpload.status == "completed"`,
- require a real `youtube_video_id`,
- require `YouTubeUpload.title.startswith("Reddit Stories That Will Keep You Up At Night")`,
- rebuild the title from the upload's stories,
- update the live YouTube video first,
- commit the new database title only after the YouTube API update succeeds.

## YouTube Update Flow

YouTube `videos.update` needs the existing snippet fields preserved. The updater will first call `videos.list(part="snippet,status", id=video_id)`, copy the existing snippet, replace only `snippet.title`, then call `videos.update(part="snippet", body=...)`.

If the YouTube API update fails, the local database row must remain unchanged and the command should report the failure. This prevents the dashboard from claiming a title that is not actually live.

## CLI

Add a worker command for the one-time cleanup, for example:

`python -m storyfactory retitle-legacy-long-form`

Options:

- `--dry-run`: print matching uploads and proposed titles without calling YouTube or changing the DB.
- `--limit N`: update at most N matching uploads.

Dry-run mode is useful because this touches live metadata.

## Error Handling

- Skip uploads with missing or `DRY_RUN_` video IDs.
- Skip rows where no related stories can be found, unless a safe category fallback title can be built.
- If YouTube returns no video for an ID, report it and leave the DB unchanged.
- If a generated title equals the current title, skip the update.
- Track YouTube quota usage for `videos.list` and `videos.update` calls if the existing quota tracker supports it.

## Testing

Worker tests should cover:

- long-form metadata no longer starts with the old generic phrase,
- generated titles are at most 100 characters,
- category-specific titles are produced for relationship and family drama videos,
- Shorts metadata behavior is unchanged,
- the legacy retitle query only selects completed long-form uploads with the old prefix,
- dry-run retitle does not call YouTube and does not update the DB,
- successful retitle updates YouTube before committing the local title,
- failed YouTube update leaves the local title unchanged.

## Implementation Notes

The current folder is not a git repository, so this design file cannot be committed from this workspace unless git metadata is restored or the work is moved into a git checkout.
