"""Real ffmpeg: 5 synthetic images, 2 s dwell, a 30 s tone -> reel + looped final. Skipped without ffmpeg."""
import shutil
import subprocess

import pytest

from sof import render, tts

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_reel_and_loop_assembly(tmp_path):
    from PIL import Image
    imgs = []
    for i in range(5):
        p = tmp_path / f"{i}.jpg"
        Image.new("RGB", (1600, 900), (30 * i, 60, 120)).save(p)
        imgs.append(str(p))
    audio = tmp_path / "narration.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=30", "-b:a", "64k", str(audio)],
                   check=True, capture_output=True)
    reel = render.build_reel(imgs, str(tmp_path / "tmp"), dwell=2.0, crossfade=0.5, width=640, height=360, fps=24, batch=2, workers=2)
    reel_dur = tts.duration_seconds(reel)
    assert 5 * 2.0 + 0.5 - 1 < reel_dur < 5 * 2.0 + 0.5 + 1.5  # 5 clips of 2.5 s minus 4 crossfades of 0.5 = 10.5 s
    final = render.assemble(reel, str(audio), str(tmp_path / "final.mp4"), audio_duration=30.0, fps=24)
    assert 29.0 < tts.duration_seconds(final) < 31.5
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,codec_name",
                            "-of", "csv=p=0", final], capture_output=True, text=True).stdout.strip()
    assert probe.startswith("h264,640,360")


def _grid_line_spacing_per_frame(path, width, height):
    """For each frame: least-squares spacing of the bright vertical grid lines (sub-pixel)."""
    import numpy as np
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "gray", "-"], stdout=subprocess.PIPE)
    spacing = []
    while True:
        buf = p.stdout.read(width * height)
        if len(buf) < width * height:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(height, width)
        row = fr[height // 2 - 40:height // 2 - 25].min(axis=0).astype(float)  # min over a band removes horizontal lines
        thr, xs, i = row > 120, [], 0
        while i < width:
            if thr[i]:
                j = i
                while j < width and thr[j]:
                    j += 1
                seg = row[i:j] - row.min()
                xs.append(i + (seg * np.arange(len(seg))).sum() / max(seg.sum(), 1e-9)); i = j
            else:
                i += 1
        xs = np.array([x for x in xs if 60 < x < width - 60])
        if len(xs) >= 4:
            spacing.append(np.polyfit(np.arange(len(xs)), xs, 1)[0])
    return np.array(spacing)


def test_ken_burns_zoom_is_smooth_not_stepped(tmp_path):
    """Regression for the 'shaking' Gian saw: the zoom must advance a little every frame, not hold
    still and jump. Measured as the std-dev of the per-frame change in grid-line spacing relative
    to its mean — the old scale(t)+crop renderer scored ~2.1 here, 4× zoompan ~0.4."""
    import numpy as np
    from PIL import Image, ImageDraw
    w, h = 1920, 1080
    im = Image.new("RGB", (3840, 2160), (30, 60, 120)); d = ImageDraw.Draw(im)
    for x in range(0, 3840, 100):
        d.line([(x, 0), (x, 2160)], fill=(230, 230, 230), width=8)
    src = tmp_path / "grid.jpg"; im.save(src, quality=92)
    covered = render.cover_image(str(src), str(tmp_path / "cover.jpg"), width=w, height=h)
    clip = str(tmp_path / "clip.mp4")
    subprocess.run(render.ken_burns_cmd(covered, clip, dwell=3.0, crossfade=0.0, width=w, height=h, fps=24), check=True, capture_output=True)
    sp = _grid_line_spacing_per_frame(clip, w, h)
    assert len(sp) >= 70 and sp[-1] > sp[0] * 1.03          # it did zoom in
    steps = np.diff(sp)
    ratio = steps.std() / abs(steps.mean())
    assert ratio < 1.0, f"stepped zoom: std/mean={ratio:.2f}"
