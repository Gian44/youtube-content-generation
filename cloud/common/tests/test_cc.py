from pathlib import Path

from cc import broll, segments


def _chain_len(segs, cf):
    return sum(s.seconds for s in segs) - cf * (len(segs) - 1)


def test_plan_segments_rotates_clips_and_offsets():
    segs = segments.plan_segments(70.0, [("a.mp4", 45.0), ("b.mp4", 20.0)], [], card="card.png", card_seconds=2.5,
                                  clip_max=12.0, crossfade=0.6)
    assert segs[0].kind == "card" and segs[0].seconds == 3.1 and _chain_len(segs, 0.6) >= 70.0
    a = [s for s in segs if s.src == "a.mp4"]
    assert a[0].offset == 0.0 and a[1].offset == 12.0 and min(s.seconds for s in segs[1:]) >= 2.0


def test_plan_segments_photos_card_only_and_no_card():
    segs = segments.plan_segments(30.0, [], ["p1", "p2"], card="c", card_seconds=2.5, clip_max=12.0, crossfade=0.6)
    assert [s.kind for s in segs] == ["card", "photo", "photo", "photo"]
    only = segments.plan_segments(40.0, [], [], card="c", card_seconds=2.5, clip_max=12.0, crossfade=0.6)
    assert len(only) == 1 and only[0].seconds == 40.0
    short = segments.plan_segments(20.0, [("a", 45.0)], [], card=None, card_seconds=0, clip_max=6.0, crossfade=0.4)
    assert short[0].kind == "clip" and _chain_len(short, 0.4) >= 20.0


def test_commands():
    cmd = segments.xfade_cmd(["a", "b"], [5.0, 5.0], "o.mp4", crossfade=0.5, fps=30, total=9.25)
    assert cmd[-3:] == ["-t", "9.25", "o.mp4"] and "xfade=transition=fade:duration=0.5:offset=4.5[vx1]" in " ".join(cmd)
    one = segments.xfade_cmd(["a"], [5.0], "o.mp4", crossfade=0.5, fps=30, total=None)
    assert one[-1] == "o.mp4" and "-t" not in one
    win = segments.clip_window_cmd("s.mp4", "o.mp4", offset=12.0, seconds=6.6, fps=30)
    assert win[win.index("-ss") + 1] == "12" and win[win.index("-t") + 1] == "6.6"
    still = segments.still_cmd("c.png", "o.mp4", seconds=3.1, width=1080, height=1920, fps=30)
    assert "-loop" in still and "scale=1080:1920" in still[still.index("-vf") + 1]


def test_title_card(tmp_path):
    p = segments.title_card_png(str(tmp_path / "c.png"), title="A long story title that wraps onto lines", kicker="TODAY", footer="Brand",
                                width=540, height=960)
    assert Path(p).stat().st_size > 1000


class FakeVision:
    def __init__(self, active, answer):
        self.active, self.answer, self.asked = active, answer, []

    def ask(self, prompt, image):
        self.asked.append(prompt)
        return self.answer


def _jpgs(tmp_path, n):
    from PIL import Image
    out = []
    for i in range(n):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (64, 64), (i * 20, 0, 0)).save(p)
        out.append(str(p))
    return out


def test_label_broll_maps_keep_to_subject(tmp_path):
    paths = _jpgs(tmp_path, 3)
    vis = FakeVision(["codex"], {"keep": [1, 3], "reject": [2]})
    labels = broll.label_broll(vis, paths, query="rain on a window", summary='He says "stay".', kind="story")
    assert [labels[p] for p in paths] == ["subject", "reject", "subject"]
    assert "rain on a window" in vis.asked[0] and "He says 'stay'." in vis.asked[0] and "narrated story" in vis.asked[0]
    assert broll.label_broll(FakeVision([], None), paths, query="q", summary="s") == {p: "subject" for p in paths}
    assert broll.label_broll(FakeVision(["codex"], None), paths, query="q", summary="s") == {p: "subject" for p in paths}
    assert broll.label_broll(vis, [], query="q", summary="s") == {}


def test_used_ids_and_footage_for_reads_cache(tmp_path):
    d = tmp_path / "f"
    d.mkdir()
    (d / "x.mp4").write_bytes(b"0")
    (d / "footage.json").write_text('[{"file": "x.mp4", "id": "pexels-1"}, {"file": "gone.mp4", "id": "pexels-2"}]', encoding="utf-8")
    assert broll.used_ids(str(d)) == {"pexels-1", "pexels-2"}
    got = broll.footage_for(queries=["q"], summary="s", out_dir=str(d), vis=FakeVision([], None), pexels_key="", pixabay_key="",
                            want=3, candidates=5, budget=1.0)
    assert got == [str((d / "x.mp4").resolve())]
    assert broll.footage_for(queries=[], summary="s", out_dir=str(tmp_path / "none"), vis=FakeVision([], None), pexels_key="",
                             pixabay_key="", want=3, candidates=5, budget=1.0) == []
