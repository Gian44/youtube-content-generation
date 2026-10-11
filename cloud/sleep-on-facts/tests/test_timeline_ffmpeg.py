"""Real ffmpeg: build_timeline_reel on synthetic photos (small frames, ~10 s total). Skipped without ffmpeg."""
import shutil
import subprocess

import numpy as np
import pytest

from sof import timeline

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg not installed")

COLOURS = [(150, 40, 40), (40, 120, 40), (40, 40, 150), (130, 130, 40), (110, 40, 110)]
CAPTION = "Photo: Someone · CC BY-SA 4.0 · Wikimedia Commons"


def _photos(tmp_path, colours, size=(640, 360)):
    from PIL import Image
    out = []
    for i, c in enumerate(colours):
        p = tmp_path / f"photo{i}.jpg"
        Image.new("RGB", size, c).save(p, quality=95)
        out.append(str(p))
    return out


def _frames(path, w, h, fmt="rgb24"):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", fmt, "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape((-1, h, w, 3) if fmt == "rgb24" else (-1, h, w))


def _centre(frame):
    return frame[60:120, 100:220].reshape(-1, 3).mean(axis=0)        # clear of the bottom-left credit line


def _decoded_frames(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return int(out.strip())


def test_reel_follows_the_slots_frame_exactly(tmp_path):
    imgs = _photos(tmp_path, COLOURS)
    work = tmp_path / "t"
    reel = timeline.build_timeline_reel(imgs, str(work), dwell=2.0, crossfade=0.5, width=320, height=180, fps=24,
                                        total=9.37, fade_in=0.25, fade_out=0.25, captions={imgs[1]: CAPTION}, workers=2)
    assert sorted(p.name for p in work.iterdir()) == ["timeline.mp4"]          # segments/covers cleaned up
    f = _frames(reel, 320, 180)
    assert len(f) == 225 == timeline.probe(reel)[0] == _decoded_frames(reel)    # round(9.37 * 24), plays end to end
    assert f[0].max() < 12 and f[-1].max() < 12                                 # fades in from / out to black
    for k, c in enumerate(COLOURS):                                             # each slot's middle shows its photo
        mid = (48 * k + min(48 * (k + 1), 225)) // 2
        assert np.abs(_centre(f[mid]) - c).max() < 8, (k, _centre(f[mid]))
    for k in range(4):                                                          # mid-crossfade is a 50/50 blend
        blend = (np.array(COLOURS[k]) + COLOURS[k + 1]) / 2
        assert np.abs(_centre(f[48 * (k + 1) - 6]) - blend).max() < 8, (k, _centre(f[48 * (k + 1) - 6]))
        assert np.abs(_centre(f[48 * (k + 1)]) - COLOURS[k + 1]).max() < 8      # crossfade done at the slot start
    corner = lambda fr: int(fr[158:176, 4:160].max())                          # noqa: E731
    assert corner(f[72]) > _centre(f[72]).max() + 40                            # credit line on the captioned photo
    assert corner(f[120]) < _centre(f[120]).max() + 10                          # none on the others


def test_duplicates_merge_and_repeated_photos_render_once(tmp_path):
    a, b, c = _photos(tmp_path, COLOURS[:3])
    work = tmp_path / "t"
    reel = timeline.build_timeline_reel([a, a, b, c, b, c, b], str(work), dwell=2.0, crossfade=0.5, width=320,
                                        height=180, fps=24, fade_in=0.25, fade_out=0.25, cleanup=False)
    f = _frames(reel, 320, 180)
    assert len(f) == 7 * 48
    # A A merged: one 84-frame body, no crossfade into itself; B-mid, C-mid and B→C, C→B reused
    assert len(list(work.glob("body_*.mp4"))) == 4 and len(list(work.glob("trans_*.mp4"))) == 3
    assert len((work / "timeline.txt").read_text().splitlines()) == 11
    assert not list(work.glob("*.part.*"))
    for i in (24, 42, 48, 72):
        assert np.abs(_centre(f[i]) - COLOURS[0]).max() < 8, i
    for i, col in ((120, 1), (168, 2), (216, 1), (264, 2), (312, 1)):
        assert np.abs(_centre(f[i]) - COLOURS[col]).max() < 8, i


def test_unreadable_or_failing_photo_is_replaced_by_its_neighbour(tmp_path, monkeypatch):
    a, b, c, d = _photos(tmp_path, COLOURS[:4])
    (tmp_path / "photo1.jpg").write_bytes(b"not a jpeg")                       # b: the cover step fails
    real = timeline.body_cmd

    def flaky(image, out, **kw):                                               # d's cover works but ffmpeg fails on it
        cmd = real(image, out, **kw)
        return ["ffmpeg", "-v", "error", "-i", "/nonexistent.jpg", out] if image == covers.get(d) else cmd

    covers = {}
    real_cover = timeline._cover_all

    def spy(images, tmp, **kw):
        covers.update(real_cover(images, tmp, **kw))
        return covers

    monkeypatch.setattr(timeline, "body_cmd", flaky)
    monkeypatch.setattr(timeline, "_cover_all", spy)
    reel = timeline.build_timeline_reel([a, b, c, d, a], str(tmp_path / "t"), dwell=2.0, crossfade=0.5, width=320,
                                        height=180, fps=24, fade_in=0.25, fade_out=0.25)
    f = _frames(reel, 320, 180)
    assert len(f) == 5 * 48                                                      # timing never shifts
    for i, col in ((24, 0), (72, 0), (120, 2), (168, 2), (216, 0)):              # b → a (previous), d → c (previous)
        assert np.abs(_centre(f[i]) - COLOURS[col]).max() < 8, i


def test_split_plane_zoom_gives_the_same_luma_as_one_zoompan(tmp_path):
    """The faster split-plane chain must be the same picture: luma bit-identical, chroma within a few levels."""
    from PIL import Image, ImageDraw
    from sof import render
    w, h, n = 640, 360, 4
    im = Image.new("RGB", (1600, 900))
    d = ImageDraw.Draw(im)
    for x in range(0, 1600, 4):
        d.line([(x, 0), (x, 900)], fill=(x % 256, (x * 3) % 256, 255 - x % 256))
    for y in range(0, 900, 37):
        d.line([(0, y), (1600, y)], fill=(250, 250, 250), width=2)
    im.save(tmp_path / "src.png")
    cover = render.cover_image(str(tmp_path / "src.png"), str(tmp_path / "cover.jpg"), width=w, height=h, dim=0.75)
    rate = timeline.zoom_rate(20.0, 1.5, 24)
    planes = {}
    for split in (False, True):
        g = timeline._photo(0, None, "p", frames=n, tau=300, rate=rate, width=w, height=h, fps=24, split=split)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", cover, "-filter_complex", f"{g};[p]null[v]", "-map", "[v]",
                              "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                             capture_output=True, check=True).stdout
        f = np.frombuffer(raw, np.uint8).reshape(n, -1).astype(int)
        planes[split] = f[:, :w * h], f[:, w * h:]
    (y0, c0), (y1, c1) = planes[False], planes[True]
    assert np.array_equal(y0, y1)
    assert np.abs(c0 - c1).max() <= 12 and abs((c0 - c1).mean()) < 0.5


def _spacing(profile):
    """Sub-pixel spacing of the bright lines in a 1-D profile (centroids, least-squares slope); None if too faint."""
    p = profile.astype(float)
    lo, hi = p.min(), p.max()
    if hi - lo < 12:
        return None
    thr, xs, i, n = p > (lo + hi) / 2, [], 0, len(p)
    while i < n:
        if thr[i]:
            j = i
            while j < n and thr[j]:
                j += 1
            seg = p[i:j] - lo
            xs.append(i + (seg * np.arange(j - i)).sum() / seg.sum())
            i = j
        else:
            i += 1
    xs = np.array([x for x in xs if 8 < x < n - 8])
    return float(np.polyfit(np.arange(len(xs)), xs, 1)[0]) if len(xs) >= 4 else None


def _jump(series, start, join, stop):
    """Discontinuity at ``join`` between straight-line fits of frames [start, join) and [join, stop)."""
    x1, x2 = np.arange(start, join), np.arange(join, stop)
    p1 = np.polyfit(x1, [series[i] for i in x1], 1)
    p2 = np.polyfit(x2, [series[i] for i in x2], 1)
    return float(np.polyval(p2, join - 0.5) - np.polyval(p1, join - 0.5))


def test_zoom_runs_on_across_segment_joins(tmp_path):
    """Photo A (vertical lines) → crossfade → photo B (horizontal lines), as three separate ffmpeg runs.
    A's zoom must run on from its body into the transition, and B's from the transition into its body:
    an off-by-one zoom clock would show up as a one-step jump (~0.08 px here) at the join."""
    from PIL import Image, ImageDraw
    w, h = 960, 540
    a = Image.new("RGB", (2 * w, 2 * h), (20, 30, 60))
    d = ImageDraw.Draw(a)
    for x in range(0, 2 * w, 96):
        d.line([(x, 0), (x, 2 * h)], fill=(235, 235, 235), width=3)
    b = Image.new("RGB", (2 * w, 2 * h), (60, 30, 20))
    d = ImageDraw.Draw(b)
    for y in range(0, 2 * h, 96):
        d.line([(0, y), (2 * w, y)], fill=(235, 235, 235), width=3)
    a.save(tmp_path / "a.png")
    b.save(tmp_path / "b.png")
    # dwell 1 s, crossfade 0.5 s → A body frames 0-11, transition 12-23, B body 24-47
    reel = timeline.build_timeline_reel([str(tmp_path / "a.png"), str(tmp_path / "b.png")], str(tmp_path / "t"),
                                        dwell=1.0, crossfade=0.5, width=w, height=h, fps=24, total=2.0, fade_in=0, fade_out=0)
    f = _frames(reel, w, h, fmt="gray")
    assert len(f) == 48
    sa = [_spacing(fr[h // 2 - 12:h // 2 + 12].min(axis=0)) for fr in f]       # min over rows hides B's lines
    sb = [_spacing(fr[:, w // 2 - 12:w // 2 + 12].min(axis=1)) for fr in f]    # min over columns hides A's lines
    a_frames, b_frames = range(0, 22), range(15, 48)                           # while each photo is >= 25 % visible
    assert all(sa[i] is not None for i in a_frames) and all(sb[i] is not None for i in b_frames)
    step_a, step_b = np.diff([sa[i] for i in a_frames]), np.diff([sb[i] for i in b_frames])
    for steps in (step_a, step_b):
        assert (steps > 0).all() and steps.std() / steps.mean() < 0.5           # zooming in, smoothly
    expected = 48 * timeline.zoom_rate(1.0, 0.5, 24)                            # spacing 48 px × zoom per frame ≈ 0.08 px
    assert abs(np.median(step_a) - expected) < 0.25 * expected and abs(np.median(step_b) - expected) < 0.25 * expected
    assert abs(_jump(sa, 0, 12, 22)) < 0.4 * expected                           # A: body → transition
    assert abs(_jump(sb, 15, 24, 48)) < 0.4 * expected                          # B: transition → body
