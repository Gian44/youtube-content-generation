import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bqd import config, interactive, pipeline, seo, stories


class FakeLLM:
    def __init__(self, answers):
        self.answers, self.prompts, self.stats = list(answers), [], {"claude": 0}

    def generate_json(self, prompt, **kw):
        self.prompts.append(prompt)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return json.loads(json.dumps(a))


WYR = {"title": "Pets Edition", "tags": ["pets", "roblox"],
       "rounds": [{"a": "Get a dragon pet that cannot fly", "b": "get a tiny cat that flies everywhere"},
                  {"a": "hatch a legendary egg every day", "b": "get one thousand Robux once"},
                  {"a": "beat every obby on your first try", "b": "never lag again in any game"},
                  {"a": "have the rarest hat in the game", "b": "have the fastest pet in the server"}]}
GTG_OK = {"tags": ["quiz", "kidsafe"], "rounds": [
    {"game": "Adopt Me!", "clues": ["You can raise a baby or a pet here.", "Eggs hatch into pets you can trade.",
                                    "A legendary neon pet is the big dream."]},
    {"game": "Theme Park Tycoon 2", "clues": ["You build rides and roller coasters.", "Guests pay to get in.",
                                              "You earn money as visitors have fun."]},
    {"game": "Jailbreak", "clues": ["You can play as police or a criminal.", "Players escape and rob stores.",
                                    "Cops chase cars across a big city."]}]}
PICK = {"title": "Mystery Edition", "tags": ["pets"], "rounds": [
    {"thing": "egg", "rewards": ["a rainbow dragon pet", "a single wet sock", "100 Robux"]},
    {"thing": "chest", "rewards": ["a golden crown hat", "a slightly used banana", "a speed trail"]}]}


def _params(fmt, seed="2026-11-19"):
    return interactive.prompt_for(fmt, seed=seed, used=[], recent_games=[])[1]


def test_plan_slots_mixes_one_story_and_two_different_formats_and_rotates():
    a = interactive.plan_slots(["story", "interactive", "interactive"], count=3, seed="2026-11-19", avoid=set())
    assert a.count("story") == 1 and len(set(a) - {"story"}) == 2 and set(a) <= {"story", *interactive.FORMATS}
    assert a == interactive.plan_slots(["story", "interactive", "interactive"], count=3, seed="2026-11-19", avoid=set())
    # yesterday's formats come last: with two used, the fresh one is always in today's mix
    b = interactive.plan_slots(["story", "interactive", "interactive"], count=3, seed="x", avoid={"pick_one", "guess_the_game"})
    assert "would_you_rather" in b
    # over a fortnight the story takes every time slot
    slots = {interactive.plan_slots(["story", "interactive", "interactive"], count=3, seed=f"2026-11-{d:02d}", avoid=set()).index("story")
             for d in range(1, 15)}
    assert slots == {0, 1, 2}
    assert interactive.plan_slots([], count=2, seed="x", avoid=set(), force="pick_one") == ["pick_one", "pick_one"]
    with pytest.raises(stories.StoryError):
        interactive.plan_slots([], count=1, seed="x", avoid=set(), force="nope")
    rows = [{"stories": [{"format": "pick_one"}, {"category": "obby_fails"}]}]
    assert interactive.recent_formats(rows) == {"pick_one"}


def test_would_you_rather_validation_and_beats():
    out = interactive.validate("would_you_rather", json.loads(json.dumps(WYR)), _params("would_you_rather"))
    assert out["rounds"][0]["a"].startswith("get a dragon")              # lower-cased to follow "Would you rather"
    beats = interactive.make_beats("would_you_rather", out)
    assert len(beats) == 5 and beats[0].say.startswith("Would you rather get a dragon") and beats[0].hold == 3
    assert beats[0].panel["kind"] == "wyr" and beats[-1].panel["kind"] == "cta" and "four picks" in beats[-1].say
    bad = json.loads(json.dumps(WYR))
    bad["rounds"][1]["b"] = "get hacked or scammed"
    with pytest.raises(stories.StoryError, match="kid-safe|'or'"):
        interactive.validate("would_you_rather", bad, _params("would_you_rather"))
    bad["rounds"] = bad["rounds"][:2]
    with pytest.raises(stories.StoryError, match="exactly 4 rounds"):
        interactive.validate("would_you_rather", bad, _params("would_you_rather"))


