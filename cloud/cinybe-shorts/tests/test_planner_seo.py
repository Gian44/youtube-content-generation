import json

import pytest

from cny import planner, seo


def _acts(n=5):
    return [{"index": i, "title": f"Act {i}", "summary": "Something happens " * 8, "broll_queries": ["city at night", "rain window"],
             "stakes": "a lot"} for i in range(n)]


def _scripts(acts, *, words_per=60, shorts=3):
    return {"long_form": {"title": "Inception Explained", "description": "Spoilers. A heist in dreams.", "thumb_text": "DREAM HEIST",
                          "tags": ["inception", "nolan"],
                          "sections": [{"act_index": a["index"], "text": " ".join(["word"] * words_per)} for a in acts]},
            "shorts": [{"act_index": k, "hook": "He steals a secret.", "text": "He steals a secret. " * 5, "title": f"Short {k}",
                        "tags": ["x"]} for k in range(shorts)]}


def test_validate_acts_normalises_queries_and_rejects_bad_shapes():
    acts = _acts(5)
    acts[2]["broll_queries"] = "single query"
    out = planner.validate_acts({"acts": acts}, acts_min=4, acts_max=6)
    assert out[2]["broll_queries"] == ["single query"]
    with pytest.raises(planner.PlanError, match="expected 4-6 acts"):
        planner.validate_acts({"acts": _acts(2)}, acts_min=4, acts_max=6)
    bad = _acts(5)
    bad[1]["index"] = 7
    bad[3].pop("broll_queries")
    with pytest.raises(planner.PlanError) as e:
        planner.validate_acts({"acts": bad}, acts_min=4, acts_max=6)
    assert "contiguous" in str(e.value) and "broll_queries" in str(e.value)


def test_validate_scripts_word_band_and_shorts():
    acts = _acts(5)
    good = planner.validate_scripts(_scripts(acts), acts, target_words=300, shorts_per_film=3, short_max_words=55)
    assert good["long_form"]["tags"] == ["inception", "nolan"]
    with pytest.raises(planner.PlanError, match="outside"):
        planner.validate_scripts(_scripts(acts, words_per=10), acts, target_words=300, shorts_per_film=3, short_max_words=55)
    dup = _scripts(acts)
    dup["shorts"][1]["act_index"] = 0
    with pytest.raises(planner.PlanError, match="reuses act_index"):
        planner.validate_scripts(dup, acts, target_words=300, shorts_per_film=3, short_max_words=55)
    long_short = _scripts(acts)
    long_short["shorts"][0]["text"] = "w " * 80
    with pytest.raises(planner.PlanError, match="must be 1-55"):
        planner.validate_scripts(long_short, acts, target_words=300, shorts_per_film=3, short_max_words=55)
    missing = _scripts(acts)
    missing["long_form"]["sections"].pop()
    with pytest.raises(planner.PlanError, match="must have 5 entries"):
        planner.validate_scripts(missing, acts, target_words=300, shorts_per_film=3, short_max_words=55)


class FakeLLM:
    """Answers in order; records prompts. Mimics sof.llm.LLM.generate_json."""

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []
        self.stats = {"claude": 0}

    def generate_json(self, prompt, *, system=None, max_tokens=3000, temperature=0.7, model=None, reasoning="low"):
        self.prompts.append((prompt, system, model))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return json.loads(json.dumps(a))


def test_plan_film_retries_once_with_errors_and_polishes():
    acts = _acts(5)
    bad_scripts = _scripts(acts, words_per=5)
    llm = FakeLLM([{"acts": acts}, bad_scripts, _scripts(acts), {"paragraphs": []}, {"paragraphs": []}])
    plan = planner.plan_film(llm, title="Inception", year=2010, plot_text="plot " * 300, lead="lead", meta={"director": "Nolan"},
                             target_words=300, acts_range=(4, 6), shorts_per_film=3, short_max_words=55, noslop_rounds=1,
                             fast_model="fast")
    assert len(plan.acts) == 5 and len(plan.shorts) == 3
    assert "Validation errors" in llm.prompts[2][0]                 # the scripts call was retried with the errors
    assert "STYLE RULES" in llm.prompts[1][0]                       # noslop rules injected before "Return JSON"
    assert plan.sections[0]["heading"] == "Act 0" and plan.sections[4]["act_index"] == 4
    d = planner.FilmPlan.from_dict(plan.to_dict())
    assert d.long_form["title"] == "Inception Explained"


