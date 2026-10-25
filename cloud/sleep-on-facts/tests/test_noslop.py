"""The no-slop lint: every pattern from the skill is caught on the skill's own examples (and on lines from
real Sleep On Facts scripts), plain narration passes, and the targeted rewrite accepts only improvements."""

import pytest

from sof import noslop

PLAIN = ("The English word paper comes from the Latin papyrus, and the Latin word comes from the Greek papyros. "
         "In Greek this was the name of the plant now called Cyperus papyrus, a wetland sedge that grew across the "
         "Nile Delta. Sheets were made by laying strips of its pith in two layers, one across the other, and pressing "
         "them while wet.\n\n"
         "Papermaking in China is usually dated to 105 CE, when Cai Lun presented a process to the Han court. "
         "Fragments of paper found at Fangmatan are older than that, from the second century BCE. The early sheets were "
         "made from hemp, mulberry bark and old fishing nets, beaten in water and lifted on a screen.")


def rules(text, mode="sleep", **kw):
    return {f.rule for f in noslop.lint(text, mode=mode, **kw).hard}


def test_plain_narration_passes():
    r = noslop.lint(PLAIN, mode="sleep")
    assert r.hard == [] and r.per_1k() == 0.0 and r.flagged() == [] and r.paragraphs == 2 and r.words > 80


@pytest.mark.parametrize("text, rule", [
    # --- examples from the skill (SKILL.md) ---
    ("The question isn't the model. It's the eval.", "binary_contrast"),
    ("It's not just a tool but a habit.", "binary_contrast"),
    ("Here's the thing: the model was never the problem.", "throat_clearing"),
    ("The part everyone misses: distribution is the real moat.", "faux_insight"),
    ("The best part: it learns.", "colon_reveal"),
    ("The launch adds file search, highlighting the team's commitment to better workflows.", "ing_analysis"),
    ("The launch marks a pivotal moment for the company.", "puffery"),
    ("That last part matters more than it sounds. In other words, the eval is the product.", "metadiscourse"),
    ("Experts agree the comb is the oldest structure in the hive.", "weasel"),
    ("Not a tool. Not a framework. A habit.", "negative_listing"),
    ("It reads the draft. And scores it. And fixes it.", "fragmentation"),
    ("What if I told you the hive never sleeps.", "rhetorical_setup"),
    ("The bees went on working. In conclusion, the hive is a city.", "summary_ending"),
    ("We must delve into the rich tapestry of beekeeping.", "banned_word"),
    ("It's worth noting that the wax is white when new.", "empty_phrase"),
    # --- lines from real Sleep On Facts scripts (Paper, Honey Bees; 2026-10) ---
    ("The honey bees of the present emerge not as isolated creatures, but as travelers from a deep and branching past.",
     "binary_contrast"),
    ("The nest is not only a place where a new generation begins. It is a durable center of activity.", "binary_contrast"),
    ("The name does not erase the difference between papyrus and paper. Instead, it preserves a gentle trace of it.",
     "binary_contrast"),
    ("Each species represents not merely a label but a surviving branch of the genus.", "binary_contrast"),
    ("The broader lesson is a quiet one: a bee home is not built from wax alone.", "colon_reveal"),
    ("Such figures offer a quiet reminder that pollination has many participants.", "puffery"),
    ("It tells us that honey bees were present in Europe by then.", "metadiscourse"),
    ("Wax is made. Cells are maintained. Honey is stored.", "fragmentation"),
    ("It becomes a wall. It becomes a rim, a base, a cap. It becomes a floor.", "robotic_rhythm"),
    ("The bees crossed the field. Did the bees know?", "question"),
])
def test_pattern_is_caught(text, rule):
    assert rule in rules(text), noslop.lint(text).findings


