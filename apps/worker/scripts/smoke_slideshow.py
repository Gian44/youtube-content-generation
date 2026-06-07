"""Live smoke test for the sleep slideshow FFmpeg graphs (requires ffmpeg).

Generates a few tiny test images, builds a Ken-Burns + xfade reel, then loops it
under a short silent audio with the stream-copy assembly — exercising the exact
command builders the pipeline uses. Verifies the final video is non-empty and its
duration tracks the audio. Run: python scripts/smoke_slideshow.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from storyfactory.services import slideshow_renderer as sr


def _make_image(path: str, color: str, w: int, h: int) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}", "-frames:v", "1", path],
        check=True, capture_output=True, text=True,
    )


def _make_silent_audio(path: str, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", str(seconds), "-q:a", "9", path],
        check=True, capture_output=True, text=True,
    )


def _duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def main() -> int:
    W, H, FPS = 640, 360, 24  # small + low fps so the smoke is fast
    with tempfile.TemporaryDirectory() as tmp:
        imgs = []
        for i, color in enumerate(["0x1b2a4a", "0x2a4a3b", "0x4a2a3b", "0x3b3b1b"]):
            p = os.path.join(tmp, f"img_{i}.png")
            _make_image(p, color, W, H)
            imgs.append(p)

        # Build the reel via the real builder (Ken Burns + batched xfade).
        reel = sr.build_slideshow_reel(
            imgs, dwell=1.0, crossfade=0.5, width=W, height=H, fps=FPS, ken_burns=True, batch_size=2
        )
        assert os.path.exists(reel) and os.path.getsize(reel) > 0, "reel not produced"
        reel_dur = _duration(reel)
        print(f"reel ok: {reel} ({reel_dur:.2f}s)")

        # Loop the reel under a 6s silent audio (audio > reel, so it must loop).
        audio = os.path.join(tmp, "audio.mp3")
        _make_silent_audio(audio, 6.0)
        out = os.path.join(tmp, "final.mp4")
        cmd = sr._build_loop_assembly_cmd(reel, audio, out, audio_duration=6.0, fps=FPS)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print("ASSEMBLY FAILED:\n" + result.stderr[-1500:])
            return 1

        assert os.path.exists(out) and os.path.getsize(out) > 0, "final video empty"
        final_dur = _duration(out)
        print(f"final ok: {out} ({final_dur:.2f}s)")
        # Final duration should track the 6s audio (looping the ~shorter reel),
        # within a keyframe-rounding tolerance.
        assert abs(final_dur - 6.0) < 1.5, f"final duration {final_dur} not ~6s"
        print("SMOKE PASS: Ken Burns + xfade reel + loop-copy assembly all valid.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