def test_guess_the_game_only_listed_games_and_no_name_in_the_clues():
    p = _params("guess_the_game")
    out = interactive.validate("guess_the_game", json.loads(json.dumps(GTG_OK)), p)
    beats = interactive.make_beats("guess_the_game", out)
    assert beats[0].say == "Guess the Roblox game." and beats[3].hold == 3 and beats[4].say == "It's Adopt Me!"
    assert beats[1].say == "You can raise a baby or a pet here."               # no spoken "Clue one:" (the panel numbers them)
    assert beats[4].panel["answer"] == "Adopt Me!" and beats[-1].panel["kind"] == "cta"
    leak = json.loads(json.dumps(GTG_OK))
    leak["rounds"][1]["clues"][2] = "You build the best theme park around."
    with pytest.raises(stories.StoryError, match="gives the name away"):
        interactive.validate("guess_the_game", leak, p)
    unknown = json.loads(json.dumps(GTG_OK))
    unknown["rounds"][0]["game"] = "Doors"                                # horror: not on the list
    with pytest.raises(stories.StoryError, match="copied exactly"):
        interactive.validate("guess_the_game", unknown, p)
    assert all(g in interactive.GAMES for g in p["games"]) and not any("hell" in g.lower() for g in interactive.GAMES)
    assert "Adopt Me!" not in interactive.prompt_for("guess_the_game", seed="x", used=[], recent_games=["Adopt Me!"])[1]["games"]


def test_pick_one_rounds_follow_the_drawn_things():
    p = _params("pick_one")
    data = json.loads(json.dumps(PICK))
    data["rounds"][0]["thing"], data["rounds"][1]["thing"] = p["things"]
    out = interactive.validate("pick_one", data, p)
    beats = interactive.make_beats("pick_one", out)
    assert beats[0].hold == 3 and beats[0].panel["open"] == [None, None, None] and beats[0].say.startswith("Pick a")
    assert beats[3].panel["open"][2] == {"text": "100 Robux", "icon": ""} and beats[3].panel["now"] == 2 and len(beats) == 9
    assert beats[0].panel["icon"] == interactive.THING_ICONS[p["things"][0]] and "thing" not in beats[0].panel
    wrong = json.loads(json.dumps(data))
    wrong["rounds"][0]["thing"] = "spaceship"
    with pytest.raises(stories.StoryError, match="must be a"):
        interactive.validate("pick_one", wrong, p)


def test_write_retries_once_and_returns_a_story():
    bad = {"title": "x", "rounds": []}
    llm = FakeLLM([bad, WYR])
    s = interactive.write(llm, "would_you_rather", seed="2026-11-19", used=["Old one"])
    assert s.format == "would_you_rather" and s.title == "Roblox Would You Rather: Pets Edition"
    assert s.hook.startswith("Would you rather") and "Validation errors" in llm.prompts[1] and "- Old one" in llm.prompts[0]
    g = interactive.write(FakeLLM([GTG_OK]), "guess_the_game", seed="2026-11-19", used=[], recent_games=[])
    assert g.title in interactive.GTG_TITLES and "adopt me!" in g.tags
    d = stories.Story.from_dict(s.to_dict())
    assert d.format == "would_you_rather" and d.script["rounds"][0]["a"].startswith("get")


def test_timeline_places_the_countdown_after_each_question():
    beats = [interactive.Beat("Would you rather a, or b?", {"kind": "wyr"}, 3), interactive.Beat("Comment!", {"kind": "cta"})]
    cues, total = interactive.timeline(beats, [2.0, 1.0], gap=0.25)
    counts = [c for c in cues if c.count]
    assert [c.count for c in counts] == [3, 2, 1] and counts[0].start == 2.25 and counts[-1].end == 5.25
    assert total == 6.5 and cues[-1].end == 6.5 and cues[0].start == 0.0


