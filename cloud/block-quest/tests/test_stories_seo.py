import json
from datetime import datetime, timezone

import pytest

from bqd import config, seo, stories


def _cats():
    return [stories.Category("aita", 15, "dilemmas", "confession"), stories.Category("revenge", 12, "karma", "sarcastic"),
            stories.Category("wholesome", 5, "kindness", "warm"), stories.Category("mysteries", 3, "cold cases", "calm")]


def test_draw_is_deterministic_distinct_and_avoids_recent():
    a = stories.draw_categories(_cats(), count=3, avoid={"aita"}, seed="2026-11-08")
    b = stories.draw_categories(_cats(), count=3, avoid={"aita"}, seed="2026-11-08")
    assert [c.key for c in a] == [c.key for c in b] and len({c.key for c in a}) == 3 and "aita" not in {c.key for c in a}
    # not enough left after avoiding → the whole pool is used
    c = stories.draw_categories(_cats(), count=3, avoid={"aita", "revenge"}, seed="x")
    assert len(c) == 3
    assert [x.key for x in stories.draw_categories(_cats(), count=2, avoid=set(), seed="x", force="revenge")] == ["revenge", "revenge"]
    with pytest.raises(stories.StoryError):
        stories.draw_categories(_cats(), count=1, avoid=set(), seed="x", force="nope")


def test_recent_categories_and_hooks():
    rows = [{"date": "2026-11-06", "kind": "shorts", "stories": [{"category": "aita", "hook": "A"}, {"category": "revenge", "hook": "B"}]},
            {"date": "2026-11-01", "kind": "shorts", "stories": [{"category": "mysteries", "title": "C"}]}]
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    assert stories.recent_categories(rows, days=2, now=now) == {"aita", "revenge"}
    assert stories.recent_hooks(rows, limit=2) == ["B", "C"]


def _story(**kw):
    body = ("My sister sold my car while I was in hospital. I had the keys in my bag. She said the title was in Dad's name. "
            "Two weeks later a man in a grey van parked outside our house and asked for the spare key. I told him to leave. "
            "It was not the money but the lie that got me. Dad says I should let it go because family is family. Would you have called the police on your own sister?")
    d = {"title": "My sister sold my car while I was in hospital", "hook": "My sister sold my car while I was in hospital.",
         "body": body, "broll_queries": ["hospital corridor at night", "grey van on a suburban street", "car keys on a table"],
         "tags": ["family", "car", "AITA"]}
    d.update(kw)
    return d


def test_validate_accepts_a_good_story_and_rejects_the_bad_ones():
    out = stories.validate(_story(), words_min=40, words_max=150)
    assert out["tags"] == ["family", "car", "aita"] and len(out["broll_queries"]) == 3
    with pytest.raises(stories.StoryError, match="closing question"):
        stories.validate(_story(body=_story()["body"].rstrip("?") + "."), words_min=40, words_max=150)
    with pytest.raises(stories.StoryError, match="start with the hook"):
        stories.validate(_story(hook="Something else entirely."), words_min=40, words_max=150)
    with pytest.raises(stories.StoryError, match="Reddit"):
        stories.validate(_story(body="I posted this on Reddit. " + _story()["body"]), words_min=40, words_max=150)
    with pytest.raises(stories.StoryError, match="words"):
        stories.validate(_story(), words_min=100, words_max=150)
    with pytest.raises(stories.StoryError, match="hook must not be a question"):
        stories.validate(_story(hook="My sister sold my car?", body="My sister sold my car? " + _story()["body"]), words_min=40, words_max=160)


class FakeLLM:
    def __init__(self, answers):
        self.answers, self.prompts, self.stats = list(answers), [], {"claude": 0}

    def generate_json(self, prompt, *, system=None, max_tokens=3000, temperature=0.7, model=None, reasoning="low"):
        self.prompts.append((prompt, system, model))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return json.loads(json.dumps(a))


def test_write_story_retries_with_errors_then_polishes():
    bad = _story(body=_story()["body"].rstrip("?") + ".")
    llm = FakeLLM([bad, _story(), {"paragraphs": []}])
    cat = stories.Category("aita", 15, "dilemmas", "confession")
    s = stories.write_story(llm, cat, persona_note="honest", used_hooks=["Old hook"], words_min=40, words_max=150, words_target=70,
                            noslop_rounds=1, fast_model="fast")
    assert s.category == "aita" and s.body.endswith("?") and "Validation errors" in llm.prompts[1][0]
    assert "STYLE RULES" in llm.prompts[0][0] and "- Old hook" in llm.prompts[0][0]
    assert llm.prompts[2][2] == "fast"                      # the rewrite goes to the fast tier
    assert set(s.slop) == {"before", "after", "rewritten"}
    d = stories.Story.from_dict(s.to_dict())
    assert d.words == s.words


def test_write_story_gives_up_after_two_rejections():
    bad = _story(body="too short?")
    llm = FakeLLM([bad, bad])
    with pytest.raises(stories.StoryError):
        stories.write_story(llm, stories.Category("aita", 1, "", "calm"), persona_note="", used_hooks=[], words_min=40, words_max=150,
                            words_target=70, noslop_rounds=0)


def test_seo():
    assert seo.short_title("my sister sold my car!", "aita") == "My sister sold my car #Shorts"
    assert len(seo.short_title("x" * 200, "aita")) <= 100
    d = seo.short_description(hook="My sister sold my car.", category="aita", tags=["family", "car"])
    assert "Am I Wrong?" in d and "#storytime" in d and "Original fiction" in d
    tags = seo.short_tags("workplace_drama", ["boss", "quit"])
    assert "workplace drama" in tags and "boss" in tags and len({t.lower() for t in tags}) == len(tags)
    now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    assert seo.publish_at(0, spacing_hours=8, now=now) is None
    assert seo.publish_at(2, spacing_hours=8, now=now) == "2026-11-09T02:00:00Z"
    body = seo.video_body(title="t", description="d", tags=["a"], privacy="public", publish_at="2026-11-09T02:00:00Z")
    assert body["snippet"]["categoryId"] == "24" and body["status"]["privacyStatus"] == "private"


def test_config_env(monkeypatch):
    monkeypatch.delenv("TTS_BACKEND", raising=False)
    monkeypatch.setenv("YT_REFRESH_TOKEN_BLOCKQUEST", "rt-bq")
    cfg = config.Config.from_env(count=3, smoke=True)
    assert cfg.privacy == "unlisted" and cfg.sof.tts_backend == "pocket" and cfg.sof.yt_refresh_token == "rt-bq"
    assert cfg.sof.shorts_brand == "Block Quest Daily"
