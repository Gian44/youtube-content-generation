"""Felix (Puello Media) 2026-11: a Short shows a few emphasis words (a key number, a main character's name at its first
mention), never the narration as captions; the long recap shows no words at all."""
import shutil
import subprocess
from pathlib import Path

import pytest

from cc import textbudget as tb
from cny import config, render

BEATS = [{"text": "The car will not start, and Barbra runs for the farmhouse.", "at": 2.0},
         {"text": "Johnny laughs, \"They're coming to get you, Barbra.\" A German tourist and 7 more of them gather.", "at": 6.0},
         {"text": "Inside, Ben swings a tire iron. He tells Barbra to stay, and Ben Hollis boards the door.", "at": 9.0}]
DURS = [4.0, 6.0, 5.0]
NAMES = ["Barbra", "Johnny", "Ben Hollis"]


def _picked(beats, durs, names):
    cues = render.emphasis_cues(render.beat_cues(beats, durs), names)
    return [t for _, _, t in tb.pick(tb.words_from_cues(cues), duration=sum(durs), per_minute=100, min_gap=0)]


def test_emphasis_cues_keep_timing_and_only_main_names_and_numbers():
    raw = render.beat_cues(BEATS, DURS)
    cues = render.emphasis_cues(raw, NAMES)
    assert [(s, e, len(t)) for s, e, t in cues] == [(s, e, len(t)) for s, e, t in raw]     # timed by character share
    got = _picked(BEATS, DURS, NAMES)
    assert got == ["Barbra", "7", "Ben"]                     # first mid-sentence mention of each; the number
    assert "They're" not in got and "German" not in got     # a quoted line's first word, a nationality
    assert "Hollis" not in got and "Ben Hollis" not in got   # a person is shown once, never again by another part
    assert _picked(BEATS, DURS, []) == ["7"]                  # no people from the plan → numbers only
    # a name at a sentence start is never shown (the picker cannot tell it from any capitalised first word)
    assert _picked([{"text": "Barbra runs. Johnny falls."}], [3.0], NAMES) == []
    # titles are not part of the name; a quote that runs over two cues is still dialogue
    assert render.name_tokens(["Judge Longstreet", "Mr. Wilson"]) == {"longstreet", "wilson"}
    long_quote = [{"text": "Then he says, \"Listen to me, Wilson, because Kindler is here, and Kindler will kill again tonight, "
                           "and nobody in this town believes it.\" Mary turns to Wilson."}]
    assert _picked(long_quote, [8.0], ["Wilson", "Mary"]) == ["Wilson"]          # from the narration, not the quote


def _fake_short(monkeypatch, tmp_path):
    seen = {"texts": []}
    monkeypatch.setattr(render.tts, "duration_seconds", lambda a: sum(DURS))
    monkeypatch.setattr(render.beatmod, "render", lambda *a, **k: (["b0.mp4"], []))
    monkeypatch.setattr(render.beatmod, "concat", lambda files, out, lst: out)
    monkeypatch.setattr(render.sof_shorts, "caption_png", lambda t, p, **k: seen["texts"].append(t) or p)
    monkeypatch.setattr(render.sof_render, "run", lambda cmd, timeout=0: seen.setdefault("cmd", cmd))
    render.build_short(config.Config(), work=Path(tmp_path), name="s1", video="f.mkv", short={"title": "HOOK", "beats": BEATS},
                       durs=DURS, cuts=[], crop=None, end=30.0, film_end=30.0, audio="a.wav", names=NAMES)
    return seen


def test_build_short_overlays_are_emphasis_words_not_beat_captions(monkeypatch, tmp_path):
    seen = _fake_short(monkeypatch, tmp_path)
    captions = {t for _, _, t in render.beat_cues(BEATS, DURS)}
    assert seen["texts"] == ["Barbra", "7"]                  # 15 s at ~6 a minute: two, the number first in priority
    assert not captions & set(seen["texts"]) and all(len(t.split()) <= 3 for t in seen["texts"])
    cmd = " ".join(seen["cmd"])
    assert cmd.count("enable='between(t,") == 2 and "title.png" not in cmd


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_real_short_shows_words_only_at_the_emphasis_and_fills_the_frame(tmp_path):
    """A tiny real render: a dark 16:9 "film" → a 9:16 Short; white text appears only while an emphasis word is up."""
    import numpy as np
    from PIL import Image

    film, voice = str(tmp_path / "film.mp4"), str(tmp_path / "voice.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x203048:size=320x180:rate=10:duration=30",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", film], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={sum(DURS)}", voice], check=True)
    cfg = config.Config()
    cfg.shorts_width, cfg.shorts_height, cfg.shorts_fps = 180, 320, 10
    out = render.build_short(cfg, work=tmp_path, name="s1", video=film, short={"title": "HOOK", "beats": BEATS}, durs=DURS,
                             cuts=[], crop=None, end=30.0, film_end=30.0, audio=voice, names=NAMES)
    picks = tb.pick(tb.words_from_cues(render.emphasis_cues(render.beat_cues(BEATS, DURS), NAMES)), duration=sum(DURS))
    assert picks and all(e - s >= 0.4 for s, e, _ in picks)

    def frame(t):
        jpg = str(tmp_path / f"f{t:.2f}.png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", out, "-frames:v", "1", jpg], check=True)
        return np.asarray(Image.open(jpg).convert("L"), dtype=np.int16)

    for s, e, _ in picks:
        f = frame((s + e) / 2)
        assert f.shape == (320, 180) and (f > 200).sum() > 30            # white words on the dark film
        rows = np.where((f > 200).any(axis=1))[0]
        assert 0.6 * 320 < rows.mean() < 0.85 * 320                     # low in the frame, above the Shorts UI
    quiet = [t for t in np.arange(0.3, sum(DURS) - 0.3, 0.7) if all(not (s - 0.15 <= t <= e + 0.15) for s, e, _ in picks)]
    assert quiet and all((frame(t) > 200).sum() == 0 for t in quiet)    # no captions between the emphasis words
    assert all(frame(t).min() > 20 for t in quiet[:3])                   # the crop fills the frame: no black bars
