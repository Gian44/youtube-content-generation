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
