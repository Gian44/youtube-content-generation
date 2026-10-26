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


class FakePocket:
    sample_rate = 24000

    def __init__(self):
        self.texts, self.prompts = [], []

    def get_state_for_audio_prompt(self, wav):
        self.prompts.append(wav)
        return {"voice": wav}

    def generate_audio(self, state, text):
        self.texts.append(text)
        return np.zeros(12000, dtype=np.float32)


def test_synthesize_pocket_speaks_missing_chunks_in_the_designed_voice(tmp_path):
    voice = tmp_path / "narrator.wav"
    voice.write_bytes(b"RIFF")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "0000.mp3").write_bytes(b"done")
    eng = FakePocket()
    paths = tts.synthesize_pocket(["a.", "b."], str(tmp_path / "out"), voice_wav=str(voice), engine=eng,
                                  write_wav=lambda p, s, sr: Path(p).write_bytes(b"w"),
                                  encode=lambda w, m: Path(m).write_bytes(b"m"))
    assert [Path(p).name for p in paths] == ["0000.mp3", "0001.mp3"] and eng.texts == ["b."]
    assert eng.prompts == [str(voice)] and not list((tmp_path / "out").glob("*.wav"))


def test_speak_falls_back_to_kokoro_for_the_whole_narration(monkeypatch, tmp_path):
    def broken(*a, **k):
        raise tts.PocketUnavailable("gated")
    monkeypatch.setattr(tts, "synthesize_pocket", broken)
    seen = {}
    monkeypatch.setattr(tts, "synthesize_kokoro", lambda chunks, out, **kw: seen.setdefault("kokoro", kw) and ["k"])
    cfg = Config(tts_backend="pocket")
    tts.speak(["x"], str(tmp_path), cfg)
    assert "kokoro" in seen and cfg.tts_backend == "kokoro" and tts.voice_label(cfg).startswith("kokoro")
    assert tts.voice_label(Config(tts_backend="pocket")).startswith("pocket narrator.wav") and Path(Config().pocket_voice).exists()


def test_pocket_text_splits_only_long_sentences_at_a_natural_break():
    long = ("In 1960 the bathyscaphe Trieste, carrying Jacques Piccard and Don Walsh, sank for nearly five hours through "
            "the dark water of the western Pacific, until it settled on the soft grey floor of the Challenger Deep.")
    out = tts.pocket_text("Short one. " + long)
    sents = [x for x in out.split(". ") if x]
    assert out.startswith("Short one. ") and all(len(x.split()) <= 30 for x in sents)
    assert "Pacific. Until it settled" in out and out.count("Trieste") == 1 and out.endswith("Challenger Deep.")
    assert tts.pocket_text("No breaks here at all " * 4 + "end.") == ("No breaks here at all " * 4 + "end.")


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload
        self.text = str(payload)

    def json(self):
        return self._p


def _audio(nbytes=4800):
    import base64
    return _Resp(200, {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"inlineData": {
        "data": base64.b64encode(b"\x01\x00" * (nbytes // 2)).decode()}}]}}]})


def _quota(kind):
    return _Resp(429, {"error": {"details": [{"violations": [{"quotaId": f"GenerateRequests{kind}PerProjectPerModel-FreeTier"}]},
                                              {"retryDelay": "3s"}]}})


def test_gemini_speaker_moves_to_the_next_model_when_a_day_is_used_up(monkeypatch):
    sp = tts.GeminiSpeaker(api_key="k", models=["m1", "m2"], voice="Charon", style="Read softly:", sleep=lambda s: None,
                           clock=lambda: 0.0)
    script = {"m1": [_audio(), _quota("PerDay")], "m2": [_quota("PerMinute"), _audio()]}
    seen = []

    def post(model, text):
        seen.append(model)
        return script[model].pop(0)
    monkeypatch.setattr(sp, "_post", post)
    assert len(sp.say("One. Two.")) == 4800 and len(sp.say("Three. Four.")) == 4800
    assert seen == ["m1", "m1", "m2", "m2"] and sp.exhausted == {"m1"} and sp.calls == {"m1": 1, "m2": 1}
    body = tts.gemini_tts_body("Hello.", voice="Charon", style="Style: soft.")
    assert body["contents"][0]["parts"][0]["text"] == "### DIRECTOR'S NOTES\nStyle: soft.\n\n### TRANSCRIPT\nHello."
    assert tts.gemini_tts_body("Hello.", voice="Charon", style="")["contents"][0]["parts"][0]["text"] == "Hello."
    assert body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Charon"


def test_gemini_refusal_is_split_and_exhaustion_falls_back_to_pocket(monkeypatch, tmp_path):
    sp = tts.GeminiSpeaker(api_key="k", models=["m1"], voice="Charon", style="", sleep=lambda s: None, clock=lambda: 0.0)
    replies = [_Resp(200, {"candidates": [{"finishReason": "SAFETY"}]}), _audio(100), _audio(100)]
    monkeypatch.setattr(sp, "_post", lambda m, t: replies.pop(0))
    assert len(sp.say("First half here. Second half there.")) == 100 + 14400 + 100     # 0.3 s pause between
    monkeypatch.setattr(tts, "gemini_speaker", lambda cfg: (_ for _ in ()).throw(tts.GeminiTTSUnavailable("quota")))
    seen = {}
    monkeypatch.setattr(tts, "synthesize_pocket", lambda chunks, out, **kw: seen.setdefault("pocket", kw) and ["p"])
    cfg = Config(tts_backend="gemini")
    tts.speak(["x"], str(tmp_path), cfg)
    assert "pocket" in seen and cfg.tts_backend == "pocket"
    short_cfg = Config(tts_backend="gemini")
    tts.speak(["x"], str(tmp_path), short_cfg, short=True)
    assert short_cfg.tts_backend == "gemini"                 # a Short's fallback never changes the video's voice


def test_a_cut_off_gemini_answer_is_never_kept(monkeypatch):
    import base64
    sp = tts.GeminiSpeaker(api_key="k", models=["m1"], voice="Charon", style="", sleep=lambda s: None, clock=lambda: 0.0)
    cut = _Resp(200, {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"inlineData": {
        "data": base64.b64encode(b"\x01\x00" * 50).decode()}}]}}]})
    replies = [cut, _audio(100), _audio(100)]
    monkeypatch.setattr(sp, "_post", lambda m, t: replies.pop(0))
    assert len(sp.say("A long first part. A long second part.")) == 100 + 14400 + 100


