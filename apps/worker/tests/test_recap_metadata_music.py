"""_recap_metadata — music attribution surfaced in the upload description."""

from __future__ import annotations

import types


def _story():
    return types.SimpleNamespace(title="Spider-Noir Recap", hook="Watch this twist.")


def test_recap_metadata_appends_music_attribution():
    from storyfactory.services import youtube_uploader

    render_job = types.SimpleNamespace(
        render_config={
            "music_attribution": 'Music: "Calm Tide" by Artist A (CC BY 3.0) via Jamendo'
        }
    )
    meta = youtube_uploader._recap_metadata(_story(), "spider-noir", render_job)

    assert "Music:" in meta["description"]
    assert "Jamendo" in meta["description"]
    assert "Calm Tide" in meta["description"]


def test_recap_metadata_without_music_has_no_credit():
    from storyfactory.services import youtube_uploader

    render_job = types.SimpleNamespace(render_config={})
    meta = youtube_uploader._recap_metadata(_story(), "spider-noir", render_job)

    assert "Music:" not in meta["description"]
    # The hook is still present.
    assert "Watch this twist." in meta["description"]
