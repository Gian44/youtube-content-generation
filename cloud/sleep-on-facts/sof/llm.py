"""One JSON-returning LLM call: OpenAI chat completions first, Gemini (free tier) on capacity errors."""

from __future__ import annotations

import json
import logging
import time

import httpx

log = logging.getLogger("sof.llm")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
CAPACITY = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


class _HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status, self.body = status, body


def parse_json(raw: str) -> dict:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    if isinstance(raw, str):
        s, e = raw.find("{"), raw.rfind("}") + 1
        if 0 <= s < e:
            try:
                data = json.loads(raw[s:e])
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    raise LLMError(f"LLM output was not a JSON object: {str(raw)[:120]}")


class LLM:
    def __init__(
        self,
        openai_key: str,
        gemini_key: str,
        openai_model: str,
        gemini_models: list[str],
        *,
        openai_transport=None,
        gemini_transport=None,
        retries: int = 4,
        timeout: float = 180.0,
        sleep=time.sleep,
    ):
        self.openai_model, self.gemini_models, self.retries = openai_model, list(gemini_models), retries
        self._sleep = sleep
        self._oa = (
            httpx.Client(timeout=timeout, transport=openai_transport,
                         headers={"Authorization": f"Bearer {openai_key}"})
            if openai_key else None
        )
        self._gm = (
            httpx.Client(timeout=timeout, transport=gemini_transport, headers={"x-goog-api-key": gemini_key})
            if gemini_key else None
        )

    # -- transport -----------------------------------------------------------

    def _post_retry(self, client: httpx.Client, url: str, payload: dict) -> httpx.Response:
        delay = 2.0
        resp = None
        for attempt in range(self.retries + 1):
            try:
                resp = client.post(url, json=payload)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise _HttpError(503, f"transport error: {exc}")
                self._sleep(delay)
                delay *= 2
                continue
            if resp.status_code in CAPACITY and attempt < self.retries:
                ra = resp.headers.get("retry-after")
                self._sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else delay)
                delay *= 2
                continue
            return resp
        return resp  # type: ignore[return-value]

    @staticmethod
    def openai_payload(model: str, msgs: list[dict], max_tokens: int, temperature: float, reasoning: str) -> dict:
        """GPT-5-family models reject ``max_tokens``/``temperature`` and take ``max_completion_tokens``
        (which also pays for hidden reasoning tokens, hence the headroom) plus ``reasoning_effort``."""
        body = {"model": model, "messages": msgs, "response_format": {"type": "json_object"}}
        if model.startswith("gpt-5") or model.startswith("o"):
            body["max_completion_tokens"] = max_tokens * 2
            body["reasoning_effort"] = reasoning
        else:
            body["max_tokens"] = max_tokens
            body["temperature"] = temperature
        return body

    def _openai(self, prompt, system, max_tokens, temperature, model, reasoning) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        resp = self._post_retry(self._oa, OPENAI_URL, self.openai_payload(model, msgs, max_tokens, temperature, reasoning))
        if resp.status_code != 200:
            raise _HttpError(resp.status_code, resp.text[:300])
        return resp.json()["choices"][0]["message"]["content"]

    def _gemini(self, model, prompt, system, max_tokens, temperature) -> str:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                                 "responseMimeType": "application/json"},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        resp = self._post_retry(self._gm, f"{GEMINI_BASE}/models/{model}:generateContent", payload)
        if resp.status_code != 200:
            raise _HttpError(resp.status_code, resp.text[:300])
        parts = (resp.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            raise _HttpError(502, "gemini returned no text")
        return text

    # -- public --------------------------------------------------------------

    def generate_json(self, prompt: str, *, system: str | None = None, max_tokens: int = 3000,
                      temperature: float = 0.7, model: str | None = None, reasoning: str = "low") -> dict:
        """``model`` overrides the default OpenAI model for this call (e.g. a cheap one for metadata)."""
        last: Exception | None = None
        if self._oa is not None:
            try:
                return parse_json(self._openai(prompt, system, max_tokens, temperature, model or self.openai_model, reasoning))
            except _HttpError as exc:
                if exc.status not in CAPACITY:
                    raise LLMError(f"OpenAI HTTP {exc.status}: {exc.body}")
                log.warning("openai capacity error %s; falling back to Gemini", exc.status)
                last = exc
        if self._gm is None:
            raise LLMError(f"OpenAI unavailable and no Gemini key configured: {last}")
        for model in self.gemini_models:
            try:
                return parse_json(self._gemini(model, prompt, system, max_tokens, temperature))
            except _HttpError as exc:
                last = exc
                if exc.status not in CAPACITY:
                    raise LLMError(f"Gemini {model} HTTP {exc.status}: {exc.body}")
                log.warning("gemini %s capacity error %s", model, exc.status)
        raise LLMError(f"all models exhausted: {last}")
