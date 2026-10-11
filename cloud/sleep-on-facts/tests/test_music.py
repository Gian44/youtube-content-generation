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


def test_progressions_differ_per_variant(tmp_path):
    import hashlib
    a = music.render_loop(str(tmp_path / "a.wav"), seed=8, progression=1)
    b = music.render_loop(str(tmp_path / "b.wav"), seed=9, progression=2)
    assert hashlib.md5(open(a, "rb").read()).hexdigest() != hashlib.md5(open(b, "rb").read()).hexdigest()
    assert len(music.PROGRESSIONS) >= 5 and all(len(p) == 2 for p in music.PROGRESSIONS)