def test_stretch_pauses_lengthens_only_real_pauses():
    import numpy as np
    loud = (np.sin(np.arange(4800) / 3.0) * 12000).astype(np.int16)
    gap = np.zeros(int(24000 * 0.4), dtype=np.int16)        # a 0.4 s pause
    tiny = np.zeros(int(24000 * 0.1), dtype=np.int16)       # a 0.1 s breath between words
    pcm = np.concatenate([loud, tiny, loud, gap, loud]).tobytes()
    out = tts.stretch_pauses(pcm, factor=2.0)
    added = (len(out) - len(pcm)) / 2 / 24000
    assert 0.3 <= added <= 0.45 and tts.stretch_pauses(pcm, factor=1.0) == pcm
    assert tts.tune_filter(1.0, 1.0) == "" and "0.92" in tts.tune_filter(0.92, 1.0)


def test_split_at_pauses_cuts_one_recording_back_into_its_texts():
    import numpy as np
    speech = lambda sec: (np.sin(np.arange(int(24000 * sec)) / 3.0) * 12000).astype(np.int16)
    gap = np.zeros(int(24000 * 2.0), dtype=np.int16)
    breath = np.zeros(int(24000 * 0.3), dtype=np.int16)
    pcm = np.concatenate([speech(10), breath, speech(10), gap, speech(15), gap, speech(12)]).tobytes()
    pieces = tts.split_at_pauses(pcm, [200, 150, 120])
    assert pieces is not None and len(pieces) == 3
    secs = [len(p) / 2 / 24000 for p in pieces]
    assert abs(secs[0] - 20.3) < 0.2 and abs(secs[1] - 15) < 0.2 and abs(secs[2] - 12) < 0.2
    assert tts.split_at_pauses(np.concatenate([speech(30)]).tobytes(), [1, 1]) is None    # no pause → None


def test_long_video_leaves_a_flash_request_for_the_shorts(monkeypatch, tmp_path):
    sp = tts.GeminiSpeaker(api_key="k", models=["flash", "lite"], voice="Charon", style="", sleep=lambda s: None,
                           clock=lambda: 0.0)
    seen, cmds = [], []

    def post(model, text):
        seen.append(model)
        return _audio(4800)
    monkeypatch.setattr(sp, "_post", post)
    monkeypatch.setattr(tts, "gemini_speaker", lambda cfg: sp)
    monkeypatch.setattr(tts, "_run", lambda cmd, timeout: (cmds.append(cmd), Path(cmd[-1]).write_bytes(b"x")))
    cfg = Config(tts_backend="gemini", gemini_long_limits=(("flash", 2),),
                 gemini_tuning=(("flash", (1.0, 1.0, 1.0)), ("lite", (1.0, 0.9, 0.91))))
    tts.speak(["One.", "Two.", "Three."], str(tmp_path / "long"), cfg)
    assert seen == ["flash", "flash", "lite"] and sp.limits == {}
    filters = [" ".join(c) for c in cmds]
    assert "rubberband" not in filters[0] and "rubberband" not in filters[1]     # Flash chunks untouched ("R")
    assert ("tempo=0.9" in filters[2] and "pitch=0.91" in filters[2]) or not tts.has_rubberband()   # Lite chunk = "R2"
    seen.clear()
    tts.speak(["Teaser."], str(tmp_path / "short"), cfg, short=True)
    assert seen == ["flash"]                                     # the kept request voices the Shorts
