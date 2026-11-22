import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _no_wikimedia_api_pacing(monkeypatch):
    """Tests talk to mock transports: no gap between Wikimedia API calls (test_images sets its own where it matters)."""
    from sof import images
    monkeypatch.setattr(images, "WIKI_API_GAP", 0.0)
    monkeypatch.setattr(images, "_api_at", [0.0])
    monkeypatch.setattr(images, "_api_waits", [0])
