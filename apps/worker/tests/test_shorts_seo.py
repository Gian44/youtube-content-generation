"""shorts_seo — optimizer-shaped Shorts metadata (titles, tags, hashtags)."""

from __future__ import annotations

from storyfactory.services import shorts_seo


def test_sanitize_title_strips_hashtags_and_collapses_space():
    assert shorts_seo.sanitize_title("Crazy #shorts twist  #viral ending") == "Crazy twist ending"


def test_sanitize_title_truncates_to_limit():
    long = "word " * 50
    out = shorts_seo.sanitize_title(long)
    assert len(out) <= shorts_seo.YOUTUBE_TITLE_LIMIT


def test_build_tags_three_tiers_and_dedupe():
    tags = shorts_seo.build_tags(
        ["Spider-Noir", "spider-noir recap", "spider-noir"],  # dup dropped
        ["recap", "tv recap"],
    )
    # broad tier auto-appended
    assert "viral" in tags and "shorts" in tags
    assert "recap" in tags
    # case-insensitive dedupe — "spider-noir" appears once
    assert tags.count("spider-noir") == 1
    assert len(tags) <= shorts_seo.MAX_TAGS


def test_build_tags_caps_each_tier():
    post = [f"p{i}" for i in range(10)]
    niche = [f"n{i}" for i in range(10)]
    tags = shorts_seo.build_tags(post, niche, per_tier_cap=3)
    assert sum(t.startswith("p") for t in tags) == 3
    assert sum(t.startswith("n") for t in tags) == 3


def test_normalize_hashtags_limits_and_cleans():
    out = shorts_seo.normalize_hashtags(["shorts", "#recap", "re cap!", "#recap"])
    assert out == ["#shorts", "#recap"]  # 're cap!' -> '#recap' dup dropped


def test_build_description_has_exactly_three_hashtags():
    desc = shorts_seo.build_description(
        "A shocking betrayal",
        ["#shorts", "#recap", "#spidernoir", "#extra"],
        extra="Music: x",
    )
    assert desc.count("#") == 3
    assert "A shocking betrayal" in desc
    assert "Music: x" in desc


def test_build_short_metadata_shape():
    meta = shorts_seo.build_short_metadata(
        title="The #viral twist nobody saw",
        hook="You won't believe it",
        post_specific_tags=["spider-noir"],
        niche_tags=["recap"],
        hashtags=["#shorts", "#recap", "#spidernoir"],
    )
    assert "#" not in meta["title"]  # title never carries hashtags
    assert meta["description"].count("#") == 3
    assert "viral" in meta["tags"]  # broad tier present


def test_keyword_tags_from_title_drops_stopwords():
    out = shorts_seo.keyword_tags_from_title("The Shadow Weaver Returns")
    assert "the" not in out
    assert "shadow" in out and "weaver" in out
    assert len(out) <= 3
