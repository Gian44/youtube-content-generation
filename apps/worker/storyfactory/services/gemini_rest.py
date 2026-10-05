"""gemini_rest — minimal REST client for the Google Gemini API (v1beta).

Sends the key in the ``x-goog-api-key`` header (never in the URL), supports
``systemInstruction`` and JSON mode, and retries on 429 / 5xx with backoff.
"""

from __future__ import annotations

import json
import time

import httpx

from storyfactory.channel_context import resolve_api_key
from storyfactory.logger import get_logger

log = get_logger("gemini_rest")

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TEXT_MODEL = "gemini-3.6-flash"
_RETRY_BACKOFF = (1.0, 2.0, 4.0, 8.0)  # seconds; retries on 429 and 5xx
_MAX_RETRY_AFTER = 30.0
_PLACEHOLDER_KEY = "your-gemini-api-key"


class GeminiError(RuntimeError):
    """Raised for configuration, transport, HTTP and response-shape errors."""


class Gemini:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if api_key is None:
            api_key = resolve_api_key("text.gemini", "gemini_api_key")
        if not api_key or api_key == _PLACEHOLDER_KEY:
            raise GeminiError(
                "Gemini API key not configured. Set GEMINI_API_KEY in .env "
                "or the text.gemini app integration."
            )
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={"x-goog-api-key": api_key},
        )

    # -- transport -----------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}/{path}"
        attempt = 0
        while True:
            try:
                resp = self._client.post(url, json=payload)
                status, body = resp.status_code, resp.text
            except httpx.HTTPError as exc:
                status, body = 599, f"transport error: {exc}"
            if 200 <= status < 300:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise GeminiError(f"Gemini returned non-JSON body: {body[:300]}") from exc
            if status != 429 and status < 500:
                raise GeminiError(f"Gemini HTTP {status}: {body[:300]}")
            if attempt >= len(_RETRY_BACKOFF):
                raise GeminiError(f"Gemini HTTP {status} after {attempt} retries: {body[:300]}")
            delay = _RETRY_BACKOFF[attempt]
            if status != 599:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = min(float(retry_after), _MAX_RETRY_AFTER)
            log.warning("gemini_rest_retry", status=status, attempt=attempt + 1)
            time.sleep(delay)
            attempt += 1

    # -- public API ----------------------------------------------------------

    def generate(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
    ) -> str:
        """Same positional order as the UGC client: ``generate(model, prompt, system=...)``."""
        gen_cfg: dict = {"temperature": temperature}
        if max_output_tokens is not None:
            gen_cfg["maxOutputTokens"] = max_output_tokens
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        payload: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        log.info("gemini_rest_request", model=model, json_mode=json_mode)
        body = self._post(f"models/{model}:generateContent", payload)

        candidates = body.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        if not text:
            detail = {}
            if body.get("promptFeedback"):
                detail["promptFeedback"] = body["promptFeedback"]
            if candidates and candidates[0].get("finishReason"):
                detail["finishReason"] = candidates[0]["finishReason"]
            raise GeminiError(f"Gemini returned no text: {json.dumps(detail)}")
        return text

    def generate_json(
        self,
        model: str,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.6,
    ):
        raw = self.generate(model, prompt, system=system, json_mode=True, temperature=temperature)
        data = _loads(raw)
        if not isinstance(data, (dict, list)):
            raise GeminiError(f"Gemini returned non-JSON: {raw[:200]}")
        return data

    def list_models(self) -> list[str]:
        try:
            resp = self._client.get(f"{self.base_url}/models")
        except httpx.HTTPError as exc:
            raise GeminiError(f"Gemini transport error: {exc}") from exc
        if not (200 <= resp.status_code < 300):
            raise GeminiError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
        # "models/gemini-3.6-flash" -> "gemini-3.6-flash", as the UGC client did
        return [m["name"].split("/", 1)[-1] for m in resp.json().get("models", [])]


def _loads(raw: str):
    """Tolerant JSON parse: strips ``` fences, then salvages the outermost {...} / [...]."""
    text = raw.strip()
    if text.startswith("```"):
        text = text[3:]
        if text.lower().startswith("json"):
            text = text[4:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                continue
    return None
