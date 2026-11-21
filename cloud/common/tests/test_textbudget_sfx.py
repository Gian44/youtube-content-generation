import shutil

import pytest

from cc import sfx, textbudget as tb

CUES = [(0.0, 4.0, "The Sun holds 99.86 percent of the mass of the Solar System."),
        (4.0, 9.0, "Its core reaches 15 million degrees, and its light takes 8 minutes to reach Earth."),
        (9.0, 13.0, "In 1610, Galileo turned a telescope toward it."),
        (13.0, 60.0, "Sunspots, he wrote, moved across its face, and the Cassini team saw the same on Saturn.")]


def test_words_from_cues_spreads_each_cue_over_its_words():
    w = tb.words_from_cues([(2.0, 4.0, "one two three four")])
    assert [x[2] for x in w] == ["one", "two", "three", "four"]
    assert w[0][0] == 2.0 and abs(w[-1][1] - 4.0) < 1e-6 and all(a[1] <= b[0] + 1e-6 for a, b in zip(w, w[1:]))


def test_pick_shows_numbers_with_units_never_the_sentence():
    out = tb.pick(tb.words_from_cues(CUES), duration=60.0, per_minute=6)
    texts = [t for _, _, t in out]
    assert texts[0] == "99.86%" and "15 million" in texts and "1610" in texts
    assert all(len(t.split()) <= 3 for t in texts)
    assert tb.shown_words(out) <= 12                      # a minute of narration: ~45 words spoken, a handful shown
    assert all(b[0] - a[0] >= 3.0 for a, b in zip(out, out[1:]))       # never crowding each other
    assert all(e > s and e - s <= 1.6 + 1e-6 for s, e, _ in out)


def test_pick_budget_names_and_skip():
    words = tb.words_from_cues([(0.0, 30.0, "We met Kai and Adin Ross in Brookhaven, then Kai left. Roblox is fun.")])
    out = [t for _, _, t in tb.pick(words, duration=30.0, per_minute=6, skip={"brookhaven"})]
    assert out[:1] == ["Kai"] and "Brookhaven" not in out and out.count("Kai") <= 1 and "Roblox" not in out  # sentence start
    assert len(out) <= 3
    assert tb.pick(words, duration=30.0, names=False) == []


def test_pick_currency_and_year_suffix():
    words = tb.words_from_cues([(0.0, 10.0, "A classic Cuban sandwich costs $14 here, as it did in the 1990s.")])
    assert [t for _, _, t in tb.pick(words, duration=10.0, per_minute=12)] == ["$14", "1990s"]


def test_on_cuts_is_sparse_and_keeps_the_payoff():
    for n in (3, 5, 12, 40):
        cuts = sfx.on_cuts(n, seed="v1")
        assert n - 1 in cuts and len(cuts) <= max(1, round(n * 0.34)) + 1
        assert not any(i + 1 in cuts for i in cuts if i != n - 2)      # never two in a row (the payoff may follow)
    assert sfx.on_cuts(0) == set() and 0 in sfx.on_cuts(5, first=True)


def test_variants_differ_by_seed_and_never_shrill():
    a, b = sfx.variant("tick", "2026-11-21"), sfx.variant("tick", "2026-11-22")
    assert a != b and sfx.variant("tick", "2026-11-21") == a
    for kind in sfx.SHAPES:
        v = sfx.variant(kind, 7)
        assert v["freq"] <= 2100 and v["seconds"] <= 0.3
    cmd = " ".join(sfx.synth_cmd("pluck", "o.wav", seed=1))
    assert "sine" not in cmd.split("aevalsrc")[0] and "lowpass" in cmd and "volume=0.0631" in cmd   # -24 dBFS
    with pytest.raises(ValueError):
        sfx.variant("whoosh")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_synth_level_is_far_under_speech(tmp_path):
    out = str(tmp_path / "t.wav")
    sfx.synth("tick", out, seed=3, pad_to=1.0)
    assert -32.0 <= sfx.peak_db(out) <= -23.0
    assert sfx.loudness(out)["I"] < -35.0


def test_pick_never_singles_out_groups_or_quoted_capitals():
    words = tb.words_from_cues([(0.0, 40.0, 'He told Mary that Marx was not a German, he was a Jew. "They\'re here," '
                                            'Johnny says, "Don\'t run." Then Johnny runs.')])
    out = [t for _, _, t in tb.pick(words, duration=40.0, per_minute=12, min_gap=0.5)]
    assert "German" not in out and "Jew" not in out and "They're" not in out and "Don't" not in out
    assert out[:2] == ["Mary", "Marx"] and "Johnny" in out


def test_pick_dollars_and_digit_skip():
    words = tb.words_from_cues([(0.0, 20.0, "He spent 14 dollars on rank 3 and then 500 more.")])
    out = [t for _, _, t in tb.pick(words, duration=20.0, per_minute=30, min_gap=0.5, skip={"#3"})]
    assert out == ["$14", "500"]


def test_bare_count_takes_its_noun_but_not_filler():
    def shown(text):
        return [t for _, _, t in tb.pick(tb.words_from_cues([(0.0, 30.0, text)]), duration=30.0, per_minute=30,
                                         min_gap=0.1, names=False)]
    assert shown("They counted 7 survivors in the house.") == ["7 survivors"]
    assert shown("Then 7 more came, in 1610, and 3 of them stayed.") == ["7", "1610", "3"]
    assert shown("It cost 25 dollar flat.") == ["$25"]
