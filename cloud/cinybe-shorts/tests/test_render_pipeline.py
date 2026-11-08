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
    assert rows == [{"start": 0.0, "title": "Setup"}, {"start": 33.0, "title": "Part 2"}]


def test_thumbnail_is_hook_only_and_shots_wanted(tmp_path):
    from cny import thumbnail, visuals
    jpg = thumbnail.compose(base=None, out=str(tmp_path / "t.jpg"), hook="dream heist", width=640, height=360)
    assert Path(jpg).stat().st_size > 1000
    from PIL import Image
    boxed = Image.new("RGB", (640, 360), (0, 0, 0))                      # a 2.39:1 frame padded to 16:9
    boxed.paste(Image.new("RGB", (640, 268), (90, 140, 200)), (0, 46))
    assert thumbnail.trim_bars(boxed).size == (640, 268)
    assert thumbnail.trim_bars(Image.new("RGB", (64, 64), (0, 0, 0))).size == (64, 64)   # an all-black frame is left alone
    boxed.save(tmp_path / "b.jpg")
    assert Image.open(thumbnail.compose(base=str(tmp_path / "b.jpg"), out=str(tmp_path / "t2.jpg"), hook="x", width=640, height=360)).size == (640, 360)
    assert not hasattr(render, "act_card_png")                       # recap style: no title cards between acts
    from cny import config
    c = config.Config()
    assert 230 <= c.words_per_minute <= 250 and 3900 <= c.target_words <= 4300 and c.minutes == 17   # faster than the samples
    assert not c.film_audio and c.music_enabled                                     # muted footage over the quiet bed
    vis = {0: {"clips": ["w/shots/act00_00.mp4", "w/shots/act01_01.mp4"], "shots": [[12.0, 4.0]], "clips_v": ["w/shots_v/act00_00.mp4"]},
           1: {"clips": ["w/shots/act01_00.mp4", "w/shots/act01_01.mp4"], "shots": [[80.0, 5.0], [90.5, 3.0]]}}
    assert visuals.shot_starts(vis) == {"w/shots/act00_00.mp4": 12.0, "w/shots/act01_01.mp4": 90.5, "w/shots_v/act00_00.mp4": 12.0,
                                        "w/shots/act01_00.mp4": 80.0}
    # 145 words ≈ 60 s of narration → 14 shots of 4.5 s + 2 spare; a near-empty act still gets the floor
    assert visuals.shots_wanted(145, words_per_minute=145, shot_seconds=4.5) == 16
    assert visuals.shots_wanted(3, words_per_minute=145, shot_seconds=4.5) == 4
    # the sequence planner plays every shot once, in order, and cycles only when the footage is short
    plan = render.plan_sequence(20.0, [("a.mp4", 6.0), ("b.mp4", 6.0), ("c.mp4", 6.0), ("d.mp4", 6.0)], min_seconds=1.5, max_seconds=8.0)
    assert [x.src for x in plan] == ["a.mp4", "b.mp4", "c.mp4", "d.mp4"] and abs(sum(x.seconds for x in plan) - 20.0) < 0.01
    cyc = render.plan_sequence(30.0, [("a.mp4", 6.0), ("b.mp4", 6.0)], min_seconds=1.5, max_seconds=8.0)
    assert [x.src for x in cyc][:3] == ["a.mp4", "b.mp4", "a.mp4"] and abs(sum(x.seconds for x in cyc) - 30.0) < 0.01


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


def test_pick_film_takes_the_first_queued_film_with_a_file(tmp_path, capsys):
    cfg = _cfg(tmp_path, minutes=10)
    assert pipeline.pick_film(cfg, [], []) is None                                   # empty store → nothing, films stay queued
    assert "no file" in capsys.readouterr().out
    film, files = pipeline.pick_film(cfg, [], ["two-2002.mkv", "Two-2002.srt", "notes.txt"])
    assert film.title == "Two" and files.video_key == "two-2002.mkv" and files.srt_key == "Two-2002.srt"
    assert "One (2001)" in capsys.readouterr().out                                  # the skipped film is named
    # a published film is not picked again; a forced film ignores the queue order
    rows = [{"kind": "video", "film": "Two", "year": 2002, "video_id": "v", "date": "2026-11-01"}]
    assert pipeline.pick_film(cfg, rows, ["two-2002.mkv"]) is None
    cfg.film, cfg.year = "Two", 2002
    assert pipeline.pick_film(cfg, rows, ["two-2002.mkv"])[0].title == "Two"


