# Sleep On Facts Cloud Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone Python pipeline under `cloud/sleep-on-facts/` in the (now public) `Gian44/youtube-content-generation` repo that, on a GitHub Actions cron, turns one topic into a ~3-hour narrated image-slideshow video and uploads it to the Sleep On Facts YouTube channel, recording the result in a committed ledger.

**Architecture:** Six pure-ish stages behind a tiny `Config`, each writing into `work/<run-id>/` and skipping itself when its output exists: `topics → script → tts → images → render → upload → ledger`. No database, no StoryFactory imports; the script generator and the reel-loop renderer are ported from `apps/worker/storyfactory/services/{sleep_script_generator,slideshow_renderer}.py`. LLM calls go through one `llm.py` with OpenAI primary and Gemini fallback on capacity errors.

**Tech Stack:** Python 3.12, httpx, openai (official SDK), google-api-python-client + google-auth, Pillow, PyYAML, ffmpeg (apt on the runner), pytest + httpx.MockTransport. GitHub Actions `ubuntu-latest`.

**Spec:** `docs/superpowers/specs/2026-10-06-sleep-on-facts-cloud-pipeline-design.md`

**Conventions:** work from `cloud/sleep-on-facts/`; run tests with `python -m pytest -q`; package name `sof`. Every network client accepts `transport=` for tests. Commit messages end with the session attribution lines. Branch `feat/sleep-cloud`, merged to `main` at the end (the cron must live on the default branch).

---

## File map

| Path (under `cloud/sleep-on-facts/`) | Responsibility |
| --- | --- |
| `requirements.txt`, `README.md` | deps, how to run |
| `topics.yml`, `ledger.json` | topic list with image queries; uploaded-video ledger |
| `run.py` | CLI: `--minutes --topic --privacy --skip-upload --work-dir` |
| `sof/config.py` | `Config` dataclass (all tunables + secrets from env) |
| `sof/llm.py` | `LLM.generate_json(prompt, system, max_tokens, temperature)`: OpenAI → Gemini fallback |
| `sof/topics.py` | load `topics.yml`, `pick_next(topics, ledger)` |
| `sof/research.py` | Wikipedia lead extract (MediaWiki Action API) as grounding hints |
| `sof/script.py` | outline + movement expansion loop + metadata; ported prompts |
| `sof/tts.py` | chunking, OpenAI `tts-1` per chunk, concat + loudnorm |
| `sof/images.py` | Pexels/Pixabay pool, dedupe, pad, download, credits |
| `sof/render.py` | ffmpeg command builders + reel/assembly orchestration + thumbnail |
| `sof/upload.py` | YouTube resumable upload + thumbnail set |
| `sof/ledger.py` | read/append ledger, `git commit` helper |
| `sof/pipeline.py` | `run(cfg)` orchestrating the stages with skip-if-exists |
| `tests/test_*.py` | one per module |
| `.github/workflows/sleep-daily.yml` (repo root) | cron + dispatch |

---

### Task 1: Skeleton, config, CLI parsing

**Files:** `cloud/sleep-on-facts/{requirements.txt,README.md,run.py}`, `sof/__init__.py`, `sof/config.py`, `tests/test_config.py`

- [ ] **Step 1: Failing test**

```python
# tests/test_config.py
import os
from sof.config import Config, parse_args


def test_defaults_and_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("PEXELS_API_KEY", "px")
    cfg = Config.from_env(minutes=180)
    assert cfg.minutes == 180 and cfg.target_words == 27000
    assert cfg.tts_model == "tts-1" and cfg.tts_voice == "onyx" and cfg.tts_speed == 0.9
    assert cfg.script_model == "gpt-4o-mini"
    assert cfg.gemini_models == ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
    assert cfg.images_target == 150 and cfg.images_min == 100
    assert cfg.privacy == "public"
    assert cfg.openai_api_key == "sk-test" and cfg.pexels_api_key == "px"


def test_smoke_mode_forces_unlisted(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = Config.from_env(minutes=3, privacy="public")
    assert cfg.privacy == "unlisted" and cfg.is_smoke


def test_parse_args():
    ns = parse_args(["--minutes", "3", "--topic", "Whales", "--skip-upload"])
    assert ns.minutes == 3 and ns.topic == "Whales" and ns.skip_upload is True and ns.privacy is None
```

- [ ] **Step 2: Run** `python -m pytest tests/test_config.py -q` → FAIL (no module).
- [ ] **Step 3: Implement**

```python
# sof/config.py
"""All tunables in one place. Secrets come from the environment (GitHub Actions secrets)."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

WORDS_PER_MINUTE = 150
SMOKE_MAX_MINUTES = 10  # at/below this the run is a smoke test: always unlisted


@dataclass
class Config:
    minutes: int = 180
    topic: str | None = None
    privacy: str = "public"
    skip_upload: bool = False
    work_dir: str = "work"
    # script
    script_model: str = "gpt-4o-mini"
    gemini_models: list[str] = field(default_factory=lambda: [
        "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"])
    num_movements: int = 16
    segment_max_tokens: int = 2800
    max_segments: int = 40
    fill_ratio: float = 0.9
    max_words_ratio: float = 1.3
    # tts
    tts_model: str = "tts-1"
    tts_voice: str = "onyx"
    tts_speed: float = 0.9
    tts_chunk_chars: int = 4000
    # images
    images_target: int = 150
    images_min: int = 100
    images_fail_below: int = 30
    # render
    width: int = 1920
    height: int = 1080
    fps: int = 24
    dwell_seconds: float = 20.0
    crossfade_seconds: float = 1.5
    xfade_batch: int = 20
    # secrets
    openai_api_key: str = ""
    gemini_api_key: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    yt_client_id: str = ""
    yt_client_secret: str = ""
    yt_refresh_token: str = ""

    @property
    def target_words(self) -> int:
        return self.minutes * WORDS_PER_MINUTE

    @property
    def is_smoke(self) -> bool:
        return self.minutes <= SMOKE_MAX_MINUTES

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        cfg = cls(
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            pexels_api_key=os.environ.get("PEXELS_API_KEY", ""),
            pixabay_api_key=os.environ.get("PIXABAY_API_KEY", ""),
            yt_client_id=os.environ.get("YT_CLIENT_ID", ""),
            yt_client_secret=os.environ.get("YT_CLIENT_SECRET", ""),
            yt_refresh_token=os.environ.get("YT_REFRESH_TOKEN", ""),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(cfg, k, v)
        if cfg.is_smoke:
            cfg.privacy = "unlisted"
        return cfg


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sleep On Facts cloud pipeline")
    p.add_argument("--minutes", type=int, default=180)
    p.add_argument("--topic", default=None)
    p.add_argument("--privacy", choices=["public", "unlisted", "private"], default=None)
    p.add_argument("--skip-upload", action="store_true")
    p.add_argument("--work-dir", default="work")
    return p.parse_args(argv)
```

