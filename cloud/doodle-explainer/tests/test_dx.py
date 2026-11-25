import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dx  # noqa: E402,F401
from dx import figures, pipeline, scenes, video, writer  # noqa: E402


def test_every_pose_and_face_renders(tmp_path):
    els = "".join(f"<fig x='{100 + i * 100}' y='500' pose='{p}' face='{f}'/>"
                  for i, (p, f) in enumerate(zip(figures.POSES, list(figures.FACES) * 3)))
    svg = scenes.compose(scenes.parse_body(els), width=1920, height=1080, upto=1, variant=1, seed=0)
    scenes.render_png(svg, str(tmp_path / "a.png"), width=480, height=270)
    assert (tmp_path / "a.png").stat().st_size > 1000


def test_check_rejects_bad_drawings():
    assert scenes.check("<script>x</script>", width=1080, height=1920)
    assert scenes.check("<text x='1' y='1'>one two three four five six seven eight nine</text><circle r='3'/>", width=1080, height=1920)
    assert scenes.check("<text x='1' y='9'>only</text>", width=1080, height=1920)
    assert scenes.check("<circle cx='5' cy='5' r='3'", width=1080, height=1920)
    assert scenes.check("<fig x='540' y='1400' pose='think'/>", width=1080, height=1920) is None


def test_event_handlers_are_stripped():
    kids = scenes.parse_body("<circle cx='1' cy='1' r='1' onload='x()' style='a'/>")
    assert "onload" not in kids[0].attrib and "style" not in kids[0].attrib


def test_reveals_and_frame_plan():
    kids = scenes.parse_body("<fig x='1' y='1'/><g data-at='0.5'><circle r='2'/></g>")
    assert scenes.reveal_times(kids) == [0.0, 0.5]
    states = [(0.0, ["a0", "a1", "a2"]), (0.5, ["b0", "b1", "b2"])]
    plan = video.frame_plan(states, 2.0)
    assert len(plan) == 16 and plan[0][0] == "a0" and plan[7][0].startswith("a") and plan[8][0].startswith("b")
    assert abs(sum(d for _, d in plan) - 2.0) < 1e-6


def test_hidden_layer_not_drawn_before_its_time():
    kids = scenes.parse_body("<circle cx='1' cy='1' r='1'/><g data-at='0.6'><rect id='zzhidden' width='2' height='2'/></g>")
    assert "zzhidden" not in scenes.compose(kids, width=10, height=10, upto=0.5, variant=0, seed=0)
    assert "zzhidden" in scenes.compose(kids, width=10, height=10, upto=0.6, variant=0, seed=0)


def test_long_day_rule():
    assert pipeline.is_long_day(date(2026, 11, 26), 2)
    assert not pipeline.is_long_day(date(2026, 11, 27), 2)
    assert pipeline.is_long_day(date(2026, 11, 28), 2)


def test_chapters_need_three_and_start_at_zero():
    s = writer.Script(kind="long", question="q", title="t", hook="h", sections=["A", "B", "C"],
                      scenes=[writer.Scene(say="x", section=k) for k in (0, 0, 1, 1, 2)])
    out = pipeline.chapters(s, [0, 8, 20, 30, 45])
    assert out.splitlines() == ["0:00 A", "0:20 B", "0:45 C"]


def test_fallback_svg_is_valid():
    for kind, (w, h) in (("long", (1920, 1080)), ("short", (1080, 1920))):
        assert scenes.check(writer.fallback_svg(kind, 1), width=w, height=h) is None


def test_build_needs_scenes():
    raw = {"title": "T", "sections": [{"heading": "H", "scenes": [{"say": f"line {i}"} for i in range(5)]}]}
    s = writer.build("short", "q", raw, "c")
    assert len(s.scenes) == 5 and s.words == 10
    back = writer.Script.from_dict(s.to_dict())
    assert back.scenes[2].say == "line 2"


def test_auto_view_zooms_small_drawings_and_keeps_full_ones():
    small = scenes.parse_body("<circle cx='540' cy='960' r='150' fill='#f6c343'/>")
    v = scenes.auto_view(small, width=1080, height=1920)
    assert v and abs(v[2] - 540) < 1 and abs(v[3] / v[2] - 1920 / 1080) < 1e-6      # capped at 2x
    full = scenes.parse_body("<rect x='20' y='20' width='1040' height='1880' fill='#58a65c'/>")
    assert scenes.auto_view(full, width=1080, height=1920) is None


def test_writer_font_is_replaced():
    kids = scenes.parse_body("<text x='1' y='1' font-family='Arial'>hi</text>")
    out = scenes.compose(kids, width=10, height=10, upto=1, variant=0, seed=0)
    assert "Arial" not in out and scenes.FONT in out


def test_recent_run_is_a_time_window():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 25, 21, 30, tzinfo=timezone.utc)
    assert not pipeline.recent_run([{"kind": "short", "date": "2026-11-25", "at": "2026-11-25T13:00:00Z"}], "short", now=now)
    assert pipeline.recent_run([{"kind": "short", "date": "2026-11-25", "at": "2026-11-25T14:00:00Z"}], "short", now=now)
    assert not pipeline.recent_run([{"kind": "long", "date": "2026-11-25", "at": "2026-11-25T21:00:00Z"}], "short", now=now)
    assert pipeline.recent_run([{"kind": "short", "date": "2026-11-25"}], "short", now=now)
