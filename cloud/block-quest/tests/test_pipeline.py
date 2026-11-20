import json
from pathlib import Path

from bqd import config, pipeline, stories


def _cfg(tmp_path, **kw):
    import os
    os.environ.pop("TTS_BACKEND", None)
    cfg = config.Config.from_env(**kw)
    cfg.ledger_path = str(tmp_path / "ledger.json")
    cfg.work_dir = str(tmp_path / "work")
    cfg.topics_path = str(Path(__file__).resolve().parents[1] / "topics.yml")
    return cfg


class FakeLLM:
    def __init__(self, answers):
        self.answers, self.stats = list(answers), {"claude": 0}

    def generate_json(self, prompt, **kw):
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return json.loads(json.dumps(a))


def _answer(n):
    body = (f"Story {n} starts with a stolen bike. " + "Then it kept going with one concrete detail after another for a while. " * 4
            + "Would you have told him?")
    return {"title": f"Story {n} title", "hook": f"Story {n} starts with a stolen bike.", "body": body,
            "broll_queries": ["bike lane at dusk", "city street rain"], "tags": ["bike"]}


def test_write_stories_skips_failures_and_is_resumable(tmp_path):
    cfg = _cfg(tmp_path, count=3)
    cfg.mix = ("story",)                       # all three slots are stories here; the mix is tested in test_interactive
    cfg.noslop_rounds = 0
    cfg.words_min = 40
    work = tmp_path / "work" / "2026-11-08"
    work.mkdir(parents=True)
    bad = {"title": "x", "hook": "y", "body": "no?", "broll_queries": []}
    llm = FakeLLM([_answer(1), bad, bad, _answer(3)])
    out = pipeline.write_stories(cfg, llm, [], today="2026-11-08", work=work)
    assert [s.title for s in out] == ["Story 1 title", "Story 3 title"] and (work / "stories.json").exists()
    again = pipeline.write_stories(cfg, FakeLLM([]), [], today="2026-11-08", work=work)
    assert [s.title for s in again] == ["Story 1 title", "Story 3 title"]


def test_run_is_a_noop_when_today_is_in_the_ledger(tmp_path):
    cfg = _cfg(tmp_path, count=3)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (tmp_path / "ledger.json").write_text(json.dumps([{"date": today, "kind": "shorts", "stories": []}]), encoding="utf-8")
    assert pipeline.run(cfg) == pipeline.EXIT_OK


def test_categories_in_topics_yml_are_wired():
    cats, personas = stories.load_topics(str(Path(__file__).resolve().parents[1] / "topics.yml"))
    assert len(cats) == 10 and all(c.persona in personas for c in cats) and sum(c.weight for c in cats) == 100
    from bqd import seo
    assert all(c.key in seo.CATEGORY_LABELS for c in cats)                    # every Roblox category has a label


# ---- Roblox gameplay backgrounds

def _manifest(tmp_path, n=6, credit=True):
    import yaml
    src = {"channel": "Some Channel", "url": "https://www.youtube.com/watch?v=abc", "licence": "CC BY"} if credit else {"url": "x"}
    data = {"base_url": "https://example.test/dl/", "sources": {"abc": src},
            "clips": [{"file": f"abc_{i:03d}.mp4", "source": "abc", "seconds": 75 if i else 20} for i in range(n)]}
    p = tmp_path / "gameplay.yml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(p)


def test_gameplay_manifest_pick_and_credit(tmp_path):
    from bqd import gameplay
    clips = gameplay.load(_manifest(tmp_path))
    assert len(clips) == 6 and clips[1].download_url == "https://example.test/dl/abc_001.mp4"
    assert clips[0].credit == "Gameplay: Some Channel (CC BY) https://www.youtube.com/watch?v=abc"
    a = gameplay.pick(clips, count=3, seed="2026-11-19", avoid=[], min_seconds=60)
    assert len({c.file for c in a}) == 3 and all(c.seconds >= 60 for c in a)             # long enough, all different
    assert [c.file for c in a] == [c.file for c in gameplay.pick(clips, count=3, seed="2026-11-19", avoid=[], min_seconds=60)]
    b = gameplay.pick(clips, count=2, seed="2026-11-19", avoid=[c.file for c in a], min_seconds=60)
    assert not {c.file for c in b} & {c.file for c in a}                                 # clips used lately are skipped
    assert len(gameplay.pick(clips, count=3, seed="x", avoid=[c.file for c in clips])) == 3   # unless nothing else is left
    assert gameplay.recent_files([{"stories": [{"gameplay": "a.mp4"}, {}]}, {"stories": [{"gameplay": "b.mp4"}]}], limit=1) == ["b.mp4"]


