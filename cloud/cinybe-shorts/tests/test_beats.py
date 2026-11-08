import subprocess
from pathlib import Path

from cny import beats, config, cut
from cny.align import Cue


def test_parse_at_reads_every_way_the_writer_writes_a_time():
    assert beats.parse_at(95) == 95.0 and beats.parse_at("95.5") == 95.5 and beats.parse_at("01:35") == 95.0
    assert beats.parse_at("1:02:10") == 3730.0 and beats.parse_at("[12:04]") == 724.0 and beats.parse_at("95:06") == 5706.0
    assert beats.parse_at("soon") is None and beats.parse_at(None) is None and beats.parse_at(-3) is None


def test_normalize_interpolates_orders_and_clamps():
    out = beats.normalize([{"at": "01:40", "text": "a"}, {"at": None, "text": "b"}, {"at": "02:00", "text": "c"},
                           {"at": "01:50", "text": "d"}, {"at": "59:00", "text": "e"}], lo=90, hi=300)
    assert [b["at"] for b in out] == [100.0, 110.0, 120.0, 120.0, 300.0]          # gap filled, never backwards, clamped
    assert [b["at"] for b in beats.normalize([{"text": "x"}, {"text": "y"}], lo=10, hi=30)] == [10.0, 20.0]


def test_quoted_lines_snap_to_their_subtitle():
    cues = [Cue(100.0, 102.0, "They're coming to get you, Barbara."), Cue(140.0, 141.0, "Get in the house!")]
    bs = [{"at": 95.0, "text": "Johnny laughs."}, {"at": 120.0, "text": 'He says, "They\'re coming to get you, Barbara."'},
          {"at": 121.0, "text": "A man walks up."}, {"at": 150.0, "text": 'Ben yells, "Get in the house!" and runs.'}]
    n = beats.snap_quotes(bs, cues, lo=60, hi=200)
    assert n == 2 and bs[1]["at"] == 99.5 and bs[3]["at"] == 139.5
    assert [b["at"] for b in bs] == sorted(b["at"] for b in bs)                    # still in screen order
    assert beats.snap_quotes([{"at": 10.0, "text": 'She says "no."'}], cues, lo=0, hi=200) == 0   # too short to trust


def test_windows_start_at_the_moment_and_never_drift():
    ats, durs = [100.0, 104.0, 160.0], [6.0, 5.0, 4.0]
    w = beats.windows(ats, durs, end=200.0, film_end=300.0)
    assert w[0][0] == 99.5                                                        # a breath before the moment
    assert w[1][0] <= ats[1] + 3.0                                                # never more than 3 s past its moment
    assert w[2][0] == 159.5 and all(e - s >= d * 1.3 + 1.5 - 1e-6 for (s, e), d in zip(w, durs))
    assert all(e - s <= (d * 1.3 + 1.5) * 1.5 + 1e-6 for (s, e), d in zip(w, durs))   # stays on the moment
    end = beats.windows([295.0], [8.0], end=300.0, film_end=300.0)[0]
    assert end[1] == 300.0 and end[1] - end[0] >= 8 * 1.3 + 1.5 - 1e-6           # pulled back at the end of the film


def test_shots_and_segments_fill_the_beat_exactly_in_order():
    shots = beats.shots_for((98.5, 110.0), [101.0, 104.0, 107.5], dur=7.0, shot_seconds=3.0, min_len=2.0, max_len=5.0)
    assert sum(d for _, d in shots) >= 7.0 and [a for a, _ in shots] == sorted(a for a, _ in shots)
    segs = beats.segments(shots, 7.0, min_play=1.2, max_len=5.0)
    assert abs(sum(d for _, d in segs) - 7.0) < 0.01 and all(98.5 <= a < 110.0 for a, _ in segs)
    short = beats.shots_for((10.0, 12.0), [], dur=6.0, shot_seconds=3.0, min_len=2.0, max_len=5.0)
    assert sum(d for _, d in short) >= 6.0                                        # held longer, never replayed


def test_render_cmd_reads_each_piece_straight_from_the_film():
    cmd = beats.render_cmd("film.mkv", [(10.0, 2.0), (14.5, 3.0)], "o.mp4", dur=5.0, width=1920, height=1080, fps=30,
                           fit="blur", crop="1440:1080:240:0")
    j = " ".join(cmd)
    assert cmd.count("-i") == 2 and cmd[cmd.index("-ss") + 1] == "10.000" and "concat=n=2:v=1:a=0" in j
    assert "crop=1440:1080:240:0" in j and "tpad=stop_mode=clone" in j and cmd[cmd.index("-t", 10) + 1] and "-an" in cmd
    withsound = beats.render_cmd("film.mkv", [(10.0, 2.0)], "o.mp4", dur=2.0, width=1080, height=1920, fps=30, fit="crop", crop=None,
                                 audio=True)
    assert "[aout]" in " ".join(withsound) and "-an" not in withsound


def _color_film(path: str) -> None:
    """60 s, 4:3, six 10-s scenes with hard (bright/dark) cuts: black, lime, black, yellow, black, white."""
    cols = ["black", "0x00FF00", "black", "yellow", "black", "white"]
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for c in cols:
        cmd += ["-f", "lavfi", "-i", f"color=c={c}:size=640x480:rate=25:duration=10"]
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(6)) + "concat=n=6:v=1:a=0[v]", "-map", "[v]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)


def _center_rgb(video: str, at: float, tmp: Path) -> tuple[int, int, int]:
    from PIL import Image
    jpg = str(tmp / f"f{at:.2f}.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", video, "-frames:v", "1", jpg], check=True)
    with Image.open(jpg) as im:
        w, h = im.size
        px = im.convert("RGB").crop((w // 2 - 20, h // 2 - 20, w // 2 + 20, h // 2 + 20)).resize((1, 1)).getpixel((0, 0))
    return px


def test_each_beat_shows_the_moment_it_tells(tmp_path):
    """Real ffmpeg, end to end: beats stamped in the lime, yellow and white scenes show lime, yellow and white."""
    film = str(tmp_path / "film.mp4")
    _color_film(film)
    cuts = cut.scene_cuts(film, start=0, end=60, threshold=0.3)
    assert [round(c) for c in cuts] == [10, 20, 30, 40, 50]
    cfg = config.Config()
    bs = [{"at": 12.0, "text": "lime"}, {"at": 33.0, "text": "yellow"}, {"at": 51.0, "text": "white"}]
    durs = [3.0, 2.5, 2.0]
    files, info = beats.render(cfg, video=film, beats=bs, durs=durs, cuts=cuts, out_dir=tmp_path / "b", prefix="a00", end=60.0,
                               film_end=60.0, width=640, height=360, fps=25, fit="blur", crop=None)
    reel = beats.concat(files, str(tmp_path / "reel.mp4"), str(tmp_path / "list.txt"))
    total = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", reel],
                                 capture_output=True, text=True).stdout)
    assert abs(total - sum(durs)) < 0.15                                          # the reel is exactly as long as the voice
    want = {"lime": (0, 255, 0), "yellow": (255, 255, 0), "white": (255, 255, 255)}
    t = 0.0
    for b, d in zip(bs, durs):
        r, g, bl = _center_rgb(reel, t + d / 2, tmp_path)
        er, eg, eb = want[b["text"]]
        assert abs(r - er) < 60 and abs(g - eg) < 60 and abs(bl - eb) < 60, (b["text"], (r, g, bl))
        t += d
    assert all(r["window"][0] <= r["at"] for r in info)                          # footage starts at or before the moment
