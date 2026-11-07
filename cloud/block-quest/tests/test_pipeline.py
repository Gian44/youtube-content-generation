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
    assert len(cats) == 13 and all(c.persona in personas for c in cats) and sum(c.weight for c in cats) == 100
