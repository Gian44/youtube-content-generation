"""renderer._build_recap_short_cmd — clip-native A/V + quiet music + subtitles.

The FFmpeg command is built by a pure function so the audio/video graph can be
asserted without invoking FFmpeg.
"""

from __future__ import annotations


def _cmd(**overrides):
    from storyfactory.services import renderer

    params = dict(
        clip_path="clip.mp4",
        music_path="music.mp3",
        caption_path="caps.ass",
        output_path="out.mp4",
        width=1080,
        height=1920,
        fps=30,
        music_volume=0.10,
        has_audio=True,
        duration=52.0,
    )
    params.update(overrides)
    return renderer._build_recap_short_cmd(**params)


def test_cmd_preserves_original_audio_and_mixes_quiet_music():
    cmd = _cmd()
    joined = " ".join(cmd)

    assert "-an" not in cmd, "original audio must never be stripped"
    assert "amix=inputs=2" in joined, "clip audio must be mixed with music"
    assert "duration=first" in joined, "mix length is bounded by the clip audio"
    assert "normalize=0" in joined, "dialogue must not be auto-attenuated by amix"
    assert "volume=0.1" in joined, "music sits quietly under the dialogue"
    assert "-stream_loop" in cmd, "music loops to cover the clip"


def test_cmd_is_vertical_short_and_burns_captions():
    cmd = _cmd()
    joined = " ".join(cmd)
    assert "scale=1080:1920" in joined
    assert "crop=1080:1920" in joined
    assert "ass=" in joined, "subtitles burned in when a caption file is given"


def test_cmd_without_music_maps_clip_audio_directly():
    cmd = _cmd(music_path=None)
    joined = " ".join(cmd)
    assert "amix" not in joined, "nothing to mix when there is no music"
    assert "-an" not in cmd
    assert "0:a" in joined, "the clip's own audio is mapped through"


def test_cmd_music_only_when_clip_has_no_audio():
    cmd = _cmd(has_audio=False)
    joined = " ".join(cmd)
    assert "amix" not in joined, "no clip audio to mix against"
    assert "volume=0.1" in joined, "music plays as the only bed"
    assert "-stream_loop" in cmd


def test_cmd_without_captions_omits_ass_filter():
    cmd = _cmd(caption_path=None)
    joined = " ".join(cmd)
    assert "ass=" not in joined
    # still a valid vertical render
    assert "scale=1080:1920" in joined


def test_escape_ass_path_double_escapes_windows_drive_colon():
    # Regression: a single "\:" makes FFmpeg mis-parse the drive colon as a
    # filter-option separator on Windows; libass requires the doubled "\\:".
    from storyfactory.services import renderer

    assert renderer._escape_ass_path("C:\\data\\caps.ass") == "C\\\\:/data/caps.ass"
    # POSIX paths have no drive colon and pass through unchanged.
    assert renderer._escape_ass_path("/data/caps.ass") == "/data/caps.ass"


def test_cmd_uses_double_escaped_caption_path_for_windows():
    cmd = _cmd(caption_path="C:/data/caps.ass")
    joined = " ".join(cmd)
    assert "ass=C\\\\:/data/caps.ass" in joined


def test_cmd_bounds_looped_music_even_when_duration_unknown():
    # Music is fed with -stream_loop -1 (infinite). If duration can't be probed
    # (0.0), the encode must still be bounded by -shortest, or it runs forever.
    music_only = _cmd(has_audio=False, music_path="m.mp3", caption_path=None, duration=0.0)
    assert "-shortest" in music_only, "music-only render must be bounded"

    with_audio = _cmd(has_audio=True, music_path="m.mp3", duration=0.0)
    assert "-shortest" in with_audio


def test_cmd_without_music_does_not_force_shortest():
    # No looped/infinite input → the clip bounds the output; no -shortest needed.
    cmd = _cmd(music_path=None, has_audio=True)
    assert "-shortest" not in cmd
