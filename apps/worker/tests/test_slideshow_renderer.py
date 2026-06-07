"""slideshow_renderer — image slideshow reel + memory-cheap 3h assembly.

Sleep videos open directly on a slideshow of stock images (no blue title card,
no captions). The reel of Ken Burns clips is encoded ONCE, then looped under the
narration with a stream-copy so the 3-hour final assembly does not re-encode video.
These tests pin the FFmpeg command builders (pure) and the dry-run path.
"""

from __future__ import annotations

from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Ken Burns per-image clip command
# ---------------------------------------------------------------------------

def test_ken_burns_clip_cmd_has_zoompan_and_loop():
    from storyfactory.services import slideshow_renderer as sr

    cmd = sr._build_ken_burns_clip_cmd(
        "/img/a.jpg", "/out/clip0.mp4",
        dwell=20, crossfade=2, width=1920, height=1080, fps=24, ken_burns=True,
    )
    joined = " ".join(cmd)
    assert "/img/a.jpg" in cmd
    assert "/out/clip0.mp4" == cmd[-1]
    assert "-loop" in cmd and "1" in cmd
    assert "zoompan" in joined
    # clip duration covers dwell + crossfade so the xfade overlap is hidden
    assert "22" in cmd  # str(dwell + crossfade)


def test_ken_burns_clip_cmd_static_when_disabled():
    from storyfactory.services import slideshow_renderer as sr

    cmd = sr._build_ken_burns_clip_cmd(
        "/img/a.jpg", "/out/clip0.mp4",
        dwell=20, crossfade=2, width=1920, height=1080, fps=24, ken_burns=False,
    )
    joined = " ".join(cmd)
    assert "zoompan" not in joined
    assert "force_original_aspect_ratio=increase" in joined
    assert "crop=1920:1080" in joined


# ---------------------------------------------------------------------------
# xfade chain command (offsets from cumulative durations)
# ---------------------------------------------------------------------------

def test_xfade_chain_cmd_offsets_and_node_count():
    from storyfactory.services import slideshow_renderer as sr

    cmd = sr._build_xfade_chain_cmd(
        ["/c0.mp4", "/c1.mp4", "/c2.mp4"],
        [22.0, 22.0, 22.0],
        "/reel.mp4",
        crossfade=2, width=1920, height=1080, fps=24,
    )
    joined = " ".join(cmd)
    # one xfade per transition (n-1 = 2)
    assert joined.count("xfade=") == 2
    # offsets accumulate: first at dwell (20), second at 2*dwell (40)
    assert "offset=20" in joined
    assert "offset=40" in joined
    assert "duration=2" in joined
    # all three inputs present
    assert cmd.count("-i") == 3
    assert cmd[-1] == "/reel.mp4"


def test_xfade_chain_cmd_handles_unequal_partial_durations():
    from storyfactory.services import slideshow_renderer as sr

    # Two partial reels of different lengths (e.g. batches of differing size).
    cmd = sr._build_xfade_chain_cmd(
        ["/p0.mp4", "/p1.mp4"],
        [200.0, 80.0],
        "/reel.mp4",
        crossfade=2, width=1920, height=1080, fps=24,
    )
    joined = " ".join(cmd)
    assert joined.count("xfade=") == 1
    assert "offset=198" in joined  # 200 - crossfade


# ---------------------------------------------------------------------------
# final loop assembly (NO video re-encode)
# ---------------------------------------------------------------------------

def test_loop_assembly_stream_copies_video_under_audio():
    from storyfactory.services import slideshow_renderer as sr

    cmd = sr._build_loop_assembly_cmd(
        "/reel.mp4", "/audio.mp3", "/final.mp4", audio_duration=10800.0, fps=24,
    )
    assert "-stream_loop" in cmd and "-1" in cmd
    # video is copied, never re-encoded (the whole point — 3h encode would be huge)
    vi = cmd.index("-c:v")
    assert cmd[vi + 1] == "copy"
    # bounded by the narration duration, no -shortest needed
    ti = cmd.index("-t")
    assert cmd[ti + 1] == "10800.0"
    assert "+faststart" in cmd
    # no title card / no captions
    joined = " ".join(cmd)
    assert "drawtext" not in joined
    assert "ass=" not in joined


def test_loop_assembly_uses_shortest_when_duration_unknown():
    from storyfactory.services import slideshow_renderer as sr

    cmd = sr._build_loop_assembly_cmd(
        "/reel.mp4", "/audio.mp3", "/final.mp4", audio_duration=None, fps=24,
    )
    assert "-shortest" in cmd
    assert "-t" not in cmd


# ---------------------------------------------------------------------------
# render_sleep_video — dry run (no ffmpeg, no DB)
# ---------------------------------------------------------------------------

def test_render_sleep_video_dry_run_writes_placeholder(monkeypatch, tmp_path):
    from storyfactory.services import slideshow_renderer as sr

    class _FakeSession:
        def add(self, *_a):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sr, "get_settings", lambda: SimpleNamespace(dry_run=True))
    monkeypatch.setattr(sr, "get_session", lambda: _FakeSession())

    story = SimpleNamespace(id="abcd1234", voice_persona="calm")
    tts_job = SimpleNamespace(output_path=str(tmp_path / "a.mp3"), duration_seconds=10800.0)

    job = sr.render_sleep_video(
        story, tts_job, [], batch_id="batch123",
        output_dir=str(tmp_path / "renders"),
    )

    assert job.status == "completed"
    assert job.output_path.endswith(".mp4")
    import os
    assert os.path.exists(job.output_path)
    assert job.duration_seconds == 10800.0


def test_render_sleep_video_fails_clean_without_audio(monkeypatch, tmp_path):
    """No tts output_path must fail fast with a clear error, BEFORE the expensive
    reel encode — never crash with a TypeError deep inside the ffmpeg command."""
    from storyfactory.services import slideshow_renderer as sr

    class _FakeSession:
        def add(self, *_a):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sr, "get_settings", lambda: SimpleNamespace(dry_run=False))
    monkeypatch.setattr(sr, "get_session", lambda: _FakeSession())

    def _boom(*_a, **_k):
        raise AssertionError("reel must not be built when there is no narration audio")

    monkeypatch.setattr(sr, "build_slideshow_reel", _boom)

    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    asset = SimpleNamespace(id="a1", local_path=str(img))
    story = SimpleNamespace(id="s1", voice_persona="calm")
    tts_job = SimpleNamespace(output_path=None, duration_seconds=10800.0)

    job = sr.render_sleep_video(
        story, tts_job, [asset], batch_id="b1", output_dir=str(tmp_path / "r")
    )

    assert job.status == "failed"
    assert "output_path" in (job.error or "")