def test_plan_film_raises_after_second_failure():
    acts = _acts(5)
    llm = FakeLLM([{"acts": acts}, _scripts(acts, words_per=5), _scripts(acts, words_per=5)])
    with pytest.raises(planner.PlanError):
        planner.plan_film(llm, title="X", year=None, plot_text="p", lead="", meta={}, target_words=300, acts_range=(4, 6),
                          shorts_per_film=3, short_max_words=55)


def test_despop_keeps_short_within_cap():
    acts = _acts(2)
    scripts = _scripts(acts, shorts=2)
    scripts["shorts"][0]["text"] = "It is not a dream but a heist. Ultimately, this matters."
    llm = FakeLLM([{"paragraphs": []}, {"paragraphs": [{"id": 0, "text": "w " * 70}]}])
    stats = planner.despop(llm, scripts, title="X", rounds=1, short_max_words=55, fast_model="fast")
    assert scripts["shorts"][0]["text"].startswith("It is not a dream")        # over-long rewrite rejected
    assert set(stats) <= {"long_form", "shorts"} and stats["shorts"]["before"] >= 1


def test_long_title_and_description_and_tags():
    t = seo.long_title("Inception", 2010, minutes=10, writer_title="The Heist Inside a Dream: Inception", variant=0)
    assert t == "The Heist Inside a Dream Inception | Full Recap" or t.endswith("| Full Recap")
    assert seo.long_title("Inception", 2010, minutes=10, writer_title="A very long line that does not name the film at all and runs on", variant=1) \
        == "Inception (2010): The Whole Story, Start to Finish"
    assert len(seo.long_title("X" * 120, 2000, minutes=9)) <= 100
    d = seo.long_description(film="Inception", year=2010, blurb="A thief. Spoilers.", chapters=[{"start": 0, "title": "Setup"}, {"start": 61, "title": "Heist"}, {"start": 130, "title": "Limbo"}],
                             plot_url="https://en.wikipedia.org/wiki/Inception#Plot", director="Christopher Nolan", theme="Christopher Nolan")
    assert "0:00 Setup" in d and "1:01 Heist" in d and "CC BY-SA" in d and "Directed by Christopher Nolan" in d
    tags = seo.long_tags("Inception", director="Christopher Nolan", genres=["Science Fiction", "Heist"], cast=["Leonardo DiCaprio"],
                         extra=["dreams"], theme="Christopher Nolan")
    assert "Inception" in tags and "science fiction" in tags and len(tags) <= 30 and sum(len(t) + 2 for t in tags) <= 480
    assert len({t.lower() for t in tags}) == len(tags)


def test_short_title_and_publish_times():
    assert seo.short_title("The totem never falls", "Inception") == "The totem never falls (Inception) #Shorts"
    assert seo.short_title("Inception's ending", "Inception") == "Inception's ending #Shorts"
    from datetime import datetime, timezone
    now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    assert seo.short_publish_at(None, delay_minutes=60, index=0, spacing_hours=24, now=now) == "2026-11-08T11:00:00Z"
    assert seo.short_publish_at(None, delay_minutes=60, index=2, spacing_hours=24, now=now) == "2026-11-10T11:00:00Z"
    body = seo.video_body(title="t", description="d", tags=["a"], privacy="public")
    assert body["snippet"]["categoryId"] == "1" and body["status"]["privacyStatus"] == "public"
    body = seo.video_body(title="t", description="d", tags=["a"], privacy="public", publish_at="2026-11-08T11:00:00Z")
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"] == "2026-11-08T11:00:00Z"