def test_kicker_on_paragraph_end_from_real_script():
    para = ("Workers build the comb from wax scales secreted on their abdomens, each scale weighing about a milligram. "
            "They chew the scales and press them into six-sided cells about 5.2 millimetres across. "
            "Together, they make the colony more than a gathering of individuals: a living social home, renewed cell by "
            "cell, season by season.")
    assert {"kicker", "colon_reveal"} <= rules(para)


def test_kicker_needs_a_paragraph_and_spares_a_plain_last_fact():
    plain = "The queen lays one egg per cell. A worker cell is 5.2 millimetres across. A drone cell is 6.4 millimetres across."
    assert "kicker" not in rules(plain)
    assert "kicker" not in rules("Season by season, the comb grows.")       # a single sentence is not a kicker


def test_recap_paragraph_is_only_the_last_paragraph_of_a_sleep_movement():
    body = PLAIN + "\n\nSo the paper sheet grew from the patient meeting of fiber and water, and the craft moved onward."
    r = noslop.lint(body, mode="sleep")
    assert "recap_paragraph" in {f.rule for f in r.hard} and r.by_para()[2]
    assert "recap_paragraph" not in rules(body, mode="fiction")
    assert "recap_paragraph" not in rules("So it goes.\n\n" + PLAIN, mode="sleep")


def test_mood_words_sleep_only_and_density():
    para = "The quiet comb hangs in the gentle dark, and the patient bees move slowly across it."
    r = noslop.lint(para, mode="sleep")
    assert "mood_words" in {f.rule for f in r.hard}
    assert "mood_words" not in rules(para, mode="fiction")
    soft = noslop.lint("A quiet comb hangs in the dark.", mode="sleep")
    assert soft.hard == [] and any(f.rule == "mood_words" and not f.hard for f in soft.findings)


def test_questions_only_flagged_in_sleep_mode():
    assert "question" in rules("Why does it hang? Why so low? Gravity pulls it.", mode="sleep")     # a run of questions
    assert "question" in rules("The comb hangs low. Why does it hang downward?", mode="sleep")     # ends on a question
    assert "question" not in rules("Why does the comb hang downward? Gravity pulls it.", mode="sleep")  # one, answered
    assert "question" not in rules("Why does it hang? Why so low? Gravity.", mode="recap")


def test_em_dashes_hard_outside_sleep():
    s = "The comb — six-sided, waxen — holds the brood — and the honey."
    assert "em_dashes" in rules(s, mode="fiction")
    assert "em_dashes" not in rules(s, mode="sleep")
    assert any(f.rule == "em_dashes" for f in noslop.lint(s, mode="sleep").findings)


def test_colon_lists_and_times_are_not_reveals():
    assert "colon_reveal" not in rules("Three things were kept in the comb: honey, pollen, and brood.")
    assert "colon_reveal" not in rules("Such as: nectar, pollen, water, resin.")
    assert "colon_reveal" not in rules("The train left at 8:30 and reached Kyoto at 11:05.")


def test_exempt_lines_are_skipped():
    sig = "Three quiet things to keep from tonight."
    assert "mood_words" not in rules(sig + " The gentle, soft, quiet comb.", exempt=(sig,))


def test_report_summary_and_dict():
    r = noslop.lint("Here's the thing: bees delve into flowers. Experts agree.", mode="fiction")
    d = r.to_dict()
    assert d["findings"] >= 3 and d["flagged"] == 1 and d["per_1k"] > 100 and "banned_word" in d["counts"]
    assert r.summary().startswith("slop ") and "throat_clearing" in r.summary()


def test_style_block_and_inject():
    s = noslop.style_block("sleep")
    assert s.startswith("THE NARRATOR") and "STYLE RULES" in s and "Not X but Y" in s and "quiet, gentle" in s
    f = noslop.style_block("fiction")
    assert "THE NARRATOR" not in f and "Plot twist" in f
    out = noslop.inject("Write it.\n\nOutput as JSON:\n{}", "BLOCK")
    assert out.index("BLOCK") < out.index("Output as JSON") and out.startswith("Write it.")
    assert noslop.inject("Write it.", "BLOCK").endswith("BLOCK\n")
    with pytest.raises(ValueError):
        noslop.style_block("poetry")


