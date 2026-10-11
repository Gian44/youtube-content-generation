"""YouTube Data API v3: health preflight, resumable upload, thumbnail."""

from __future__ import annotations

import logging
import time

import socket
import ssl
from http.client import HTTPException

log = logging.getLogger("sof.upload")
TOKEN_URI = "https://oauth2.googleapis.com/token"
TRANSIENT = (ssl.SSLError, socket.timeout, ConnectionError, HTTPException, OSError)


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


CATEGORY_EDUCATION = "27"


def video_body(*, title: str, description: str, tags: list[str], privacy: str, publish_at: str | None = None) -> dict:
    """``publish_at`` (RFC 3339, UTC) uploads the video private and lets YouTube publish it at that time."""
    status = {"privacyStatus": "private" if publish_at else privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        status["publishAt"] = publish_at
    return {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:30],
                    "categoryId": CATEGORY_EDUCATION, "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        # No altered/synthetic-content label: YouTube requires it for realistic depictions of real people,
        # events or places, or AI music as the main focus — not for a TTS narrator reading an AI-assisted
        # script over real photos ("production assistance" / voice-over in its own examples).
        "status": status,
    }


BAD_STATES = {"rejected", "failed", "deleted", "missing"}


def upload_state(yt, video_ids: list[str]) -> dict[str, tuple[str, str]]:
    """video_id → (uploadStatus, reason). "missing" = not returned (deleted, or processing abandoned).

    The 2026-10-07 Ancient Egypt video was accepted by the upload API and then abandoned by YouTube
    ("Video is too long": the channel was not yet verified for >15-minute videos) — nobody noticed for
    four days. This is how we notice now.
    """
    resp = yt.videos().list(part="status,processingDetails", id=",".join(video_ids), maxResults=50).execute()
    found = {v.get("id"): v for v in resp.get("items") or []}
    out: dict[str, tuple[str, str]] = {}
    for vid in video_ids:
        v = found.get(vid)
        if not v:
            out[vid] = ("missing", "")
            continue
        st = v.get("status") or {}
        reason = (st.get("rejectionReason") or st.get("failureReason")
                  or (v.get("processingDetails") or {}).get("processingFailureReason") or "")
        out[vid] = (str(st.get("uploadStatus") or "unknown"), str(reason))
    return out


def wait_until_accepted(yt, video_id: str, *, timeout: float = 300, every: float = 30, sleep=time.sleep) -> tuple[str, str]:
    """Poll right after upload. Rejections for length/duplicates arrive within minutes; a 3-hour video
    still "uploaded" (processing) at the end of the window is fine.

    An abandoned upload simply vanishes from the API (that is how the Ancient Egypt video looked), so a
    video that was visible and then disappears, or never shows up for the whole window, counts as
    "missing". A brief miss right after the insert is tolerated (index lag)."""
    waited, seen = 0.0, False
    while True:
        state, reason = upload_state(yt, [video_id])[video_id]
        if state == "missing":
            if seen:
                return "missing", "video disappeared after upload (processing abandoned or removed)"
        else:
            seen = True
        if state == "processed" or (state in BAD_STATES and state != "missing"):
            return state, reason
        if waited >= timeout:
            if state == "missing":
                return state, f"not visible to the API {int(timeout)} s after upload"
            return state, reason
        sleep(every)
        waited += every


def ensure_playlist(yt, title: str, description: str = "") -> str:
    """ID of the channel's playlist called ``title`` (case-insensitive), creating it public if missing."""
    req = yt.playlists().list(part="snippet", mine=True, maxResults=50)
    while req is not None:
        resp = req.execute()
        for it in resp.get("items") or []:
            if str((it.get("snippet") or {}).get("title") or "").strip().lower() == title.strip().lower():
                return it["id"]
        req = yt.playlists().list_next(req, resp)
    body = {"snippet": {"title": title[:150], "description": description[:5000], "defaultLanguage": "en"},
            "status": {"privacyStatus": "public"}}
    created = yt.playlists().insert(part="snippet,status", body=body).execute()
    log.info("created playlist %r (%s)", title, created.get("id"))
    return created["id"]


def add_to_playlist(yt, playlist_id: str, video_id: str, *, attempts: int = 4, sleep=time.sleep) -> None:
    """Add ``video_id`` unless it is already in the playlist. A playlist created seconds ago can
    answer 404 for a while (eventual consistency), so listing is best-effort and the insert retries."""
    from googleapiclient.errors import HttpError

    try:
        req = yt.playlistItems().list(part="snippet", playlistId=playlist_id, maxResults=50)
        while req is not None:
            resp = req.execute()
            if any(((it.get("snippet") or {}).get("resourceId") or {}).get("videoId") == video_id for it in resp.get("items") or []):
                return
            req = yt.playlistItems().list_next(req, resp)
    except HttpError as exc:
        if getattr(exc.resp, "status", None) != 404:
            raise
    body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    for attempt in range(attempts):
        try:
            yt.playlistItems().insert(part="snippet", body=body).execute()
            return
        except HttpError as exc:
            if getattr(exc.resp, "status", None) != 404 or attempt == attempts - 1:
                raise
            sleep(5 * (attempt + 1))


def update_snippet(yt, video_id: str, *, title: str, description: str, tags: list[str]) -> None:
    """Replace title/description/tags (and set Education + English) on an existing video."""
    yt.videos().update(part="snippet", body={"id": video_id, "snippet": {
        "title": title[:100], "description": description[:4900], "tags": tags[:30], "categoryId": CATEGORY_EDUCATION,
        "defaultLanguage": "en", "defaultAudioLanguage": "en"}}).execute()


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
        except TRANSIENT as exc:
            # Dropped TLS/TCP connections mid-chunk (SSLEOFError, ConnectionReset, timeouts) are
            # routine on a 1-3 GB upload; the resumable session survives them, so retry the chunk.
            if errors >= 10:
                raise
            errors += 1
            log.warning("upload chunk failed (%s: %s); retry %d", type(exc).__name__, exc, errors)
            sleep(min(60, 2 ** errors))
    video_id = response["id"]
    if thumb_path:
        try:
            yt.thumbnails().set(videoId=video_id, media_body=thumb_path).execute()
        except Exception as exc:  # noqa: BLE001 — a thumbnail failure must not fail a 3-hour run
            log.warning("thumbnail set failed: %s", exc)
    return video_id