`run.py`:

```python
import sys
from sof.config import Config, parse_args
from sof.pipeline import run

if __name__ == "__main__":
    ns = parse_args()
    cfg = Config.from_env(minutes=ns.minutes, topic=ns.topic, privacy=ns.privacy,
                          skip_upload=ns.skip_upload, work_dir=ns.work_dir)
    sys.exit(run(cfg))
```

`requirements.txt`: `httpx>=0.27`, `openai>=1.30`, `google-api-python-client>=2.130`, `google-auth>=2.29`, `google-auth-httplib2>=0.2`, `Pillow>=10.3`, `PyYAML>=6.0`, `pytest>=8`.

- [ ] **Step 4: Run** → PASS (pipeline import may need a stub `sof/pipeline.py` with `def run(cfg): raise NotImplementedError` until Task 10; `run.py` is not imported by tests).
- [ ] **Step 5: Commit** `feat(sof): skeleton and config`

---

### Task 2: `llm.py` — OpenAI with Gemini fallback

**Files:** `sof/llm.py`, `tests/test_llm.py`

- [ ] **Step 1: Failing test**

```python
import httpx
import pytest
from sof.llm import LLM, LLMError, parse_json


def _openai_transport(status=200, content='{"ok": 1}'):
    def handler(req):
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return httpx.MockTransport(handler)


def _gemini_transport(text='{"ok": 2}'):
    return httpx.MockTransport(lambda r: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]}))


def test_openai_primary():
    llm = LLM("sk", "g", "gpt-4o-mini", ["gemini-x"], openai_transport=_openai_transport(), gemini_transport=_gemini_transport(), retries=0)
    assert llm.generate_json("p") == {"ok": 1}


def test_falls_back_to_gemini_on_503():
    llm = LLM("sk", "g", "gpt-4o-mini", ["gemini-x"], openai_transport=_openai_transport(503), gemini_transport=_gemini_transport(), retries=0)
    assert llm.generate_json("p") == {"ok": 2}


def test_no_gemini_key_raises():
    llm = LLM("sk", "", "gpt-4o-mini", ["gemini-x"], openai_transport=_openai_transport(429), gemini_transport=_gemini_transport(), retries=0)
    with pytest.raises(LLMError):
        llm.generate_json("p")


def test_parse_json_tolerates_prose():
    assert parse_json('Sure! {"a": 1} thanks') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json("nope")
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**

```python
# sof/llm.py
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


def parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        if isinstance(raw, str):
            s, e = raw.find("{"), raw.rfind("}") + 1
            if 0 <= s < e:
                try:
                    return json.loads(raw[s:e])
                except json.JSONDecodeError:
                    pass
        raise LLMError(f"LLM output was not JSON: {str(raw)[:120]}")


class LLM:
    def __init__(self, openai_key: str, gemini_key: str, openai_model: str, gemini_models: list[str], *,
                 openai_transport=None, gemini_transport=None, retries: int = 4, timeout: float = 180.0):
        self.openai_model, self.gemini_models, self.retries = openai_model, gemini_models, retries
        self.gemini_key = gemini_key
        self._oa = httpx.Client(timeout=timeout, transport=openai_transport,
                                headers={"Authorization": f"Bearer {openai_key}"}) if openai_key else None
        self._gm = httpx.Client(timeout=timeout, transport=gemini_transport,
                                headers={"x-goog-api-key": gemini_key}) if gemini_key else None

    def _post_retry(self, client, url, payload) -> httpx.Response:
        delay = 2.0
        for attempt in range(self.retries + 1):
            try:
                resp = client.post(url, json=payload)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise LLMError(f"transport error: {exc}")
                time.sleep(delay); delay *= 2; continue
            if resp.status_code in CAPACITY and attempt < self.retries:
                ra = resp.headers.get("retry-after")
                time.sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else delay); delay *= 2
                continue
            return resp
        return resp  # pragma: no cover

    def _openai(self, prompt, system, max_tokens, temperature) -> str:
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        resp = self._post_retry(self._oa, OPENAI_URL, {
            "model": self.openai_model, "messages": msgs, "temperature": temperature,
            "max_tokens": max_tokens, "response_format": {"type": "json_object"}})
        if resp.status_code != 200:
            raise _HttpError(resp.status_code, resp.text[:300])
        return resp.json()["choices"][0]["message"]["content"]

    def _gemini(self, model, prompt, system, max_tokens, temperature) -> str:
        payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                                        "responseMimeType": "application/json"}}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        resp = self._post_retry(self._gm, f"{GEMINI_BASE}/models/{model}:generateContent", payload)
        if resp.status_code != 200:
            raise _HttpError(resp.status_code, resp.text[:300])
        parts = (resp.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        if not text:
            raise _HttpError(502, "gemini returned no text")
        return text

    def generate_json(self, prompt: str, *, system: str | None = None, max_tokens: int = 3000,
                      temperature: float = 0.7) -> dict:
        last: Exception | None = None
        if self._oa is not None:
            try:
                return parse_json(self._openai(prompt, system, max_tokens, temperature))
            except _HttpError as exc:
                if exc.status not in CAPACITY:
                    raise LLMError(f"OpenAI HTTP {exc.status}: {exc.body}")
                log.warning("openai capacity error %s; falling back to Gemini", exc.status)
                last = exc
        if self._gm is None:
            raise LLMError(f"OpenAI unavailable and no Gemini key: {last}")
        for model in self.gemini_models:
            try:
                return parse_json(self._gemini(model, prompt, system, max_tokens, temperature))
            except _HttpError as exc:
                last = exc
                if exc.status not in CAPACITY:
                    raise LLMError(f"Gemini {model} HTTP {exc.status}: {exc.body}")
                log.warning("gemini %s capacity error %s", model, exc.status)
        raise LLMError(f"all models exhausted: {last}")


class _HttpError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body}")
        self.status, self.body = status, body
