"""timeline: the pure planner and ffmpeg command builders (no ffmpeg run here)."""
import math

import pytest

from sof import render, timeline
from sof.timeline import Segment, plan_segments


def _check_contiguous(segs, end):
    assert segs[0].start == 0 and segs[-1].end == end
    assert sum(s.frames for s in segs) == end
    for a, b in zip(segs, segs[1:]):
        assert a.end == b.start
    assert all(s.frames >= timeline.MIN_SEGMENT_FRAMES for s in segs)


def _check_tau_continuity(segs):
    """Each photo's zoom clock runs on unbroken: body → its outgoing transition → next photo's body."""
    for a, b in zip(segs, segs[1:]):
        if a.kind == "body" and b.kind == "trans":
            assert b.slots[0] == a.slots[0] and b.tau[0] == a.tau[0] + a.frames
        elif a.kind == "trans" and b.kind == "body":
            assert b.slots[0] == a.slots[1] and b.tau[0] == a.tau[1] + a.frames
        else:
            pytest.fail(f"two {a.kind}s in a row")
    for s in segs:
        if s.kind == "trans":
            assert s.tau[1] == 0                      # the incoming photo starts its clock as it starts fading in


def test_plan_basic_layout_matches_the_slot_grid():
    segs = plan_segments(5, dwell=20.0, crossfade=1.5, fps=24)       # 5 slots, no total → 100 s
    _check_contiguous(segs, 2400)
    _check_tau_continuity(segs)
    assert [s.kind for s in segs] == ["body", "trans"] * 4 + ["body"]
    assert segs[0] == Segment("body", (0,), 0, 444, (0,), fade_in=48)
    assert segs[1] == Segment("trans", (0, 1), 444, 36, (444, 0))   # crossfade ends exactly on slot 1's start (480)
    assert segs[2] == Segment("body", (1,), 480, 444, (36,))
    assert segs[3] == Segment("trans", (1, 2), 924, 36, (480, 0))
    assert segs[-1] == Segment("body", (4,), 1920, 480, (36,), fade_out=72)
    # every middle body and middle transition has the same clock range → rendered once per photo / pair
    assert {(s.tau, s.frames) for s in segs[2:-1:2]} == {((36,), 444)}
    assert {(s.tau, s.frames) for s in segs[3:-1:2]} == {((480, 0), 36)}


def test_plan_ends_exactly_at_total_with_an_odd_length():
    segs = plan_segments(5, dwell=20.0, crossfade=1.5, fps=24, total=97.37)
    _check_contiguous(segs, round(97.37 * 24))                       # 2337: last slot shortened to 417 frames
    _check_tau_continuity(segs)
    assert segs[-1].slots == (4,) and segs[-1].start == 1920 and segs[-1].frames == 417 and segs[-1].fade_out == 72


def test_plan_stretches_the_last_slot_when_total_is_longer():
    segs = plan_segments(3, dwell=20.0, crossfade=1.5, fps=24, total=75.0)
    _check_contiguous(segs, 1800)
    assert segs[-1].slots == (2,) and segs[-1].start == 960 and segs[-1].frames == 840


def test_plan_drops_slots_past_the_end_and_folds_a_too_short_last_slot():
    segs = plan_segments(5, dwell=20.0, crossfade=1.5, fps=24, total=50.0)       # slots 3 and 4 start after the end
    _check_contiguous(segs, 1200)
    assert {k for s in segs for k in s.slots} == {0, 1, 2}
    segs = plan_segments(5, dwell=20.0, crossfade=1.5, fps=24, total=80.5)       # slot 4 would show for 0.5 s < fade-out
    _check_contiguous(segs, 1932)
    _check_tau_continuity(segs)
    assert segs[-1].slots == (3,) and segs[-1].start == 1440 and segs[-1].frames == 492 and segs[-1].fade_out == 72
    assert all(4 not in s.slots for s in segs)


