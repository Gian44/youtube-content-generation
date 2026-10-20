"""The vendored no-slop lint stays identical to the cloud copy, and the fiction generator uses it:
style rules land in every resolved prompt, flagged story bodies get one targeted rewrite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from storyfactory.services import noslop

WORKER = Path(__file__).resolve().parents[1]
CLOUD_COPY = WORKER.parents[1] / "cloud" / "sleep-on-facts" / "sof" / "noslop.py"


def test_vendored_copy_matches_the_cloud_module():
    if not CLOUD_COPY.exists():
        pytest.skip("cloud/sleep-on-facts not checked out beside apps/worker")
    mine = (WORKER / "storyfactory" / "services" / "noslop.py").read_text(encoding="utf-8")
    assert mine == CLOUD_COPY.read_text(encoding="utf-8"), "copy cloud/sleep-on-facts/sof/noslop.py over the worker's"


def test_fiction_rules_catch_the_reddit_story_tics():
    body = ("What if I told you my sister stole my wedding? Here's the thing: she did. Not a joke. Not a prank. A plan.\n\n"
            "She booked the venue — my venue — on the same day. It's not about the money. It's about respect.\n\n"
            "Ultimately, family isn't blood. It's who shows up.")
    r = noslop.lint(body, mode="fiction")
    rules = {f.rule for f in r.hard}
    assert {"rhetorical_setup", "throat_clearing", "negative_listing", "em_dashes", "binary_contrast", "summary_ending"} <= rules
    assert "question" not in rules and "mood_words" not in rules          # fiction keeps questions and feelings


def test_style_block_is_injected_before_the_output_spec_in_dry_run(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.db.engine import get_session
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.models import DailyBatch
    from storyfactory.db.seed import run_seed
    from storyfactory.pipeline.story_generator import generate_short_stories
    from storyfactory.services.channel_service import create_channel

    run_migrations()
    run_seed()
    session = get_session()
    try:
        ch = create_channel(session, name="Fic", slug="fic")
        batch = DailyBatch(id="b1", channel_id=ch.id, date="2026-01-01", category="aita", status="topic_selected")
        session.add(batch)
        session.commit()
        (story,) = generate_short_stories(batch, 1)
        assert "STYLE RULES" in story.prompt_used and "Plot twist" in story.prompt_used
        assert story.prompt_used.index("STYLE RULES") < story.prompt_used.index("Output as JSON")
        assert "noslop" in json.loads(story.raw_output)          # linted even in dry-run (no rewrite call)
    finally:
        session.close()


SLOPPY = ("I'm not a dramatic person. I'm just tired. My sister took my car on Tuesday without asking.\n\n"
          "She drove it to Reno and left it at a casino. I found it three days later with a parking ticket. "
          "Here's the thing: she never apologized.")
CLEAN0 = "I'm a tired person, not a dramatic one, and my sister took my car on Tuesday without asking."
CLEAN1 = ("She drove it to Reno and left it at a casino. I found it three days later with a parking ticket. "
          "She never apologized.")


def test_despop_rewrites_flagged_paragraphs_with_the_cheap_call(monkeypatch):
    from storyfactory.pipeline import story_generator as sg

    calls = []

    def fake_generate_text(**kw):
        calls.append(kw)
        return json.dumps({"paragraphs": [{"id": 0, "text": CLEAN0}, {"id": 1, "text": CLEAN1}]})

    monkeypatch.setattr(sg, "generate_text", fake_generate_text)
    monkeypatch.setattr(sg, "effective_config", lambda key, default=None: 1)
    data = sg.despop({"hook": "I'm not a dramatic person.", "body": SLOPPY, "word_count": 1}, dry_run=False)
    assert data["body"] == CLEAN0 + "\n\n" + CLEAN1 and data["word_count"] == len(data["body"].split())
    assert data["hook"] == CLEAN0                                 # the old hook vanished, so the first sentence is the hook
    assert data["noslop"]["rewritten"] == 2 and data["noslop"]["after"]["findings"] == 0
    assert len(calls) == 1 and calls[0]["temperature"] == 0.2 and calls[0]["response_format"] == "json"
    assert "PARAGRAPH id=0" in calls[0]["prompt"] and "throat_clearing" in calls[0]["prompt"]


def test_despop_is_lint_only_in_dry_run_and_survives_a_failing_editor(monkeypatch):
    from storyfactory.pipeline import story_generator as sg

    def boom(**kw):
        raise RuntimeError("no key")

    monkeypatch.setattr(sg, "generate_text", boom)
    monkeypatch.setattr(sg, "effective_config", lambda key, default=None: 1)
    dry = sg.despop({"body": SLOPPY}, dry_run=True)
    assert dry["body"] == SLOPPY and dry["noslop"]["before"]["findings"] >= 2 and dry["noslop"]["rounds"] == 0
    live = sg.despop({"body": SLOPPY}, dry_run=False)
    assert live["body"] == SLOPPY and live["noslop"]["rounds"] == 0
    assert sg.despop({"body": ""}, dry_run=False) == {"body": ""}