```

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(sof): llm client with Gemini fallback`

---

### Task 3: `topics.py` + `ledger.py`

**Files:** `topics.yml`, `ledger.json` (`[]`), `sof/topics.py`, `sof/ledger.py`, `tests/test_topics_ledger.py`

- [ ] **Step 1: Failing tests**

```python
import json
from sof.topics import load_topics, pick_next, Topic
from sof.ledger import load_ledger, append_entry, commit_message

YAML = """
topics:
  - name: Ancient Egypt
    queries: [egypt pyramids desert, nile river sunset]
  - name: The Deep Sea
    queries: [deep ocean dark water]
"""


def test_load_and_pick_round_robin(tmp_path):
    p = tmp_path / "topics.yml"; p.write_text(YAML)
    topics = load_topics(str(p))
    assert topics[0] == Topic(name="Ancient Egypt", queries=["egypt pyramids desert", "nile river sunset"])
    assert pick_next(topics, []).name == "Ancient Egypt"
    assert pick_next(topics, [{"topic": "Ancient Egypt"}]).name == "The Deep Sea"
    assert pick_next(topics, [{"topic": "Ancient Egypt"}, {"topic": "The Deep Sea"}]).name == "Ancient Egypt"
    assert pick_next(topics, [], forced="The Deep Sea").name == "The Deep Sea"


def test_ledger_roundtrip(tmp_path):
    p = tmp_path / "ledger.json"
    assert load_ledger(str(p)) == []
    e = append_entry(str(p), {"date": "2026-10-07", "topic": "Whales", "video_id": "abc"})
    assert load_ledger(str(p)) == [e] and json.loads(p.read_text())[0]["video_id"] == "abc"
    assert commit_message(e) == "ledger: 2026-10-07 Whales (abc)"
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement**

```python
# sof/topics.py
from __future__ import annotations
from dataclasses import dataclass
import yaml


@dataclass(frozen=True)
class Topic:
    name: str
    queries: list[str]


def load_topics(path: str) -> list[Topic]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for t in data.get("topics") or []:
        out.append(Topic(name=str(t["name"]).strip(), queries=[str(q) for q in (t.get("queries") or [])]))
    if not out:
        raise ValueError(f"no topics in {path}")
    return out


def pick_next(topics: list[Topic], ledger: list[dict], forced: str | None = None) -> Topic:
    """Fewest ledger entries wins; ties by list order. ``forced`` selects by name (case-insensitive)."""
    if forced:
        for t in topics:
            if t.name.lower() == forced.strip().lower():
                return t
        return Topic(name=forced.strip(), queries=[forced.strip()])
    counts = {t.name: 0 for t in topics}
    for e in ledger:
        if e.get("topic") in counts:
            counts[e["topic"]] += 1
    return min(topics, key=lambda t: (counts[t.name], topics.index(t)))
```

```python
# sof/ledger.py
from __future__ import annotations
import json, os, subprocess


def load_ledger(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f) or []


def append_entry(path: str, entry: dict) -> dict:
    rows = load_ledger(path)
    rows.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return entry


def commit_message(entry: dict) -> str:
    return f"ledger: {entry.get('date')} {entry.get('topic')} ({entry.get('video_id')})"


def git_commit_and_push(path: str, entry: dict) -> None:
    """Used by the workflow step; fails loudly so the run goes red if the ledger can't be saved."""
    subprocess.run(["git", "config", "user.name", "sleep-on-facts-bot"], check=True)
    subprocess.run(["git", "config", "user.email", "actions@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", path], check=True)
    subprocess.run(["git", "commit", "-m", commit_message(entry)], check=True)
    subprocess.run(["git", "pull", "--rebase"], check=True)
    subprocess.run(["git", "push"], check=True)
```

`topics.yml` — the ten StoryFactory topics, three hand-written landscape-friendly queries each (e.g. Ancient Egypt: `egypt pyramids desert`, `nile river sunset`, `ancient egyptian temple columns`; The Deep Sea: `deep ocean dark water`, `jellyfish glowing underwater`, `underwater cave light rays`; Outer Space: `nebula stars space`, `milky way night sky`, `planet surface horizon`; The Roman Empire: `roman ruins colosseum`, `ancient roman aqueduct`, `marble statue columns`; Volcanoes: `volcano lava night`, `volcanic landscape fog`, `crater lake mountain`; The Human Brain: `neurons abstract blue`, `calm person sleeping`, `brain scan abstract`; Whales: `humpback whale ocean`, `whale tail sunset sea`, `blue ocean surface aerial`; Antarctica: `antarctica iceberg`, `penguins snow landscape`, `aurora over ice`; The Solar System: `saturn rings planet`, `moon surface craters`, `sunrise from space earth`; Dinosaurs: `fossil skeleton museum`, `prehistoric forest ferns fog`, `dinosaur footprint rock`).

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** `feat(sof): topics and ledger`

---

### Task 4: `research.py` — Wikipedia grounding

**Files:** `sof/research.py`, `tests/test_research.py`

- [ ] **Step 1: Failing test**

```python
import httpx
from sof.research import wikipedia_hints

def test_hints_from_extract():
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"query": {"pages": {"1": {"extract": "Ancient Egypt was a civilization. " * 20}}}}))
    hints = wikipedia_hints("Ancient Egypt", transport=t)
    assert hints.startswith("Ancient Egypt was a civilization.") and len(hints) <= 4000

