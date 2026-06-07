"""asset_collector — image collection at scale (pagination) for sleep slideshows.

A 3-hour sleep video wants 100+ topic-matched stock IMAGES, but the providers cap
a single page at a handful of results. These tests pin the pagination loop, the
graceful stop/partial-failure behavior, and cross-query de-duplication — without
real network calls.
"""

from __future__ import annotations

import pytest


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stands in for ``httpx.Client`` as a context manager; records page params."""

    def __init__(self, responder):
        self._responder = responder
        self.requests: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        self.requests.append(params)
        return self._responder(int(params.get("page", 1)), params)


def _pexels_photos(page: int, n: int) -> dict:
    return {
        "photos": [
            {
                "src": {"original": f"https://img/pexels/{page}_{i}.jpg"},
                "width": 1920,
                "height": 1080,
                "user": {"name": "Photographer"},
            }
            for i in range(n)
        ]
    }


def _install_fake_client(monkeypatch, responder):
    from storyfactory.services import asset_collector as ac

    fake = _FakeClient(responder)
    monkeypatch.setattr(ac.httpx, "Client", lambda *a, **k: fake)
    monkeypatch.setattr(ac, "track_api_call", lambda **k: None)
    import time as _time
    monkeypatch.setattr(ac, "time", _time)
    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    return fake


# ---------------------------------------------------------------------------
# Pexels image pagination
# ---------------------------------------------------------------------------

def test_pexels_paginates_until_target_reached(isolated_env, monkeypatch):
    from storyfactory.db.migrations import run_migrations
    from storyfactory.services import asset_collector as ac

    run_migrations()

    def responder(page, params):
        # Infinite supply: return a full page of the requested size every time.
        return _FakeResp(200, _pexels_photos(page, int(params["per_page"])))

    fake = _install_fake_client(monkeypatch, responder)

    assets = ac._collect_from_pexels("calm ocean", 150, "image", "real-key")

    assert len(assets) == 150
    assert all(a.type == "image" for a in assets)
    assert len({a.original_url for a in assets}) == 150       # all unique
    pages = [r.get("page") for r in fake.requests]
    assert max(pages) >= 2                                     # actually paginated


def test_pexels_stops_when_results_exhausted(isolated_env, monkeypatch):
    from storyfactory.db.migrations import run_migrations
    from storyfactory.services import asset_collector as ac

    run_migrations()

    def responder(page, params):
        return _FakeResp(200, _pexels_photos(page, 10) if page == 1 else _pexels_photos(page, 0))

    _install_fake_client(monkeypatch, responder)

    assets = ac._collect_from_pexels("calm ocean", 150, "image", "real-key")
    assert len(assets) == 10          # not an infinite loop; returns what exists


def test_pexels_returns_partial_on_error_status(isolated_env, monkeypatch):
    from storyfactory.db.migrations import run_migrations
    from storyfactory.services import asset_collector as ac

    run_migrations()

    def responder(page, params):
        if page == 1:
            return _FakeResp(200, _pexels_photos(1, 10))
        return _FakeResp(429, {})     # rate-limited on page 2

    _install_fake_client(monkeypatch, responder)

    assets = ac._collect_from_pexels("calm ocean", 150, "image", "real-key")
    assert len(assets) == 10          # page-1 results survive; no exception


# ---------------------------------------------------------------------------
# Pixabay image pagination
# ---------------------------------------------------------------------------

def test_pixabay_paginates_until_target_reached(isolated_env, monkeypatch):
    from storyfactory.db.migrations import run_migrations
    from storyfactory.services import asset_collector as ac

    run_migrations()

    def responder(page, params):
        n = int(params["per_page"])
        return _FakeResp(
            200,
            {
                "hits": [
                    {
                        "largeImageURL": f"https://img/pixabay/{page}_{i}.jpg",
                        "imageWidth": 1920,
                        "imageHeight": 1080,
                        "user": "Creator",
                    }
                    for i in range(n)
                ]
            },
        )

    fake = _install_fake_client(monkeypatch, responder)

    assets = ac._collect_from_pixabay("calm ocean", 120, "image", "real-key")

    assert len(assets) == 120
    assert all(a.type == "image" for a in assets)
    assert len({a.original_url for a in assets}) == 120
    assert max(r.get("page") for r in fake.requests) >= 2


# ---------------------------------------------------------------------------
# collect_topic_assets — cross-query de-duplication + target
# ---------------------------------------------------------------------------

def test_collect_topic_assets_dedupes_across_queries(monkeypatch):
    from storyfactory.db.models import Asset
    from storyfactory.services import asset_collector as ac

    def fake_collect(query=None, count=3, asset_type="video", category=None):
        # Every query returns the SAME urls → must be de-duplicated to 2 total.
        return [
            Asset(id=f"{query}-1", original_url="https://img/dup1.jpg", type=asset_type),
            Asset(id=f"{query}-2", original_url="https://img/dup2.jpg", type=asset_type),
        ]

    monkeypatch.setattr(ac, "collect_background_assets", fake_collect)

    assets = ac.collect_topic_assets(
        ["ocean", "deep sea", "calm water"], count=150, asset_type="image"
    )
    assert {a.original_url for a in assets} == {"https://img/dup1.jpg", "https://img/dup2.jpg"}
    assert len(assets) == 2