def test_ffmpeg_commands_are_well_formed():
    tick, tock = interactive.tick_cmds("t.wav", "k.wav", seed="2026-11-21|0")
    assert tick[-1] == "t.wav" and tock[-1] == "k.wav" and all("apad=whole_dur=1" in " ".join(c) for c in (tick, tock))
    assert "sine=" not in " ".join(tick + tock)                          # Felix 2026-11: no sine pings
    assert tick != interactive.tick_cmds("t.wav", "k.wav", seed="2026-11-21|1")[0]   # another Short, another tick
    assert interactive.TICK_DB <= -24.0
    assert interactive.silence_cmd("s.wav", 0.25)[interactive.silence_cmd("s.wav", 0.25).index("-t") + 1] == "0.250"


@pytest.mark.parametrize("panel", [
    {"kind": "wyr", "round": 1, "of": 4, "a": {"text": "Dragon pet", "icon": "🐉"}, "b": {"text": "Flying cat", "icon": ""}},
    {"kind": "clues", "round": 2, "of": 3, "clues": [{"text": "Baby or pet", "icon": "🍼"}, {"text": "Tornadoes and floods", "icon": ""}],
     "answer": "Natural Disaster Survival"},
    {"kind": "pick", "round": 1, "of": 2, "icon": "🥚", "open": [{"text": "Rainbow dragon", "icon": "🐉"}, None, None], "now": 0},
    {"kind": "pick", "round": 2, "of": 2, "icon": "", "open": [None, None, None]},
    {"kind": "cta", "icon": "💬", "text": "A or B?"},
    {"kind": "intro", "icon": "🎮", "text": "3 clues"}])
@pytest.mark.parametrize("emoji_font", [True, False])
def test_panel_png_draws_every_kind(tmp_path, monkeypatch, panel, emoji_font):
    from PIL import Image
    if not emoji_font:                                                   # no emoji font: the words alone, no crash
        monkeypatch.setattr(interactive.icons, "emoji_image", lambda text, target_h=96: None)
    out = interactive.panel_png(panel, str(tmp_path / "p.png"), width=540, height=960, count=3)
    im = Image.open(out)
    assert im.size == (540, 960) and im.mode == "RGBA"
    assert im.getpixel((5, 950))[3] == 0                                  # the gameplay shows through outside the boxes
    assert im.getpixel((270, 125))[3] == (255 if panel.get("round") else 0)   # the round chip, nothing else up there


def _panel_texts(panel: dict) -> list[str]:
    out = []
    for k, v in panel.items():
        if k in ("kind", "icon", "answer"):                               # the answer is the game's name: the one needed word
            continue
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, dict):
                out.append(str(x.get("text") or ""))
            elif isinstance(x, str):
                out.append(x)
    return out


def test_panels_never_show_the_spoken_sentence():
    """Felix 2026-11: 1-3 words per choice / clue / reward and per call to action, never what the narrator says."""
    p_one = _params("pick_one")
    pick = json.loads(json.dumps(PICK))
    pick["rounds"][0]["thing"], pick["rounds"][1]["thing"] = p_one["things"]
    for fmt, data, params in (("would_you_rather", WYR_SHORT, _params("would_you_rather")), ("would_you_rather", WYR, _params("would_you_rather")),
                              ("guess_the_game", GTG_SHORT, _params("guess_the_game")), ("guess_the_game", GTG_OK, _params("guess_the_game")),
                              ("pick_one", pick, p_one)):
        beats = interactive.make_beats(fmt, interactive.validate(fmt, json.loads(json.dumps(data)), params))
        for b in beats:
            texts = _panel_texts(b.panel)
            words = [[w for w in t.split() if w.lower() not in ("and", "or")] for t in texts]   # "1, 2 or 3?" is three
            assert all(1 <= len(w) <= 3 for w in words), (fmt, b.panel)        # (a closed pick shows icons only)
            assert not any(t.lower().strip(".!?") == b.say.lower().strip(".!?") for t in texts)
            assert "header" not in b.panel                                 # the round number only (the chip)