def test_rewrite_request_lists_each_pattern_with_its_fix():
    r = noslop.lint("The best part: it learns. Experts agree.", mode="fiction")
    items = [{"id": 0, "text": "The best part: it learns. Experts agree.", "findings": r.by_para()[0]}]
    req = noslop.rewrite_request(items)
    assert "PARAGRAPH id=0" in req and "colon_reveal" in req and "weasel" in req and "name the source" in req
    assert '"paragraphs"' in req


def test_parse_rewrite_tolerates_shapes():
    assert noslop.parse_rewrite({"paragraphs": [{"id": "1", "text": " a  b "}, {"id": 9, "text": "x"}, "junk"]}, [1, 2]) == {1: "a b"}
    assert noslop.parse_rewrite([{"id": 2, "text": ""}], [2]) == {2: ""}
    assert noslop.parse_rewrite("nonsense", [1]) == {}


SLOPPY = ("The comb is six-sided. Each cell is 5.2 millimetres across. The hive is not a building. It is a body.\n\n"
          + PLAIN.split("\n\n")[0] + "\n\n"
          "So the hive carries itself onward, season by season, holding what it has gathered.")


def test_polish_rewrites_only_flagged_paragraphs_and_accepts_improvements():
    seen = []

    def fake(prompt):
        seen.append(prompt)
        return {"paragraphs": [{"id": 0, "text": "The comb is six-sided. Each cell is 5.2 millimetres across. A summer hive holds about 50,000 bees."},
                               {"id": 2, "text": ""}]}

    res = noslop.polish(SLOPPY, rewrite=fake, mode="sleep", rounds=2)
    assert res.before.flagged() == [0, 2] and len(seen) == 1
    assert "PARAGRAPH id=1" not in seen[0] and "PARAGRAPH id=0" in seen[0] and "PARAGRAPH id=2" in seen[0]
    assert res.rewritten == 2 and res.after.hard == [] and res.rounds == 1
    assert res.text.count("\n\n") == 1 and "season by season" not in res.text and "papyros" in res.text


def test_polish_rejects_worse_or_resized_replacements_and_survives_errors():
    def worse(prompt):
        return {"paragraphs": [{"id": 0, "text": "Here's the thing: the hive is not a building. It is a body. Experts agree."},
                               {"id": 2, "text": "x " * 400}]}
    res = noslop.polish(SLOPPY, rewrite=worse, mode="sleep", rounds=2)
    assert res.rewritten == 0 and res.text == SLOPPY and res.rounds == 1

    def boom(prompt):
        raise RuntimeError("model down")
    res = noslop.polish(SLOPPY, rewrite=boom, mode="sleep")
    assert res.text == SLOPPY and res.rounds == 0 and res.after.per_1k() == res.before.per_1k()


def test_polish_never_deletes_the_only_paragraph():
    one = "So the hive carries itself onward, season by season, holding what it has gathered. It is a quiet, gentle, patient thing."
    res = noslop.polish(one, rewrite=lambda p: {"paragraphs": [{"id": 0, "text": ""}]}, mode="sleep")
    assert res.text == one


def test_polish_stops_when_nothing_is_flagged():
    calls = []
    res = noslop.polish(PLAIN, rewrite=lambda p: calls.append(p), mode="sleep")
    assert calls == [] and res.text == PLAIN and res.rounds == 0


def test_merge_stats():
    a = noslop.polish(SLOPPY, rewrite=lambda p: {"paragraphs": []}, mode="sleep").to_dict()
    m = noslop.merge_stats([a, a])
    assert m["before"] == 2 * a["before"]["findings"] and m["after"] == 2 * a["after"]["findings"] and m["rewritten"] == 0
    assert "binary_contrast" in m["remaining"] and noslop.merge_stats([]) == {}
