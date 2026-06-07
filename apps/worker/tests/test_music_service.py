"""music_service — scene-mood inference + Jamendo CC track fetching."""

from __future__ import annotations


# ---------------------------------------------------------------------------
# infer_music_mood
# ---------------------------------------------------------------------------

def test_infer_music_mood_uses_llm_phrase(monkeypatch):
    from storyfactory.services import music_service

    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "sk-real")
    monkeypatch.setattr(
        music_service, "generate_text", lambda **k: '{"mood": "tense suspenseful"}'
    )

    mood = music_service.infer_music_mood("He pulls the trigger.", fallback="ambient")
    assert mood == "tense suspenseful"


def test_infer_music_mood_falls_back_on_empty_transcript(monkeypatch):
    from storyfactory.services import music_service

    # Must not call the LLM at all when there is no dialogue text.
    def _boom(**kwargs):
        raise AssertionError("LLM should not be called for empty transcript")

    monkeypatch.setattr(music_service, "generate_text", _boom)
    assert music_service.infer_music_mood("   ", fallback="cinematic ambient") == "cinematic ambient"


def test_infer_music_mood_falls_back_when_no_api_key(monkeypatch):
    from storyfactory.services import music_service

    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "")
    assert music_service.infer_music_mood("some dialogue", fallback="ambient") == "ambient"


def test_infer_music_mood_falls_back_on_llm_error(monkeypatch):
    from storyfactory.services import music_service

    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "sk-real")

    def _raise(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(music_service, "generate_text", _raise)
    assert music_service.infer_music_mood("dialogue here", fallback="ambient") == "ambient"


# ---------------------------------------------------------------------------
# license filtering
# ---------------------------------------------------------------------------

def test_license_allows_commercial_by_and_cc0():
    from storyfactory.services import music_service

    assert music_service._license_allows_use(
        "http://creativecommons.org/licenses/by/4.0/", allow_noncommercial=False
    )
    assert music_service._license_allows_use(
        "http://creativecommons.org/licenses/by-sa/3.0/", allow_noncommercial=False
    )
    assert music_service._license_allows_use(
        "http://creativecommons.org/publicdomain/zero/1.0/", allow_noncommercial=False
    )


def test_license_excludes_nc_and_nd_by_default():
    from storyfactory.services import music_service

    assert not music_service._license_allows_use(
        "http://creativecommons.org/licenses/by-nc/3.0/", allow_noncommercial=False
    )
    assert not music_service._license_allows_use(
        "http://creativecommons.org/licenses/by-nd/4.0/", allow_noncommercial=False
    )
    assert not music_service._license_allows_use(
        "http://creativecommons.org/licenses/by-nc-sa/3.0/", allow_noncommercial=False
    )


def test_license_allows_nc_when_opted_in():
    from storyfactory.services import music_service

    assert music_service._license_allows_use(
        "http://creativecommons.org/licenses/by-nc/3.0/", allow_noncommercial=True
    )
    # ND stays excluded even when NC is opted into (derivative mixing risk).
    assert not music_service._license_allows_use(
        "http://creativecommons.org/licenses/by-nd/3.0/", allow_noncommercial=True
    )


def test_license_label_is_human_readable():
    from storyfactory.services import music_service

    assert music_service._license_label(
        "http://creativecommons.org/licenses/by/4.0/"
    ) == "CC BY 4.0"
    assert music_service._license_label(
        "http://creativecommons.org/licenses/by-sa/3.0/"
    ) == "CC BY-SA 3.0"


# ---------------------------------------------------------------------------
# fetch_music_track
# ---------------------------------------------------------------------------

_SAMPLE = {
    "headers": {"status": "success", "results_count": 2},
    "results": [
        {
            "id": "11",
            "name": "Calm Tide",
            "artist_name": "Artist A",
            "duration": 130,
            "audio": "https://stream/11",
            "audiodownload": "https://dl/11.mp3",
            "license_ccurl": "http://creativecommons.org/licenses/by/3.0/",
        },
        {
            "id": "22",
            "name": "Forbidden NC",
            "artist_name": "Artist B",
            "duration": 95,
            "audio": "https://stream/22",
            "audiodownload": "https://dl/22.mp3",
            "license_ccurl": "http://creativecommons.org/licenses/by-nc/3.0/",
        },
    ],
}


def test_fetch_music_track_returns_none_without_client_id(monkeypatch):
    from storyfactory.services import music_service

    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "")
    assert music_service.fetch_music_track("ambient", min_duration=50) is None


