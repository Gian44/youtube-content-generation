"""Unit tests for the pure recap segmentation + filename-parsing helpers.

No DB or external services — exercises the deterministic windowing, the dedupe
filter, and the filename convention parser directly.
"""

from __future__ import annotations

from storyfactory.services.recap_service import (
    parse_filename,
    pending_windows,
    plan_segments,
)


def test_plan_segments_even_windows_drops_short_tail():
    windows = plan_segments(
        170, target_short_seconds=52, overlap_seconds=0, min_short_seconds=40
    )
    # 0-52, 52-104, 104-156; the trailing 14s sliver (156-170) is below min and dropped.
    assert windows == [(0.0, 52.0), (52.0, 104.0), (104.0, 156.0)]


def test_plan_segments_with_overlap():
    windows = plan_segments(
        100, target_short_seconds=50, overlap_seconds=10, min_short_seconds=10
    )
    # step = 40 → starts at 0, 40, 80.
    assert windows[0] == (0.0, 50.0)
    assert windows[1] == (40.0, 90.0)
    assert windows[2] == (80.0, 100.0)


def test_plan_segments_empty_for_zero_or_missing_duration():
    assert plan_segments(0) == []
    assert plan_segments(None) == []
    assert plan_segments(-5) == []


def test_plan_segments_max_count_caps():
    windows = plan_segments(100000, target_short_seconds=52, max_count=5)
    assert len(windows) == 5


def test_plan_segments_is_deterministic():
    a = plan_segments(600, target_short_seconds=55, overlap_seconds=5)
    b = plan_segments(600, target_short_seconds=55, overlap_seconds=5)
    assert a == b


def test_pending_windows_filters_already_done():
    planned = [(0.0, 52.0), (52.0, 104.0), (104.0, 156.0)]
    existing = [(0.0, 52.0)]
    assert pending_windows(planned, existing) == [(52.0, 104.0), (104.0, 156.0)]


def test_pending_windows_matches_within_tolerance():
    planned = [(0.0, 52.0)]
    # A ledger row whose start is within tolerance (0.5s) counts as the same window.
    assert pending_windows(planned, [(0.3, 52.3)]) == []
    # A ledger row beyond the tolerance does NOT dedupe an adjacent window — this
    # guards high-overlap configs where window starts can be ~1s apart.
    assert pending_windows(planned, [(1.0, 53.0)]) == [(0.0, 52.0)]


def test_pending_windows_all_pending_when_ledger_empty():
    planned = [(0.0, 52.0), (52.0, 104.0)]
    assert pending_windows(planned, []) == planned


def test_parse_filename_series_sxxexx():
    parsed = parse_filename("Breaking Bad S01E03.mkv")
    assert parsed["type"] == "series"
    assert parsed["season"] == 1
    assert parsed["episode"] == 3
    assert "Breaking Bad" in parsed["series_title"]


def test_parse_filename_series_nxnn():
    parsed = parse_filename("The.Office.2x05.mp4")
    assert parsed["type"] == "series"
    assert parsed["season"] == 2
    assert parsed["episode"] == 5


def test_parse_filename_movie_with_year():
    parsed = parse_filename("Inception (2010).mp4")
    assert parsed["type"] == "movie"
    assert parsed["season"] is None
    assert "Inception" in parsed["series_title"]


def test_parse_filename_movie_with_part():
    parsed = parse_filename("Dune Part 2.mkv")
    assert parsed["type"] == "movie"
    assert parsed["part_index"] == 2


def test_parse_filename_unknown_falls_back_to_movie():
    parsed = parse_filename("some_random_clip.webm")
    assert parsed["type"] == "movie"
    assert parsed["series_title"]
