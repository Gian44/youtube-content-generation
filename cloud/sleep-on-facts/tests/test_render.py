import os
import subprocess
from sof.render import ken_burns_cmd, xfade_chain_cmd, loop_assembly_cmd, plan_batches


def test_cmd_builders():
    kb = " ".join(ken_burns_cmd("a.jpg", "a.mp4", dwell=20, crossfade=1.5, width=1920, height=1080, fps=24))
    assert "zoompan=" in kb and "d=516" in kb and "-frames:v 516" in kb and "s=1920x1080" in kb and "-loop" not in kb
    xf = " ".join(xfade_chain_cmd(["a.mp4", "b.mp4", "c.mp4"], [21.5, 21.5, 21.5], "r.mp4", crossfade=1.5, fps=24))
    assert xf.count("xfade=") == 2 and "offset=20" in xf and "offset=40" in xf
    la = " ".join(loop_assembly_cmd("reel.mp4", "n.mp3", "f.mp4", audio_duration=10800.0, fps=24))
    assert "-stream_loop -1" in la and "-c:v copy" in la and "-t 10800.0" in la
    assert "-shortest" in " ".join(loop_assembly_cmd("r", "n", "f", audio_duration=None, fps=24))


def test_plan_batches():
    assert plan_batches(45, 20) == [(0, 20), (20, 40), (40, 45)]
    assert plan_batches(3, 20) == [(0, 3)]


def test_cover_image_fills_landscape_and_shows_portrait_whole_dimmed(tmp_path):
    from PIL import Image, ImageStat
    from sof.render import cover_image
    land = tmp_path / "land.jpg"; Image.new("RGB", (1600, 900), (200, 200, 200)).save(land)
    port = tmp_path / "port.jpg"; Image.new("RGB", (900, 1600), (200, 200, 200)).save(port)
    a = cover_image(str(land), str(tmp_path / "a.jpg"), width=160, height=90, scale=2)
    b = cover_image(str(port), str(tmp_path / "b.jpg"), width=160, height=90, scale=2, dim=0.75)
    with Image.open(a) as ia, Image.open(b) as ib:
        assert ia.size == ib.size == (320, 180)
        assert ImageStat.Stat(ia.convert("L")).mean[0] > 190                        # untouched brightness
        centre = ImageStat.Stat(ib.convert("L").crop((140, 20, 180, 160))).mean[0]
        edge = ImageStat.Stat(ib.convert("L").crop((0, 20, 30, 160))).mean[0]
        assert 135 < centre < 165 and edge < centre - 40                          # whole picture, dimmed; darker blurred sides
