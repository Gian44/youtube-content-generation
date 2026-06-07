"""asset_collector — audio download extension + Jamendo license validation."""

from __future__ import annotations


def test_download_extension_for_audio_is_mp3():
    from storyfactory.services import asset_collector

    assert asset_collector._download_extension("audio") == ".mp3"
    assert asset_collector._download_extension("video") == ".mp4"
    assert asset_collector._download_extension("image") == ".jpg"


def test_validate_asset_license_accepts_jamendo():
    from storyfactory.db.models import Asset
    from storyfactory.services.asset_collector import validate_asset_license

    # Even when not pre-marked "verified", a Jamendo (CC) asset is a free provider.
    asset = Asset(
        id="a1",
        provider="jamendo",
        type="audio",
        original_url="https://dl/x.mp3",
        policy_status="pending",
    )
    assert validate_asset_license(asset) is True
