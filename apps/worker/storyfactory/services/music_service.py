"""Background music service (CinybeShorts ``original_audio`` mode).

Fetches a quiet, uncopyrighted Creative Commons instrumental track from the
Jamendo API to sit under a recap Short's original audio, and infers a music
*mood* from the clip's transcribed dialogue so the bed matches the scene.

Licensing: Jamendo tracks carry different CC licenses. By default this module
excludes ``-nc`` (non-commercial) and ``-nd`` (no-derivatives) tracks so the
result is safe to monetize and safe to mix under footage; the actual license is
recorded on the :class:`Asset` and surfaced as attribution in the upload
description. Pixabay was considered but exposes no music API (images/videos
only), so Jamendo is the v1 provider.
"""

from __future__ import annotations

import json
import os
import random
import re
import uuid

import httpx

from storyfactory.channel_context import get_current_channel_id, resolve_api_key
from storyfactory.db.engine import get_session
from storyfactory.db.models import Asset
from storyfactory.logger import get_logger
from storyfactory.services.ai_provider import AIProvider, generate_text
from storyfactory.services.api_tracker import track_api_call
from storyfactory.services.asset_collector import download_asset

log = get_logger("music_service")

JAMENDO_TRACKS_URL = "https://api.jamendo.com/v3.0/tracks/"
_MOOD_MAX_LEN = 60


# ---------------------------------------------------------------------------
# Scene-mood inference
# ---------------------------------------------------------------------------

def infer_music_mood(transcript: str, fallback: str) -> str:
    """Infer a short music-search mood (2-4 words) from a clip's dialogue.

    Returns ``fallback`` when there is no dialogue, no text LLM key, or any
    error — the caller must always get a usable search phrase.
    """
    text = (transcript or "").strip()
    if not text:
        return fallback

    api_key = resolve_api_key("text.openai", "openai_api_key")
    if not api_key or api_key.startswith("sk-your"):
        return fallback

    prompt = (
        "Given this transcript of a short video clip, respond with 2-4 words "
        "describing background-music mood/genre that fits the scene's emotional "
        "tone (e.g. \"tense suspenseful\", \"melancholic piano\", "
        "\"triumphant orchestral\"). No track names, no artists.\n\n"
        f"Transcript:\n{text[:1200]}\n\n"
        'Return JSON: {"mood": "..."}'
    )
    try:
        raw = generate_text(
            prompt=prompt,
            provider=AIProvider.OPENAI,
            temperature=0.5,
            max_tokens=30,
            response_format="json",
        )
        data = json.loads(raw)
        mood = str(data.get("mood", "")).strip()
        return mood[:_MOOD_MAX_LEN] or fallback
    except Exception as exc:  # noqa: BLE001 — mood is best-effort, never fatal
        log.warning("music_mood_inference_failed", error=str(exc))
        return fallback


# ---------------------------------------------------------------------------
# License handling
# ---------------------------------------------------------------------------

def _license_code(ccurl: str) -> str:
    """Extract the license code from a Creative Commons URL.

    e.g. ``.../licenses/by-nc-sa/3.0/`` -> ``by-nc-sa``;
    ``.../publicdomain/zero/1.0/`` -> ``cc0``.
    """
    text = (ccurl or "").lower()
    if "publicdomain/zero" in text or "/cc0" in text:
        return "cc0"
    m = re.search(r"/licenses/([a-z-]+)/", text)
    return m.group(1) if m else ""


def _license_allows_use(ccurl: str, allow_noncommercial: bool) -> bool:
    """Whether a track's license is safe for a (potentially monetized) recap Short.

    Excludes ``-nd`` (no-derivatives) always — mixing under footage is a
    derivative use. Excludes ``-nc`` (non-commercial) unless explicitly opted in.
    """
    code = _license_code(ccurl)
    if not code:
        return False
    if code == "cc0":
        return True
    parts = code.split("-")  # e.g. ["by", "nc", "sa"]
    if "nd" in parts:
        return False
    if "nc" in parts:
        return allow_noncommercial
    return "by" in parts


def _license_label(ccurl: str) -> str:
    """Human-readable license name, e.g. ``CC BY-SA 3.0`` / ``CC0 1.0``."""
    code = _license_code(ccurl)
    version_match = re.search(r"/(\d+\.\d+)/", ccurl or "")
    version = version_match.group(1) if version_match else ""
    if code == "cc0":
        return f"CC0 {version}".strip()
    if not code:
        return "Creative Commons"
    label = "CC " + code.upper()
    return f"{label} {version}".strip()


# ---------------------------------------------------------------------------
# Jamendo fetch
# ---------------------------------------------------------------------------

def _parse_jamendo_track(item: dict, *, mood: str) -> Asset:
    """Build an (unsaved) audio :class:`Asset` from a Jamendo track record."""
    title = item.get("name") or "Untitled"
    artist = item.get("artist_name") or "Unknown"
    ccurl = item.get("license_ccurl") or ""
    label = _license_label(ccurl)
    return Asset(
        id=str(uuid.uuid4()),
        channel_id=get_current_channel_id(),
        provider="jamendo",
        type="audio",
        original_url=item.get("audiodownload") or "",
        creator=artist,
        license=label[:100],
        attribution=f'Music: "{title}" by {artist} ({label}) via Jamendo',
        source_query=mood[:255],
        checksum="",
        duration_seconds=item.get("duration"),
        local_path="",
        usage_count=0,
        policy_status="verified",
    )


