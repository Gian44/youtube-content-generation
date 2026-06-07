"""recap_service.cut_clip — audio-preservation behavior."""

from __future__ import annotations

import types


def _capture_ffmpeg(monkeypatch):
    """Patch recap_service's subprocess.run to capture the command, no real ffmpeg."""
    from storyfactory.services import recap_service

    captured = {}

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(recap_service.subprocess, "run", fake_run)
    return captured


def _make_source(tmp_path) -> str:
    src = tmp_path / "source.mp4"
    src.write_bytes(b"not-a-real-video-but-exists")
    return str(src)


def test_cut_clip_drops_audio_by_default(tmp_path, monkeypatch):
    from storyfactory.services import recap_service

    captured = _capture_ffmpeg(monkeypatch)
    src = _make_source(tmp_path)

    recap_service.cut_clip(src, 10.0, 5.0, output_dir=str(tmp_path / "clips"))

    cmd = captured["cmd"]
    assert "-an" in cmd, "default behavior must strip source audio (tts_narration mode)"


def test_cut_clip_keeps_audio_when_requested(tmp_path, monkeypatch):
    from storyfactory.services import recap_service

    captured = _capture_ffmpeg(monkeypatch)
    src = _make_source(tmp_path)

    recap_service.cut_clip(
        src, 10.0, 5.0, output_dir=str(tmp_path / "clips"), keep_audio=True
    )

    cmd = captured["cmd"]
    assert "-an" not in cmd, "keep_audio=True must preserve the clip's original audio"
    assert "-c:a" in cmd, "audio must be encoded when preserved"
    assert "aac" in cmd
