"""sleep_script_generator — segmented (~3h) calm script generation.

The generator turns ONE topic into a long, flowing single-topic narration by
(1) asking the LLM for an outline of "movements" and (2) expanding each movement
in its own call, so the script is never bounded by a single call's token ceiling.
"""

from __future__ import annotations

import json

import pytest

OUTLINE_TEMPLATE = "OUTLINE {{topic}}|{{topic_hints}}|{{target_minutes}}|{{num_movements}}"
SEGMENT_TEMPLATE = (
    "SEGMENT {{topic}}|{{movement_heading}}|{{movement_beats}}|"
    "{{running_summary}}|{{previous_tail}}|{{target_words}}"
)


def _outline_payload(n_movements: int = 3) -> str:
    movements = [
        {"heading": f"Movement {i}", "beats": [f"beat {i}a", f"beat {i}b"]}
        for i in range(1, n_movements + 1)
    ]
    return json.dumps(
        {
            "title": "Three Hours of Calm Ocean Facts to Fall Asleep To",
            "hook": "Let us drift gently into the quiet of the deep.",
            "movements": movements,
            "asset_keywords": ["ocean", "deep sea", "calm water"],
            "image_search_queries": ["calm ocean", "underwater light"],
        }
    )


# ---------------------------------------------------------------------------
# prompt builders (pure)
# ---------------------------------------------------------------------------

def test_build_outline_prompt_substitutes_all_variables():
    from storyfactory.services import sleep_script_generator as gen

    prompt = gen.build_outline_prompt(
        OUTLINE_TEMPLATE, topic="The Ocean", hints="salty", target_minutes=180, num_movements=16
    )
    assert "{{" not in prompt
    assert "The Ocean" in prompt and "salty" in prompt
    assert "180" in prompt and "16" in prompt


def test_build_segment_prompt_substitutes_all_variables():
    from storyfactory.services import sleep_script_generator as gen

    prompt = gen.build_segment_prompt(
        SEGMENT_TEMPLATE,
        topic="The Ocean",
        heading="Tides",
        beats=["the moon pulls the sea", "two tides a day"],
        running_summary="Already covered: Origins.",
        previous_tail="...and so the waters settled.",
        target_words=1500,
    )
    assert "{{" not in prompt
    assert "Tides" in prompt
    assert "the moon pulls the sea" in prompt
    assert "Already covered: Origins." in prompt
    assert "and so the waters settled." in prompt
    assert "1500" in prompt


# ---------------------------------------------------------------------------
# generate_sleep_script — dry run (no API calls)
# ---------------------------------------------------------------------------

def test_generate_sleep_script_dry_run_makes_no_api_calls(monkeypatch):
    from storyfactory.services import sleep_script_generator as gen

    def _boom(**kwargs):
        raise AssertionError("LLM must not be called in dry-run")

    monkeypatch.setattr(gen, "generate_text", _boom)

    data = gen.generate_sleep_script(
        topic="The Ocean",
        hints="",
        target_minutes=180,
        target_words=27000,
        outline_template=OUTLINE_TEMPLATE,
        segment_template=SEGMENT_TEMPLATE,
        num_movements=4,
        dry_run=True,
    )
    assert data["body"].strip()
    assert "ocean" in data["body"].lower()
    assert data["word_count"] > 0
    assert data["title"]


# ---------------------------------------------------------------------------
# generate_sleep_script — full segmented flow
# ---------------------------------------------------------------------------

