"""One JSON-returning LLM call.

Order (2026-10-25, Gian: "I want Claude writing the scripts"): Claude Code CLI on the Claude subscription
(``claude -p`` authenticated by CLAUDE_CODE_OAUTH_TOKEN from ``claude setup-token``, no API billing) →
OpenAI chat completions → Gemini (free tier). Claude is skipped for the rest of a run once it reports a
usage limit or an auth error, so a capped subscription costs one failed call, not one per prompt.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time

import httpx

log = logging.getLogger("sof.llm")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
CAPACITY = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


class _ClaudeError(Exception):
    pass


CLAUDE_SYSTEM = ("You are a careful writer for a documentary narration pipeline. Follow the user's instructions "
                 "exactly. Reply with the requested JSON object only: no prose before or after it, no code fences.")
CLAUDE_JSON_SUFFIX = "\n\nReply with the JSON object only."
# A capped or logged-out subscription: stop asking Claude for the rest of the run.
_CLAUDE_FATAL = ("usage limit", "rate limit", "limit reached", "credit", "invalid api key", "invalid bearer",
                 "unauthorized", "authentication", "oauth", "not logged in", "please run /login", "401", "403")


def _run(cmd: list[str], prompt: str, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, encoding="utf-8")


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
        claude: bool = False,
        claude_writer: str = "sonnet",
        claude_fast: str = "haiku",
        claude_runner=None,
        claude_timeout: float = 600.0,
    ):
        self.openai_model, self.gemini_models, self.retries = openai_model, list(gemini_models), retries
        self._sleep = sleep
        self.claude_writer, self.claude_fast, self._claude_timeout = claude_writer, claude_fast, claude_timeout
        self._claude_run = claude_runner or _run
        self._claude_off: str | None = None if claude else "disabled"
        if claude and claude_runner is None and not shutil.which("claude"):
            self._claude_off = "claude CLI not installed"
            log.warning("claude CLI not found; scripts go to OpenAI/Gemini")
        self.stats: dict[str, int] = {"claude": 0, "openai": 0, "gemini": 0}
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

    def _claude(self, prompt: str, system: str | None, model: str) -> str:
        cmd = ["claude", "-p", "--output-format", "json", "--model", model, "--tools", "", "--strict-mcp-config",
               "--disable-slash-commands", "--no-session-persistence", "--max-turns", "2",
               "--system-prompt", (system + "\n\n" + CLAUDE_SYSTEM) if system else CLAUDE_SYSTEM]
        try:
            r = self._claude_run(cmd, prompt + CLAUDE_JSON_SUFFIX, self._claude_timeout)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise _ClaudeError(f"claude CLI did not answer: {exc}")
        out = (r.stdout or "").strip()
        try:
            data = json.loads(out) if out else {}
        except json.JSONDecodeError:
            data = {}
        if r.returncode != 0 or not isinstance(data, dict) or data.get("is_error") or not data.get("result"):
            detail = (data.get("result") if isinstance(data, dict) else "") or (r.stderr or out)[-300:]
            raise _ClaudeError(f"exit {r.returncode}: {detail}")
        return str(data["result"])

    def check_claude(self, timeout: float = 120.0) -> str | None:
        """Preflight: one tiny call on the fast tier. Returns None when Claude answers; otherwise switches
        Claude off for the run and returns why (a dead token must not cost 2 × 10 minutes per prompt)."""
        if self._claude_off is not None:
            return self._claude_off
        keep, self._claude_timeout = self._claude_timeout, timeout
        try:
            parse_json(self._claude('Return {"ok": true}', None, self.claude_fast))
            return None
        except (_ClaudeError, LLMError) as exc:
            self._claude_off = f"preflight failed: {str(exc)[:200]}"
            log.warning("claude %s", self._claude_off)
            return self._claude_off
        finally:
            self._claude_timeout = keep

    def claude_model_for(self, model: str | None) -> str:
        """The writer call (no override, or the configured writer model) → claude_writer; any fast/cheap
        override → claude_fast."""
        return self.claude_writer if (model is None or model == self.openai_model) else self.claude_fast

    def _gemini(self, model, prompt, system, max_tokens, temperature) -> str:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # thinking models spend output budget before the answer: 2026-10-25 a 3,200-token cap cut a
            # movement's JSON mid-string ("not a JSON object: {"), so give Gemini ample headroom
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max(8192, max_tokens * 3),
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
        """``model`` overrides the default OpenAI model for this call (e.g. a cheap one for metadata); with
        Claude on, it picks the Claude tier instead (see ``claude_model_for``)."""
        last: Exception | None = None
        if self._claude_off is None:
            cm = self.claude_model_for(model)
            for attempt in range(2):                    # one retry: a stray sentence around the JSON, a blip
                try:
                    out = parse_json(self._claude(prompt, system, cm))
                    self.stats["claude"] += 1
                    return out
                except (_ClaudeError, LLMError) as exc:
                    last = exc
                    msg = str(exc).lower()
                    if isinstance(exc, _ClaudeError) and any(k in msg for k in _CLAUDE_FATAL):
                        self._claude_off = str(exc)[:200]
                        log.warning("claude unavailable for the rest of this run (%s); falling back", self._claude_off)
                        break
                    log.warning("claude %s attempt %d failed: %s", cm, attempt + 1, str(exc)[:200])
        if self._oa is not None:
            try:
                out = parse_json(self._openai(prompt, system, max_tokens, temperature, model or self.openai_model, reasoning))
                self.stats["openai"] += 1
                return out
            except _HttpError as exc:
                if exc.status not in CAPACITY:
                    raise LLMError(f"OpenAI HTTP {exc.status}: {exc.body}")
                log.warning("openai capacity error %s; falling back to Gemini", exc.status)
                last = exc
        if self._gm is None:
            raise LLMError(f"OpenAI unavailable and no Gemini key configured: {last}")
        for model in self.gemini_models:
            try:
                out = parse_json(self._gemini(model, prompt, system, max_tokens, temperature))
                self.stats["gemini"] += 1
                return out
            except _HttpError as exc:
                last = exc
                if exc.status not in CAPACITY:
                    raise LLMError(f"Gemini {model} HTTP {exc.status}: {exc.body}")
                log.warning("gemini %s capacity error %s", model, exc.status)
        raise LLMError(f"all models exhausted: {last}")