def test_gameplay_clip_without_a_credit_is_never_used(tmp_path):
    from bqd import gameplay
    assert gameplay.load(_manifest(tmp_path, credit=False)) == [] and gameplay.load(str(tmp_path / "missing.yml")) == []


def test_real_gameplay_manifest_is_complete():
    from bqd import gameplay
    clips = gameplay.load(str(Path(__file__).resolve().parents[1] / "gameplay.yml"))
    assert len(clips) >= 30 and all(c.licence == "CC BY" and c.url.startswith("https://www.youtube.com/watch?v=") for c in clips)
    assert len({c.file for c in clips}) == len(clips)


def test_roblox_seo_credits_the_gameplay_and_is_gaming():
    from bqd import seo
    credit = "Gameplay: Some Channel (CC BY) https://www.youtube.com/watch?v=abc"
    d = seo.short_description(hook="I lost my best pet.", category="trade_scams", tags=["trade", "pet"], credit=credit)
    assert credit in d and "Not affiliated with or endorsed by Roblox Corporation" in d and "Pexels" not in d
    assert "#roblox" in d and "github" not in d.lower()
    assert seo.short_title("The trade I should not have accepted", "trade_scams", roblox=True).endswith("#roblox #Shorts")
    assert len(seo.short_title("x" * 120, "trade_scams", roblox=True)) <= 100
    assert seo.short_tags("trade_scams", ["pet"], roblox=True)[0] == "roblox"
    assert seo.video_body(title="t", description="d", tags=[], privacy="public", gaming=True)["snippet"]["categoryId"] == "20"
    assert "Pexels" in seo.short_description(hook="h", category="aita", tags=[])           # the stock path is unchanged


def test_story_validation_needs_no_stock_searches_over_gameplay():
    import pytest
    a = _answer(1)
    a["broll_queries"] = []
    assert stories.validate(dict(a), words_min=20, words_max=150, need_queries=False)["body"]
    with pytest.raises(stories.StoryError):
        stories.validate(dict(a), words_min=20, words_max=150)


def test_story_shorts_show_a_few_words_never_the_channel_wide_ones():
    """Felix 2026-11: a story Short shows emphasis words only; "Roblox" / "Robux" alone would be on every Short."""
    from cc import textbudget
    from sof import shorts as sof_shorts
    body = ("I joined a Roblox obby with my friend Maya last night. We were stuck on stage 99 for an hour. "
            "Then Maya found a secret ladder behind the lava, and we got 500 Robux each. Every Roblox obby has one. "
            "Would you have taken the ladder?")
    cues = sof_shorts.caption_cues(body, 40.0)
    picked = textbudget.pick(textbudget.words_from_cues(cues), duration=40.0, skip=pipeline.SKIP_WORDS)
    texts = [t for _, _, t in picked]
    assert "500 Robux" in texts and not {"roblox", "robux"} & {t.lower() for t in texts}
    assert "Roblox" in [t for _, _, t in textbudget.pick(textbudget.words_from_cues(cues), duration=40.0)]   # without the skip
    assert textbudget.shown_words(picked) <= 8 and all(len(t.split()) <= 3 for t in texts)
    poss = textbudget.words_from_cues([(0.0, 20.0, "We waited while Roblox's servers came back, then Theo typed hi.")])
    assert [t for _, _, t in textbudget.pick(poss, duration=20.0, per_minute=30, min_gap=0.5, skip=pipeline.SKIP_WORDS)] == ["Theo"]
