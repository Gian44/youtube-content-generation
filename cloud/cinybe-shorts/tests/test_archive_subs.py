import json
import struct
from pathlib import Path

from cny import archive, config, films, pipeline, store, subs


def _f(name, mb, fmt=""):
    return archive.ArchiveFile(name=name, size=int(mb * 1_000_000), fmt=fmt)


def test_best_video_prefers_mp4_mkv_and_skips_derivatives_and_oversized():
    files = [_f("film.ogv", 700), _f("film_512kb.mp4", 300), _f("film.ia.mp4", 900), _f("film.mpeg", 3200, "MPEG2"),
             _f("film.mp4", 800, "h.264"), _f("trailer.mp4", 40), _f("film.srt", 0.05), _f("film.asr.srt", 0.06)]
    assert archive.best_video(files).name == "film.mp4"                                # mp4 beats the bigger mpeg
    assert archive.best_video([f for f in files if f.name != "film.mp4"]).name == "film.mpeg"
    assert archive.best_video(files, max_bytes=500_000_000) is None                     # nothing under the cap
    assert archive.best_video([_f("Night.1080p.mkv", 11500), _f("night.mp4", 900)], max_bytes=5_000_000_000).name == "night.mp4"
    assert archive.subtitle_file(files).name == "film.srt" and not archive.is_asr(archive.subtitle_file(files))
    only_asr = [f for f in files if f.name != "film.srt"]
    assert archive.subtitle_file(only_asr).name == "film.asr.srt" and archive.is_asr(archive.subtitle_file(only_asr))
    assert archive.subtitle_file([_f("a.mp4", 500)]) is None
    # an ASR .srt made from another transfer of the item is not used (its timings drift); a human one still is
    notld = [_f("NightOfTheLivingDead_PS3.mp4", 1860), _f("NightOfTheLivingDead_DVD5.mpg", 3989), _f("NightOfTheLivingDead_DVD5.asr.srt", 0.09)]
    assert archive.subtitle_file(notld, video=notld[0]) is None and archive.subtitle_file(notld, video=notld[1]).name.endswith("DVD5.asr.srt")
    hgf = [_f("his_girl_friday.mp4", 580), _f("his_girl_friday.asr.srt", 0.08)]
    assert archive.subtitle_file(hgf, video=hgf[0]).name == "his_girl_friday.asr.srt"
    assert archive.subtitle_file(notld + [_f("Night.of.the.Living.Dead.eng.srt", 0.1)], video=notld[0]).name == "Night.of.the.Living.Dead.eng.srt"
    assert archive.stem("Dementia 13 (1963)/mp4/Dementia.13.1963.mp4") == "dementia" and archive.stem("X_DVD5.asr.srt") == "x_dvd5"
    assert archive.download_url("Some Item", "A File.mp4") == "https://archive.org/download/Some%20Item/A%20File.mp4"


def test_opensubtitles_hash_and_ranking(tmp_path):
    p = tmp_path / "v.bin"
    data = bytes(range(256)) * 1024                                   # 256 KB
    p.write_bytes(data)
    h = subs.movie_hash(str(p))
    expect = len(data)
    for off in (0, len(data) - 65536):
        for (v,) in struct.iter_unpack("<Q", data[off:off + 65536]):
            expect = (expect + v) & 0xFFFFFFFFFFFFFFFF
    assert h == f"{expect:016x}" and len(h) == 16
    (tmp_path / "small.bin").write_bytes(b"x" * 1000)
    assert subs.movie_hash(str(tmp_path / "small.bin")) is None
    items = [{"attributes": {"download_count": 900, "hearing_impaired": True, "files": [{"file_id": 1}]}},
             {"attributes": {"download_count": 50, "moviehash_match": True, "files": [{"file_id": 2}]}},
             {"attributes": {"download_count": 5000, "files": []}},
             {"attributes": {"download_count": 300, "from_trusted": True, "files": [{"file_id": 3}]}}]
    assert [it["attributes"]["files"][0]["file_id"] for it in subs.rank(items)] == [2, 3, 1]
    cfg = config.Config()
    assert not subs.configured(cfg) and subs.fetch_srt(cfg, title="X", year=2000, video_path=str(p), dest=str(tmp_path / "o.srt")) is None


