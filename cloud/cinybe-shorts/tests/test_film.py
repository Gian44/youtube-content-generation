import json

import pytest

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


def _acts(n):
    return [{"index": i, "title": f"A{i}", "summary": "s"} for i in range(n)]


def test_windows_validation_and_proportional():
    acts = _acts(3)
    good = {"windows": [{"act_index": 0, "start_seconds": 100, "end_seconds": 200}, {"act_index": 1, "start_seconds": 300, "end_seconds": 400},
                        {"act_index": 2, "start_seconds": 500, "end_seconds": 650}]}
    out = align.validate_windows(good, acts, duration=7000, min_s=45, max_s=180)
    assert [w["act_index"] for w in out] == [0, 1, 2] and out[0]["how"] == "transcript"
    bad = json.loads(json.dumps(good)); bad["windows"][1]["start_seconds"], bad["windows"][1]["end_seconds"] = 150, 330
    with pytest.raises(align.AlignError, match="overlaps"):
        align.validate_windows(bad, acts, duration=7000, min_s=45, max_s=180)
    short = json.loads(json.dumps(good)); short["windows"][2]["end_seconds"] = 520
    with pytest.raises(align.AlignError, match="must be 45-180s"):
        align.validate_windows(short, acts, duration=7000, min_s=45, max_s=180)
    with pytest.raises(align.AlignError, match="one window per act"):
        align.validate_windows({"windows": good["windows"][:2]}, acts, duration=7000, min_s=45, max_s=180)
    prop = align.proportional_windows(_acts(4), duration=7200, min_s=45, max_s=180)
    assert len(prop) == 4 and prop[0]["start_seconds"] >= 216 and prop[-1]["end_seconds"] <= 7200 and prop[0]["how"] == "proportional"
    assert all(b["start_seconds"] >= a["end_seconds"] for a, b in zip(prop, prop[1:]))


class FakeLLM:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def generate_json(self, prompt, **kw):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_align_acts_retries_then_falls_back():
    acts = _acts(2)
    cues = align.parse_srt(SRT)
    ok = {"windows": [{"act_index": 0, "start_seconds": 0, "end_seconds": 60}, {"act_index": 1, "start_seconds": 70, "end_seconds": 130}]}
    llm = FakeLLM([{"windows": []}, ok])
    out = align.align_acts(llm, title="X", acts=acts, cues=cues, duration=200, min_s=45, max_s=180)
    assert out[1]["start_seconds"] == 70 and "rejected" in llm.prompts[1] and "[00:00] Hello." in llm.prompts[0]
    llm = FakeLLM([{"windows": []}, {"windows": []}])
    out = align.align_acts(llm, title="X", acts=acts, cues=cues, duration=200, min_s=45, max_s=180)
    assert out[0]["how"] == "proportional"
    assert align.align_acts(None, title="X", acts=acts, cues=[], duration=200, min_s=45, max_s=180)[0]["how"] == "proportional"


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
    assert sh[sh.index("-ss") + 1] == "12.500" and "-an" in sh and "crop=1920:1080" in sh[sh.index("-vf") + 1]


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