WYR_SHORT = {"title": "Pets Edition", "tags": ["pets"], "rounds": [
    {"a": "Get a dragon pet that cannot fly", "a_short": "Dragon pet", "a_icon": "🐉",
     "b": "get a tiny cat that flies everywhere", "b_short": "Flying cat", "b_icon": "🐱"},
    {"a": "hatch a legendary egg every day", "a_short": "Legendary egg every single day", "a_icon": "🥚🐣",   # too long, two icons
     "b": "get one thousand Robux once", "b_short": "1,000 Robux", "b_icon": "💰"},
    {"a": "beat every obby on your first try", "a_short": "Haunted obby", "a_icon": "💀",                   # not kid-safe
     "b": "never lag again in any game", "b_short": "Never lag", "b_icon": "⚡"},
    {"a": "have the rarest hat in the game", "b": "have the fastest pet in the server"}]}                 # no shorts at all
GTG_SHORT = {"tags": ["quiz"], "rounds": [
    {"game": "Adopt Me!", "clues": [{"say": "You can raise a baby or a pet here.", "short": "Baby or pet", "icon": "🍼"},
                                    {"say": "Eggs hatch into pets you can trade.", "short": "Adopt pets", "icon": "🥚"},   # name leak
                                    "A legendary neon pet is the big dream."]},                                            # old shape
    GTG_OK["rounds"][1], GTG_OK["rounds"][2]]}


def test_writer_labels_are_validated_with_a_fallback_and_the_spoken_lines_stay():
    out = interactive.validate("would_you_rather", json.loads(json.dumps(WYR_SHORT)), _params("would_you_rather"))
    r = out["rounds"]
    assert r[0]["a_label"] == {"text": "Dragon pet", "icon": "🐉"} and r[0]["b_label"] == {"text": "Flying cat", "icon": "🐱"}
    assert r[1]["a_label"] == {"text": "hatch legendary egg", "icon": ""} and r[1]["b_label"]["text"] == "1,000 Robux"
    assert r[2]["a_label"] == {"text": "beat obby", "icon": ""} and r[2]["b_label"] == {"text": "Never lag", "icon": "⚡"}
    assert r[3]["a_label"] == {"text": "rarest hat", "icon": ""} and r[3]["b_label"] == {"text": "fastest pet", "icon": ""}
    assert r[0]["a"] == "get a dragon pet that cannot fly"                # the spoken line is untouched
    g = interactive.validate("guess_the_game", json.loads(json.dumps(GTG_SHORT)), _params("guess_the_game"))["rounds"][0]
    assert g["clues"] == GTG_OK["rounds"][0]["clues"]                     # same spoken clues whichever shape came in
    assert g["labels"][0] == {"text": "Baby or pet", "icon": "🍼"}
    assert g["labels"][1] == {"text": "Eggs hatch", "icon": "🥚"}       # "Adopt" would give the game away
    assert g["labels"][2] == {"text": "legendary neon pet", "icon": ""}
    p = _params("pick_one")
    pick = {"title": "Mystery Edition", "tags": [], "rounds": [
        {"thing": p["things"][0], "rewards": [{"say": "a rainbow dragon pet that flies", "short": "Rainbow dragon", "icon": "🐉"},
                                             {"say": "a single wet sock", "short": "Wet sock", "icon": "🔫"}, "100 Robux"]},
        {"thing": p["things"][1], "rewards": ["a golden crown hat", "a slightly used banana", "a speed trail"]}]}
    k = interactive.validate("pick_one", pick, p)["rounds"][0]
    assert k["rewards"][0] == "a rainbow dragon pet that flies"
    assert k["labels"] == [{"text": "Rainbow dragon", "icon": "🐉"}, {"text": "Wet sock", "icon": ""}, {"text": "100 Robux", "icon": ""}]
    # the spoken lines are still checked as before: a bad one is a rejection (and the retry), not a fallback
    bad = json.loads(json.dumps(WYR_SHORT))
    bad["rounds"][0]["a"] = "get scammed"
    with pytest.raises(stories.StoryError):
        interactive.validate("would_you_rather", bad, _params("would_you_rather"))