def test_plan_merges_consecutive_duplicates_into_one_span():
    same = [False, True, False, True, True]                           # A A B B B
    segs = plan_segments(5, dwell=20.0, crossfade=1.5, fps=24, same=same)
    _check_contiguous(segs, 2400)
    _check_tau_continuity(segs)
    assert [(s.kind, s.slots) for s in segs] == [("body", (0,)), ("trans", (0, 2)), ("body", (2,))]
    assert segs[0].frames == 960 - 36 and segs[1].start == 924 and segs[1].tau == (924, 0)
    assert segs[2].start == 960 and segs[2].frames == 1440 and segs[2].tau == (36,)
    assert plan_segments(3, dwell=20.0, crossfade=1.5, fps=24, same=[False, True, True]) == [
        Segment("body", (0,), 0, 1440, (0,), fade_in=48, fade_out=72)]
    with pytest.raises(ValueError):
        plan_segments(3, dwell=20.0, crossfade=1.5, fps=24, same=[False, True])


def test_plan_single_slot_and_tiny_reels_clamp_their_fades():
    assert plan_segments(1, dwell=20.0, crossfade=1.5, fps=24, total=7.3) == [
        Segment("body", (0,), 0, 175, (0,), fade_in=48, fade_out=72)]
    (only,) = plan_segments(1, dwell=20.0, crossfade=1.5, fps=24, total=1.0)
    assert only.frames == 24 and only.fade_in + only.fade_out == 24 and only.fade_in < only.fade_out
    (only,) = plan_segments(4, dwell=20.0, crossfade=1.5, fps=24, total=0.5)    # everything after 0.5 s dropped
    assert only.slots == (0,) and only.frames == 12


def test_plan_short_slots_clamp_the_fade_in_and_fold_a_last_slot_shorter_than_the_fade_out():
    segs = plan_segments(3, dwell=2.0, crossfade=0.5, fps=24)        # 2-s slots vs 2-s fade-in / 3-s fade-out
    _check_contiguous(segs, 144)
    assert [(s.kind, s.slots, s.start, s.frames) for s in segs] == [
        ("body", (0,), 0, 36), ("trans", (0, 1), 36, 12), ("body", (1,), 48, 96)]   # slot 2 folded into slot 1
    assert segs[0].fade_in == 36 and segs[-1].fade_out == 72


def test_plan_without_crossfade_cuts_hard_and_restarts_each_zoom():
    segs = plan_segments(3, dwell=2.0, crossfade=0.0, fps=24, fade_in=0, fade_out=0)
    assert [(s.kind, s.start, s.frames, s.tau) for s in segs] == [
        ("body", 0, 48, (0,)), ("body", 48, 48, (0,)), ("body", 96, 48, (0,))]


def test_plan_three_hours_of_slots_never_drift():
    total = 10795.3
    n = math.ceil(total / 20.0)
    segs = plan_segments(n, dwell=20.0, crossfade=1.5, fps=24, total=total)
    _check_contiguous(segs, round(total * 24))
    _check_tau_continuity(segs)
    bodies = [s for s in segs if s.kind == "body"]
    assert len(bodies) == n and all(b.start == k * 480 for k, b in enumerate(bodies))
    assert len({(s.kind, s.tau, s.frames, s.fade_in, s.fade_out) for s in segs}) == 5    # first/mid/last body, 2 trans kinds


def test_plan_non_integer_frames_per_slot_rounds_each_boundary_once():
    segs = plan_segments(200, dwell=2.3, crossfade=0.5, fps=24, fade_in=0.5, fade_out=0.5)
    _check_contiguous(segs, round(200 * 2.3 * 24))
    _check_tau_continuity(segs)
    bodies = [s for s in segs if s.kind == "body"]
    assert len(bodies) == 200
    assert all(b.start == round(k * 2.3 * 24) for k, b in enumerate(bodies) if k)   # never off by accumulated rounding
    assert {b.frames for b in bodies[1:-1]} == {43, 44}                               # 55.2 frames per slot, minus 12


