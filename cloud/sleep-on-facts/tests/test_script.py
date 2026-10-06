import pytest
from sof.prompts import OUTLINE_PROMPT, SEGMENT_PROMPT, METADATA_PROMPT
from sof.script import Script, ScriptTooLong, generate_script


class FakeLLM:
    def __init__(self, words_per_segment=700, movements=4):
        self.w, self.m, self.calls = words_per_segment, movements, []

    def generate_json(self, prompt, *, system=None, max_tokens=3000, temperature=0.7):
        self.calls.append(prompt)
        if "Plan exactly" in prompt:
            start = len([c for c in self.calls if "Plan exactly" in c]) * 100
            return {"title": "Calm Facts", "hook": "Close your eyes.",
                    "movements": [{"heading": f"Part {start + i}", "beats": ["a", "b"]} for i in range(self.m)],
                    "image_search_queries": ["q1", "q2"]}
        if "CURRENT MOVEMENT" in prompt:
            return {"body": " ".join(["calm"] * self.w)}
        return {"title": "Calm Facts About Whales to Fall Asleep To (3 Hours)", "description": "d", "tags": ["a", "b"]}


KW = dict(minutes=20, target_words=3000, num_movements=4, max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)


def test_loops_until_target_reached():
    llm = FakeLLM(words_per_segment=600, movements=4)
    s = generate_script(llm, topic="Whales", hints="", **KW)
    assert s.word_count == 3000 and s.movements == 5  # 4 planned + 1 from the refill, stops at target
    assert sum("Plan exactly" in c for c in llm.calls) == 2
    assert s.title.startswith("Calm Facts") and s.tags == ["a", "b"] and s.image_queries == ["q1", "q2"]
    assert Script.from_dict(s.to_dict()) == s


def test_too_long_raises():
    with pytest.raises(ScriptTooLong):
        generate_script(FakeLLM(words_per_segment=2000, movements=4), topic="W", hints="", **KW)


def test_segment_cap_stops_loop():
    s = generate_script(FakeLLM(words_per_segment=100, movements=4), topic="W", hints="", **{**KW, "max_segments": 6})
    assert s.movements == 6


def test_prompts_have_placeholders():
    for key in ("{{topic}}", "{{topic_hints}}", "{{num_movements}}", "{{target_minutes}}"):
        assert key in OUTLINE_PROMPT
    for key in ("{{movement_heading}}", "{{movement_beats}}", "{{previous_tail}}", "{{target_words}}", "{{running_summary}}"):
        assert key in SEGMENT_PROMPT
    for key in ("{{topic}}", "{{hours}}", "{{opening}}"):
        assert key in METADATA_PROMPT


def test_smoke_target_scales_plan_down():
    llm = FakeLLM(words_per_segment=230, movements=16)
    s = generate_script(llm, topic="W", hints="", minutes=3, target_words=450, num_movements=16,
                        max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)
    assert "Plan exactly 2 movements" in llm.calls[0]
    assert s.word_count <= 450 * 1.3


def test_full_target_keeps_sixteen_movements():
    llm = FakeLLM(words_per_segment=1500, movements=16)
    s = generate_script(llm, topic="W", hints="", minutes=180, target_words=27000, num_movements=16,
                        max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)
    assert "Plan exactly 16 movements" in llm.calls[0] and "about 1500 words" in llm.calls[1]
    assert s.movements >= 16