def test_reference_plot_is_optional_and_cached(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, minutes=10)
    film = films.load_films(cfg.films_path)[0]
    monkeypatch.setattr(meta_mod, "resolve", lambda t, y=None: meta_mod.MovieMeta(title=t, year=y, wikipedia_title=f"{t} (film)"))

    class FakeWiki:
        def fetch_any(self, titles):
            return plot_mod.PlotText(title=titles[0], text="thin plot", word_count=2, url="u", section="Plot")

    monkeypatch.setattr(plot_mod, "WikiPlot", FakeWiki)
    work = Path(cfg.work_dir) / "a"
    work.mkdir(parents=True)
    meta, plot = pipeline.reference_plot(cfg, film, work)                          # thin plot → no reference, run goes on
    assert meta.title == "One" and plot is None and (work / "plot.json").read_text() == "{}"
    (tmp_path / "plots").mkdir()
    (tmp_path / "plots" / "one-2001.md").write_text(" ".join(["word"] * 300), encoding="utf-8")
    work2 = Path(cfg.work_dir) / "b"
    work2.mkdir()
    _, plot2 = pipeline.reference_plot(cfg, film, work2)                           # an operator synopsis wins
    assert plot2 is not None and plot2.source == "operator"
    monkeypatch.setattr(plot_mod, "WikiPlot", None)                                # resumable: cached plot.json is read back
    assert pipeline.reference_plot(cfg, film, work2)[1].text == plot2.text

    class Boom:
        def fetch_any(self, titles):
            raise RuntimeError("offline")

    monkeypatch.setattr(plot_mod, "WikiPlot", Boom)
    work3 = Path(cfg.work_dir) / "c"
    work3.mkdir()
    assert pipeline.reference_plot(cfg, films.load_films(cfg.films_path)[1], work3)[1] is None   # Wikipedia down ≠ dead run


def test_run_posts_nothing_without_a_film_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, minutes=10, skip_upload=True)
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "GDRIVE_SERVICE_ACCOUNT_JSON", "GDRIVE_FOLDER_ID"):
        monkeypatch.delenv(k, raising=False)
    cfg.r2_account_id = cfg.r2_access_key_id = cfg.r2_secret_access_key = cfg.gdrive_service_account_json = ""
    cfg.sof.claude_enabled = False
    assert pipeline.run(cfg) == pipeline.EXIT_NO_FILM
    assert not (tmp_path / "ledger.json").exists()


def test_run_posts_nothing_when_no_queued_film_has_a_file(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, minutes=10, skip_upload=True)
    cfg.r2_account_id, cfg.r2_access_key_id, cfg.r2_secret_access_key = "a", "k", "s"
    cfg.sof.claude_enabled = False
    monkeypatch.setattr(pipeline.store, "list_keys", lambda c: ["something-else-1999.mkv"])
    assert pipeline.run(cfg) == pipeline.EXIT_NO_FILM
    assert not (tmp_path / "ledger.json").exists()


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


def test_quicken_caps_pauses_and_speeds_up_each_chunk(tmp_path):
    import subprocess
    from cny import config, pipeline
    cfg = config.Config()
    assert "silenceremove" in pipeline.voice_filter(cfg) and "atempo=1.4" in pipeline.voice_filter(cfg)
    src = str(tmp_path / "c0.mp3")                     # 0.4 s lead-in, 1.2 s "speech", 0.6 s pause, 1.2 s, 0.5 s tail
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=1.2,volume=0.3", "-filter_complex",
                    "[0:a]atrim=duration=0.4[a];[0:a]atrim=duration=0.6[b];[0:a]atrim=duration=0.5[c];[1:a]asplit=2[t0][t1];"
                    "[a][t0][b][t1][c]concat=n=5:v=0:a=1", "-ar", "24000", src], check=True)
    out = pipeline.quicken([src], tmp_path / "fast", cfg)
    before, after = render.tts.duration_seconds(src), render.tts.duration_seconds(out[0])
    assert before > 3.8 and after < before / 1.4 - 0.2                              # faster than tempo alone: pauses capped
    assert pipeline.quicken([src], tmp_path / "fast", cfg) == out                   # resumable
    cfg.voice_tempo, cfg.voice_tighten = 1.0, ""
    assert pipeline.quicken([src], tmp_path / "x", cfg) == [src]
