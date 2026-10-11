import pytest
from sof.prompts import OUTLINE_PROMPT, SEGMENT_PROMPT, METADATA_PROMPT
from sof.script import Script, ScriptTooLong, generate_script


class FakeLLM:
    def __init__(self, words_per_segment=700, movements=4):
        self.w, self.m, self.calls = words_per_segment, movements, []

    def generate_json(self, prompt, *, system=None, max_tokens=3000, temperature=0.7, model=None, reasoning="low"):
        self.calls.append(prompt); self.models = getattr(self, 'models', []) + [model]
        if "Plan exactly" in prompt:
            start = len([c for c in self.calls if "Plan exactly" in c]) * 100
            return {"title": "Calm Facts", "hook": "Close your eyes.",
                    "movements": [{"heading": f"Part {start + i}", "beats": ["a", "b"]} for i in range(self.m)],
                    "image_search_queries": ["q1", "q2"]}
        if "CURRENT MOVEMENT" in prompt:
            return {"body": " ".join(["calm"] * self.w)}
        if "Three quiet things to keep" in prompt:
            return {"body": "Three quiet things to keep from tonight. One. Two. Three. Sleep well."}
        return {"title": "Calm Facts About Whales to Fall Asleep To (3 Hours)", "description": "d", "tags": ["a", "b"]}


KW = dict(minutes=20, target_words=3000, num_movements=4, max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)


def test_loops_until_target_reached():
    llm = FakeLLM(words_per_segment=600, movements=4)
    s = generate_script(llm, topic="Whales", corpus=None, **KW)
    # 4 planned + 1 from the refill (stops at target), + the closing signature; opening line adds 10 words
    assert s.movements == 6 and s.word_count == 3000 + 10 + 13
    assert sum("Plan exactly" in c for c in llm.calls) == 2
    assert s.title.startswith("Calm Facts") and s.tags == ["a", "b"] and s.image_queries == ["q1", "q2"]
    assert Script.from_dict(s.to_dict()) == s


def test_too_long_raises():
    with pytest.raises(ScriptTooLong):
        generate_script(FakeLLM(words_per_segment=2000, movements=4), topic="W", corpus=None, **KW)


def test_segment_cap_stops_loop():
    s = generate_script(FakeLLM(words_per_segment=100, movements=4), topic="W", corpus=None, **{**KW, "max_segments": 6})
    assert s.movements == 6 + 1          # the cap applies to research movements; the closing signature is extra


def test_prompts_have_placeholders():
    for key in ("{{topic}}", "{{lead}}", "{{sections}}", "{{wiki_title}}", "{{num_movements}}", "{{target_minutes}}",
                "{{format_name}}", "{{format_guidance}}"):
        assert key in OUTLINE_PROMPT
    for key in ("{{movement_heading}}", "{{movement_beats}}", "{{notes}}", "{{previous_tail}}", "{{target_words}}", "{{running_summary}}"):
        assert key in SEGMENT_PROMPT
    for key in ("{{topic}}", "{{hours}}", "{{opening}}"):
        assert key in METADATA_PROMPT


def test_smoke_target_scales_plan_down():
    llm = FakeLLM(words_per_segment=230, movements=16)
    s = generate_script(llm, topic="W", corpus=None, minutes=3, target_words=450, num_movements=16,
                        max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)
    assert "Plan exactly 2 movements" in llm.calls[0]
    assert s.word_count <= 450 * 1.3


def test_full_target_keeps_sixteen_movements():
    llm = FakeLLM(words_per_segment=1500, movements=16)
    s = generate_script(llm, topic="W", corpus=None, minutes=180, target_words=27000, num_movements=16,
                        max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)
    assert "Plan exactly 16 movements" in llm.calls[0] and "about 1500 words" in llm.calls[1]
    assert s.movements >= 16