def test_short_label_and_icon_rules():
    assert interactive.short_label("get a dragon pet that cannot fly") == "dragon pet"
    assert interactive.short_label("never lag again in any game") == "never lag"
    assert interactive.short_label("Tornadoes and floods hit the map.") == "Tornadoes and floods"
    assert interactive.short_label("get one thousand Robux once") == "one thousand Robux"
    assert interactive.short_label("spend 1,000 Robux on a hat") == "spend 1,000 Robux"
    for s in ("hatch a legendary egg every day", "You can play as police or a criminal.", "a slightly used banana"):
        t = interactive.short_label(s)
        assert 1 <= len(t.split()) <= 3 and len(t) <= interactive.LABEL_CHARS and set(t.lower().split()) <= set(s.lower().split())
    assert interactive._icon("🐉") == "🐉" and interactive._icon(" ❤️ ") == "❤️" and interactive._icon("👍🏽") == "👍🏽"
    assert interactive._icon("👨\u200d👩\u200d👧") == "👨\u200d👩\u200d👧"           # one ZWJ family is one icon
    for bad in ("🐉🐱", "A", "", None, "龍", "🔫", "💀", "🗡️", "1️⃣", "dragon 🐉"):
        assert interactive._icon(bad) == "", bad
    assert interactive.label("Hacked account", "🐉", "get a cat that flies")["text"] == "cat flies"   # kid-safe check
    assert interactive.label("Dragon 🐉", "🐉", "get a dragon")["text"] == "dragon"                  # no emoji in the words
    assert interactive.label(["Dragon", "pet"], ["🐉"], "get a dragon pet") == {"text": "dragon pet", "icon": ""}
    # no creed, political sign, flag, grave, romance or monster as an icon on an all-ages channel
    for bad in ("✝️", "☪", "🕉", "☭", "🏳️‍🌈", "🏴", "🇺🇸", "🇺", "🪦", "🫦", "💏", "👹", "🕷", "🧨"):
        assert interactive._icon(bad) == "", bad


def test_guess_the_game_icons_never_picture_the_name_and_bad_rounds_are_rejections():
    p = _params("guess_the_game")
    p["games"] = list(interactive.GAMES)
    data = json.loads(json.dumps(GTG_OK))
    data["rounds"][0] = {"game": "Work at a Pizza Place", "clues": [
        {"say": "You make food and deliver it to houses.", "short": "Deliver food", "icon": "🍕"},
        {"say": "Someone rings up orders at the counter.", "short": "Counter", "icon": "🧾"},
        {"say": "The boxes go out on a moped.", "short": "Moped", "icon": "🛵"}]}
    labels = interactive.validate("guess_the_game", data, p)["rounds"][0]["labels"]
    assert labels[0] == {"text": "Deliver food", "icon": ""} and labels[1]["icon"] == "🧾" and labels[2]["icon"] == "🛵"
    for fmt, bad in (("guess_the_game", {"rounds": ["Adopt Me!", 3, None]}),
                     ("pick_one", {"title": "Mystery", "rounds": ["egg", "chest"]})):
        with pytest.raises(stories.StoryError):                          # not an AttributeError that ends the whole run
            interactive.validate(fmt, bad, _params(fmt))


def test_panel_words_are_never_cut_off():
    """A label too long for its box shrinks; no word is dropped ("HATCH LEGENDARY EGG" lost "EGG" in a WYR card)."""
    from PIL import Image, ImageDraw
    d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    for text, max_w, size, lines in (("HATCH LEGENDARY EGG", 383, 80, 2), ("LEGENDARY NEON PET", 300, 65, 1),
                                     ("SLIGHTLY USED BANANA", 255, 46, 3), ("NATURAL DISASTER SURVIVAL", 820, 86, 2)):
        font, rows, s = interactive._fit(d, text, max_w, size, lines=lines, min_size=round(size * 0.55))
        assert " ".join(rows).split() == text.split() and len(rows) <= lines, (text, rows)
        assert all(d.textlength(r, font=font) <= max_w for r in rows)