def test_failure_degrades_to_empty():
    t = httpx.MockTransport(lambda r: httpx.Response(403))
    assert wikipedia_hints("X", transport=t) == ""
```

- [ ] **Step 2–3:** Implement:

```python
# sof/research.py
"""Keyless factual grounding: the Wikipedia lead section via the MediaWiki Action API. Best-effort."""
from __future__ import annotations
import logging
import httpx

log = logging.getLogger("sof.research")
API = "https://en.wikipedia.org/w/api.php"
UA = "SleepOnFacts/1.0 (+https://github.com/Gian44/youtube-content-generation) python-httpx"


def wikipedia_hints(topic: str, *, transport=None, max_chars: int = 4000) -> str:
    try:
        with httpx.Client(timeout=20.0, transport=transport, headers={"User-Agent": UA}) as c:
            r = c.get(API, params={"action": "query", "prop": "extracts", "exintro": "1", "explaintext": "1",
                                   "redirects": "1", "titles": topic, "format": "json"})
            r.raise_for_status()
            pages = (r.json().get("query") or {}).get("pages") or {}
            text = " ".join((p.get("extract") or "") for p in pages.values()).strip()
            return text[:max_chars]
    except Exception as exc:  # noqa: BLE001
        log.warning("wikipedia grounding unavailable for %s: %s", topic, exc)
        return ""
```

- [ ] **Step 4–5:** PASS; commit `feat(sof): wikipedia grounding`

---

### Task 5: `script.py` — outline, expansion loop, metadata

**Files:** `sof/script.py`, `tests/test_script.py`

- [ ] **Step 1: Failing tests**

```python
import pytest
from sof.script import generate_script, ScriptTooLong, OUTLINE_PROMPT, SEGMENT_PROMPT


class FakeLLM:
    def __init__(self, words_per_segment=700, movements=4):
        self.w, self.m, self.calls = words_per_segment, movements, []

    def generate_json(self, prompt, *, system=None, max_tokens=3000, temperature=0.7):
        self.calls.append(prompt)
        if "Plan exactly" in prompt:
            start = len([c for c in self.calls if "Plan exactly" in c]) * 100
            return {"title": "Calm Facts", "hook": "Close your eyes.",
                    "movements": [{"heading": f"Part {start+i}", "beats": ["a", "b"]} for i in range(self.m)],
                    "image_search_queries": ["q1", "q2"]}
        if "CURRENT MOVEMENT" in prompt:
            return {"body": " ".join(["calm"] * self.w)}
        return {"title": "Calm Facts About Whales to Fall Asleep To (3 Hours)", "description": "d", "tags": ["a", "b"]}


def test_loops_until_target_reached():
    llm = FakeLLM(words_per_segment=700, movements=4)
    s = generate_script(llm, topic="Whales", hints="", minutes=20, target_words=3000,
                        num_movements=4, max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)
    assert s.word_count >= 2700 and s.movements >= 4
    assert sum("Plan exactly" in c for c in llm.calls) == 2  # one refill
    assert s.title.startswith("Calm Facts") and s.tags == ["a", "b"] and s.image_queries == ["q1", "q2"]


def test_too_long_raises():
    llm = FakeLLM(words_per_segment=2000, movements=4)
    with pytest.raises(ScriptTooLong):
        generate_script(llm, topic="W", hints="", minutes=20, target_words=3000,
                        num_movements=4, max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)


def test_segment_cap_stops_loop():
    llm = FakeLLM(words_per_segment=100, movements=4)
    s = generate_script(llm, topic="W", hints="", minutes=20, target_words=3000,
                        num_movements=4, max_segments=6, fill_ratio=0.9, max_words_ratio=1.3)
    assert s.movements == 6


def test_prompts_have_placeholders():
    for key in ("{{topic}}", "{{topic_hints}}", "{{num_movements}}"):
        assert key in OUTLINE_PROMPT
    for key in ("{{movement_heading}}", "{{movement_beats}}", "{{previous_tail}}", "{{target_words}}"):
        assert key in SEGMENT_PROMPT
```

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** — port `sleep_script_generator.py` with the StoryFactory outline/segment prompts verbatim (from `db/seed.py`, names `sleep_facts_outline` / `sleep_facts_segment`), plus a metadata prompt:

```python
# sof/script.py
from __future__ import annotations
import logging
from dataclasses import dataclass, field

log = logging.getLogger("sof.script")
_MIN_SEG, _MAX_SEG, _TAIL, _MAX_REFILLS = 600, 1500, 400, 8

OUTLINE_PROMPT = """<verbatim StoryFactory sleep_facts_outline template>"""
SEGMENT_PROMPT = """<verbatim StoryFactory sleep_facts_segment template>"""
METADATA_PROMPT = """You write YouTube metadata for a calm "facts to fall asleep to" channel.
Topic: {{topic}}. Video length: about {{hours}} hours. First 600 characters of the narration:
{{opening}}

Return JSON: {"title": "<≤ 95 chars, pattern 'Calm Facts About <Topic> to Fall Asleep To (<N> Hours)', no clickbait>",
 "description": "<2–3 calm sentences describing the video, then a blank line, then exactly this line: Narration is AI-generated; facts are drawn from public sources.>",
 "tags": ["<8–12 lowercase tags: sleep, facts, the topic, calm, relaxation, bedtime, …>"]}"""


class ScriptTooLong(RuntimeError):
    pass


@dataclass
class Script:
    title: str
    description: str
    tags: list[str]
    body: str
    word_count: int
    movements: int
    image_queries: list[str] = field(default_factory=list)


def _fill(t: str, **kw) -> str:
    for k, v in kw.items():
        t = t.replace("{{" + k + "}}", str(v))
    return t


