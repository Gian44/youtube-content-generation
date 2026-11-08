import json

import pytest

from cny import planner, seo

FILM = "Inception"


def _acts(n=5, span=600.0):
    return [{"index": i, "title": f"Act {i}", "summary": "Something happens " * 8, "start_seconds": 60 + i * span,
             "end_seconds": 60 + i * span + span * 0.8, "stakes": "a lot"} for i in range(n)]


def _people():
    return [{"name": "Dom Cobb", "descriptor": "the extractor"}, {"name": "Mal", "descriptor": "his dead wife"}]


def _scripts(acts, *, words_per=60, shorts=3, title="A Thief Steals Secrets From Inside People's Dreams"):
    return {"long_form": {"title": title, "description": f"Spoilers. {FILM} (2010): a heist in dreams.", "thumb_text": "DREAM HEIST",
                          "tags": ["inception", "nolan"],
                          "sections": [{"act_index": a["index"], "text": " ".join(["word"] * words_per)} for a in acts]},
            "shorts": [{"act_index": k, "hook": "He steals a secret.", "text": "He steals a secret. " * 5, "title": f"The totem never falls {k}",
                        "tags": ["x"]} for k in range(shorts)]}


def test_validate_acts_checks_order_ranges_and_people():
    acts, people = planner.validate_acts({"acts": _acts(5), "people": _people() + [{"descriptor": "nameless"}]}, acts_min=4, acts_max=6, duration=4000)
    assert [a["index"] for a in acts] == [0, 1, 2, 3, 4] and acts[1]["start_seconds"] == 660.0 and len(people) == 2
    with pytest.raises(planner.PlanError, match="expected 4-6 acts"):
        planner.validate_acts({"acts": _acts(2)}, acts_min=4, acts_max=6, duration=4000)
    bad = _acts(5)
    bad[2]["start_seconds"] = bad[1]["start_seconds"] + 10            # overlaps act 1
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
    with pytest.raises(planner.PlanError, match="must not name the film"):
        planner.validate_scripts(_scripts(acts, title="Inception Explained: The Dream Heist Nobody Understood"), acts, film=FILM,
                                 target_words=300, shorts_per_film=3, short_max_words=80)
    with pytest.raises(planner.PlanError, match="outside"):
        planner.validate_scripts(_scripts(acts, words_per=10), acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    named = _scripts(acts)
    named["long_form"]["sections"][2]["text"] = "Inception begins when Cobb " + "word " * 50
    with pytest.raises(planner.PlanError, match="never says the film's name"):
        planner.validate_scripts(named, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    dup = _scripts(acts)
    dup["shorts"][1]["act_index"] = 0
    with pytest.raises(planner.PlanError, match="reuses act_index"):
        planner.validate_scripts(dup, acts, film=FILM, target_words=300, shorts_per_film=3, short_max_words=80)
    # a two-word film name counts only when both words appear
    assert planner._names_film("the dark knight rises again", "The Dark Knight") and not planner._names_film("a dark night", "The Dark Knight")


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


def test_plan_film_from_transcript_retries_once_and_polishes():
    acts = _acts(5)
    llm = FakeLLM([{"acts": acts, "people": _people()}, _scripts(acts, words_per=5), _scripts(acts), {"paragraphs": []}, {"paragraphs": []}])
    plan = planner.plan_film(llm, title=FILM, year=2010, transcript="[00:10] Hello.\n[00:20] Who are you?", duration=4000,
                             plot_text="Cobb steals.", target_words=300, acts_range=(4, 6), shorts_per_film=3, short_max_words=80,
                             noslop_rounds=1, fast_model="fast")
    assert len(plan.acts) == 5 and plan.acts[0]["start_seconds"] == 60.0 and len(plan.shorts) == 3
    assert "[00:10] Hello." in llm.prompts[0][0] and "published plot summary" in llm.prompts[0][0]
    assert "the extractor" in llm.prompts[1][0] and "STYLE RULES" in llm.prompts[1][0]     # people + noslop rules in the writer prompt
    assert "Validation errors" in llm.prompts[2][0]                                      # scripts retried with the errors
    assert plan.long_form["people"] == _people() and plan.sections[4]["act_index"] == 4
    assert planner.FilmPlan.from_dict(plan.to_dict()).long_form["title"] == plan.long_form["title"]


def test_plan_film_raises_after_second_failure():
    acts = _acts(5)
    llm = FakeLLM([{"acts": acts}, _scripts(acts, words_per=5), _scripts(acts, words_per=5)])
    with pytest.raises(planner.PlanError):
        planner.plan_film(llm, title="X", year=None, transcript="t", duration=4000, target_words=300, acts_range=(4, 6),
                          shorts_per_film=3, short_max_words=80)


def test_despop_keeps_short_within_cap():
    acts = _acts(2)
    scripts = _scripts(acts, shorts=2)
    scripts["shorts"][0]["text"] = "It is not a dream but a heist. Ultimately, this matters."
    llm = FakeLLM([{"paragraphs": []}, {"paragraphs": [{"id": 0, "text": "w " * 90}]}])
    stats = planner.despop(llm, scripts, title="X", rounds=1, short_max_words=80, fast_model="fast")
    assert scripts["shorts"][0]["text"].startswith("It is not a dream")        # over-long rewrite rejected
    assert set(stats) <= {"long_form", "shorts"} and stats["shorts"]["before"] >= 1


def test_titles_description_and_tags():
    assert seo.long_title("poor man wants to die, but accidentally becomes a millionaire") == \
        "Poor man wants to die, but accidentally becomes a millionaire | Movie Recap"
    assert len(seo.long_title("x" * 150)) <= 100 and seo.long_title("", fallback="Inception explained") == "Inception explained | Movie Recap"
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