def test_interactive_seo():
    credit = "Gameplay: Some Channel (CC BY) https://www.youtube.com/watch?v=abc"
    d = seo.interactive_description(fmt="would_you_rather", hook="Would you rather a, or b?", tags=["pets"], credit=credit)
    assert credit in d and "Not affiliated" in d and "#wouldyourather" in d and "comment" in d.lower()
    assert seo.interactive_tags("guess_the_game", ["adopt me!"])[0] == "roblox"
    # general audience: no tag or hashtag that reads as children's content, whatever the writer suggests
    assert not any("kid" in t.lower() for t in seo.interactive_tags("pick_one", ["kidsafe", "for kids", "pets"]))
    assert "#kidsafe" not in seo.interactive_description(fmt="pick_one", hook="h", tags=["kidsafe", "pets"], credit="")
    assert "#kidfriendly" not in seo.short_description(hook="h", category="obby_fails", tags=["kid friendly"], credit="c")
    assert seo.short_title("Roblox Would You Rather: Pets Edition", "would_you_rather", roblox=True).endswith("#roblox #Shorts")


def test_pipeline_writes_the_days_mix(tmp_path):
    import os
    os.environ.pop("TTS_BACKEND", None)
    cfg = config.Config.from_env(count=3)
    cfg.topics_path = str(Path(__file__).resolve().parents[1] / "topics.yml")
    cfg.noslop_rounds = 0
    cfg.words_min = 40
    slots = pipeline.plan(cfg, [], today="2026-11-19")
    story = {"title": "The obby I finally beat", "hook": "I fell off stage ninety-nine eleven times.",
             "body": "I fell off stage ninety-nine eleven times. " + "Then I tried again with one more careful jump. " * 6
                     + "What stage do you always fall on?", "broll_queries": [], "tags": ["obby"]}
    p_one = interactive.prompt_for("pick_one", seed="2026-11-19", used=[])[1]
    pick = json.loads(json.dumps(PICK))
    pick["rounds"][0]["thing"], pick["rounds"][1]["thing"] = p_one["things"]
    answers = {"story": story, "would_you_rather": WYR, "guess_the_game": GTG_OK, "pick_one": pick}
    work = tmp_path / "w"
    work.mkdir()
    out = pipeline.write_stories(cfg, FakeLLM([answers[s] for s in slots]), [], today="2026-11-19", work=work)
    assert [s.format for s in out] == slots and slots.count("story") == 1


def test_story_writer_rejects_creepy_and_scam_stories():
    body = ("The only other player in the creepy server knew my name. " + "I kept walking through the empty lobby slowly. " * 6
            + "Would you have logged off?")
    with pytest.raises(stories.StoryError, match="kid-safe"):
        stories.validate({"title": "Empty server", "hook": "The only other player in the creepy server knew my name.",
                          "body": body}, words_min=20, words_max=150, need_queries=False)
    with pytest.raises(stories.StoryError, match="hell"):
        stories.validate({"title": "The yellow bar", "hook": "We were stuck in Tower of Hell all night.",
                          "body": "We were stuck in Tower of Hell all night. " + "We jumped again and again. " * 12 + "Would you quit?"},
                         words_min=20, words_max=150, need_queries=False)
    assert not stories.DARK_WORDS.search("Hello there, shell collector")
    cats, personas = stories.load_topics(str(Path(__file__).resolve().parents[1] / "topics.yml"))
    assert not {"creepy_servers", "trade_scams", "banned_or_hacked", "revenge_on_trolls", "friends_and_betrayal"} & {c.key for c in cats}
    assert "horror" not in personas


def test_voice_speaks_every_beat_respelled_and_writes_the_timeline(tmp_path, monkeypatch):
    """interactive.voice end to end without ffmpeg or a model: what the narrator is given, and the cues."""
    from bqd import interactive as it
    spoken = []

    def fake_speak(chunks, out_dir, cfg, short=False):
        spoken.extend(chunks)
        return [f"{out_dir}/{i:04d}.mp3" for i in range(len(chunks))]

    monkeypatch.setattr(it.tts, "speak", fake_speak)
    monkeypatch.setattr(it.tts, "duration_seconds", lambda p: 2.0)
    monkeypatch.setattr(it.sof_render, "run", lambda cmd, timeout: None)
    wyr = json.loads(json.dumps(WYR))
    wyr["rounds"][1]["b"] = "get one thousand Robux in Roblox once"
    s = it.write(FakeLLM([wyr]), "would_you_rather", seed="x", used=[])
    cfg = config.Config.from_env(count=1)
    it.voice(s, tmp_path, cfg, index=0)
    assert len(spoken) == 5 and "Roebucks in Roeblox" in spoken[1] and "Robux" in s.script["rounds"][1]["b"]
    tl = json.loads((tmp_path / "s0" / "timeline.json").read_text())
    assert sum(1 for c in tl["cues"] if c["count"]) == 12 and tl["total"] > 20