def test_plan_rejects_bad_timing():
    for kw in (dict(dwell=0, crossfade=0.5), dict(dwell=2.0, crossfade=2.0), dict(dwell=2.0, crossfade=0.05),
               dict(dwell=2.0, crossfade=-1)):
        with pytest.raises(ValueError):
            plan_segments(3, fps=24, **kw)
    with pytest.raises(ValueError):
        plan_segments(0, dwell=2.0, crossfade=0.5, fps=24)
    with pytest.raises(ValueError):
        plan_segments(2, dwell=2.0, crossfade=0.5, fps=24, total=0.01)


def test_zoom_is_the_ken_burns_push_in_and_eases_out_for_long_spans():
    rate = timeline.zoom_rate(20.0, 1.5, 24)
    assert timeline.zoom_at(0, rate) == 1.0
    assert timeline.zoom_at(516, rate) == pytest.approx(render.ZOOM_END)     # one slot on screen = ken_burns_cmd's zoom
    knee = (timeline.ZOOM_KNEE - 1) / rate
    for t in (knee - 1, knee, knee + 1):                                       # same slope on both sides of the knee
        assert timeline.zoom_at(t + 1, rate) - timeline.zoom_at(t, rate) == pytest.approx(rate, rel=0.01)
    long = [timeline.zoom_at(t, rate) for t in range(0, 24 * 120, 24)]       # a photo kept for six slots
    assert all(b > a for a, b in zip(long, long[1:])) and timeline.ZOOM_MAX - 1e-4 < long[-1] < timeline.ZOOM_MAX
    assert timeline.zoom_at(10 ** 7, rate) <= timeline.ZOOM_MAX


def test_zoom_expr_offsets_the_clock_and_matches_zoom_at():
    rate = timeline.zoom_rate(20.0, 1.5, 24)
    expr = timeline.zoom_expr(444, rate)
    assert "(on+444)" in expr and "," in expr and "'" not in expr
    on = 10                                                            # evaluate the ffmpeg expression in Python
    py = expr.replace("on", str(on)).replace("if(", "_if(").replace("lte(", "_lte(").replace("exp(", "_exp(")
    val = eval(py, {"_if": lambda c, a, b: a if c else b, "_lte": lambda a, b: a <= b, "_exp": math.exp})
    assert val == pytest.approx(timeline.zoom_at(454, rate), abs=1e-9)
    far = timeline.zoom_expr(5000, rate).replace("on", "0").replace("if(", "_if(").replace("lte(", "_lte(").replace("exp(", "_exp(")
    assert eval(far, {"_if": lambda c, a, b: a if c else b, "_lte": lambda a, b: a <= b, "_exp": math.exp}) == \
        pytest.approx(timeline.zoom_at(5000, rate), abs=1e-9)


def test_body_cmd():
    rate = timeline.zoom_rate(20.0, 1.5, 24)
    cmd = timeline.body_cmd("c.jpg", "b.mp4", frames=444, tau=36, rate=rate, width=1920, height=1080, fps=24)
    s = " ".join(cmd)
    assert cmd[:4] == ["ffmpeg", "-y", "-i", "c.jpg"] and cmd[-1] == "b.mp4" and cmd.count("-i") == 1
    assert "zoompan=z='if(lte((on+36)" in s and ":d=444:" in s and "s=1920x1080:fps=24" in s
    assert "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'" in s                     # centred like render.ken_burns_cmd
    assert "-frames:v 444" in s and "fade=" not in s and "overlay" not in s
    assert " ".join(timeline.X264) in s
    cap = timeline.body_cmd("c.jpg", "b.mp4", frames=444, tau=0, rate=rate, width=1920, height=1080, fps=24,
                            caption="cap.png", fade_in=48, fade_out=72)
    s = " ".join(cap)
    assert cap.count("-i") == 2 and cap[cap.index("-i", 3) + 1] == "cap.png"
    assert s.index("zoompan") < s.index("overlay=0:0") < s.index("fade=t=in:s=0:n=48")   # caption after the zoom
    assert "fade=t=out:s=372:n=71" in s                                                # last frame is black


