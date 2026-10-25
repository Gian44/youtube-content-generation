from sof import host


def test_intro_combines_parts_and_varies_by_subject():
    a = host.intro("Saturn", teaser="Tonight, we travel out to Saturn and its rings.")
    welcome, tonight = a.split("\n\n")
    assert welcome.startswith(host.GREETINGS) and "comment" in welcome.lower() and "subscrib" in welcome.lower()
    assert tonight == "Tonight, we travel out to Saturn and its rings."
    intros = {host.intro(s).split("\n\n")[0] for s in ("Saturn", "The Moon", "Pompeii", "Venice", "Jupiter", "Rice")}
    assert len(intros) >= 5                                    # no fixed template across videos
    assert host.intro("Saturn") == host.intro("saturn")        # deterministic per subject (a resumed run says the same)
    assert 70 <= len(a.split()) <= 200                         # about a minute of speech


def test_teaser_is_cleaned_and_falls_back():
    assert host.clean_teaser("Tonight, we go deep!  Why? Because it is there. ") == "Tonight, we go deep. Because it is there."
    assert host.intro("X", teaser="", fallback="Tonight, we're taking a slow journey through X.").endswith(
        "Tonight, we're taking a slow journey through X.")
    long = " ".join(["Tonight we walk."] * 40)
    assert len(host.clean_teaser(long).split()) <= 75