def _has_flite() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return " flite " in r.stdout


def _peak(path, start: float, seconds: float) -> float:
    """Sample peak (dBFS) of a window, decoded from the start (atrim): an input seek into an MP3 lands a few
    milliseconds late and misses a tick's attack."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"atrim=start={start:.3f}:duration={seconds:.3f},volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else -99.0


@pytest.mark.skipif(not _has_flite(), reason="needs ffmpeg with the flite voice (the runner's apt ffmpeg has it)")
def test_countdown_ticks_keep_time_and_stay_far_under_the_voice(tmp_path, monkeypatch):
    """interactive.voice with real ffmpeg (tick synthesis, concat, the channel's loudnorm); ffmpeg's flite voice
    stands in for Kokoro at its loudness. Felix 2026-11: the pings were too loud. The dynamic loudnorm lifts the quiet
    countdown, so the gap is measured after it, as the viewer hears it (with Kokoro: 21-22 dB; at the old -27 dBFS
    tick level the last count was 16-17 dB under the voice and this test fails)."""
    from bqd import interactive as it

    def flite_speak(chunks, out_dir, cfg, short=False):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        paths = []
        for i, c in enumerate(chunks):
            p = f"{out_dir}/{i:04d}.wav"
            text = re.sub(r"[^A-Za-z ]", " ", c)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"flite=text='{text}':voice=slt",
                            "-af", "volume=0.45", p], check=True)
            paths.append(p)
        return paths

    monkeypatch.setattr(it.tts, "speak", flite_speak)
    s = it.write(FakeLLM([WYR_SHORT]), "would_you_rather", seed="2026-11-21", used=[])
    out = it.voice(s, tmp_path, config.Config.from_env(count=1), index=0, seed="2026-11-21")
    tl = json.loads((tmp_path / "s0" / "timeline.json").read_text())
    speech = [c for c in tl["cues"] if not c["count"]]
    ticks = [c for c in tl["cues"] if c["count"]]
    # one tick per countdown second, each exactly 1 s: the number on screen keeps time with the voice
    assert [c["count"] for c in ticks] == [3, 2, 1] * 4
    assert abs(it.tts.duration_seconds(out) - tl["total"]) < 0.15
    # the stand-in is as loud as the real voice before the mix (Kokoro af_heart's interactive Shorts: -23 LUFS, 2026-11-21)
    assert -25.0 <= it.sfx.loudness(str(tmp_path / "s0" / "pieces" / "joined.wav"))["I"] <= -21.0
    voice_peak = max(_peak(out, c["start"], c["end"] - c["start"]) for c in speech)
    tick_peaks = [_peak(out, c["start"], 1.0) for c in ticks]
    assert voice_peak - max(tick_peaks) >= 18.0, (voice_peak, tick_peaks)
    assert min(tick_peaks) > -45.0                                       # quiet, still heard


def test_guess_the_game_clue_3_may_not_repeat_clue_1():
    p = _params("guess_the_game")
    assert interactive.same_clue("You can raise a baby or a pet here.", "Raise your pets and a baby.") == {"raise", "pet", "baby"}
    assert not interactive.same_clue("You start in a quiet forest.", "You chop wood and sell it.")
    assert not interactive.same_clue("You can raise a baby or a pet here.", "A legendary neon pet is the big dream.")   # one shared word
    again = json.loads(json.dumps(GTG_OK))
    again["rounds"][2]["clues"][2] = "Play as a criminal or the police."
    with pytest.raises(stories.StoryError, match="round 3: clue 3 repeats clue 1"):
        interactive.validate("guess_the_game", again, p)
    assert "Clue 1 is only the setting" in interactive.prompt_for("guess_the_game", seed="x", used=[])[0]
