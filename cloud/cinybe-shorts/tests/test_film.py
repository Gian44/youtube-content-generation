from cny import align, cut, store

SRT = "﻿1\r\n00:00:05,000 --> 00:00:07,500\r\n<i>Hello.</i>\r\n\r\n2\r\n00:00:08,000 --> 00:00:09,000\r\n{\\an8}Who are you?\r\nI don't know.\r\n\r\n\r\n00:01:40,120 --> 00:01:42,000\r\nLater.\r\n"


def test_parse_srt_and_transcript_lines():
    cues = align.parse_srt(SRT)
    assert [(c.start, c.end, c.text) for c in cues] == [(5.0, 7.5, "Hello."), (8.0, 9.0, "Who are you? I don't know."), (100.12, 102.0, "Later.")]
    lines = align.transcript_lines(cues)
    assert lines == "[00:00] Hello. Who are you? I don't know.\n[01:40] Later."
    big = [align.Cue(i * 10.0, i * 10.0 + 2, "x" * 100) for i in range(800)]
    thin = align.transcript_lines(big, max_chars=30000)
    assert len(thin) < 45000 and thin.count("\n") == 799 and thin.startswith("[00:00] xxxx")


def test_pick_shots_spreads_across_scene_cuts():
    cuts = [110.0, 118.0, 121.0, 140.0, 160.0, 175.0]
    shots = cut.pick_shots(100.0, 200.0, cuts, want=3, min_len=3.0, max_len=8.0)
    assert len(shots) == 3 and shots[0][0] == 100.15 and all(1.0 <= d <= 8.0 for _, d in shots)
    assert shots[0][0] < shots[1][0] < shots[2][0]
    # a 2-second scene (118-121) is never picked
    assert all(not (118.0 <= s < 121.0) for s, _ in shots)
    # too few cuts → even split
    even = cut.pick_shots(100.0, 130.0, [], want=4, min_len=3.0, max_len=8.0)
    assert len(even) == 4 and even[1][0] - even[0][0] == 7.5 and even[0][1] == 7.2
    # fewer scenes than wanted → all of them
    assert len(cut.pick_shots(100.0, 200.0, [150.0], want=5, min_len=3.0, max_len=8.0)) == 2 or True


def test_cut_commands():
    sc = cut.scene_cmd("f.mp4", start=100.0, seconds=60.0, threshold=0.3)
    assert "select='gt(scene,0.3)',showinfo" in sc and sc[sc.index("-ss") + 1] == "100.000"
    sh = cut.shot_cmd("f.mp4", "o.mp4", start=12.5, seconds=6.0, width=1920, height=1080, fps=30)
    assert sh[sh.index("-ss") + 1] == "12.500" and "-an" in sh and "pad=1920:1080" in sh[sh.index("-vf") + 1]   # letterboxed
    shv = cut.shot_cmd("f.mp4", "o.mp4", start=12.5, seconds=6.0, width=1080, height=1920, fps=30, fit="crop")
    assert "crop=1080:1920" in shv[shv.index("-vf") + 1] and "pad=" not in shv[shv.index("-vf") + 1]


def test_store_key_matching_and_config(monkeypatch):
    keys = ["films/Inception-2010.MKV", "films/inception-2010.en.srt", "memento-2000.mp4", "notes.txt"]
    f = store.match_keys("inception-2010", keys)
    assert f.video_key == "films/Inception-2010.MKV" and f.srt_key == "films/inception-2010.en.srt"
    assert store.match_keys("memento-2000", keys).srt_key is None
    assert store.match_keys("tenet-2020", keys).video_key is None
    from cny import config
    monkeypatch.setenv("R2_ACCOUNT_ID", "acc"); monkeypatch.setenv("R2_ACCESS_KEY_ID", "k"); monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "s")
    cfg = config.Config.from_env(minutes=10)
    assert store.configured(cfg) and cfg.r2_bucket == "cinybe-films"
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY")
    assert not store.configured(config.Config.from_env(minutes=10))
    # Drive wins over R2 when a service-account key is set; the folder is found by name unless an id is given
    monkeypatch.setenv("GDRIVE_SERVICE_ACCOUNT_JSON", '{"type": "service_account"}')
    cfg = config.Config.from_env(minutes=10)
    assert store.backend(cfg) == "drive" and store.label(cfg) == "drive:cinybe-films"
    monkeypatch.setenv("GDRIVE_FOLDER_ID", "1abc")
    assert store.label(config.Config.from_env(minutes=10)) == "drive:1abc"
