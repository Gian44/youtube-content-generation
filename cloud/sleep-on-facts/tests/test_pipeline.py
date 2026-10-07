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
    from sof.research import Corpus, Chunk
    monkeypatch.setattr(pipeline.research, "build_corpus", lambda name, **kw: (calls.append(f"research:{name}"),
        Corpus(topic=name, title=name, url="u", lead="lead", sections=["A"], chunks=[Chunk(name, "fact " * 30)], sources=[name]))[1])
    monkeypatch.setattr(pipeline.topicgen, "choose", lambda llm, **kw: (calls.append("topicgen"),
        __import__("sof.topicgen", fromlist=["Pick"]).Pick(topic="Honeybees", wiki_title="Honey bee", category="nature"))[1])
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
    def mix(narration, out, **kw):
        calls.append("music"); Path(out).write_bytes(b"mix"); return out
    monkeypatch.setattr(pipeline.music, "mix", mix)
    monkeypatch.setattr(pipeline.images, "collect_urls", lambda qs, **kw: (calls.append("images"), [{"url": f"u{i}", "credit": "c"} for i in range(image_count)])[1])
    def dl(urls, out_dir, **kw):
        Path(out_dir).mkdir(parents=True, exist_ok=True); out = []
        for i, _ in enumerate(urls):
            p = Path(out_dir) / f"{i:03d}.jpg"; p.write_bytes(b"img"); out.append(str(p))
        return out
    monkeypatch.setattr(pipeline.images, "download_all", dl)
    monkeypatch.setattr(pipeline.images, "verify_relevance", lambda paths, **kw: (calls.append("vision"), paths)[1])
    def build_reel(imgs, tmp, **kw):
        calls.append("render"); Path(tmp).mkdir(parents=True, exist_ok=True)
        p = Path(tmp) / "reel.mp4"; p.write_bytes(b"reel"); return str(p)
    monkeypatch.setattr(pipeline.render, "build_reel", build_reel)
    def assemble(reel, narration, out, **kw):
        calls.append(f"assemble:{Path(narration).name}"); Path(out).write_bytes(b"final"); return out
    monkeypatch.setattr(pipeline.render, "assemble", assemble)
    monkeypatch.setattr(pipeline.thumbnail, "make", lambda imgs, out, **kw: (Path(out).write_bytes(b"t"), out)[1])
    def up(yt, path, body, thumb, **kw):
        calls.append("upload"); return "vid123"
    monkeypatch.setattr(pipeline.upload, "upload_video", up)
    monkeypatch.setattr(pipeline.ledger, "git_commit_and_push", lambda *a, **k: calls.append("git"))


def test_pipeline_order_and_ledger(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    assert pipeline.run(_cfg(tmp_path)) == 0
    assert calls == ["preflight", "research:Whales", "script", "tts", "music", "images", "vision", "render", "assemble:soundtrack.mp3", "upload"]
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


def test_image_target_scales_with_narration(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    seen = {}
    monkeypatch.setattr(pipeline.images, "collect_urls", lambda qs, **kw: (seen.update(kw), [{"url": f"u{i}", "credit": "c"} for i in range(kw["target"])])[1])
    cfg = _cfg(tmp_path, minutes=3)  # fake narration is 120 s → 120//20 + 6 = 12 images
    cfg.images_fail_below, cfg.images_min = 1, 100
    pipeline.run(cfg)
    assert seen["target"] == int(12 * 1.4) + 5   # 12 needed (120 s // 20 + 6), fetched with a 40 % margin


def test_music_can_be_disabled(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    assert pipeline.run(_cfg(tmp_path, music_enabled=False)) == 0
    assert "music" not in calls and "assemble:narration.mp3" in calls


def test_generated_topic_when_seeds_exhausted(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    (tmp_path / "ledger.json").write_text(json.dumps([{"date": "2026-10-07", "topic": "Whales", "video_id": "x"}]))
    cfg = _cfg(tmp_path)
    (tmp_path / "ledger.json").write_text(json.dumps([{"date": "2026-10-07", "topic": "Whales", "video_id": "x"}]))
    assert pipeline.run(cfg) == 0
    assert calls[:3] == ["preflight", "topicgen", "research:Honey bee"]
    rows = json.loads((tmp_path / "ledger.json").read_text())
    assert rows[-1]["topic"] == "Honeybees" and rows[-1]["category"] == "nature" and rows[-1]["wiki_title"] == "Honey bee"
    assert (tmp_path / "work" / "2026-10-07-honeybees").exists() or any(p.name.endswith("-honeybees") for p in (tmp_path / "work").iterdir())
