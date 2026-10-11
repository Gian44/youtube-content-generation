from datetime import datetime, timezone

from sof import seo


def test_titles_lead_with_length_and_search_phrase():
    assert seo.build_title("Saturn", "space & astronomy", 3) == "3 Hours of Calm Space Facts to Fall Asleep To | Saturn"
    assert seo.build_title("Indus Valley Civilization", "history & ancient civilisations", 3) == \
        "3 Hours of Calm History to Fall Asleep To | Indus Valley Civilization"
    assert seo.build_title("Steam Engine", "how things work (machines, materials, everyday engineering)", 3).startswith(
        "3 Hours of Calm Science Facts")
    assert seo.build_title("Whales", "", 1) == "1 Hour of Calm Facts to Fall Asleep To | Whales"
    long = seo.build_title("A" * 90, "space & astronomy", 3)
    assert len(long) <= 100 and "<" not in seo.build_title("x <b>", "", 3)


def test_profiles_cover_every_category():
    cats = ["nature & animals", "history & ancient civilisations", "space & astronomy", "oceans & rivers",
            "how things work (machines, materials, everyday engineering)", "cities, islands & landscapes",
            "food, farming & plants", "inventions, crafts & ideas"]
    assert all(seo.profile(c) is not seo.DEFAULT for c in cats)
    assert seo.profile("oceans & rivers").playlist == "Ocean Facts to Fall Asleep To"


def test_chapter_lines_follow_youtube_rules():
    ch = [{"start": 3.2, "title": "The Rings."}, {"start": 600, "title": "Titan"}, {"start": 605, "title": "Too close"},
          {"start": 3725, "title": "Cassini's last dive"}]
    assert seo.chapter_lines(ch) == ["0:00 The Rings", "10:00 Titan", "1:02:05 Cassini's last dive"]
    assert seo.chapter_lines(ch[:2]) == []                     # fewer than 3 → no chapters at all


def test_description_is_keyword_first_with_chapters_footer_photo_line_and_hashtags():
    footer = "Facts in this video are drawn from the public sources below.\n\nSources (Wikipedia, CC BY-SA 4.0):\n" + \
             "\n".join(f"- Article {i}: https://en.wikipedia.org/wiki/Article_{i}" for i in range(12))
    ch = [{"start": 0, "title": "One"}, {"start": 700, "title": "Two"}, {"start": 1500, "title": "Three"}]
    d = seo.build_description(subject="Saturn", category="space & astronomy", hours=3, blurb="Rings and moons.",
                              chapters=ch, footer=footer, photos="Photos: Wikimedia Commons contributors. Licences: x",
                              extra="Thumbnail photo: Someone, CC BY 2.0, via Wikimedia Commons")
    assert d.startswith("Fall asleep to 3 hours of calm space facts: Saturn. Rings and moons.")
    assert "Chapters\n0:00 One\n11:40 Two\n25:00 Three" in d
    assert "Facts in this video are drawn from" in d and "Photos: Wikimedia Commons contributors" in d
    assert d.index("Photos:") < d.index("Thumbnail photo:") < d.index("#sleep")
    assert d.rstrip().endswith("#sleep #spacefacts #Saturn")
    tiny = seo.build_description(subject="Saturn", category="space & astronomy", hours=3, footer=footer, limit_bytes=600)
    assert len(tiny.encode()) <= 600 and "Facts in this video are drawn from" in tiny      # sources trimmed first


def test_tags_are_deduped_and_capped():
    tags = seo.build_tags("Saturn", "space & astronomy", ["Saturn", "sleep", "x" * 300, "rings"])
    assert tags[0] == "sleep" and "space facts to fall asleep to" in tags
    assert sum(len(t) + 1 for t in tags) <= 451 and [t.lower() for t in tags].count("saturn") == 1


def test_publish_slot_is_830pm_eastern_or_now():
    at = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    assert seo.rfc3339(seo.publish_slot(at("2026-10-11T21:00:00"), 20, 30, tz="America/New_York")) == "2026-10-12T00:30:00Z"
    assert seo.rfc3339(seo.publish_slot(at("2026-11-05T20:00:00"), 20, 30, tz="America/New_York")) == "2026-11-06T01:30:00Z"  # EST
    assert seo.publish_slot(at("2026-10-12T01:00:00"), 20, 30, tz="America/New_York") is None    # late run → publish now


def test_title_shapes_rotate_and_keep_subject_length_and_sleep_intent():
    titles = [seo.build_title("Saturn", "space & astronomy", 3, hook="The Silent Rings of Saturn", variant=v)
              for v in range(seo.TITLE_VARIANTS)]
    assert len(set(titles)) == seo.TITLE_VARIANTS                       # every shape is different
    for t in titles:
        assert "Saturn" in t and ("3 Hour" in t or "3-Hour" in t) and ("Sleep" in t or "Asleep" in t) and len(t) <= 100
    assert titles[1] == "The Silent Rings of Saturn | 3 Hours of Calm Space Facts to Fall Asleep To"
    assert titles[4] == "The Silent Rings of Saturn | 3-Hour Space Documentary for Sleep"
    assert seo.build_title("Saturn", "space & astronomy", 3, variant=7) == titles[0]      # wraps around


def test_hooks_are_cleaned_and_combined_with_the_subject_when_needed():
    assert seo.clean_hook("Shocking Secrets of Saturn!") == "" and seo.clean_hook("Why Is Saturn So Big?") == ""
    assert seo.clean_hook("  'Life Along the Ancient Nile.' ") == "Life Along the Ancient Nile"
    assert seo.clean_hook("Rings") == "" and seo.clean_hook("Top 10 Moons of Saturn") == ""
    t = seo.build_title("Ancient Egypt", "history & ancient civilisations", 3, hook="Life Along the Ancient Nile", variant=1)
    assert t == "Ancient Egypt: Life Along the Ancient Nile | 3 Hours of Calm History to Fall Asleep To"
    no_hook = {seo.build_title("Mount Fuji", "cities, islands & landscapes", 3, variant=v) for v in range(seo.TITLE_VARIANTS)}
    assert len(no_hook) == seo.TITLE_VARIANTS                          # still varied without a hook


def test_subject_phrase_adds_the_article_only_where_english_needs_it():
    assert seo.subject_phrase("Great Barrier Reef") == "the Great Barrier Reef"
    assert seo.subject_phrase("Steam Engine") == "the Steam Engine"
    assert seo.subject_phrase("Saturn") == "Saturn" and seo.subject_phrase("Ancient Egypt") == "Ancient Egypt"
    assert seo.subject_phrase("Mount Fuji") == "Mount Fuji"


def test_playlist_description_has_no_ai_line():
    assert "AI-generated" not in seo.playlist_description("space & astronomy")
