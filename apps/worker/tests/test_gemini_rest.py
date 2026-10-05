"""gemini_rest — REST client request shape, retries, and tolerant JSON parsing."""

from __future__ import annotations

import json

import httpx
import pytest

from storyfactory.services import gemini_rest
from storyfactory.services.gemini_rest import Gemini, GeminiError, _RETRY_BACKOFF, _loads


def _text_response(*texts: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": t} for t in texts]}}]}


def _client(handler, monkeypatch) -> tuple[Gemini, list[httpx.Request]]:
    """Build a Gemini client over a MockTransport, recording every request."""
    monkeypatch.setattr(gemini_rest.time, "sleep", lambda *_: None)
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    return Gemini(api_key="test-key", transport=httpx.MockTransport(record)), seen


def _scripted(*responses: httpx.Response):
    """Handler that returns the n-th scripted response, repeating the last one."""

    def handler(_request, n):
        return responses[min(n, len(responses)) - 1]

    return handler


# ---------------------------------------------------------------------------
# request shape
# ---------------------------------------------------------------------------

def test_generate_request_shape(monkeypatch):
    client, seen = _client(_scripted(httpx.Response(200, json=_text_response("ok"))), monkeypatch)

    client.generate("gemini-3.6-flash", "hello", system="be brief", json_mode=True)

    req = seen[0]
    assert req.url.path.endswith("/models/gemini-3.6-flash:generateContent")
    assert req.headers["x-goog-api-key"] == "test-key"
    assert "key" not in dict(req.url.params)
    body = json.loads(req.content)
    assert body["contents"][0]["parts"][0]["text"] == "hello"
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_generate_without_json_mode_omits_mime_type(monkeypatch):
    client, seen = _client(_scripted(httpx.Response(200, json=_text_response("ok"))), monkeypatch)

    client.generate("gemini-3.6-flash", "hello", temperature=0.2, max_output_tokens=50)

    body = json.loads(seen[0].content)
    assert "responseMimeType" not in body["generationConfig"]
    assert "systemInstruction" not in body
    assert body["generationConfig"]["temperature"] == 0.2
    assert body["generationConfig"]["maxOutputTokens"] == 50


def test_generate_json_sets_mime_type(monkeypatch):
    client, seen = _client(_scripted(httpx.Response(200, json=_text_response('{"a": 1}'))), monkeypatch)

    assert client.generate_json("gemini-3.6-flash", "give json") == {"a": 1}
    body = json.loads(seen[0].content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"


def test_generate_joins_multiple_parts(monkeypatch):
    client, _ = _client(_scripted(httpx.Response(200, json=_text_response("Hello, ", "world"))), monkeypatch)

    assert client.generate("gemini-3.6-flash", "hi") == "Hello, world"


# ---------------------------------------------------------------------------
# retries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first_status", [429, 503])
def test_retries_once_then_succeeds(monkeypatch, first_status):
    client, seen = _client(
        _scripted(httpx.Response(first_status, text="slow down"), httpx.Response(200, json=_text_response("fine"))),
        monkeypatch,
    )

    assert client.generate("gemini-3.6-flash", "hi") == "fine"
    assert len(seen) == 2


def test_retry_transport_error_then_succeeds(monkeypatch):
    def handler(request, n):
        if n == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=_text_response("fine"))

    client, seen = _client(handler, monkeypatch)
    assert client.generate("gemini-3.6-flash", "hi") == "fine"
    assert len(seen) == 2


def test_exhausted_retries_raise(monkeypatch):
    client, seen = _client(_scripted(httpx.Response(500, text="server on fire")), monkeypatch)

    with pytest.raises(GeminiError, match="500"):
        client.generate("gemini-3.6-flash", "hi")
    assert len(seen) == len(_RETRY_BACKOFF) + 1


def test_retry_after_header_is_honored_and_capped(monkeypatch):
    sleeps: list[float] = []
    client, _ = _client(
        _scripted(
            httpx.Response(429, text="x", headers={"Retry-After": "3"}),
            httpx.Response(429, text="x", headers={"Retry-After": "999"}),
            httpx.Response(200, json=_text_response("fine")),
        ),
        monkeypatch,
    )
    monkeypatch.setattr(gemini_rest.time, "sleep", sleeps.append)

    assert client.generate("gemini-3.6-flash", "hi") == "fine"
    assert sleeps == [3.0, 30.0]


def test_400_raises_immediately(monkeypatch):
    client, seen = _client(_scripted(httpx.Response(400, text="bad request body")), monkeypatch)

    with pytest.raises(GeminiError, match="400.*bad request body"):
        client.generate("gemini-3.6-flash", "hi")
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# response handling
# ---------------------------------------------------------------------------

def test_generate_json_parses_fenced_reply(monkeypatch):
    fenced = '```json\n{"title": "x", "n": 2}\n```'
    client, _ = _client(_scripted(httpx.Response(200, json=_text_response(fenced))), monkeypatch)

    assert client.generate_json("gemini-3.6-flash", "json please") == {"title": "x", "n": 2}


def test_generate_json_rejects_non_json(monkeypatch):
    client, _ = _client(_scripted(httpx.Response(200, json=_text_response("I refuse to answer."))), monkeypatch)

    with pytest.raises(GeminiError, match="non-JSON"):
        client.generate_json("gemini-3.6-flash", "json please")


def test_empty_candidates_raise_with_feedback(monkeypatch):
    blocked = {"promptFeedback": {"blockReason": "SAFETY"}}
    client, _ = _client(_scripted(httpx.Response(200, json=blocked)), monkeypatch)

    with pytest.raises(GeminiError, match="SAFETY"):
        client.generate("gemini-3.6-flash", "hi")


def test_finish_reason_reported_when_no_text(monkeypatch):
    body = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]}
    client, _ = _client(_scripted(httpx.Response(200, json=body)), monkeypatch)

    with pytest.raises(GeminiError, match="MAX_TOKENS"):
        client.generate("gemini-3.6-flash", "hi")


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["", "your-gemini-api-key"])
def test_missing_or_placeholder_key_raises(key):
    with pytest.raises(GeminiError, match="not configured"):
        Gemini(api_key=key)


def test_list_models(monkeypatch):
    body = {"models": [{"name": "models/gemini-3.6-flash"}, {"name": "models/gemini-3.6-pro"}]}
    client, seen = _client(_scripted(httpx.Response(200, json=body)), monkeypatch)

    assert client.list_models() == ["gemini-3.6-flash", "gemini-3.6-pro"]
    assert seen[0].method == "GET"
    assert seen[0].url.path.endswith("/models")
    assert seen[0].headers["x-goog-api-key"] == "test-key"


def test_list_models_error(monkeypatch):
    client, _ = _client(_scripted(httpx.Response(403, text="forbidden")), monkeypatch)

    with pytest.raises(GeminiError, match="403"):
        client.list_models()


# ---------------------------------------------------------------------------
# _loads
# ---------------------------------------------------------------------------

def test_loads_plain_fenced_and_prose():
    assert _loads('{"a": 1}') == {"a": 1}
    assert _loads('```json\n{"a": 1}\n```') == {"a": 1}
    assert _loads('```\n[1, 2]\n```') == [1, 2]
    assert _loads('Here you go: {"a": 1} thanks') == {"a": 1}
    assert _loads("Sure: [1, 2, 3].") == [1, 2, 3]
    assert _loads("no json here") is None
