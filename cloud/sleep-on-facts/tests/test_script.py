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
    # 4 planned + 1 from the refill (stops at target), + the closing signature; the host intro adds its words
    from sof import host
    intro = host.intro("Whales", fallback="Tonight, we're taking a slow journey through Whales.")
    assert s.movements == 6 and s.word_count == 3000 + 12 + len(intro.split())   # 12 = the closing
    assert s.body.startswith(intro)
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
    assert s.blurb == "d" and s.footer.startswith("Facts in this video are drawn from") and "AI-generated" not in s.footer
    again = Script.from_dict(s.to_dict())
    assert again.sections == s.sections and again.blurb == "d" and again.title_hook == s.title_hook
    old = Script.from_dict({"title": "T", "description": "Blurb here.\n\nNarration is AI-generated; facts...", "tags": [],
                            "body": "b", "word_count": 1, "movements": 1})
    assert old.blurb == "Blurb here." and old.footer.startswith("Narration is AI-generated") and old.sections == []
    new = Script.from_dict({"title": "T", "description": "Blurb.\n\nFacts in this video are drawn from the public sources below.",
                            "tags": [], "body": "b", "word_count": 1, "movements": 1})
    assert new.blurb == "Blurb." and new.footer.startswith("Facts in this video")


def test_metadata_title_hook_is_kept_and_sees_the_chapters():
    class HookLLM(FakeLLM):
        def generate_json(self, prompt, **kw):
            if "title_hook" in prompt:
                self.meta_prompt = prompt
                return {"title": "T", "description": "d", "tags": [], "title_hook": "The Silent Rings of Saturn",
                        "thumb_text": "RINGS OF ICE"}
            return super().generate_json(prompt, **kw)
    llm = HookLLM(words_per_segment=600, movements=4)
    s = generate_script(llm, topic="Saturn", corpus=None, **KW)
    assert s.title_hook == "The Silent Rings of Saturn" and s.thumb_text == "RINGS OF ICE"
    assert Script.from_dict(s.to_dict()).thumb_text == "RINGS OF ICE"
    assert "- Part 100" in llm.meta_prompt                     # chapter headings reach the metadata model



def test_story_shape_opening_line_and_closing_signature():
    llm = FakeLLM(words_per_segment=800, movements=4)
    s = generate_script(llm, topic="Saturn", corpus=None, story_format="day", **KW)
    outline = next(c for c in llm.calls if "Plan exactly" in c)
    assert 'STORY SHAPE FOR THIS VIDEO — "A day and a night"' in outline and "one orbit or rotation" in outline
    from sof import host
    assert s.body.startswith(host.GREETINGS) and "Tonight, we're taking a day and a night with Saturn." in s.body[:1200]
    assert "in the comments" in s.body[:1200] or "comment" in s.body[:1200]
    assert s.sections[-1]["heading"] == "Three Quiet Things to Keep" and s.sections[-1]["body"].startswith("Three quiet")
    closing_prompt = next(c for c in llm.calls if "Three quiet things to keep" in c)
    assert "Part 100" in closing_prompt and "This is Sleep On Facts" not in closing_prompt   # built from the movements
    assert s.story_format == "day" and Script.from_dict(s.to_dict()).story_format == "day"
    assert generate_script(FakeLLM(), topic="X", corpus=None, story_format="nope", **KW).story_format == "journey"


def test_signature_segments_do_not_trip_the_length_guard():
    class LongClosing(FakeLLM):
        def generate_json(self, prompt, **kw):
            if "Three quiet things to keep" in prompt:
                self.calls.append(prompt)
                return {"body": " ".join(["gentle"] * 200)}
            return super().generate_json(prompt, **kw)
    llm = LongClosing(words_per_segment=300, movements=2)
    s = generate_script(llm, topic="Mount Fuji", corpus=None, minutes=4, target_words=600, num_movements=16,
                        max_segments=40, fill_ratio=0.9, max_words_ratio=1.3)
    assert s.sections[-1]["heading"] == "Three Quiet Things to Keep" and s.word_count > 780   # was ScriptTooLong
    assert "about 60 words" in next(c for c in llm.calls if "Three quiet things to keep" in c)  # scaled for a smoke


