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


def _fakes(monkeypatch, calls, *, health_ok=True, image_count=3, accepted=("uploaded", ""), earlier=None):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(pipeline.upload, "upload_state", lambda yt, ids: {i: (earlier or {}).get(i, ("processed", "")) for i in ids})
    monkeypatch.setattr(pipeline.upload, "wait_until_accepted", lambda yt, vid, **kw: (calls.append("status"), accepted)[1])
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
    monkeypatch.setattr(pipeline.images, "gather_urls", lambda **kw: (calls.append("images"),
        [{"url": f"u{i}", "credit": "c", "source": "commons"} for i in range(image_count)])[1])
    def dl(urls, out_dir, *, start=0, want=None, **kw):
        Path(out_dir).mkdir(parents=True, exist_ok=True); out = []
        end = len(urls) if not want else min(len(urls), start + want)
        for i in range(start, end):
            p = Path(out_dir) / f"{i:03d}.jpg"; p.write_bytes(b"img"); out.append(str(p))
        cf = Path(out_dir) / "credits.json"
        rows = json.loads(cf.read_text()) if cf.exists() else []
        rows += [{"file": Path(o).name, **urls[int(Path(o).stem)]} for o in out]
        cf.write_text(json.dumps(rows))
        return out, end
    monkeypatch.setattr(pipeline.images, "download_range", dl)
    monkeypatch.setattr(pipeline.images, "classify", lambda paths, **kw: (calls.append("vision"), {p: "subject" for p in paths})[1])
    def build_reel(imgs, tmp, **kw):
        calls.append("render"); Path(tmp).mkdir(parents=True, exist_ok=True)
        p = Path(tmp) / "reel.mp4"; p.write_bytes(b"reel"); return str(p)
    monkeypatch.setattr(pipeline.render, "build_reel", build_reel)
    def assemble(reel, narration, out, **kw):
        calls.append(f"assemble:{Path(narration).name}"); Path(out).write_bytes(b"final"); return out
    monkeypatch.setattr(pipeline.render, "assemble", assemble)
    monkeypatch.setattr(pipeline.render, "sample_frames", lambda video, out_dir, times, **kw: calls.append("frames") or [])
    monkeypatch.setattr(pipeline.thumbnail, "make", lambda imgs, out, **kw: (Path(out).write_bytes(b"t"), out)[1])
    def up(yt, path, body, thumb, **kw):
        calls.append("upload"); uploads.append(body); return "vid123"
    uploads = []
    monkeypatch.setattr(pipeline, "_UPLOADS", uploads, raising=False)
    monkeypatch.setattr(pipeline.upload, "upload_video", up)
    monkeypatch.setattr(pipeline.upload, "ensure_playlist", lambda yt, title, desc="": (calls.append(f"playlist:{title}"), "PL")[1])
    monkeypatch.setattr(pipeline.upload, "add_to_playlist", lambda yt, pl, vid: None)
    monkeypatch.setattr(pipeline.ledger, "git_commit_and_push", lambda *a, **k: calls.append("git"))


