"""cast_bible — character bible build/validate/store and Flow prompt pack (pure, tmp_path)."""

from __future__ import annotations

import re

import pytest

from storyfactory.content_defaults import CAST_EMOTION_TAGS
from storyfactory.services import cast_bible as cb


# ---- fixtures / helpers ----

BASE_MOTION = (
    "Continuous shot: phone propped still at chest height, vertical 9:16. Head and "
    "shoulders centered, camera does not move. Same kitchen nook by the window. Natural "
    "glances, blinks, small nods and a slow breath. Warm window light, quiet room tone. "
    + cb.NO_SPEECH_RULE
)


def _bible(variants: int = 3) -> dict:
    """A valid bible for all 10 tags with distinct action sentences per (tag, n)."""
    actions = {}
    for tag in CAST_EMOTION_TAGS:
        actions[tag] = [
            f"{tag.replace('_', ' ')} take {n}: the eyes shift and the head settles into it"
            for n in range(1, variants + 1)
        ]
    return {
        "name": "Marisol",
        "age_range": "early 30s",
        "identity_lock": (
            "A woman in her early 30s with an oval face, dark brown eyes, thick straight brows, "
            "shoulder-length black hair tucked behind her ears, warm olive skin with a small mole "
            "under the left eye, slim build, wearing a faded forest-green crewneck sweatshirt."
        ),
        "setting": (
            "A small kitchen nook with pale yellow walls, a chipped blue mug and a stack of mail "
            "on the counter, a window with thin white curtains letting in late-morning light."
        ),
        "base_motion": BASE_MOTION,
        "master_frame_prompt": (
            "A woman in her early 30s ... neutral listening pose in the kitchen nook, propped-iPhone "
            "selfie framing about 1.2 m away, natural window light, candid, faint grain, "
            "no retouching, no text, no watermark."
        ),
        "emotion_actions": actions,
    }


class FakeClient:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[dict] = []

    def generate_json(self, model, prompt, system=None, temperature=0.6):
        self.calls.append(
            {"prompt": prompt, "model": model, "system": system, "temperature": temperature}
        )
        return self.result


def _placeholders(template: str) -> set[str]:
    return set(re.findall(r"\{\{(\w+)\}\}", template))