def test_transition_cmd_crossfades_two_zooms_with_their_own_captions():
    rate = timeline.zoom_rate(20.0, 1.5, 24)
    cmd = timeline.transition_cmd("a.jpg", "b.jpg", "t.mp4", frames=36, tau_a=480, rate=rate, width=1920, height=1080,
                                  fps=24, caption_b="cb.png", split=False)
    s = " ".join(cmd)
    assert cmd[cmd.index("-filter_complex") - 5:cmd.index("-filter_complex")] == ["a.jpg", "-i", "b.jpg", "-i", "cb.png"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.count("zoompan=") == 2 and "(on+480)" in graph and "(on+0)" in graph and ":d=36:" in graph
    assert "[1:v]zoompan" in graph and "[2:v]overlay" in graph and graph.count("overlay") == 1
    assert graph.index("overlay") < graph.index("xfade=transition=fade:duration=1.500000:offset=0")
    assert "-frames:v 36" in s and cmd[-1] == "t.mp4" and " ".join(timeline.X264) in s
    both = timeline.transition_cmd("a.jpg", "b.jpg", "t.mp4", frames=36, tau_a=480, rate=rate, width=1920, height=1080,
                                   fps=24, caption_a="ca.png", caption_b="cb.png", split=True)
    g2 = both[both.index("-filter_complex") + 1]
    assert "[2:v]overlay" in g2 and "[3:v]overlay" in g2 and both.count("-i") == 4
    assert g2.count("zoompan=") == 6 and g2.count("extractplanes") == 2 and g2.count("mergeplanes") == 2
    assert g2.index("[3:v]overlay") < g2.index("xfade=")


def test_split_planes_zoom_luma_and_chroma_on_the_same_clock():
    rate = timeline.zoom_rate(20.0, 1.5, 24)
    g = timeline._photo(0, None, "p", frames=444, tau=36, rate=rate, width=1920, height=1080, fps=24, split=True)
    assert g.startswith("[0:v]format=yuvj444p,extractplanes=y+u+v[py0][pu0][pv0];") and g.endswith("[p]")
    assert g.count("(on+36)") == 3 * 3                                     # 3 planes × 3 uses in the expression
    assert g.count("s=1920x1080:fps=24") == 1 and g.count("s=960x540:fps=24") == 2
    assert g.count("scale=iw/2:ih/2") == 2 and f"mergeplanes=0x001020:yuvj420p,setsar=1,format=yuv420p,{timeline.COLOUR}[p]" in g
    one = timeline._photo(0, 1, "p", frames=444, tau=36, rate=rate, width=1920, height=1080, fps=24, split=False)
    assert one.startswith("[0:v]zoompan=") and f"[pz][1:v]overlay=0:0:format=auto,format=yuv420p,{timeline.COLOUR}[p]" in one
    assert timeline.SPLIT_PLANES and "extractplanes" in timeline._photo(0, None, "p", frames=4, tau=0, rate=rate,
                                                                         width=320, height=180, fps=24)


def test_body_and_transition_share_identical_encoder_settings():
    """A stream-copy concat needs every segment encoded the same way (identical SPS/PPS)."""
    rate = timeline.zoom_rate(20.0, 1.5, 24)
    b = timeline.body_cmd("c.jpg", "o.mp4", frames=444, tau=36, rate=rate, width=1920, height=1080, fps=24)
    t = timeline.transition_cmd("a.jpg", "b.jpg", "o.mp4", frames=36, tau_a=480, rate=rate, width=1920, height=1080, fps=24)

    def encoder(cmd):
        out = cmd[cmd.index("-map"):]
        out[out.index("-frames:v") + 1] = "N"
        return out

    assert encoder(b) == encoder(t)
    assert timeline.concat_cmd("l.txt", "r.mp4") == ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "l.txt", "-c",
                                                     "copy", "-map_metadata", "-1", "r.mp4"]


def test_substitute_uses_the_previous_then_next_neighbour():
    assert timeline._substitute(["a", "x", "b", "x", "x"], {"x"}) == ["a", "a", "b", "b", "b"]
    assert timeline._substitute(["x", "x", "a", "b"], {"x"}) == ["a", "a", "a", "b"]
    with pytest.raises(RuntimeError):
        timeline._substitute(["x", "y"], {"x", "y"})