def test_fetch_srt_uses_hash_then_title_and_never_raises(tmp_path, monkeypatch):
    cfg = config.Config()
    cfg.opensubtitles_api_key, cfg.opensubtitles_username, cfg.opensubtitles_password = "k", "u", "p"
    calls = []

    def fake_call(self, method, path, *, params=None, body=None):
        calls.append((method, path, params, body))
        if path == "/login":
            return {"token": "t"}
        if path == "/subtitles" and params and "moviehash" in params:
            return {"data": [{"attributes": {"download_count": 1, "files": [{"file_id": 5}]}}]}     # no moviehash_match flag → dropped
        if path == "/subtitles":
            return {"data": [{"attributes": {"download_count": 10, "release": "Film.1950.DVD", "files": [{"file_id": 77, "file_name": "f.srt"}]}}]}
        if path == "/download":
            return {"link": "https://dl.example/f.srt", "remaining": 4}
        raise AssertionError(path)

    monkeypatch.setattr(subs.Client, "_call", fake_call)

    class Resp:
        def __init__(self, b):
            self.b = b

        def read(self):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(subs.urllib.request, "urlopen", lambda req, timeout=0: Resp(b"1\n00:00:01,000 --> 00:00:02,000\nHi\n" * 200))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\0" * 200_000)
    out = subs.fetch_srt(cfg, title="Detour", year=1945, video_path=str(video), dest=str(tmp_path / "o.srt"))
    assert out and Path(out).stat().st_size > 2000
    assert calls[0][1] == "/login" and "moviehash" in calls[1][2] and calls[2][2]["query"] == "Detour" and calls[2][2]["year"] == 1945
    assert calls[3][3] == {"file_id": 77, "sub_format": "srt"}
    calls.clear()
    assert subs.fetch_srt(cfg, title="Detour", year=1945, video_path=str(video), dest=str(tmp_path / "o3.srt"), mode="hash") is None
    assert [c[1] for c in calls] == ["/login", "/subtitles"]                      # hash mode never falls through to a title search
    monkeypatch.setattr(subs.Client, "_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("401")))
    assert subs.fetch_srt(cfg, title="Detour", year=1945, video_path=str(video), dest=str(tmp_path / "o2.srt")) is None


def _cfg(tmp_path, **kw):
    import os
    os.environ.pop("TTS_BACKEND", None)
    cfg = config.Config.from_env(**kw)
    cfg.ledger_path = str(tmp_path / "ledger.json")
    cfg.work_dir = str(tmp_path / "work")
    cfg.plots_dir = str(tmp_path / "plots")
    cfg.films_path = str(tmp_path / "films.yml")
    (tmp_path / "films.yml").write_text("themes:\n  - name: Nolan\n    films:\n      - {title: One, year: 2001}\n"
                                        "  - name: Public domain\n    films:\n      - {title: Detour, year: 1945, archive: Detour}\n"
                                        "      - {title: Suddenly, year: 1954, archive: suddenly}\n", encoding="utf-8")
    return cfg


def test_archive_film_is_picked_when_the_store_has_nothing_and_skipped_rows_move_the_queue(tmp_path, capsys):
    cfg = _cfg(tmp_path, minutes=10)
    all_films = films.load_films(cfg.films_path)
    assert all_films[1].archive == "Detour" and all_films[0].archive is None
    film, files = pipeline.pick_film(cfg, [], [])
    assert film.title == "Detour" and files.source == "archive" and files.archive == "Detour" and files.video_key == "archive.org/Detour"
    assert "One (2001)" in capsys.readouterr().out                                # Gian's film is named as still waiting
    # Gian's file wins when it is there (queue order)
    assert pipeline.pick_film(cfg, [], ["one-2001.mkv"])[0].title == "One"
    # a dead end on an archive film writes a skipped row; the next run takes the next film
    rc = pipeline.give_up(cfg, film, files, today="2026-11-08", reason="only 3 dialogue cues (archive-asr)")
    rows = json.loads((tmp_path / "ledger.json").read_text())
    assert rc == pipeline.EXIT_NO_FILM and rows[0]["kind"] == "skipped" and rows[0]["film"] == "Detour" and "3 dialogue" in rows[0]["reason"]
    assert pipeline.pick_film(cfg, rows, [])[0].title == "Suddenly"
    assert not films.ran_recently(rows, gap_hours=36)                              # a skipped row is not a video
    # Gian's own file never gets a skipped row (he fixes the file; the next run retries)
    own = store.match_keys("one-2001", ["one-2001.mkv"])
    assert pipeline.give_up(cfg, all_films[0], own, today="2026-11-08", reason="no shots") == pipeline.EXIT_NO_FILM
    assert len(json.loads((tmp_path / "ledger.json").read_text())) == 1
    assert not store.delete_film(cfg, files)                                       # an Archive print is not ours to remove


def test_fetch_film_from_archive(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, minutes=10)
    work = tmp_path / "w"
    work.mkdir()

    def fake_fetch(identifier, w, *, max_bytes):
        (w / "film").mkdir(parents=True, exist_ok=True)
        (w / "film" / "source.mp4").write_bytes(b"v")
        (w / "film" / "source.asr.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        return str(w / "film" / "source.mp4"), str(w / "film" / "source.asr.srt"), archive.ArchiveFile("Detour.MPEG", 1_783_000_000, "MPEG2"), True

    monkeypatch.setattr(archive, "fetch", fake_fetch)
    files = store.fetch_film(cfg, store.archive_files("detour-1945", "Detour"), work)
    assert files.video_path.endswith("source.mp4") and files.srt_asr and files.video_key == "archive.org/Detour/Detour.MPEG" and files.size == 1_783_000_000
    monkeypatch.setattr(archive, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("item gone")))
    assert store.fetch_film(cfg, store.archive_files("detour-1945", "Detour"), work / "x") is None
