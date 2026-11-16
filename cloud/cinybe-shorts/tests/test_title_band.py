"""Gian 2026-11-16: no title burned over the Shorts (it covered the picture)."""
from pathlib import Path

from cny import config, render


def test_title_band_off_by_default():
    assert config.Config().shorts_title_band is False


def _run(monkeypatch, tmp_path, on: bool):
    seen = {}
    monkeypatch.setattr(render.tts, "duration_seconds", lambda a: 5.0)
    monkeypatch.setattr(render.beatmod, "render", lambda *a, **k: (["b0.mp4"], []))
    monkeypatch.setattr(render.beatmod, "concat", lambda files, out, lst: out)
    monkeypatch.setattr(render.sof_shorts, "caption_png", lambda t, p, **k: p)
    monkeypatch.setattr(render.sof_shorts, "title_png", lambda *a, **k: seen.setdefault("title", "title.png"))
    monkeypatch.setattr(render.sof_render, "run", lambda cmd, timeout=0: seen.setdefault("cmd", cmd))
    cfg = config.Config()
    cfg.shorts_title_band = on
    render.build_short(cfg, work=Path(tmp_path), name="s1", video="f.mkv",
                       short={"title": "A HOOK", "beats": [{"text": "Lance steps through a cellar door.", "at": 10.0}]},
                       durs=[5.0], cuts=[], crop=None, end=100.0, film_end=100.0, audio="a.wav")
    return seen


def test_no_title_overlay_when_off(monkeypatch, tmp_path):
    seen = _run(monkeypatch, tmp_path, False)
    assert "title" not in seen and "title.png" not in seen["cmd"]


def test_title_overlay_when_on(monkeypatch, tmp_path):
    seen = _run(monkeypatch, tmp_path, True)
    assert "title.png" in seen["cmd"]
