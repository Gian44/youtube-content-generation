import json
from pathlib import Path

from cny import config, films, pipeline, render
from cny import meta as meta_mod
from cny import plot as plot_mod


def _chain_len(segs, cf):
    return sum(s.seconds for s in segs) - cf * (len(segs) - 1)


def test_plan_segments_covers_the_section_with_card_and_rotating_clips():
    clips = [("a.mp4", 45.0), ("b.mp4", 20.0)]
    segs = render.plan_segments(70.0, clips, [], card="card.png", card_seconds=2.5, clip_max=12.0, crossfade=0.6)
    assert segs[0].kind == "card" and segs[0].seconds == 3.1
    assert all(s.kind == "clip" for s in segs[1:]) and _chain_len(segs, 0.6) >= 70.0
    # the first clip returns at a later offset the second time round
    a_uses = [s for s in segs if s.src == "a.mp4"]
    assert a_uses[0].offset == 0.0 and a_uses[1].offset == 12.0
    # the short clip wraps its offset rather than running past its end
    b_uses = [s for s in segs if s.src == "b.mp4"]
    assert all(u.offset + u.seconds <= 20.0 + 12.6 for u in b_uses) and b_uses[1].offset in (12.0, 0.0)
    assert min(s.seconds for s in segs[1:]) >= 2.0                   # no sliver segments


def test_plan_segments_photos_and_nothing():
    segs = render.plan_segments(30.0, [], ["p1.jpg", "p2.jpg"], card="c.png", card_seconds=2.5, clip_max=12.0, crossfade=0.6)
    assert [s.kind for s in segs] == ["card", "photo", "photo", "photo"] and segs[1].src == "p1.jpg" and segs[3].src == "p1.jpg"
    only = render.plan_segments(40.0, [], [], card="c.png", card_seconds=2.5, clip_max=12.0, crossfade=0.6)
    assert len(only) == 1 and only[0].seconds == 40.0
    short = render.plan_segments(20.0, [("a.mp4", 45.0)], [], card=None, card_seconds=0, clip_max=6.0, crossfade=0.4)
    assert short[0].kind == "clip" and _chain_len(short, 0.4) >= 20.0 and len(short) == 3 and short[2].seconds == 8.0


def test_ffmpeg_commands():
    cmd = render.xfade_cmd(["a", "b", "c"], [5.0, 5.0, 5.0], "out.mp4", crossfade=0.5, fps=30, total=12.3)
    assert cmd[-1] == "out.mp4" and cmd[-3:-1] == ["-t", "12.3"] and "xfade=transition=fade:duration=0.5:offset=4.5[vx1]" in " ".join(cmd)
    one = render.xfade_cmd(["a"], [5.0], "o.mp4", crossfade=0.5, fps=30, total=4.0)
    assert one[:6] == ["ffmpeg", "-y", "-v", "error", "-i", "a"] and one[-3:] == ["-t", "4", "o.mp4"]
    win = render.clip_window_cmd("src.mp4", "o.mp4", offset=12.0, seconds=6.6, fps=30)
    assert win[win.index("-ss") + 1] == "12" and win[win.index("-t") + 1] == "6.6" and "-stream_loop" in win
    still = render.still_cmd("c.png", "o.mp4", seconds=3.1, width=1920, height=1080, fps=30)
    assert "-loop" in still and still[still.index("-t") + 1] == "3.1"


def test_durations_scale_to_the_assembled_narration(monkeypatch):
    monkeypatch.setattr(render.tts, "duration_seconds", lambda p: {"a": 10.0, "b": 20.0, "c": 30.0}[p])
    per = render.scaled_durations(["a", "b", "c"], [0, 0, 1], 2, total=66.0)
    assert per == [33.0, 33.0] and render.section_starts(per) == [0.0, 33.0]
    rows = render.chapter_rows([{"heading": "Setup"}, {"heading": ""}], render.section_starts(per))
    assert rows == [{"start": 0.0, "title": "Setup"}, {"start": 33.0, "title": "Act 2"}]