def _corpus():
    from sof.research import Corpus, Chunk
    return Corpus(topic="Whales", title="Whale", url="https://en.wikipedia.org/wiki/Whale", lead="Whales are marine mammals.",
                  sections=["Anatomy", "Song", "Migration"], sources=["Whale", "Whale vocalization"],
                  chunks=[Chunk("Whale — Song", "Humpback whale song is produced by males and can last twenty minutes."),
                          Chunk("Whale — Migration", "Gray whales migrate ten thousand miles between Mexico and the Arctic."),
                          Chunk("Whale — Anatomy", "Baleen plates filter krill from seawater.")])


def test_notes_and_sources_flow_into_prompts_and_description():
    llm = FakeLLM(words_per_segment=800, movements=4)
    s = generate_script(llm, topic="Whales", corpus=_corpus(), writer_model="writer", fast_model="fast", **KW)
    seg = next(c for c in llm.calls if "CURRENT MOVEMENT" in c)
    assert "RESEARCH NOTES" in seg and "[Whale — " in seg            # retrieved chunks are in the segment prompt
    assert "Section headings" in llm.calls[0] or "section headings" in llm.calls[0]
    assert "Whale vocalization" in s.description and "en.wikipedia.org/wiki/Whale" in s.description
    assert s.sources == ["Whale", "Whale vocalization"]
    # outline/metadata use the fast model, movements use the writer
    assert llm.models[0] == "fast" and "writer" in llm.models and llm.models[-1] == "fast"


def test_repeated_movement_is_rewritten_once():
    class RepeatLLM(FakeLLM):
        def generate_json(self, prompt, **kw):
            self.calls.append(prompt)
            if "Plan exactly" in prompt:
                return {"movements": [{"heading": f"P{i}", "beats": ["a"]} for i in range(4)], "image_search_queries": []}
            if "CURRENT MOVEMENT" in prompt:
                if "previous draft repeated" in prompt:
                    return {"body": " ".join(f"fresh{i}" for i in range(700))}
                return {"body": " ".join(["the same calm sentence again and again"] * 100)}   # identical every time
            return {"title": "T", "description": "D", "tags": []}
    llm = RepeatLLM()
    s = generate_script(llm, topic="W", corpus=None, **{**KW, "target_words": 2000})
    assert sum("previous draft repeated" in c for c in llm.calls) >= 1
    assert "fresh1" in s.body


def test_sections_blurb_and_footer_are_kept():
    llm = FakeLLM(words_per_segment=600, movements=4)
    s = generate_script(llm, topic="Whales", corpus=None, **KW)
    assert [x["heading"] for x in s.sections][:2] == ["Part 100", "Part 101"] and len(s.sections) == s.movements
    assert s.blurb == "d" and s.footer.startswith("Narration is AI-generated")
    again = Script.from_dict(s.to_dict())
    assert again.sections == s.sections and again.blurb == "d"
    old = Script.from_dict({"title": "T", "description": "Blurb here.\n\nNarration is AI-generated; facts...", "tags": [],
                            "body": "b", "word_count": 1, "movements": 1})
    assert old.blurb == "Blurb here." and old.footer.startswith("Narration is AI-generated") and old.sections == []



def test_story_shape_opening_line_and_closing_signature():
    llm = FakeLLM(words_per_segment=800, movements=4)
    s = generate_script(llm, topic="Saturn", corpus=None, story_format="day", **KW)
    outline = next(c for c in llm.calls if "Plan exactly" in c)
    assert 'STORY SHAPE FOR THIS VIDEO — "A day and a night"' in outline and "one orbit or rotation" in outline
    assert s.body.startswith("This is Sleep On Facts. Tonight: a day and a night with Saturn.")
    assert s.sections[-1]["heading"] == "Three Quiet Things to Keep" and s.sections[-1]["body"].startswith("Three quiet")
    closing_prompt = next(c for c in llm.calls if "Three quiet things to keep" in c)
    assert "Part 100" in closing_prompt and "This is Sleep On Facts" not in closing_prompt   # built from the movements
    assert s.story_format == "day" and Script.from_dict(s.to_dict()).story_format == "day"
    assert generate_script(FakeLLM(), topic="X", corpus=None, story_format="nope", **KW).story_format == "journey"