def test_pipeline_order_and_ledger(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    assert pipeline.run(_cfg(tmp_path)) == 0
    assert calls == ["preflight", "research:Whales", "script", "tts", "music", "images", "vision", "render", "assemble:soundtrack.mp3",
                     "frames", "upload", "status", "playlist:Calm Facts to Fall Asleep To"]
    rows = json.loads((tmp_path / "ledger.json").read_text())
    assert rows[0]["video_id"] == "vid123" and rows[0]["topic"] == "Whales" and rows[0]["duration_seconds"] == 120
    assert rows[0]["title"] == "1 Hour of Calm Facts to Fall Asleep To | Whales" and rows[0]["image_sources"]["commons"] == 3
    assert rows[0]["title_variant"] == 0 and "credits" not in rows[0]
    body = pipeline._UPLOADS[0]
    desc = body["snippet"]["description"]
    assert body["snippet"]["categoryId"] == "27" and desc.startswith("Fall asleep to 1 hour of calm facts: Whales.")
    assert "github" not in desc.lower() and "AI-generated" not in desc
    assert "Licences: https://creativecommons.org/licenses/" in desc


def test_rerun_skips_completed_stages(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    cfg = _cfg(tmp_path)
    pipeline.run(cfg)
    calls.clear()
    (tmp_path / "ledger.json").write_text("[]")  # pretend upload failed last time; artifacts remain
    pipeline.run(cfg)
    assert calls == ["preflight", "frames", "upload", "status", "playlist:Calm Facts to Fall Asleep To"]


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
    assert not any(c.startswith("playlist") for c in calls)                      # tests never enter public playlists
    assert pipeline._UPLOADS[0]["status"] == {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False}


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
    monkeypatch.setattr(pipeline.images, "gather_urls", lambda **kw: (seen.update(kw), [{"url": f"u{i}", "credit": "c"} for i in range(kw["target"])])[1])
    cfg = _cfg(tmp_path, minutes=3)  # fake narration is 120 s → 120//20 + 6 = 12 images
    cfg.images_fail_below, cfg.images_min = 1, 100
    pipeline.run(cfg)
    assert seen["target"] == 12                  # 120 s // 20 + 6 shown; margins are added inside images.*


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


def test_scheduled_publish_and_chapters(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    def gen(llm, **kw):
        calls.append("script")
        secs = [{"heading": f"Part {i}", "body": "Calm words. " * 50} for i in range(4)]
        return Script(title="T", description="D", tags=["t"], body="x", word_count=300, movements=4, sections=secs,
                      blurb="Gentle.", footer="Narration is AI-generated; facts are drawn from the public sources below.")
    monkeypatch.setattr(pipeline.script, "generate_script", gen)
    def synth(chunks, out_dir, **kw):
        Path(out_dir).mkdir(parents=True, exist_ok=True); out = []
        for i, _ in enumerate(chunks):
            p = Path(out_dir) / f"{i:04d}.mp3"; p.write_bytes(b"x"); out.append(str(p))
        return out
    monkeypatch.setattr(pipeline.tts, "synthesize", synth)
    monkeypatch.setattr(pipeline.tts, "duration_seconds", lambda p: 3 * 3600.0 if p.endswith("narration.mp3") else 300.0)
    from datetime import datetime as real_dt
    class FakeDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt(2026, 10, 11, 21, 0, tzinfo=tz)
    monkeypatch.setattr(pipeline, "datetime", FakeDT)
    assert pipeline.run(_cfg(tmp_path, tts_chunk_chars=200)) == 0
    body = pipeline._UPLOADS[0]
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"] == "2026-10-12T00:30:00Z"
    d = body["snippet"]["description"]
    assert body["snippet"]["title"] == "3 Hours of Calm Facts to Fall Asleep To | Whales"
    assert "Chapters\n0:00 Part 0\n" in d and "Narration is AI-generated" in d
    rows = json.loads((tmp_path / "ledger.json").read_text())
    assert rows[0]["publish_at"] == "2026-10-12T00:30:00Z"


def test_story_shape_rotates_with_the_ledger(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    seen = []
    def gen(llm, **kw):
        seen.append(kw["story_format"])
        return Script(title="T", description="D", tags=["t"], body="word " * 300, word_count=300, movements=2,
                      story_format=kw["story_format"])
    monkeypatch.setattr(pipeline.script, "generate_script", gen)
    cfg = _cfg(tmp_path)
    (tmp_path / "ledger.json").write_text(json.dumps([{"topic": f"T{i}", "video_id": str(i)} for i in range(5)]))
    pipeline.run(cfg)
    from sof.prompts import STORY_ORDER
    assert seen == [STORY_ORDER[5 % len(STORY_ORDER)]]
    assert json.loads((tmp_path / "ledger.json").read_text())[-1]["story_format"] == seen[0]


def test_rejected_upload_fails_without_ledger(tmp_path, monkeypatch, capsys):
    calls = []
    _fakes(monkeypatch, calls, accepted=("rejected", "length"))
    assert pipeline.run(_cfg(tmp_path)) == 1
    assert json.loads((tmp_path / "ledger.json").read_text()) == []              # topic stays unused → made again
    assert not any(c.startswith("playlist") for c in calls)
    assert "did not accept the upload: rejected (length)" in capsys.readouterr().out


def test_vanished_upload_fails(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls, accepted=("missing", "video disappeared after upload"))
    assert pipeline.run(_cfg(tmp_path)) == 1


def test_status_api_hiccup_does_not_fail_a_good_upload(tmp_path, monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    def boom(yt, vid, **kw):
        raise RuntimeError("quota")
    monkeypatch.setattr(pipeline.upload, "wait_until_accepted", boom)
    assert pipeline.run(_cfg(tmp_path)) == 0
    assert json.loads((tmp_path / "ledger.json").read_text())[0]["video_id"] == "vid123"


def test_preflight_warns_about_an_earlier_abandoned_video(tmp_path, monkeypatch, capsys):
    calls = []
    _fakes(monkeypatch, calls, earlier={"old2": ("missing", "")})
    cfg = _cfg(tmp_path)
    (tmp_path / "ledger.json").write_text(json.dumps([{"video_id": "old1", "topic": "Saturn"},
                                                      {"video_id": "old2", "topic": "Ancient Egypt"}]))
    assert pipeline.run(cfg) == 0                                                  # a warning, never a blocker
    warnings = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::warning::")]
    assert warnings == ["::warning::earlier video old2 (Ancient Egypt) is missing — check YouTube Studio"]


def test_second_run_on_the_same_day_is_a_no_op(tmp_path, monkeypatch, capsys):
    calls = []
    _fakes(monkeypatch, calls)
    cfg = _cfg(tmp_path)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_path / "ledger.json").write_text(json.dumps([{"date": today, "topic": "Saturn", "video_id": "v0"}]))
    assert pipeline.run(cfg) == 0 and calls == []
    assert "already published a video today" in capsys.readouterr().out
    cfg.topic = "Whales"                                                           # a forced topic always runs
    assert pipeline.run(cfg) == 0 and "upload" in calls
