import json
import os
from pathlib import Path

import pytest

from sof import pipeline
from sof.config import Config
from sof.script import Script

TOPICS = "topics:\n  - name: Whales\n    queries: [whale ocean]\n"


def _cfg(tmp_path, **kw):
    (tmp_path / "topics.yml").write_text(TOPICS)
    (tmp_path / "ledger.json").write_text("[]")
    return Config(minutes=kw.pop("minutes", 180), work_dir=str(tmp_path / "work"), topics_path=str(tmp_path / "topics.yml"),
                  ledger_path=str(tmp_path / "ledger.json"), openai_api_key="sk", yt_client_id="a", yt_client_secret="b",
                  yt_refresh_token="c", images_min=2, images_fail_below=1, **kw)


def _fakes(monkeypatch, calls, *, health_ok=True, image_count=3):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(pipeline.upload, "build_client", lambda cfg: "YT")
    def health(yt):
        calls.append("preflight")
        if not health_ok:
            raise RuntimeError("invalid_grant")
        return "Sleep On Facts"
    monkeypatch.setattr(pipeline.upload, "health", health)
    monkeypatch.setattr(pipeline, "LLM", lambda *a, **k: "LLM")
    monkeypatch.setattr(pipeline.research, "wikipedia_hints", lambda t: "")
    def gen(llm, **kw):
        calls.append("script")
        return Script(title="T", description="D", tags=["t"], body="word " * 300, word_count=300, movements=2, image_queries=["q"])
    monkeypatch.setattr(pipeline.script, "generate_script", gen)
    def synth(chunks, out_dir, **kw):
        calls.append("tts"); Path(out_dir).mkdir(parents=True, exist_ok=True)
        p = Path(out_dir) / "0000.mp3"; p.write_bytes(b"x"); return [str(p)]
    monkeypatch.setattr(pipeline.tts, "synthesize", synth)
    def assemble_tts(paths, out_dir):
        p = Path(out_dir) / "narration.mp3"; p.write_bytes(b"audio"); return str(p)
    monkeypatch.setattr(pipeline.tts, "assemble", assemble_tts)
    monkeypatch.setattr(pipeline.tts, "duration_seconds", lambda p: 120.0)
    monkeypatch.setattr(pipeline.images, "collect_urls", lambda qs, **kw: (calls.append("images"), [{"url": f"u{i}", "credit": "c"} for i in range(image_count)])[1])
    def dl(urls, out_dir, **kw):
        Path(out_dir).mkdir(parents=True, exist_ok=True); out = []
        for i, _ in enumerate(urls):
            p = Path(out_dir) / f"{i:03d}.jpg"; p.write_bytes(b"img"); out.append(str(p))
        return out
    monkeypatch.setattr(pipeline.images, "download_all", dl)
    def build_reel(imgs, tmp, **kw):
        calls.append("render"); Path(tmp).mkdir(parents=True, exist_ok=True)
        p = Path(tmp) / "reel.mp4"; p.write_bytes(b"reel"); return str(p)
    monkeypatch.setattr(pipeline.render, "build_reel", build_reel)
    def assemble(reel, narration, out, **kw):
        Path(out).write_bytes(b"final"); return out
    monkeypatch.setattr(pipeline.render, "assemble", assemble)
    monkeypatch.setattr(pipeline.render, "thumbnail", lambda img, title, out: (Path(out).write_bytes(b"t"), out)[1])
    def up(yt, path, body, thumb, **kw):
        calls.append("upload"); return "vid123"
    monkeypatch.setattr(pipeline.upload, "upload_video", up)
    monkeypatch.setattr(pipeline.ledger, "git_commit_and_push", lambda *a, **k: calls.append("git"))


def test_pipeline_order_and_ledger(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    assert pipeline.run(_cfg(tmp_path)) == 0
    assert calls == ["preflight", "script", "tts", "images", "render", "upload"]
    rows = json.loads((tmp_path / "ledger.json").read_text())
    assert rows[0]["video_id"] == "vid123" and rows[0]["topic"] == "Whales" and rows[0]["duration_seconds"] == 120


def test_rerun_skips_completed_stages(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    cfg = _cfg(tmp_path)
    pipeline.run(cfg)
    calls.clear()
    (tmp_path / "ledger.json").write_text("[]")  # pretend upload failed last time; artifacts remain
    pipeline.run(cfg)
    assert calls == ["preflight", "upload"]


def test_skip_upload_writes_no_ledger(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    assert pipeline.run(_cfg(tmp_path, skip_upload=True)) == 0
    assert "preflight" not in calls and "upload" not in calls
    assert json.loads((tmp_path / "ledger.json").read_text()) == []


def test_dead_token_fails_before_tts(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls, health_ok=False)
    assert pipeline.run(_cfg(tmp_path)) == 2
    assert calls == ["preflight"]


def test_too_few_images_fails(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls, image_count=0)
    assert pipeline.run(_cfg(tmp_path)) == 3


def test_smoke_run_uploads_unlisted_without_ledger(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    cfg = _cfg(tmp_path, minutes=3)
    cfg.privacy = "unlisted"
    assert pipeline.run(cfg) == 0 and "upload" in calls
    assert json.loads((tmp_path / "ledger.json").read_text()) == []


def test_ledger_pushed_in_actions(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert pipeline.run(_cfg(tmp_path)) == 0
    assert calls[-1] == "git"
