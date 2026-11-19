import json
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
    assert beats[3].panel["open"][2] == "100 Robux" and beats[3].panel["now"] == 2 and len(beats) == 9
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
    assert interactive.tick_cmd("t.wav")[-1] == "t.wav" and "apad=whole_dur=1" in " ".join(interactive.tick_cmd("t.wav"))
    assert "1320" in " ".join(interactive.tick_cmd("t.wav", last=True))
    assert interactive.silence_cmd("s.wav", 0.25)[interactive.silence_cmd("s.wav", 0.25).index("-t") + 1] == "0.250"


@pytest.mark.parametrize("panel", [
    {"kind": "wyr", "round": 1, "of": 4, "a": "get a dragon pet that cannot fly at all", "b": "get a cat"},
    {"kind": "clues", "round": 2, "of": 3, "clues": ["One clue here.", "Another clue that is a bit longer than one line."],
     "answer": "Natural Disaster Survival"},
    {"kind": "pick", "round": 1, "of": 2, "thing": "egg", "open": ["a rainbow dragon pet", None, None], "now": 0},
    {"kind": "cta", "text": "COMMENT YOUR PICKS"}])
def test_panel_png_draws_every_kind(tmp_path, panel):
    from PIL import Image
    out = interactive.panel_png(panel, str(tmp_path / "p.png"), header="WOULD YOU RATHER", width=540, height=960, count=3,
                                caption="Would you rather?")
    im = Image.open(out)
    assert im.size == (540, 960) and im.mode == "RGBA"
    assert im.getpixel((5, 950))[3] == 0                                  # the gameplay shows through outside the boxes


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
