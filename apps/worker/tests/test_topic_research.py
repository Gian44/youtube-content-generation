"""Unit tests for the keyless Wikipedia topic grounding (Sleep On Facts).

httpx is mocked, so these never touch the network. They lock in the MediaWiki
Action API contract, the policy-compliant User-Agent, graceful degradation on
non-200 / missing pages, and the dry-run / disabled no-network guarantees.
"""

from __future__ import annotations

import storyfactory.services.topic_research as tr


class _FakeResp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, resp: _FakeResp, capture: dict | None = None):
        self._resp = resp
        self._capture = capture

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, headers=None):
        if self._capture is not None:
            self._capture.update(url=url, params=params, headers=headers)
        return self._resp


def _patch_client(monkeypatch, resp: _FakeResp, capture: dict | None = None):
    monkeypatch.setattr(tr.httpx, "Client", lambda *a, **k: _FakeClient(resp, capture))


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network must not be used here")

    monkeypatch.setattr(tr.httpx, "Client", boom)


def test_dry_run_skips_network(monkeypatch):
    _no_network(monkeypatch)
    out = tr.research_topic("Ancient Egypt", dry_run=True)
    assert out["hints"] == ""
    assert "Ancient Egypt" in out["keywords"]


def test_disabled_skips_network(monkeypatch):
    _no_network(monkeypatch)
    out = tr.research_topic("Volcanoes", enabled=False)
    assert out["hints"] == ""
    assert "Volcanoes" in out["keywords"]


def test_parses_action_api_extract(monkeypatch):
    payload = {
        "query": {
            "pages": [
                {"title": "Ancient Egypt", "extract": "Ancient Egypt was a civilization along the Nile."}
            ]
        }
    }
    cap: dict = {}
    _patch_client(monkeypatch, _FakeResp(200, payload), cap)

    out = tr.research_topic("Ancient Egypt")
    assert "Nile" in out["hints"]
    assert "Ancient Egypt" in out["keywords"]
    # Hits the stable Action API with a contact-bearing (policy-compliant) UA.
    assert cap["url"] == tr._ACTION_API
    assert cap["params"]["action"] == "query"
    assert "@" in cap["headers"]["User-Agent"]


def test_non_200_degrades_gracefully(monkeypatch):
    _patch_client(monkeypatch, _FakeResp(403, {}))
    out = tr.research_topic("Anything")
    assert out["hints"] == ""
    assert out["keywords"]  # topic-derived keywords always present


def test_missing_page_degrades_gracefully(monkeypatch):
    payload = {"query": {"pages": [{"title": "Nope", "missing": True}]}}
    _patch_client(monkeypatch, _FakeResp(200, payload))
    out = tr.research_topic("Nonexistent Topic XYZ")
    assert out["hints"] == ""


def test_canonical_title_folds_into_keywords(monkeypatch):
    payload = {"query": {"pages": [{"title": "Roman Empire", "extract": "The Roman Empire was vast."}]}}
    _patch_client(monkeypatch, _FakeResp(200, payload))
    out = tr.research_topic("the romans")
    # Canonical Wikipedia title contributes extra visual keywords.
    joined = " ".join(out["keywords"]).lower()
    assert "roman" in joined
