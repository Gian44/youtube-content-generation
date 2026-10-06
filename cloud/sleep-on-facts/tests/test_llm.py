import httpx
import pytest
from sof.llm import LLM, LLMError, parse_json


def _openai(status=200, content='{"ok": 1}'):
    def handler(req):
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return httpx.MockTransport(handler)


def _gemini(text='{"ok": 2}', status=200):
    return httpx.MockTransport(lambda r: httpx.Response(status, json={"candidates": [{"content": {"parts": [{"text": text}]}}]}))


def _llm(oa, gm, gemini_key="g", models=("gemini-x",)):
    return LLM("sk", gemini_key, "gpt-4o-mini", list(models), openai_transport=oa, gemini_transport=gm, retries=1, sleep=lambda s: None)


def test_openai_primary():
    assert _llm(_openai(), _gemini()).generate_json("p") == {"ok": 1}


def test_falls_back_to_gemini_on_503():
    assert _llm(_openai(503), _gemini()).generate_json("p") == {"ok": 2}


def test_non_capacity_openai_error_raises():
    with pytest.raises(LLMError, match="400"):
        _llm(_openai(400), _gemini()).generate_json("p")


def test_no_gemini_key_raises():
    with pytest.raises(LLMError):
        _llm(_openai(429), _gemini(), gemini_key="").generate_json("p")


def test_all_gemini_models_exhausted():
    with pytest.raises(LLMError, match="exhausted"):
        _llm(_openai(503), _gemini(status=503), models=("a", "b")).generate_json("p")


def test_parse_json_tolerates_prose():
    assert parse_json('Sure! {"a": 1} thanks') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json("nope")