def _jamendo_get(client_id: str, mood: str) -> dict | None:
    """Call the Jamendo tracks endpoint for instrumental CC tracks. None on error."""
    params = {
        "client_id": client_id,
        "format": "json",
        "limit": 20,
        "vocalinstrumental": "instrumental",
        "audioformat": "mp32",
        # `stats` returns audiodownload_allowed so the redistribution-permission
        # guard in fetch_music_track is explicit, not just an empty-URL side effect.
        "include": "licenses musicinfo stats",
        "fuzzytags": mood,
        "order": "popularity_total",
        "boost": "popularity_month",
    }
    try:
        with httpx.Client() as client:
            response = client.get(JAMENDO_TRACKS_URL, params=params, timeout=30)
        track_api_call(
            provider="jamendo",
            endpoint="v3.0/tracks",
            status_code=response.status_code,
        )
        if response.status_code != 200:
            log.warning("jamendo_api_error", status=response.status_code, body=response.text[:200])
            return None
        return response.json()
    except Exception as exc:  # noqa: BLE001 — music is optional; never fatal
        log.error("jamendo_request_failed", error=str(exc))
        return None


def _find_reusable_track(mood: str, min_duration: float) -> Asset | None:
    """Reuse an already-downloaded Jamendo track for the same channel + mood.

    Limits redundant API calls/downloads (and keeps a consistent feel when a
    scene mood recurs) without forcing the same track across different moods.
    """
    session = get_session()
    try:
        candidates = (
            session.query(Asset)
            .filter_by(
                provider="jamendo",
                type="audio",
                channel_id=get_current_channel_id(),
                source_query=mood[:255],
            )
            .all()
        )
        for asset in candidates:
            if (
                asset.local_path
                and os.path.exists(asset.local_path)
                and (asset.duration_seconds or 0) >= min_duration
            ):
                return asset
        return None
    finally:
        session.close()


def _delete_asset(asset_id: str) -> None:
    """Remove a persisted Asset row (used to clean up after a failed download)."""
    session = get_session()
    try:
        row = session.query(Asset).filter_by(id=asset_id).first()
        if row is not None:
            session.delete(row)
            session.commit()
    except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
        session.rollback()
        log.warning("jamendo_orphan_cleanup_failed", asset_id=asset_id, error=str(exc))
    finally:
        session.close()


def _persist_local_path(asset_id: str, local_path: str) -> None:
    """Write a downloaded track's local_path back to its DB row.

    The asset was committed then detached (its building session is closed), so an
    in-memory ``asset.local_path = ...`` would never reach the DB — and the reuse
    lookup (:func:`_find_reusable_track`) queries the DB, so without this every
    render for the same mood would re-download. Best-effort; the in-memory path is
    still used for the current render either way.
    """
    session = get_session()
    try:
        row = session.query(Asset).filter_by(id=asset_id).first()
        if row is not None:
            row.local_path = local_path
            session.commit()
    except Exception as exc:  # noqa: BLE001 — best-effort
        session.rollback()
        log.warning("jamendo_local_path_persist_failed", asset_id=asset_id, error=str(exc))
    finally:
        session.close()


def fetch_music_track(
    mood: str,
    min_duration: float,
    *,
    allow_noncommercial: bool = False,
) -> Asset | None:
    """Fetch one downloaded CC instrumental track matching ``mood``.

    Returns a persisted, downloaded audio :class:`Asset`, or ``None`` when no
    Jamendo key is configured / the search yields no commercially-usable track /
    the request or download fails. The caller falls back to original audio only.
    A previously-downloaded track for the same channel + mood is reused.
    """
    client_id = resolve_api_key("assets.jamendo", "jamendo_client_id")
    if not client_id or client_id.startswith("your-"):
        return None

    reusable = _find_reusable_track(mood, min_duration)
    if reusable is not None:
        log.info("jamendo_track_reused", mood=mood, asset_id=reusable.id)
        return reusable

    data = _jamendo_get(client_id, mood)
    if not data:
        return None

    results = data.get("results", []) or []
    usable = [
        t
        for t in results
        if t.get("audiodownload")
        # Honor the artist's redistribution permission explicitly (Jamendo also
        # empties `audiodownload` when disallowed, but don't rely on that alone).
        and t.get("audiodownload_allowed", True) is not False
        and _license_allows_use(t.get("license_ccurl", ""), allow_noncommercial)
    ]
    if not usable:
        log.warning("jamendo_no_usable_track", mood=mood, count=len(results))
        return None

    # Prefer a track at least as long as the clip (less obvious looping).
    long_enough = [t for t in usable if (t.get("duration") or 0) >= min_duration]
    track = random.choice(long_enough or usable)

    asset = _parse_jamendo_track(track, mood=mood)

    session = get_session()
    try:
        session.add(asset)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.error("jamendo_asset_persist_failed", error=str(exc))
        session.close()
        return None
    finally:
        session.close()

    local_path = download_asset(asset)
    if not local_path:
        log.warning("jamendo_download_failed", asset_id=asset.id)
        # Drop the committed placeholder so empty-path rows don't accumulate or
        # pollute the reuse lookup above.
        _delete_asset(asset.id)
        return None
    _persist_local_path(asset.id, local_path)  # so reuse lookup finds it later
    asset.local_path = local_path
    log.info("jamendo_track_selected", title=asset.attribution, mood=mood)
    return asset
