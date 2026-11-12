import json

import pytest

from cny import planner, seo

FILM = "Inception"


def _acts(n=5, span=600.0):
    return [{"index": i, "title": f"Act {i}", "summary": "Something happens " * 8, "start_seconds": 60 + i * span,
             "end_seconds": 60 + i * span + span * 0.8, "stakes": "a lot"} for i in range(n)]


def _people():
    return [{"name": "Dom Cobb", "descriptor": "the extractor"}, {"name": "Mal", "descriptor": "his dead wife"}]


def _mmss(x):
    x = int(x)
    return f"{x // 60:02d}:{x % 60:02d}"


def _beats(act, words, n=3):
    """``n`` beats spread over the act, ``words`` words in total."""
    per = max(1, words // n)
    span = act["end_seconds"] - act["start_seconds"]
    return [{"at": _mmss(act["start_seconds"] + span * k / n), "text": " ".join(["word"] * per) + "."} for k in range(n)]


def _scripts(acts, *, words_per=60, shorts=3, title="A Thief Steals Secrets From Inside People's Dreams"):
    return {"long_form": {"title": title, "description": f"Spoilers. {FILM} (2010): a heist in dreams.", "thumb_text": "DREAM HEIST",
                          "tags": ["inception", "nolan"],
                          "sections": [{"act_index": a["index"], "beats": _beats(a, words_per)} for a in acts]},
            "shorts": [{"act_index": k, "hook": "He steals a secret.",
                        "beats": [{"at": _mmss(acts[k]["start_seconds"] + 30), "text": "He steals a secret. " * 2},
                                  {"at": _mmss(acts[k]["start_seconds"] + 60), "text": "He steals a secret. " * 3}],
                        "title": f"The totem never falls {k}", "tags": ["x"]} for k in range(shorts)]}


def test_validate_acts_checks_order_ranges_and_people():
    acts, people = planner.validate_acts({"acts": _acts(5), "people": _people() + [{"descriptor": "nameless"}]}, acts_min=4, acts_max=6, duration=4000)
    assert [a["index"] for a in acts] == [0, 1, 2, 3, 4] and acts[1]["start_seconds"] == 660.0 and len(people) == 2
    with pytest.raises(planner.PlanError, match="expected 4-6 acts"):
        planner.validate_acts({"acts": _acts(2)}, acts_min=4, acts_max=6, duration=4000)
    # a fuzzy boundary is repaired, not fatal (run 42474716380 died twice on one): the later act keeps its start
    fuzzy = _acts(5)
    fuzzy[2]["start_seconds"] = fuzzy[1]["end_seconds"] - 40
    got, _ = planner.validate_acts({"acts": fuzzy}, acts_min=4, acts_max=6, duration=4000)
    assert got[1]["end_seconds"] == got[2]["start_seconds"] == fuzzy[1]["end_seconds"]
    tiny = _acts(5)
    tiny[2]["start_seconds"] = tiny[1]["start_seconds"] + 10           # would leave act 1 only 10 s → act 2 moves instead
    got, _ = planner.validate_acts({"acts": tiny}, acts_min=4, acts_max=6, duration=4000)
    assert got[2]["start_seconds"] == got[1]["end_seconds"]
    bad = _acts(5)
    bad[2]["start_seconds"] = bad[1]["start_seconds"] - 100            # out of order: still an error
    bad[4]["end_seconds"] = 9000                                       # outside the runtime
    bad[3]["end_seconds"] = bad[3]["start_seconds"] + 5                # too short
    with pytest.raises(planner.PlanError) as e:
        planner.validate_acts({"acts": bad}, acts_min=4, acts_max=6, duration=4000)
    msg = str(e.value)
    assert "not overlap" in msg and "outside the runtime" in msg and "at least 20s" in msg
    with pytest.raises(planner.PlanError, match="not a number"):
        planner.validate_acts({"acts": [dict(_acts(4)[0], start_seconds="soon")] + _acts(4)[1:]}, acts_min=4, acts_max=6, duration=4000)


def test_validate_scripts_hook_title_word_band_and_shorts():
    acts = _acts(5)
    good = planner.validate_scripts(_scripts(acts), acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    assert good["long_form"]["tags"] == ["inception", "nolan"]
    sec = good["long_form"]["sections"][1]
    assert sec["text"].startswith("word") and [b["at"] for b in sec["beats"]] == [660.0, 820.0, 980.0]     # mm:ss → seconds
    # beats are required, their times repaired (unreadable → interpolated, backwards → lifted, outside → clamped)
    no_beats = _scripts(acts)
    no_beats["long_form"]["sections"][0] = {"act_index": 0, "text": "word " * 60}
    with pytest.raises(planner.PlanError, match="no beats"):
        planner.validate_scripts(no_beats, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    messy = _scripts(acts)
    messy["long_form"]["sections"][1]["beats"] = [{"at": "11:40", "text": "w " * 20}, {"at": "soon", "text": "w " * 20},
                                                  {"at": "10:00", "text": "w " * 20}, {"at": "99:00", "text": "w " * 20}]
    got = planner.validate_scripts(messy, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    assert [b["at"] for b in got["long_form"]["sections"][1]["beats"]] == [700.0, 700.0, 700.0, 1155.0]
    huge = _scripts(acts)
    huge["long_form"]["sections"][0]["beats"][0]["text"] = "word " * 90
    with pytest.raises(planner.PlanError, match="split them"):
        planner.validate_scripts(huge, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    with pytest.raises(planner.PlanError, match="must not name the film"):
        planner.validate_scripts(_scripts(acts, title="Inception Explained: The Dream Heist Nobody Understood"), acts, film=FILM,
                                 target_words=300, shorts_per_film=3, short_max_words=80)
    with pytest.raises(planner.PlanError, match="outside"):
        planner.validate_scripts(_scripts(acts, words_per=10), acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    named = _scripts(acts)
    named["long_form"]["sections"][2]["beats"][0]["text"] = "Inception begins when Cobb " + "word " * 15
    with pytest.raises(planner.PlanError, match="never says the film's name"):
        planner.validate_scripts(named, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    dup = _scripts(acts)
    dup["shorts"][1]["act_index"] = 0
    with pytest.raises(planner.PlanError, match="reuses act_index"):
        planner.validate_scripts(dup, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    # a person's name in the title is rejected; a trailing "| Movie Recap" is dropped
    with pytest.raises(planner.PlanError, match="person's name"):
        planner.validate_scripts(_scripts(acts, title="Dom Cobb Steals Secrets From Inside People's Dreams"), acts, film=FILM,
                                 target_words=300, shorts_per_film=3, short_max_words=80, people=_people())
    ok = planner.validate_scripts(_scripts(acts, title="A Thief Steals Secrets From Inside People's Dreams | Movie Recap"), acts,
                                  film=FILM, target_words=300, shorts_per_film=3, short_max_words=80, people=_people())
    assert ok["long_form"]["title"] == "A Thief Steals Secrets From Inside People's Dreams"
    assert planner._names_person("the man who knew too much", [{"name": "The Man"}]) is None
    # a two-word film name counts only when both words appear
    assert planner._names_film("the dark knight rises again", "The Dark Knight") and not planner._names_film("a dark night", "The Dark Knight")
    assert planner._names_film("In Dark Knight, a man", "The Dark Knight")                       # the leading article is optional
    # the title as a phrase, not its words scattered about (the 2026-11-09 run was rejected for this)
    notld = "Night of the Living Dead"
    assert not planner._names_film("One night the dead rise and the living hide in a farmhouse", notld)
    assert planner._names_film("This is Night of the Living Dead, 1968", notld)
    assert planner._names_film("he watched memento twice", "Memento") and not planner._names_film("a memento of her", "Mementos")


class FakeLLM:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []
        self.stats = {"claude": 0}

    def generate_json(self, prompt, *, system=None, max_tokens=3000, temperature=0.7, model=None, reasoning="low"):
        self.prompts.append((prompt, system, model))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return json.loads(json.dumps(a))


def test_act_dialogue_gives_each_act_its_own_lines():
    transcript = "[00:10] Hello.\n[01:05] Who are you?\n[01:50] Run.\n[10:00] Too late.\n[95:06] The end."
    acts = [{"index": 0, "start_seconds": 0, "end_seconds": 70}, {"index": 1, "start_seconds": 100, "end_seconds": 700},
            {"index": 2, "start_seconds": 5600, "end_seconds": 5800}, {"index": 3, "start_seconds": 4000, "end_seconds": 4100}]
    out = planner.act_dialogue(transcript, acts)
    blocks = out.split("\n\n")
    assert blocks[0].startswith("Act 0 (00:00-01:10):") and "Hello." in blocks[0] and "Who are you?" in blocks[0] and "Run." not in blocks[0]
    assert "Run." in blocks[1] and "Too late." in blocks[1] and "The end." in blocks[2] and "no dialogue" in blocks[3]
    big = "\n".join(f"[{m:02d}:00] " + "w" * 200 for m in range(60))
    trimmed = planner.act_dialogue(big, [{"index": 0, "start_seconds": 0, "end_seconds": 3600}], max_chars=2000)
    assert len(trimmed) < 2600 and trimmed.endswith("[…]")


def test_plan_film_from_transcript_retries_once_and_polishes():
    acts = _acts(5)
    llm = FakeLLM([{"acts": acts, "people": _people()}, _scripts(acts, words_per=5), _scripts(acts), {"paragraphs": []}, {"paragraphs": []}])
    plan = planner.plan_film(llm, title=FILM, year=2010, transcript="[00:10] Hello.\n[00:20] Who are you?", duration=4000,
                             plot_text="Cobb steals.", target_words=300, acts_range=(4, 6), shorts_per_film=3, short_max_words=80,
                             noslop_rounds=1, fast_model="fast")
    assert len(plan.acts) == 5 and plan.acts[0]["start_seconds"] == 60.0 and len(plan.shorts) == 3
    assert "[00:10] Hello." in llm.prompts[0][0] and "published plot summary" in llm.prompts[0][0]
    assert "the extractor" in llm.prompts[1][0] and "STYLE RULES" in llm.prompts[1][0]     # people + noslop rules in the writer prompt
    assert "DIALOGUE PER ACT" in llm.prompts[1][0] and "called by their names from the very first mention" in llm.prompts[1][1]
    assert "Keep it FAST" in llm.prompts[1][0] and "a drifter named Ben" in llm.prompts[1][0]       # names only, no intros
    assert "Validation errors" in llm.prompts[2][0]                                      # scripts retried with the errors
    assert "THE FOOTAGE FOLLOWS YOUR TIMESTAMPS" in llm.prompts[1][0]
    assert all(sec["beats"] and all(isinstance(b["at"], float) for b in sec["beats"]) for sec in plan.sections)
    assert plan.long_form["people"] == _people() and plan.sections[4]["act_index"] == 4
    assert planner.FilmPlan.from_dict(plan.to_dict()).long_form["title"] == plan.long_form["title"]


def test_plan_film_tries_three_times_then_raises():
    acts = _acts(5)
    llm = FakeLLM([{"acts": acts}] + [_scripts(acts, words_per=5)] * 3)
    with pytest.raises(planner.PlanError):
        planner.plan_film(llm, title="X", year=None, transcript="t", duration=4000, target_words=300, acts_range=(4, 6),
                          shorts_per_film=3, short_max_words=80)
    assert len(llm.prompts) == 4 and "Validation errors" in llm.prompts[3][0]
    # a third draft that passes saves the day (a daily channel: one bad draft must not cost the run)
    llm = FakeLLM([{"acts": acts}, _scripts(acts, words_per=5), _scripts(acts, words_per=5), _scripts(acts), {"paragraphs": []}])
    plan = planner.plan_film(llm, title="X", year=None, transcript="t", duration=4000, target_words=300, acts_range=(4, 6),
                             shorts_per_film=3, short_max_words=80, noslop_rounds=0)
    assert len(plan.sections) == 5


def test_despop_works_per_beat_and_keeps_short_within_cap():
    acts = _acts(2)
    scripts = _scripts(acts, shorts=2)
    scripts["shorts"][0]["beats"] = [{"at": 70.0, "text": "It is not a dream but a heist."}, {"at": 80.0, "text": "Ultimately, this matters."}]
    scripts["shorts"][0]["text"] = "It is not a dream but a heist. Ultimately, this matters."
    for sec in scripts["long_form"]["sections"]:
        sec["text"] = " ".join(b["text"] for b in sec["beats"])
    scripts["shorts"][1]["text"] = " ".join(b["text"] for b in scripts["shorts"][1]["beats"])
    llm = FakeLLM([{"paragraphs": []}, {"paragraphs": [{"id": 1, "text": "w " * 90}]}])
    stats = planner.despop(llm, scripts, title="X", rounds=1, short_max_words=80, fast_model="fast")
    assert scripts["shorts"][0]["beats"][1]["text"] == "Ultimately, this matters."         # over-long rewrite rejected, beats intact
    assert set(stats) <= {"long_form", "shorts"} and stats["shorts"]["before"] >= 1


def test_titles_description_and_tags():
    assert seo.long_title("poor man wants to die, but accidentally becomes a millionaire") == \
        "Poor man wants to die, but accidentally becomes a millionaire"                # the hook alone, like the samples
    assert len(seo.long_title("x" * 150)) <= 100 and seo.long_title("", fallback="Inception explained") == "Inception explained"
    long = "CIA Built the Most Sadistic Kill Team to Hunt Terrorists But It Quickly Spirals Out of Control and Then Some More"
    assert len(seo.long_title(long)) <= 100 and not seo.long_title(long).endswith(" Mor")       # ends on a whole word
    d = seo.long_description(film=FILM, year=2010, blurb="A thief. Spoilers.", director="Christopher Nolan", theme="Christopher Nolan",
                             chapters=[{"start": 0, "title": "Setup"}, {"start": 61, "title": "Heist"}, {"start": 130, "title": "Limbo"}],
                             plot_url="https://en.wikipedia.org/wiki/Inception#Plot")
    assert d.startswith("Film: Inception (2010) · directed by Christopher Nolan") and "0:00 Setup" in d and "1:01 Heist" in d
    assert "commentary, criticism and review" in d and "Wikipedia" in d
    tags = seo.long_tags(FILM, director="Christopher Nolan", genres=["Science Fiction", "Heist"], cast=["Leonardo DiCaprio"],
                         extra=["dreams"], theme="Christopher Nolan")
    assert "Inception" in tags and "movie recap" in tags and sum(len(t) + 2 for t in tags) <= 480 and len({t.lower() for t in tags}) == len(tags)


def test_short_title_and_publish_times():
    assert seo.short_title("the totem never falls", FILM) == "The totem never falls #Shorts"
    assert len(seo.short_title("x" * 150, FILM)) <= 100
    sd = seo.short_description(film=FILM, year=2010, long_video_id="abc", hook="He spins the totem.", tags=["dreams"])
    assert "Film: Inception (2010)" in sd and "https://youtu.be/abc" in sd
    from datetime import datetime, timezone
    now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    assert seo.short_publish_at(None, delay_minutes=60, index=0, spacing_hours=24, now=now) == "2026-11-08T11:00:00Z"
    assert seo.short_publish_at(None, delay_minutes=60, index=2, spacing_hours=24, now=now) == "2026-11-10T11:00:00Z"
    body = seo.video_body(title="t", description="d", tags=["a"], privacy="public", publish_at="2026-11-08T11:00:00Z")
    assert body["snippet"]["categoryId"] == "1" and body["status"]["privacyStatus"] == "private"


def test_short_over_the_cap_loses_its_last_beats_not_the_plan():
    acts = _acts(5)
    sc = _scripts(acts)
    sc["shorts"][1]["beats"] = [{"at": "11:00", "text": "He runs. " * 20}, {"at": "11:10", "text": "He runs. " * 20},
                                {"at": "11:20", "text": "She screams at him to stop and he keeps going down the road toward them."}]
    out = planner.validate_scripts(sc, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    s1 = out["shorts"][1]
    assert len(s1["text"].split()) <= 80 and len(s1["beats"]) == 2 and s1["text"] == " ".join(b["text"] for b in s1["beats"])
    too_long = _scripts(acts)
    too_long["shorts"][0]["beats"] = [{"at": "01:30", "text": "word " * 60}, {"at": "02:00", "text": "word " * 60}]
    out = planner.validate_scripts(too_long, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    assert len(out["shorts"][0]["text"].split()) <= 80                     # 120 of 80 words: trimmed, not rejected (147 of 110 failed 11-09)
    too_long = _scripts(acts)
    too_long["shorts"][0]["beats"] = [{"at": "01:30", "text": "word " * 60}, {"at": "02:00", "text": "word " * 60},
                                      {"at": "02:30", "text": "word " * 60}]
    with pytest.raises(planner.PlanError, match="180 words"):
        planner.validate_scripts(too_long, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    assert planner.trim_words("One two. Three four five.", 3) == "One two." and planner.trim_words("a b c", 5) == "a b c"
    assert planner.trim_words("no stop here at all ever", 3) == "no stop here."


def test_act_dialogue_from_cues_stamps_every_line():
    from cny.align import Cue
    cues = [Cue(12.0, 13.0, "Hello."), Cue(65.4, 66.0, "Who are you?"), Cue(3725.0, 3727.0, "Late line.")]
    out = planner.act_dialogue_cues(cues, [{"index": 0, "start_seconds": 0, "end_seconds": 70},
                                           {"index": 1, "start_seconds": 3700, "end_seconds": 3800}])
    assert "[00:12] Hello." in out and "[01:05] Who are you?" in out and "[1:02:05] Late line." in out