def _norm40(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()[:40]


CFG = {"clip_seconds": 8, "variants_per_tag": 3}


# ---- 1. render_prompt ----

def test_render_prompt_fills_every_placeholder():
    out = cb.render_prompt(
        cb.BIBLE_PROMPT_TEMPLATE,
        persona="sarcastic",
        niche="workplace drama",
        content_style="candid",
        emotion_tags=CAST_EMOTION_TAGS,
        variants_per_tag=3,
    )
    assert "{{" not in out and "}}" not in out
    assert "sarcastic" in out and "workplace drama" in out and "candid" in out
    assert ", ".join(CAST_EMOTION_TAGS) in out
    assert cb.NO_SPEECH_RULE in out  # baked into the template at import


# ---- 2. build_bible ----

def test_build_bible_calls_client_and_stamps_meta():
    client = FakeClient(_bible(3))
    bible = cb.build_bible(
        client,
        model="gemini-x",
        persona="calm",
        niche=None,
        content_style=None,
        variants_per_tag=3,
        temperature=0.5,
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["system"] == cb.BIBLE_SYSTEM
    assert call["model"] == "gemini-x"
    assert call["temperature"] == 0.5
    expected_prompt = cb.render_prompt(
        cb.BIBLE_PROMPT_TEMPLATE,
        persona="calm",
        niche="Reddit-style fiction stories",
        content_style="candid, phone-shot, realistic",
        emotion_tags=CAST_EMOTION_TAGS,
        variants_per_tag=3,
    )
    assert call["prompt"] == expected_prompt
    for tag in CAST_EMOTION_TAGS:
        assert tag in call["prompt"]
    assert "exactly 3 action sentences" in call["prompt"]

    assert bible["persona"] == "calm"
    assert bible["created_at"].endswith("+00:00")
    assert bible["name"] == "Marisol"
    assert set(bible["emotion_actions"]) == set(CAST_EMOTION_TAGS)


# ---- 3. validate_bible ----

def test_validate_raises_naming_missing_identity_lock():
    data = _bible(3)
    del data["identity_lock"]
    with pytest.raises(ValueError) as ei:
        cb.validate_bible(data, 3)
    assert "identity_lock" in str(ei.value)


def test_validate_raises_when_tag_has_too_few_sentences():
    data = _bible(3)
    data["emotion_actions"]["smug"] = data["emotion_actions"]["smug"][:2]
    with pytest.raises(ValueError) as ei:
        cb.validate_bible(data, 3)
    assert "smug" in str(ei.value)


def test_validate_trims_extras_and_drops_unknown_tags():
    data = _bible(3)
    data["emotion_actions"]["angry"].append("a fourth angry take")
    data["emotion_actions"]["bored"] = ["yawns", "yawns again", "yawns a third time"]
    out = cb.validate_bible(data, 3)
    assert len(out["emotion_actions"]["angry"]) == 3
    assert "a fourth angry take" not in out["emotion_actions"]["angry"]
    assert "bored" not in out["emotion_actions"]
    assert set(out["emotion_actions"]) == set(CAST_EMOTION_TAGS)
    # cleaned copy: only known keys, input untouched
    assert set(out) == set(cb.KNOWN_KEYS)
    assert len(data["emotion_actions"]["angry"]) == 4


def test_validate_appends_no_speech_rule_when_missing():
    data = _bible(3)
    data["base_motion"] = "Continuous shot: phone propped still at chest height, vertical 9:16."
    out = cb.validate_bible(data, 3)
    assert out["base_motion"].endswith(" " + cb.NO_SPEECH_RULE)
    assert out["base_motion"].startswith("Continuous shot:")


def test_validate_keeps_base_motion_when_rule_present():
    out = cb.validate_bible(_bible(3), 3)
    assert out["base_motion"] == BASE_MOTION
    assert out["base_motion"].count("lips stay closed") == 1


# ---- 4. write_bible / load_bible ----

def test_write_and_load_bible_round_trip(tmp_path):
    bible = _bible(3)
    bible["persona"] = "warm"
    bible["created_at"] = "2026-09-19T00:00:00+00:00"
    path = cb.write_bible(tmp_path, "warm", bible)
    assert path == tmp_path / "warm" / "character.json"
    assert path == cb.bible_path(tmp_path, "warm")
    assert cb.load_bible(tmp_path, "warm") == bible


def test_load_bible_returns_none_when_absent(tmp_path):
    assert cb.load_bible(tmp_path, "nobody") is None


# ---- 5. clip_prompt ----

def test_clip_prompt_action_first_then_base_motion():
    bible = _bible(3)
    p = cb.clip_prompt(bible, "shocked", 2)
    assert p.startswith(bible["emotion_actions"]["shocked"][1])
    assert p.endswith(bible["base_motion"])
    assert p == bible["emotion_actions"]["shocked"][1] + " " + bible["base_motion"]


def test_clip_prompt_first_40_normalized_chars_unique():
    bible = _bible(3)
    keys = cb.all_clip_keys(3)
    assert len(keys) == 30
    assert keys[:4] == [
        ("neutral_listening", 1), ("neutral_listening", 2), ("neutral_listening", 3), ("shocked", 1),
    ]
    heads = {_norm40(cb.clip_prompt(bible, t, n)) for t, n in keys}
    assert len(heads) == 30
    assert cb.clip_filename("smug", 2) == "smug_2.mp4"


# ---- 6. write_pack ----

def test_write_pack_lists_only_pending_clips(tmp_path):
    bible = _bible(3)
    pending = [("shocked", 1), ("angry", 3), ("relieved", 2)]
    path = cb.write_pack(tmp_path, "dramatic", bible, pending, CFG)
    assert path == tmp_path / "dramatic" / "outbox" / "prompt_pack.md"
    text = path.read_text(encoding="utf-8")

    assert text.startswith("# Flow prompt pack — dramatic")
    assert "Marisol" in text and "early 30s" in text
    assert bible["master_frame_prompt"] in text
    assert text.count("Save as: reference.png") == 1
    assert "8 seconds" in text
    assert "cast scan --channel" in text
    assert "_alt1.mp4" in text
    assert "## Clips to generate (3 pending of 30)" in text

    for tag, n in pending:
        assert text.count(f"Save as: inbox/{tag}_{n}.mp4") == 1
        assert f"### {tag} {n}" in text
        assert cb.clip_prompt(bible, tag, n) in text
    for tag, n in cb.all_clip_keys(3):
        if (tag, n) not in pending:
            assert f"Save as: inbox/{tag}_{n}.mp4" not in text
    assert text.count("Save as: inbox/") == 3
    # sections follow all_clip_keys order (shocked < angry < relieved)
    assert text.index("### shocked 1") < text.index("### angry 3") < text.index("### relieved 2")


def test_write_pack_empty_pending(tmp_path):
    path = cb.write_pack(tmp_path, "calm", _bible(3), [], CFG)
    text = path.read_text(encoding="utf-8")
    assert "All 30 clips are in the bank." in text
    assert "Save as: inbox/" not in text
    assert "Save as: reference.png" in text


# ---- 7. write_checklist ----

def test_write_checklist_full_bank(tmp_path):
    pending = cb.all_clip_keys(3)
    path = cb.write_checklist(tmp_path, "horror", pending, CFG)
    assert path == tmp_path / "horror" / "outbox" / "flow_checklist.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Flow checklist — horror")
    assert "- [ ] reference.png (master frame, Flow image tool)" in text
    assert text.count("- [ ] inbox/") == 30
    for tag, n in pending:
        assert f"- [ ] inbox/{tag}_{n}.mp4" in text
    assert "30 clips × 20 credits = 600 on Veo 3.1 Fast" in text
    assert "30 clips × 10 credits = 300 on Veo 3.1 Lite" in text
    assert "Google AI Pro: 50 credits/day + 1,000/month, no rollover." in text


def test_write_checklist_partial(tmp_path):
    pending = cb.all_clip_keys(3)[:7]
    text = cb.write_checklist(tmp_path, "warm", pending, CFG).read_text(encoding="utf-8")
    assert text.count("- [ ] inbox/") == 7
    assert "7 clips × 20 credits = 140 on Veo 3.1 Fast" in text
    assert "7 clips × 10 credits = 70 on Veo 3.1 Lite" in text
    assert cb.CREDITS_FAST == 20 and cb.CREDITS_LITE == 10


# ---- 8. seed registration (also proves no import cycle) ----

def test_seed_registers_cast_character_bible_prompt():
    from storyfactory.db import seed

    entries = [p for p in seed.DEFAULT_PROMPTS if p["name"] == "cast_character_bible"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["template"] is cb.BIBLE_PROMPT_TEMPLATE
    assert entry["category"] == "cast"
    assert set(entry["variables"]) == _placeholders(cb.BIBLE_PROMPT_TEMPLATE)
    assert set(entry["variables"]) == {
        "persona", "niche", "content_style", "emotion_tags", "variants_per_tag",
    }


def test_validate_bible_rejects_action_sentences_that_open_alike():
    data = _bible()
    data["emotion_actions"]["shocked"][1] = data["emotion_actions"]["shocked"][0]
    with pytest.raises(ValueError, match="open with the same words"):
        cb.validate_bible(data, 3)
