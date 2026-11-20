"""Felix (Puello Media) 2026-11 rules on the clip channel: a few emphasis words instead of running captions, short
labels and titles, quiet effects on a few cuts only (never louder than the clip), no flat empty bands. The render
tests run real ffmpeg on synthetic 16:9 clips with a tone/noise "voice" (skipped without ffmpeg)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageStat

from clipper import find, render, twitch

sfx = render.sfx                    # cc.sfx: cloud/common is on the path once clipper is imported

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
SPEECH = ("Yo chat, look at this. I just won 10,000 dollars from Adin Ross, bro. No way. Duke is going crazy right "
          "now, what is he doing? He spent 3 hours in the car.")


def timed(text: str, t0: float = 0.0, step: float = 0.35) -> list[dict]:
    """Words as speech-to-text gives them: {"t", "e", "w"}, one every `step` seconds."""
    return [{"t": round(t0 + i * step, 2), "e": round(t0 + i * step + step - 0.05, 2), "w": w}
            for i, w in enumerate(text.split())]


# ---- rule 2: emphasis words, never the speech repeated

def test_emphasis_is_a_few_key_words_not_running_captions():
    words = timed(SPEECH)                                   # 33 words in 11.5 s
    out = render.emphasis(words, 0.0, 12.0, ["Kai Cenat's", "top clips"])
    assert [t for _, _, t in out] == ["$10,000"]           # the number, once; the old captions showed all 33 words
    assert out[0][0] == pytest.approx(words[8]["t"]) and out[0][1] - out[0][0] <= 1.6 + 1e-6
    long = render.emphasis(timed(" ".join([SPEECH] * 5)), 0.0, 60.0, [])
    assert len(long) <= 6 and all(len(t.split()) <= 3 for _, _, t in long)


def test_emphasis_skips_what_is_on_screen_and_swears():
    # the label already shows the number, so the clip's one slot goes to the next number instead
    assert [t for _, _, t in render.emphasis(timed(SPEECH), 0.0, 12.0, ["$10,000 💰"])] == ["3 hours"]
    names = timed("We pulled up on Adin Ross today and it was crazy, honestly.")
    assert [t for _, _, t in render.emphasis(names, 0.0, 10.0, [])] == ["Adin Ross"]
    assert render.emphasis(names, 0.0, 10.0, ["Adin Ross"]) == []
    assert render.emphasis(timed("Then he said F*** it and left."), 0.0, 10.0, []) == []
    later = timed("He spent 3 hours in the car.", t0=20.0)     # times are from the clip's start
    assert render.emphasis(later, 18.0, 30.0, []) == [(pytest.approx(2.7), pytest.approx(4.3), "3 hours")]


def test_emphasis_never_repeats_part_of_what_is_on_screen():
    # 2026-11-21 review: a possessive or one shared word is still a repeat; the slot goes to the next pick instead
    on_screen = ["Kai Cenat's", "top GTA 6", "Kai Cenat", "Adin Ross", "Rage"]
    for said in ("We went to Kai's house.", "Then we saw Adin's car.", "Bro the GTA Online servers died.",
                 "He said Rage Mode is on."):
        assert render.emphasis(timed(said), 0.0, 10.0, on_screen) == [], said
    got = render.emphasis(timed("We went to Kai's house with Duke today."), 0.0, 10.0, on_screen)
    assert [t for _, _, t in got] == ["Duke"]


def test_caption_files_hold_only_the_emphasis_lines(tmp_path):
    chunks = render.emphasis(timed(SPEECH), 0.0, 12.0, [])
    for style in "AB":
        render.write_ass(tmp_path / "a.ass", chunks, style, 12.0, "sylusnix", "#FF8A00")
        lines = [ln for ln in (tmp_path / "a.ass").read_text(encoding="utf-8").splitlines() if ln.startswith("Dialogue")]
        assert len(lines) == 1 + len(chunks) == 2          # the watermark and one emphasis word
    render.write_ass_rank(tmp_path / "r.ass", chunks)
    assert sum(ln.startswith("Dialogue") for ln in (tmp_path / "r.ass").read_text(encoding="utf-8").splitlines()) == 1


# ---- rules 2 and 4: rank labels of 1-2 words plus the icon, a title of at most 5 words

def test_labels_are_one_or_two_words_plus_icon():
    assert find.short_label("Kai rages at chat 😡") == "Kai rages 😡"
    assert find.short_label("Falls off the bridge 💀") == "Falls off 💀"
    assert find.short_label("Rage 😡") == "Rage 😡" and find.short_label("$250,000 💰") == "$250,000 💰"
    assert find.short_label("Speed and the chair") == "Speed"           # never ends on a joining word
    assert find.short_label("") == ""


def test_title_at_most_five_words_with_a_fixed_fallback():
    assert find.short_title("xQc's", "top clips", ("a", "b")) == ("xQc's", "top clips")
    fb = ("Kai Cenat's", "top clips")
    assert find.short_title("Kai Cenat's", "craziest clips of the whole week", fb) == fb
    assert find.short_title("Kai Cenat's", "best", ("Kai Cenat's", "top GTA 6 clips")) == ("Kai Cenat's", "best")
    assert find.short_title("Kai Cenat's", "top GTA 6 clips", ("Kai Cenat's", "top GTA 6")) == ("Kai Cenat's", "top GTA 6")
    assert find.short_title("Ranking", "Biggest Prize Wins Of All Time", ("Ranking", "Biggest Prize Wins Of All Time")) \
        == ("Ranking", "Biggest Prize Wins")
    p, a = find.short_title("A B C D", "E F G", ("Ranking MrBeast's very best", "Biggest Wins Ever Seen"))
    assert len(p.split()) + len(a.split()) <= 5


def test_twitch_plan_trims_labels_and_title_not_upload_title(monkeypatch):
    seen = {}
    data = {"title_plain": "Kai Cenat's", "title_accent": "craziest clips of the whole week",
            "upload_title": "Kai Cenat's craziest Twitch clips of the week",
            "clips": [{"n": n, "label": label} for n, label in enumerate(
                ["Kai rages at chat 😡", "Gets jumpscared by a ghost 😱", "Rage 😡", "Wins $500 from Adin 💰", "Dance"], 1)]}

    def look(prompt, images):
        seen["prompt"] = prompt
        return data
    monkeypatch.setattr(twitch, "frame_sheet", lambda src, dur, dest: dest)
    monkeypatch.setattr(twitch, "ask_claude_look", look)
    cands = [{"id": f"c{n}", "views": 1000 - n, "duration": 10.0, "title": "t", "clipper": "", "url": f"u{n}",
              "file": f"/x/c{n}.mp4"} for n in range(1, 6)]
    plan = twitch.plan_ranking("Kai Cenat", "kaicenat", 7, cands, {})
    assert (plan["title_plain"], plan["title_accent"]) == ("Kai Cenat's", "top clips")
    assert plan["upload_title"] == data["upload_title"]
    assert [it["label"] for it in plan["items"]] == ["Kai rages 😡", "Gets jumpscared 😱", "Rage 😡", "Wins $500 💰", "Dance"]
    assert [it["video_id"] for it in plan["items"]] == ["c1", "c2", "c3", "c4", "c5"]      # picks and order unchanged
    assert "1 or 2 plain words" in seen["prompt"] and "at most 5 words together" in seen["prompt"]


# ---- rules 3 and 6 on a real render

def make_clip(path: Path, amp: float, seconds: float = 3.0, freq: int = 180) -> Path:
    """A 16:9 test clip whose sound is a syllable-paced tone with a little noise (a stand-in for a voice)."""
    voice = f"{amp}*sin(2*PI*{freq}*t)*abs(sin(2*PI*2.5*t))+{amp * 0.1}*(2*random(0)-1)"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d={seconds}",
                    "-f", "lavfi", "-i", f"aevalsrc='{voice}':d={seconds}:s=48000", "-c:v", "libx264", "-preset",
                    "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-shortest", str(path)], check=True)
    return path


def frame(video: Path, t: float, dest: Path) -> Image.Image:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1", str(dest)], check=True)
    return Image.open(dest).convert("L")


@pytest.fixture(scope="module")
def ranking(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    src = tmp_path_factory.mktemp("src")
    amps = {1: 0.1, 2: 0.6, 3: 0.3, 4: 0.5, 5: 0.45}           # number 1 (always an effect) is the quiet clip
    labels = {1: "Rage 😡", 2: "Jumpscared 😱", 3: "$500 💰", 4: "Wiped out 💀", 5: "Chat 😂"}
    items = [{"rank": r, "label": labels[r], "video_id": f"c{r}", "start": 0.0, "end": 3.0, "creator": "Kai Cenat",
              "src_file": str(make_clip(src / f"c{r}.mp4", amps[r], freq=160 + 20 * r))} for r in range(1, 6)]
    words = {"c2": timed("Bro he spent 3 hours in there, chat."), "c3": timed("I got 500 dollars, look.")}
    plan = {"theme": "twitch:kai:top:7d", "title_plain": "Kai Cenat's", "title_accent": "top clips", "upload_title": "x",
            "items": items}
    out = render.make_ranking(tmp_path_factory.mktemp("2026-11-21") / "twitch-kai-7d", plan, words,
                              {"channel_name": "sylusnix"})
    return out, json.loads((out.parent / "info.json").read_text(encoding="utf-8"))


@needs_ffmpeg
def test_effects_on_a_few_cuts_only_and_number_one_last(ranking):
    _, info = ranking
    fx = [p["sfx"] for p in info["parts"]]
    assert [p["rank"] for p in info["parts"]] == render.RANK_ORDER
    assert fx[-1] == "thump" and 1 <= sum(map(bool, fx)) < len(fx)        # the #1 reveal always; never every part
    assert not any(a and b for a, b in zip(fx, fx[1:]))                    # never two in a row
    assert set(filter(None, fx[:-1])) <= {"pluck"}


@needs_ffmpeg
def test_effect_peaks_well_under_the_clip(ranking):
    out, info = ranking
    for p in (p for p in info["parts"] if p["sfx"]):
        assert p["sfx_peak_db"] <= p["clip_peak_db"] - 15 and p["sfx_peak_db"] <= sfx.PEAK_DB + 0.5
        assert sfx.peak_db(str(out.parent / p["sfx_file"])) == pytest.approx(p["sfx_peak_db"], abs=0.2)
        # in the finished video the effect does not lift the clip's own peak where it plays
        mixed = sfx.peak_db(str(out), start=p["at"], seconds=0.3)
        alone = sfx.peak_db(str(out.parent / f"src{p['rank']}.mp4"), start=0.0, seconds=0.3)
        assert mixed <= alone + 1.5
    one = info["parts"][-1]
    assert one["clip_peak_db"] < -15 and one["sfx_peak_db"] == pytest.approx(one["clip_peak_db"] - render.SFX_UNDER, abs=0.5)


@needs_ffmpeg
def test_ranking_words_are_the_emphasis_only(ranking):
    _, info = ranking
    words = {p["rank"]: p["words"] for p in info["parts"]}
    assert words[2] == ["3 hours"]
    assert words[3] == []                    # "500 dollars" is already on the list as "$500"
    assert info["title"] == ["Kai Cenat's", "top clips"]


@needs_ffmpeg
def test_ranking_frame_has_no_flat_black_band(ranking, tmp_path):
    out, _ = ranking
    img = frame(out, 1.0, tmp_path / "f.png")
    assert img.size == (render.W, render.H)
    for box in ((0, render.R_TOP + render.R_H + 10, render.W, render.H),       # the band under the clip box
                (0, 0, render.W, 120)):                                         # above the title
        st = ImageStat.Stat(img.crop(box))
        assert st.mean[0] > 12 and st.stddev[0] > 4, box                       # the blurred clip, not black


@needs_ffmpeg
def test_style_b_bars_are_the_blurred_clip(tmp_path):
    seg = make_clip(tmp_path / "s.mp4", 0.4, seconds=6.0)
    render.write_ass(tmp_path / "c.ass", [], "B", 6.0, "sylusnix", "#FF8A00")
    render.panel_png(tmp_path / "p.png", "B", "He said it | then this", "", "#FF8A00")
    render.render(seg, tmp_path / "c.ass", tmp_path / "p.png", tmp_path / "o.mp4", "B", 0.5)
    img = frame(tmp_path / "o.mp4", 5.5, tmp_path / "f.png")                  # the banner is gone by now
    for box in ((0, 0, render.W, render.B_TOP - 10), (0, render.B_TOP + render.B_SIZE + 10, render.W, render.H)):
        st = ImageStat.Stat(img.crop(box))
        assert st.mean[0] > 12 and st.stddev[0] > 4, box
