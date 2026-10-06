import os
import subprocess
from sof.render import ken_burns_cmd, xfade_chain_cmd, loop_assembly_cmd, plan_batches, thumbnail


def test_cmd_builders():
    kb = " ".join(ken_burns_cmd("a.jpg", "a.mp4", dwell=20, crossfade=1.5, width=1920, height=1080, fps=24))
    assert "zoompan" not in kb and "-t 21.5" in kb and "eval=frame" in kb and "crop=1920:1080:" in kb
    xf = " ".join(xfade_chain_cmd(["a.mp4", "b.mp4", "c.mp4"], [21.5, 21.5, 21.5], "r.mp4", crossfade=1.5, fps=24))
    assert xf.count("xfade=") == 2 and "offset=20" in xf and "offset=40" in xf
    la = " ".join(loop_assembly_cmd("reel.mp4", "n.mp3", "f.mp4", audio_duration=10800.0, fps=24))
    assert "-stream_loop -1" in la and "-c:v copy" in la and "-t 10800.0" in la
    assert "-shortest" in " ".join(loop_assembly_cmd("r", "n", "f", audio_duration=None, fps=24))


def test_plan_batches():
    assert plan_batches(45, 20) == [(0, 20), (20, 40), (40, 45)]
    assert plan_batches(3, 20) == [(0, 3)]


def test_thumbnail_renders(tmp_path):
    from PIL import Image
    src = tmp_path / "src.jpg"
    Image.new("RGB", (800, 600), (40, 60, 120)).save(src)
    out = thumbnail(str(src), "Calm Facts About The Deep Sea to Fall Asleep To (3 Hours)", str(tmp_path / "t.jpg"))
    with Image.open(out) as im:
        assert im.size == (1280, 720)