def generate_script(llm, *, topic: str, hints: str, minutes: int, target_words: int, num_movements: int,
                    max_segments: int, fill_ratio: float, max_words_ratio: float) -> Script:
    per_words = max(_MIN_SEG, min(_MAX_SEG, target_words // max(1, num_movements)))
    bodies, covered, seen, tail = [], [], set(), ""

    def outline(extra: str) -> dict:
        return llm.generate_json(_fill(OUTLINE_PROMPT, topic=topic, topic_hints=(hints + extra) or "(no external notes available)",
                                       target_minutes=minutes, num_movements=num_movements), max_tokens=3000)

    def expand(m: dict) -> None:
        nonlocal tail
        heading = str(m.get("heading") or f"Part {len(bodies) + 1}")
        seen.add(heading.strip().lower())
        beats = "\n".join(f"- {b}" for b in (m.get("beats") or []) if str(b).strip()) or "(none provided)"
        summary = ("Already covered: " + "; ".join(covered) + ".") if covered else "(nothing yet — this is the opening)"
        prompt = _fill(SEGMENT_PROMPT, topic=topic, movement_heading=heading, movement_beats=beats,
                       running_summary=summary, previous_tail=tail or "(this is the very beginning)", target_words=per_words)
        try:
            body = str(llm.generate_json(prompt, max_tokens=2800).get("body") or "").strip()
        except Exception as exc:  # noqa: BLE001 — one bad movement must not abort the video
            log.warning("segment failed (%s): %s", heading, exc); return
        if body:
            bodies.append(body); covered.append(heading); tail = body[-_TAIL:]

    total = lambda: sum(len(b.split()) for b in bodies)  # noqa: E731
    first = outline("")
    movements = [m for m in (first.get("movements") or []) if isinstance(m, dict)]
    if not movements:
        raise RuntimeError("outline returned no movements")
    for m in movements:
        if len(bodies) >= max_segments:
            break
        expand(m)
    refills = 0
    while total() < target_words * fill_ratio and len(bodies) < max_segments and refills < _MAX_REFILLS:
        refills += 1
        extra = "\n\nAlready covered (do NOT repeat these; propose NEW, distinct sub-areas): " + "; ".join(covered) + "."
        fresh = [m for m in (outline(extra).get("movements") or []) if isinstance(m, dict)
                 and str(m.get("heading") or "").strip().lower() not in seen]
        if not fresh:
            break
        before = len(bodies)
        for m in fresh:
            if len(bodies) >= max_segments or total() >= target_words:
                break
            expand(m)
        if len(bodies) == before:
            break
    if not bodies:
        raise RuntimeError("script generation produced no narration")
    body = "\n\n".join(bodies)
    words = len(body.split())
    if words > target_words * max_words_ratio:
        raise ScriptTooLong(f"{words} words > {max_words_ratio}x target {target_words}")
    meta = llm.generate_json(_fill(METADATA_PROMPT, topic=topic, hours=max(1, round(minutes / 60)), opening=body[:600]), max_tokens=600)
    return Script(title=str(meta.get("title") or f"Calm Facts About {topic} to Fall Asleep To")[:100],
                  description=str(meta.get("description") or ""), tags=[str(t) for t in meta.get("tags") or []][:15],
                  body=body, word_count=words, movements=len(bodies),
                  image_queries=[str(q) for q in first.get("image_search_queries") or []])
```

(The two `<verbatim …>` strings are copied exactly from `apps/worker/storyfactory/db/seed.py` lines 193–258 during implementation.)

- [ ] **Step 4–5:** PASS; commit `feat(sof): script generator (ported) with metadata`

---

### Task 6: `tts.py`

**Files:** `sof/tts.py`, `tests/test_tts.py`

- [ ] **Step 1: Failing tests**

```python
import httpx
from sof.tts import chunk_text, synthesize, concat_cmd, loudnorm_cmd, estimate_cost_usd


def test_chunking_respects_limit_and_sentences():
    text = ("This is a sentence. " * 300).strip()
    chunks = chunk_text(text, 4000)
    assert all(len(c) <= 4000 for c in chunks) and "".join(chunks).replace(" ", "") == text.replace(" ", "")
    assert all(c.rstrip().endswith(".") for c in chunks)


def test_synthesize_writes_chunks_and_skips_existing(tmp_path):
    calls = []
    def handler(req):
        calls.append(req); return httpx.Response(200, content=b"ID3mp3")
    t = httpx.MockTransport(handler)
    paths = synthesize(["one.", "two."], str(tmp_path), api_key="sk", model="tts-1", voice="onyx", speed=0.9, transport=t)
    assert len(paths) == 2 and all((tmp_path / f"{i:04d}.mp3").exists() for i in range(2)) and len(calls) == 2
    synthesize(["one.", "two."], str(tmp_path), api_key="sk", model="tts-1", voice="onyx", speed=0.9, transport=t)
    assert len(calls) == 2


def test_commands_and_cost():
    assert concat_cmd("l.txt", "o.mp3")[:4] == ["ffmpeg", "-y", "-f", "concat"]
    assert "loudnorm=I=-18" in " ".join(loudnorm_cmd("i.mp3", "o.mp3"))
    assert abs(estimate_cost_usd(150_000, "tts-1") - 2.25) < 0.01
```

- [ ] **Step 2–3:** Implement:

```python
# sof/tts.py
from __future__ import annotations
import logging, os, re, subprocess, time
from pathlib import Path
import httpx

log = logging.getLogger("sof.tts")
URL = "https://api.openai.com/v1/audio/speech"
PRICE_PER_M = {"tts-1": 15.0, "tts-1-hd": 30.0}
_SENT = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, limit: int) -> list[str]:
    chunks, cur = [], ""
    for sent in _SENT.split(text.replace("\n", " ").strip()):
        if not sent:
            continue
        if len(cur) + len(sent) + 1 > limit and cur:
            chunks.append(cur.strip()); cur = ""
        while len(sent) > limit:  # pathological run-on sentence
            chunks.append(sent[:limit]); sent = sent[limit:]
        cur = f"{cur} {sent}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def synthesize(chunks: list[str], out_dir: str, *, api_key: str, model: str, voice: str, speed: float,
               transport=None, retries: int = 4) -> list[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    paths = []
    with httpx.Client(timeout=120.0, transport=transport, headers={"Authorization": f"Bearer {api_key}"}) as c:
        for i, chunk in enumerate(chunks):
            p = Path(out_dir) / f"{i:04d}.mp3"
            paths.append(str(p))
            if p.exists() and p.stat().st_size > 0:
                continue
            delay = 2.0
            for attempt in range(retries + 1):
                r = c.post(URL, json={"model": model, "voice": voice, "speed": speed, "input": chunk, "response_format": "mp3"})
                if r.status_code == 200 and r.content:
                    p.write_bytes(r.content); break
                if attempt == retries or r.status_code not in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"TTS chunk {i} failed: HTTP {r.status_code} {r.text[:200]}")
                time.sleep(delay); delay *= 2
            log.info("tts chunk %d/%d", i + 1, len(chunks))
    return paths


