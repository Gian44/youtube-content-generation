"""YouTube Data API v3: health preflight, resumable upload, thumbnail."""

from __future__ import annotations

import logging
import time

log = logging.getLogger("sof.upload")
TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_client(cfg):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not (cfg.yt_client_id and cfg.yt_client_secret and cfg.yt_refresh_token):
        raise RuntimeError("YouTube credentials missing (YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN)")
    creds = Credentials(None, refresh_token=cfg.yt_refresh_token, token_uri=TOKEN_URI,
                        client_id=cfg.yt_client_id, client_secret=cfg.yt_client_secret)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def health(yt) -> str:
    """Return the connected channel's title; raises if the token is dead."""
    resp = yt.channels().list(part="snippet", mine=True).execute()
    items = resp.get("items") or []
    if not items:
        raise RuntimeError("token valid but no channel returned")
    return (items[0].get("snippet") or {}).get("title") or items[0].get("id", "?")


def video_body(*, title: str, description: str, tags: list[str], privacy: str) -> dict:
    return {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30],
                    "categoryId": "24", "defaultLanguage": "en"},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True},
    }


def upload_video(yt, video_path: str, body: dict, thumb_path: str | None, *, chunk_mb: int = 8, sleep=time.sleep) -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(video_path, chunksize=chunk_mb * 1024 * 1024, resumable=True, mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response, errors = None, 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                log.info("upload %.0f%%", status.progress() * 100)
        except HttpError as exc:
            if exc.resp.status in (500, 502, 503, 504) and errors < 10:
                errors += 1
                sleep(min(60, 2 ** errors))
                continue
            raise
    video_id = response["id"]
    if thumb_path:
        try:
            yt.thumbnails().set(videoId=video_id, media_body=thumb_path).execute()
        except Exception as exc:  # noqa: BLE001 — a thumbnail failure must not fail a 3-hour run
            log.warning("thumbnail set failed: %s", exc)
    return video_id