def test_fetch_music_track_picks_commercial_track(isolated_env, monkeypatch):
    from storyfactory.db.migrations import run_migrations
    from storyfactory.services import music_service

    run_migrations()

    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "real-id")
    monkeypatch.setattr(music_service, "_jamendo_get", lambda client_id, mood: _SAMPLE)
    # Avoid real network download; just mark the asset local.
    monkeypatch.setattr(
        music_service, "download_asset", lambda asset, *a, **k: "/tmp/track.mp3"
    )

    asset = music_service.fetch_music_track("tense", min_duration=60)

    assert asset is not None
    assert asset.provider == "jamendo"
    assert asset.type == "audio"
    assert asset.original_url == "https://dl/11.mp3"  # the CC-BY track, not the NC one
    assert "CC BY 3.0" in (asset.license or "")
    assert "Calm Tide" in (asset.attribution or "")
    assert "Artist A" in (asset.attribution or "")
    assert "Jamendo" in (asset.attribution or "")


def test_fetch_music_track_rejects_download_disallowed_tracks(isolated_env, monkeypatch):
    """A CC-BY track whose artist revoked download permission must be rejected,
    even if a (stale) non-empty audiodownload URL is present."""
    from storyfactory.db.migrations import run_migrations
    from storyfactory.services import music_service

    run_migrations()

    disallowed = {
        "results": [
            {
                "id": "33",
                "name": "Locked Track",
                "artist_name": "Artist C",
                "duration": 150,
                "audio": "https://stream/33",
                "audiodownload": "https://dl/33.mp3",  # stale/non-empty URL
                "audiodownload_allowed": False,
                "license_ccurl": "http://creativecommons.org/licenses/by/3.0/",
            }
        ]
    }
    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "real-id")
    monkeypatch.setattr(music_service, "_jamendo_get", lambda client_id, mood: disallowed)
    # Mock a SUCCESSFUL download so that, if the guard were missing, the track
    # would be returned — making this assertion truly test the permission guard.
    monkeypatch.setattr(music_service, "download_asset", lambda asset, *a, **k: "/tmp/x.mp3")

    assert music_service.fetch_music_track("x", min_duration=50) is None


def test_fetch_music_track_returns_none_when_only_noncommercial(isolated_env, monkeypatch):
    from storyfactory.db.migrations import run_migrations
    from storyfactory.services import music_service

    run_migrations()

    nc_only = {"results": [_SAMPLE["results"][1]]}  # only the by-nc track
    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "real-id")
    monkeypatch.setattr(music_service, "_jamendo_get", lambda client_id, mood: nc_only)

    assert music_service.fetch_music_track("dark", min_duration=50) is None


def test_fetch_music_track_reuses_downloaded_track_for_same_mood(isolated_env, monkeypatch, tmp_path):
    """A previously-downloaded track for the same mood is reused without a new API call."""
    from storyfactory.db.engine import get_session
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.models import Asset
    from storyfactory.services import music_service

    run_migrations()

    track = tmp_path / "track.mp3"
    track.write_bytes(b"music-bytes")
    session = get_session()
    session.add(
        Asset(
            id="cached1",
            channel_id=None,
            provider="jamendo",
            type="audio",
            original_url="https://dl/cached.mp3",
            source_query="ambient",
            license="CC BY 4.0",
            attribution='Music: "Cached" by A (CC BY 4.0) via Jamendo',
            duration_seconds=120,
            local_path=str(track),
            policy_status="verified",
        )
    )
    session.commit()
    session.close()

    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "real-id")

    def _no_api(*args, **kwargs):
        raise AssertionError("must reuse the cached track, not call the Jamendo API")

    monkeypatch.setattr(music_service, "_jamendo_get", _no_api)

    asset = music_service.fetch_music_track("ambient", min_duration=50)
    assert asset is not None
    assert asset.local_path == str(track)


def test_fetch_music_track_drops_orphan_row_on_download_failure(isolated_env, monkeypatch):
    """A failed download must not leave a verified jamendo Asset with empty local_path."""
    from storyfactory.db.engine import get_session
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.models import Asset
    from storyfactory.services import music_service

    run_migrations()

    monkeypatch.setattr(music_service, "resolve_api_key", lambda *a, **k: "real-id")
    monkeypatch.setattr(music_service, "_jamendo_get", lambda client_id, mood: _SAMPLE)
    monkeypatch.setattr(music_service, "download_asset", lambda asset, *a, **k: "")  # fail

    assert music_service.fetch_music_track("tense", min_duration=60) is None

    session = get_session()
    try:
        rows = session.query(Asset).filter_by(provider="jamendo").all()
        assert rows == [], "orphaned empty-path jamendo rows must not persist"
    finally:
        session.close()
