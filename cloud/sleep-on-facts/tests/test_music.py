import wave

from sof import music


def test_loop_is_seamless_mono_wav(tmp_path):
    out = music.render_loop(str(tmp_path / "bed.wav"))
    with wave.open(out) as w:
        assert w.getnchannels() == 1 and w.getframerate() == music.SR
        secs = w.getnframes() / music.SR
    assert 40 < secs < 120  # a short loop, not the whole video


def test_commands():
    n = " ".join(music.normalize_cmd("loop.wav", "bed.wav", bed_lufs=-34))
    assert "loudnorm=I=-34" in n and "lowpass" in n
    m = " ".join(music.mix_cmd("narration.mp3", "bed.wav", "out.mp3"))
    assert "aloop=loop=-1" in m and "normalize=0" in m and "duration=first" in m
