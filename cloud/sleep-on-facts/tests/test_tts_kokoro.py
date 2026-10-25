from pathlib import Path

import numpy as np
import pytest

from sof import tts
from sof.config import Config


def test_parse_voice_mix_normalises_weights():
    assert tts.parse_voice_mix("bm_george:0.6,bm_lewis:0.4") == [("bm_george", 0.6), ("bm_lewis", 0.4)]
    assert tts.parse_voice_mix("bm_george") == [("bm_george", 1.0)]
    assert tts.parse_voice_mix("a:3, b:1") == [("a", 0.75), ("b", 0.25)]
    with pytest.raises(ValueError):
        tts.parse_voice_mix("")


class FakeKokoro:
    def __init__(self):
        self.calls = []

    def get_voice_style(self, name):
        return np.full((510, 1, 256), {"bm_george": 1.0, "bm_lewis": 0.0}[name], dtype=np.float32)

    def create(self, text, *, voice, speed, lang, sentence_pause, clause_pause):
        self.calls.append((text, float(voice.mean()), speed, lang, sentence_pause))
        return np.zeros(24000, dtype=np.float32), 24000


def test_synthesize_kokoro_blends_the_voice_and_keeps_existing_chunks(tmp_path):
    eng = FakeKokoro()
    (tmp_path / "0001.mp3").write_bytes(b"done")                # already spoken in an earlier attempt
    written = []
    paths = tts.synthesize_kokoro(["one.", "two.", "three."], str(tmp_path), voice="bm_george:0.6,bm_lewis:0.4",
                                  speed=0.88, engine=eng, write_wav=lambda p, s, sr: Path(p).write_bytes(b"wav"),
                                  encode=lambda wav, mp3: (written.append(mp3), Path(mp3).write_bytes(b"mp3")))
    assert [Path(p).name for p in paths] == ["0000.mp3", "0001.mp3", "0002.mp3"]
    assert [c[0] for c in eng.calls] == ["one.", "three."]
    assert eng.calls[0][1] == pytest.approx(0.6) and eng.calls[0][2:] == (0.88, "en-gb", 0.55)
    assert not list(tmp_path.glob("*.wav")) and len(written) == 2


def test_speak_routes_by_backend(monkeypatch):
    seen = {}
    monkeypatch.setattr(tts, "synthesize_kokoro", lambda chunks, out, **kw: seen.setdefault("kokoro", kw) and [])
    monkeypatch.setattr(tts, "synthesize", lambda chunks, out, **kw: seen.setdefault("openai", kw) and [])
    tts.speak(["x"], "d", Config(tts_backend="kokoro"), short=True)
    tts.speak(["x"], "d", Config(tts_backend="openai", openai_api_key="k"))
    assert seen["kokoro"]["speed"] == Config().kokoro_short_speed and seen["kokoro"]["voice"] == "bm_george:0.6,bm_lewis:0.4"
    assert seen["openai"]["voice"] == "echo" and seen["openai"]["api_key"] == "k"
    assert tts.post_filter(Config(tts_backend="kokoro")) == tts.KOKORO_EQ and tts.post_filter(Config(tts_backend="openai")) == ""
    assert tts.estimate_cost_usd(100_000, tts.cost_model(Config())) == 0.0


def test_loudnorm_takes_the_voice_filters():
    cmd = tts.loudnorm_cmd("a.mp3", "b.mp3", tts.KOKORO_EQ)
    assert cmd[cmd.index("-af") + 1] == tts.KOKORO_EQ + ",loudnorm=I=-18:TP=-2:LRA=11"
    assert tts.loudnorm_cmd("a", "b")[5] == "loudnorm=I=-18:TP=-2:LRA=11"