def test_generate_sleep_script_stitches_movements_in_order(monkeypatch):
    from storyfactory.services import sleep_script_generator as gen

    captured: list[str] = []

    def _fake_generate(**kwargs):
        prompt = kwargs["prompt"]
        captured.append(prompt)
        if prompt.startswith("OUTLINE"):
            return _outline_payload(3)
        # SEGMENT — echo the heading so we can assert ordering.
        heading = prompt.split("|")[1]
        return json.dumps({"body": f"Prose for {heading}. " * 20})

    monkeypatch.setattr(gen, "generate_text", _fake_generate)

    data = gen.generate_sleep_script(
        topic="The Ocean",
        hints="",
        target_minutes=180,
        target_words=27000,
        outline_template=OUTLINE_TEMPLATE,
        segment_template=SEGMENT_TEMPLATE,
        num_movements=3,
        dry_run=False,
    )

    body = data["body"]
    # Every movement's prose is present, in outline order.
    i1, i2, i3 = (body.index(f"Prose for Movement {n}") for n in (1, 2, 3))
    assert i1 < i2 < i3
    assert data["title"].startswith("Three Hours")
    assert data["asset_keywords"] == ["ocean", "deep sea", "calm water"]
    assert data["image_search_queries"] == ["calm ocean", "underwater light"]
    assert data["word_count"] == len(body.split())
    # 1 outline call + 3 segment calls
    assert len(captured) == 4


def test_generate_sleep_script_threads_continuity_into_later_movements(monkeypatch):
    from storyfactory.services import sleep_script_generator as gen

    segment_prompts: list[str] = []

    def _fake_generate(**kwargs):
        prompt = kwargs["prompt"]
        if prompt.startswith("OUTLINE"):
            return _outline_payload(2)
        segment_prompts.append(prompt)
        heading = prompt.split("|")[1]
        return json.dumps({"body": f"UNIQUEPROSE-{heading} ending-line-{heading}."})

    monkeypatch.setattr(gen, "generate_text", _fake_generate)

    gen.generate_sleep_script(
        topic="The Ocean",
        hints="",
        target_minutes=180,
        target_words=20000,
        outline_template=OUTLINE_TEMPLATE,
        segment_template=SEGMENT_TEMPLATE,
        num_movements=2,
        dry_run=False,
    )

    # Movement 1's prompt has empty continuity; movement 2 carries the prior heading
    # in running_summary and the tail of movement 1's body in previous_tail.
    assert len(segment_prompts) == 2
    first, second = segment_prompts
    assert "Movement 1" in second.split("|")[3]   # running_summary references prior movement
    assert "ending-line-Movement 1" in second.split("|")[4]  # previous_tail = movement 1 tail


def test_generate_sleep_script_tolerates_a_failed_movement(monkeypatch):
    from storyfactory.services import sleep_script_generator as gen

    def _fake_generate(**kwargs):
        prompt = kwargs["prompt"]
        if prompt.startswith("OUTLINE"):
            return _outline_payload(3)
        heading = prompt.split("|")[1]
        if heading == "Movement 2":
            raise RuntimeError("transient LLM error")
        return json.dumps({"body": f"Prose for {heading}."})

    monkeypatch.setattr(gen, "generate_text", _fake_generate)

    data = gen.generate_sleep_script(
        topic="The Ocean",
        hints="",
        target_minutes=180,
        target_words=27000,
        outline_template=OUTLINE_TEMPLATE,
        segment_template=SEGMENT_TEMPLATE,
        num_movements=3,
        dry_run=False,
    )
    # Movements 1 and 3 survive; the run is not aborted by one bad segment.
    assert "Prose for Movement 1" in data["body"]
    assert "Prose for Movement 3" in data["body"]
    assert "Movement 2" not in data["body"]


def test_generate_sleep_script_raises_when_all_movements_fail(monkeypatch):
    from storyfactory.services import sleep_script_generator as gen

    def _fake_generate(**kwargs):
        if kwargs["prompt"].startswith("OUTLINE"):
            return _outline_payload(2)
        raise RuntimeError("LLM down")

    monkeypatch.setattr(gen, "generate_text", _fake_generate)

    with pytest.raises(RuntimeError):
        gen.generate_sleep_script(
            topic="The Ocean",
            hints="",
            target_minutes=180,
            target_words=20000,
            outline_template=OUTLINE_TEMPLATE,
            segment_template=SEGMENT_TEMPLATE,
            num_movements=2,
            dry_run=False,
        )