# ---------------------------------------------------------------- noslop

SLOPPY_MOVEMENT = ("The comb is six-sided. Each cell is 5.2 millimetres across. The hive is not a building. It is a body.\n\n"
                   "Workers secrete wax from glands on the abdomen. A scale weighs about a milligram. Eight scales make one "
                   "cell wall. The queen lays one egg per cell.\n\n"
                   "So the hive carries itself onward, season by season, holding what it has gathered.")
CLEAN_FIRST = "The comb is six-sided. Each cell is 5.2 millimetres across. A summer hive holds about 50,000 bees."


class EditingLLM(FakeLLM):
    """Writes one sloppy movement; the editor call fixes paragraph 0 and deletes the recap paragraph."""

    def generate_json(self, prompt, *, system=None, max_tokens=3000, temperature=0.7, model=None, reasoning="low"):
        if "PARAGRAPH id=" in prompt:
            self.calls.append(prompt); self.edit_kw = dict(temperature=temperature, model=model)
            return {"paragraphs": [{"id": 0, "text": CLEAN_FIRST}, {"id": 2, "text": ""}]}
        if "CURRENT MOVEMENT" in prompt:
            self.calls.append(prompt)
            return {"body": SLOPPY_MOVEMENT}
        return super().generate_json(prompt, system=system, max_tokens=max_tokens, temperature=temperature, model=model, reasoning=reasoning)


def test_segment_prompt_carries_the_voice_and_style_rules():
    llm = FakeLLM(words_per_segment=600, movements=2)
    generate_script(llm, topic="Whales", corpus=None, **{**KW, "target_words": 1000}, slop_rounds=0)
    seg = next(c for c in llm.calls if "CURRENT MOVEMENT" in c)
    assert "THE NARRATOR" in seg and "STYLE RULES" in seg and "{{style}}" not in seg
    closing = next(c for c in llm.calls if "Three quiet things to keep" in c)
    assert "STYLE RULES" in closing and "{{style}}" not in closing


def test_flagged_paragraphs_are_rewritten_and_the_score_travels_with_the_script():
    llm = EditingLLM(words_per_segment=0, movements=2)
    s = generate_script(llm, topic="Honey Bees", corpus=None, minutes=1, target_words=100, num_movements=2,
                        max_segments=2, fill_ratio=0.9, max_words_ratio=20, fast_model="cheap", writer_model="big",
                        slop_rounds=2)
    assert CLEAN_FIRST in s.body and "season by season" not in s.body and "Eight scales make one cell wall" in s.body
    assert llm.edit_kw == {"temperature": 0.2, "model": "cheap"}
    assert s.slop["before"] >= 4 and s.slop["after"] == 0 and s.slop["rewritten"] >= 2 and s.slop["rounds"] >= 1
    assert Script.from_dict(s.to_dict()).slop == s.slop
    edits = [c for c in llm.calls if "PARAGRAPH id=" in c]
    assert edits and "PARAGRAPH id=1" not in edits[0] and "recap_paragraph" in edits[0] and "Honey Bees" in edits[0]


def test_slop_rounds_zero_only_lints():
    llm = EditingLLM(words_per_segment=0, movements=2)
    s = generate_script(llm, topic="Honey Bees", corpus=None, minutes=1, target_words=100, num_movements=2,
                        max_segments=2, fill_ratio=0.9, max_words_ratio=20, slop_rounds=0)
    assert "season by season" in s.body and not [c for c in llm.calls if "PARAGRAPH id=" in c]
    assert s.slop["before"] == s.slop["after"] > 0 and s.slop["rewritten"] == 0