def test_act_card_and_thumbnail_render(tmp_path):
    from cny import thumbnail
    png = render.act_card_png(str(tmp_path / "c.png"), act_title="The dream within a dream collapses", index=3, count=12,
                              film="Inception (2010)", width=640, height=360)
    assert Path(png).stat().st_size > 1000
    jpg = thumbnail.compose(base=None, out=str(tmp_path / "t.jpg"), film="Inception", year=2010, hook="dream heist", width=640, height=360)
    assert Path(jpg).stat().st_size > 1000


def _cfg(tmp_path, **kw):
    import os
    os.environ.pop("TTS_BACKEND", None)
    cfg = config.Config.from_env(**kw)
    cfg.ledger_path = str(tmp_path / "ledger.json")
    cfg.work_dir = str(tmp_path / "work")
    cfg.plots_dir = str(tmp_path / "plots")
    cfg.films_path = str(tmp_path / "films.yml")
    (tmp_path / "films.yml").write_text("themes:\n  - name: T\n    films:\n      - {title: One, year: 2001}\n      - {title: Two, year: 2002}\n",
                                        encoding="utf-8")
    return cfg


def test_pick_film_skips_thin_plots_and_takes_operator_synopsis(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, minutes=10)
    monkeypatch.setattr(meta_mod, "resolve", lambda t, y=None: meta_mod.MovieMeta(title=t, year=y, wikipedia_title=f"{t} (film)"))

    class FakeWiki:
        def fetch_any(self, titles):
            return plot_mod.PlotText(title=titles[0], text="thin plot", word_count=2, url="u", section="Plot")

    monkeypatch.setattr(plot_mod, "WikiPlot", FakeWiki)
    picked, skipped = pipeline.pick_film(cfg, [], Path(cfg.work_dir), "2026-11-08")
    assert picked is None and [r["film"] for r in skipped] == ["One", "Two"] and "plots/one-2001.md" in skipped[0]["reason"]
    (tmp_path / "plots").mkdir()
    (tmp_path / "plots" / "two-2002.md").write_text(" ".join(["word"] * 300), encoding="utf-8")
    rows = [dict(r, date="2026-11-08") for r in skipped]
    picked, skipped2 = pipeline.pick_film(cfg, rows, Path(cfg.work_dir), "2026-11-09")
    film, meta, plot, work = picked
    assert film.title == "Two" and plot.source == "operator" and skipped2 == [] and (work / "plot.json").exists()
    # resumable: the cached plot.json is read back, no fetch
    monkeypatch.setattr(plot_mod, "WikiPlot", None)
    again, _ = pipeline.pick_film(cfg, rows, Path(cfg.work_dir), "2026-11-09")
    assert again[2].text == plot.text


def test_run_is_a_noop_inside_the_gap(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, minutes=10)
    from datetime import datetime, timezone
    recent = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (tmp_path / "ledger.json").write_text(json.dumps([{"kind": "video", "film": "One", "year": 2001, "video_id": "v", "uploaded_at": recent}]),
                                          encoding="utf-8")
    assert pipeline.run(cfg) == pipeline.EXIT_OK
    assert json.loads((tmp_path / "ledger.json").read_text())[0]["film"] == "One"


def test_smoke_config_is_unlisted_and_pocket_by_default(monkeypatch):
    monkeypatch.delenv("TTS_BACKEND", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_CINYBE", "k-cinybe")
    monkeypatch.setenv("YT_REFRESH_TOKEN_CINYBE", "rt-cinybe")
    cfg = config.Config.from_env(minutes=3)
    assert cfg.is_smoke and cfg.privacy == "unlisted" and cfg.sof.tts_backend == "pocket"
    assert cfg.sof.gemini_api_key == "k-cinybe" and cfg.sof.yt_refresh_token == "rt-cinybe" and cfg.sof.shorts_brand == "CinybeShorts"
    monkeypatch.setenv("TTS_BACKEND", "gemini")
    assert config.Config.from_env(minutes=10).sof.tts_backend == "gemini"