def concat_cmd(list_file: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", out]


def loudnorm_cmd(inp: str, out: str) -> list[str]:
    return ["ffmpeg", "-y", "-i", inp, "-af", "loudnorm=I=-18:TP=-2:LRA=11", "-ar", "44100", "-b:a", "128k", out]


def assemble(paths: list[str], out_dir: str) -> str:
    list_file = os.path.join(out_dir, "concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    raw, final = os.path.join(out_dir, "narration_raw.mp3"), os.path.join(out_dir, "narration.mp3")
    for cmd in (concat_cmd(list_file, raw), loudnorm_cmd(raw, final)):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {r.stderr[-400:]}")
    return final


def estimate_cost_usd(chars: int, model: str) -> float:
    return chars / 1_000_000 * PRICE_PER_M.get(model, 15.0)


def duration_seconds(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, timeout=60)
    return float(r.stdout.strip() or 0)
```

- [ ] **Step 4–5:** PASS; commit `feat(sof): tts chunking and synthesis`

---

### Task 7: `images.py`

**Files:** `sof/images.py`, `tests/test_images.py`

- [ ] **Step 1: Failing tests**

```python
import httpx
from sof.images import collect_urls, download_all, GENERIC_QUERIES


def _transport(pexels_per_page=3, pixabay_hits=3):
    def handler(req):
        if "pexels" in req.url.host:
            page = int(req.url.params.get("page", "1"))
            q = req.url.params.get("query")
            return httpx.Response(200, json={"photos": [{"id": f"{q}-{page}-{i}", "src": {"large2x": f"https://px/{q}/{page}/{i}.jpg"},
                                                         "photographer": "A", "url": "https://pexels.com/p"} for i in range(pexels_per_page)]})
        if "pixabay" in req.url.host:
            q = req.url.params.get("q")
            return httpx.Response(200, json={"hits": [{"id": i, "largeImageURL": f"https://pb/{q}/{i}.jpg", "user": "B", "pageURL": "https://pixabay.com/p"} for i in range(pixabay_hits)]})
        return httpx.Response(200, content=b"\xff\xd8" + b"0" * 60_000)
    return httpx.MockTransport(handler)


def test_collect_pads_with_generic_until_target():
    urls = collect_urls(["egypt", "nile"], target=20, pexels_key="k", pixabay_key="k", transport=_transport(), max_pages=1)
    assert len(urls) == 20 and len({u["url"] for u in urls}) == 20
    assert any(any(g.split()[0] in u["url"] for g in GENERIC_QUERIES) for u in urls)


def test_download_filters_small_and_dedupes(tmp_path):
    def handler(req):
        return httpx.Response(200, content=(b"\xff\xd8" + b"0" * 60_000) if "big" in str(req.url) else b"tiny")
    urls = [{"url": "https://x/big1.jpg", "credit": "A"}, {"url": "https://x/tiny.jpg", "credit": "B"}, {"url": "https://x/big1.jpg", "credit": "A"}]
    paths = download_all(urls, str(tmp_path), transport=httpx.MockTransport(handler), verify_image=lambda p: True)
    assert len(paths) == 1 and (tmp_path / "credits.json").exists()
```

- [ ] **Step 2–3:** Implement:

```python
# sof/images.py
from __future__ import annotations
import hashlib, json, logging, os
from pathlib import Path
import httpx

log = logging.getLogger("sof.images")
GENERIC_QUERIES = ["night sky stars", "calm ocean", "forest fog", "desert dunes", "mountain lake dawn"]
MIN_BYTES = 50_000


def _pexels(c, key, q, page):
    r = c.get("https://api.pexels.com/v1/search", headers={"Authorization": key},
              params={"query": q, "orientation": "landscape", "size": "large", "per_page": 80, "page": page})
    if r.status_code != 200:
        return []
    return [{"url": p["src"]["large2x"], "credit": f"{p.get('photographer','')} / Pexels {p.get('url','')}"}
            for p in r.json().get("photos") or [] if (p.get("src") or {}).get("large2x")]


def _pixabay(c, key, q, page):
    r = c.get("https://pixabay.com/api/", params={"key": key, "q": q, "image_type": "photo", "orientation": "horizontal",
                                                  "min_width": 1920, "per_page": 100, "page": page, "safesearch": "true"})
    if r.status_code != 200:
        return []
    return [{"url": h["largeImageURL"], "credit": f"{h.get('user','')} / Pixabay {h.get('pageURL','')}"}
            for h in r.json().get("hits") or [] if h.get("largeImageURL")]


def collect_urls(queries: list[str], *, target: int, pexels_key: str, pixabay_key: str, transport=None, max_pages: int = 3) -> list[dict]:
    seen, out = set(), []

    def add(items):
        for it in items:
            if it["url"] not in seen:
                seen.add(it["url"]); out.append(it)

    with httpx.Client(timeout=30.0, transport=transport) as c:
        for pool in (list(queries), GENERIC_QUERIES):
            for page in range(1, max_pages + 1):
                for q in pool:
                    if len(out) >= target:
                        return out[:target]
                    if pexels_key:
                        add(_pexels(c, pexels_key, q, page))
                    if len(out) < target and pixabay_key:
                        add(_pixabay(c, pixabay_key, q, page))
            if len(out) >= target:
                break
    return out[:target]


def _pillow_ok(path: str) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.verify()
        return True
    except Exception:  # noqa: BLE001
        return False


def download_all(urls: list[dict], out_dir: str, *, transport=None, verify_image=_pillow_ok) -> list[str]:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    paths, credits, seen_hash = [], [], set()
    with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True) as c:
        for i, it in enumerate(urls):
            p = Path(out_dir) / f"{i:03d}.jpg"
            try:
                if not p.exists():
                    r = c.get(it["url"])
                    if r.status_code != 200 or len(r.content) < MIN_BYTES:
                        continue
                    p.write_bytes(r.content)
            except httpx.HTTPError as exc:
                log.warning("image %s failed: %s", it["url"], exc); continue
            h = hashlib.sha1(p.read_bytes()).hexdigest()
            if h in seen_hash or not verify_image(str(p)):
                p.unlink(missing_ok=True); continue
            seen_hash.add(h); paths.append(str(p)); credits.append({"file": p.name, **it})
    with open(Path(out_dir) / "credits.json", "w", encoding="utf-8") as f:
        json.dump(credits, f, indent=2)
    return paths
```

- [ ] **Step 4–5:** PASS; commit `feat(sof): image pool`

---

### Task 8: `render.py`

**Files:** `sof/render.py`, `tests/test_render.py`

- [ ] **Step 1: Failing tests**

```python
from sof.render import ken_burns_cmd, xfade_chain_cmd, loop_assembly_cmd, plan_batches

def test_cmd_builders():
    kb = " ".join(ken_burns_cmd("a.jpg", "a.mp4", dwell=20, crossfade=1.5, width=1920, height=1080, fps=24))
    assert "zoompan" in kb and "-t 21.5" in kb and "1920x1080" in kb
    xf = " ".join(xfade_chain_cmd(["a.mp4", "b.mp4", "c.mp4"], [21.5, 21.5, 21.5], "r.mp4", crossfade=1.5, fps=24))
    assert xf.count("xfade=") == 2 and "offset=20" in xf and "offset=40" in xf
    la = " ".join(loop_assembly_cmd("reel.mp4", "n.mp3", "f.mp4", audio_duration=10800.0))
    assert "-stream_loop -1" in la and "-c:v copy" in la and "-t 10800.0" in la

def test_plan_batches():
    assert plan_batches(45, 20) == [(0, 20), (20, 40), (40, 45)]
```

- [ ] **Step 2–3:** Port the three builders from `slideshow_renderer.py` (crossfade/dwell semantics identical), plus:

```python
def plan_batches(n: int, size: int) -> list[tuple[int, int]]:
    return [(s, min(s + size, n)) for s in range(0, n, size)]


def build_reel(image_paths, tmp_dir, *, dwell, crossfade, width, height, fps, batch, workers=None) -> str:
    """Ken Burns clip per image (ThreadPool), batched xfade partials, final xfade; concat fallback. Returns reel path."""
    ...  # orchestration identical to StoryFactory build_slideshow_reel / _assemble_reel_xfade / _concat_clips


def assemble(reel: str, narration: str, out: str, *, audio_duration: float, fps: int) -> str:
    run(loop_assembly_cmd(reel, narration, out, audio_duration=audio_duration, fps=fps), timeout=3600); return out


def thumbnail(image_path: str, title: str, out: str) -> str:
    """Pillow: darken the image 35%, draw the title (DejaVuSerif, ≤ 2 lines, 96 px) bottom-left. 1280×720 JPEG."""
```

(`run()` = `subprocess.run(..., capture_output=True, text=True)` raising `RuntimeError(stderr[-400:])` on non-zero.)

- [ ] **Step 4–5:** PASS; commit `feat(sof): renderer (ported reel-loop design)`

---

### Task 9: `upload.py`

**Files:** `sof/upload.py`, `tests/test_upload.py`

- [ ] **Step 1: Failing test** — build the request body and assert fields; the API client is injected:

```python
from sof.upload import video_body, upload_video

def test_video_body():
    b = video_body(title="T", description="D", tags=["a"], privacy="public")
    assert b["snippet"]["categoryId"] == "24" and b["status"]["privacyStatus"] == "public"
    assert b["status"]["selfDeclaredMadeForKids"] is False and b["status"]["containsSyntheticMedia"] is True

class FakeYT:
    def __init__(self): self.thumb = None
    def videos(self): return self
    def thumbnails(self): return self
    def insert(self, **kw): self.kw = kw; return self
    def set(self, **kw): self.thumb = kw; return self
    def next_chunk(self): return None, {"id": "vid123"}
    def execute(self): return {}

def test_upload_returns_id(tmp_path):
    f = tmp_path / "f.mp4"; f.write_bytes(b"x"); t = tmp_path / "t.jpg"; t.write_bytes(b"y")
    assert upload_video(FakeYT(), str(f), video_body(title="T", description="D", tags=[], privacy="public"), str(t)) == "vid123"
```

- [ ] **Step 2–3:** Implement with `googleapiclient.http.MediaFileUpload(path, chunksize=8*1024*1024, resumable=True)`, retry `next_chunk` up to 10 times on `HttpError` 5xx; `build_client(cfg)` constructs `google.oauth2.credentials.Credentials(None, refresh_token=…, token_uri="https://oauth2.googleapis.com/token", client_id=…, client_secret=…)` and `build("youtube", "v3", credentials=…)`; `health(yt)` calls `channels().list(part="snippet", mine=True)` and returns the channel title (used by the pipeline's preflight so a dead token fails *before* 3 hours of TTS are paid for).

- [ ] **Step 4–5:** PASS; commit `feat(sof): youtube upload`

---

### Task 10: `pipeline.py` + workflow

**Files:** `sof/pipeline.py`, `tests/test_pipeline.py`, `.github/workflows/sleep-daily.yml`

- [ ] **Step 1: Failing test** — orchestrate with every stage monkeypatched to cheap fakes; assert order, skip-if-exists, ledger write only after upload, `--skip-upload` writes no ledger, preflight failure stops before TTS.

```python
def test_pipeline_order_and_ledger(tmp_path, monkeypatch): ...  # fakes record calls; assert ["preflight","script","tts","images","render","upload","ledger"]
def test_skip_upload_writes_no_ledger(tmp_path, monkeypatch): ...
def test_dead_token_fails_before_tts(tmp_path, monkeypatch): ...  # health raises → tts fake never called, exit code 2
```

- [ ] **Step 2–3:** Implement `run(cfg) -> int`:

1. `run_id = f"{date}-{slug(topic)}"`, `work = Path(cfg.work_dir)/run_id`.
2. Preflight: `upload.health(build_client(cfg))` unless `skip_upload` (prints channel title) — fail code 2 on error.
3. Topic: `pick_next(load_topics("topics.yml"), load_ledger("ledger.json"), forced=cfg.topic)`.
4. Script → `work/script.json` (skip if exists); log words/movements.
5. TTS → `work/tts/*.mp3` → `work/narration.mp3`; log duration + cost estimate.
6. Images: queries = topic.queries + script.image_queries → `work/images/`; fail (code 3) if < `images_fail_below`.
7. Render: `build_reel` → `work/reel.mp4`; `assemble` → `work/final.mp4`; `thumbnail` → `work/thumb.jpg`.
8. Upload (unless skipped) → `video_id`; `append_entry("ledger.json", {...})`; `git_commit_and_push` when `GITHUB_ACTIONS` env is set.
9. Print a one-line summary; return 0.

Workflow:

```yaml
name: sleep-on-facts daily
on:
  # schedule:                      # ENABLE after the 3-minute smoke run passes
  #   - cron: "0 18 * * *"         # 02:00 Asia/Singapore
  workflow_dispatch:
    inputs:
      minutes: { description: "Target minutes (3 = smoke, unlisted)", default: "180" }
      topic:   { description: "Force a topic (optional)", default: "" }
      privacy: { description: "public | unlisted | private", default: "public" }
      skip_upload: { description: "true to render only", default: "false" }
concurrency: { group: sleep-daily, cancel-in-progress: false }
permissions: { contents: write }
jobs:
  render:
    runs-on: ubuntu-latest
    timeout-minutes: 300
    defaults: { run: { working-directory: cloud/sleep-on-facts } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip, cache-dependency-path: cloud/sleep-on-facts/requirements.txt }
      - run: sudo apt-get update && sudo apt-get install -y ffmpeg fonts-dejavu-core
      - run: pip install -r requirements.txt
      - run: python -m pytest -q
      - name: Run pipeline
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          PEXELS_API_KEY: ${{ secrets.PEXELS_API_KEY }}
          PIXABAY_API_KEY: ${{ secrets.PIXABAY_API_KEY }}
          YT_CLIENT_ID: ${{ secrets.YT_CLIENT_ID }}
          YT_CLIENT_SECRET: ${{ secrets.YT_CLIENT_SECRET }}
          YT_REFRESH_TOKEN: ${{ secrets.YT_REFRESH_TOKEN }}
        run: |
          ARGS="--minutes ${{ github.event.inputs.minutes || '180' }} --privacy ${{ github.event.inputs.privacy || 'public' }}"
          [ -n "${{ github.event.inputs.topic }}" ] && ARGS="$ARGS --topic \"${{ github.event.inputs.topic }}\""
          [ "${{ github.event.inputs.skip_upload }}" = "true" ] && ARGS="$ARGS --skip-upload"
          eval python run.py $ARGS
      - name: Upload run artifacts (logs, thumbnail, credits)
        if: always()
        uses: actions/upload-artifact@v4
        with: { name: run-${{ github.run_id }}, path: "cloud/sleep-on-facts/work/**/{script.json,thumb.jpg,credits.json}", if-no-files-found: ignore }
```

- [ ] **Step 4–5:** PASS; commit `feat(sof): pipeline orchestration + Actions workflow`

---

### Task 11: Docs, StoryFactory channel pause, merge

- [ ] `cloud/sleep-on-facts/README.md`: what it is, how to dispatch (`gh workflow run sleep-daily.yml -f minutes=3`), secrets list, cost per video, how to enable the cron, how topics/ledger work.
- [ ] Repo `README.md`: one paragraph + link under a "Cloud pipelines" heading.
- [ ] `docs/multi-channel.md` Sleep On Facts section: note that production now runs in the cloud; the desktop `sleep_facts` mode is legacy.
- [ ] Pause the desktop channel: `python -m storyfactory channel update --channel sleep-on-facts --status paused` on the PC.
- [ ] Full test run (`python -m pytest -q` in `cloud/sleep-on-facts`), commit, merge `feat/sleep-cloud` → `main`, push.
- [ ] After Gian re-authorizes YouTube: re-run `_cowork_tmp/set_gh_secrets.py` to refresh `YT_REFRESH_TOKEN`; dispatch the 3-minute smoke; review; uncomment the cron; dispatch the first 180-minute run.

---

## Self-review

- Spec §4.1–4.9 → Tasks 3,5,6,7,8,9,7,1/10,10. §5 error table: capacity retries (Task 2, 6), too long/short (Task 5), images < 30 (Task 10 code 3), ffmpeg stderr (Task 8 `run()`), upload failure leaves ledger untouched (Task 10 order), disk (sizes noted in spec; nothing to code). §6: unit tests per task; smoke via dispatch; cron disabled by default (Task 10 yaml). §7 rollout → Task 11. ✔
- Added beyond spec: a YouTube **preflight** health check before spending on TTS (today's dead-token finding makes this necessary).
- Names consistent: `Config.from_env`, `LLM.generate_json`, `pick_next`, `load_ledger/append_entry/git_commit_and_push`, `wikipedia_hints`, `generate_script→Script`, `chunk_text/synthesize/assemble/duration_seconds/estimate_cost_usd`, `collect_urls/download_all`, `ken_burns_cmd/xfade_chain_cmd/loop_assembly_cmd/plan_batches/build_reel/assemble/thumbnail`, `video_body/upload_video/build_client/health`, `run`. Note `render.assemble` vs `tts.assemble` — both exist, always referenced module-qualified.
