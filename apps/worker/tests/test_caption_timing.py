"""Tests for caption timing generation."""

import pytest


class TestCaptionTiming:
    """Tests for caption word timing generation."""

    def test_approximate_timing_basic(self):
        from storyfactory.services.caption_service import _approximate_word_timings

        text = "Hello world this is a test"
        timings = _approximate_word_timings(text, total_duration=6.0)

        assert len(timings) == 6
        assert timings[0]["start"] == 0.0
        assert timings[-1]["end"] <= 6.1  # Allow small floating point tolerance
        assert timings[-1]["end"] >= 5.9

    def test_approximate_timing_preserves_words(self):
        from storyfactory.services.caption_service import _approximate_word_timings

        text = "Every word should be present"
        timings = _approximate_word_timings(text, total_duration=4.0)

        words = [t["word"] for t in timings]
        assert words == ["Every", "word", "should", "be", "present"]

    def test_approximate_timing_ordering(self):
        from storyfactory.services.caption_service import _approximate_word_timings

        text = "One two three four five"
        timings = _approximate_word_timings(text, total_duration=5.0)

        for i in range(len(timings) - 1):
            assert timings[i]["end"] <= timings[i + 1]["start"] + 0.001
            assert timings[i]["start"] < timings[i]["end"]

    def test_empty_text_returns_empty(self):
        from storyfactory.services.caption_service import _approximate_word_timings

        timings = _approximate_word_timings("", total_duration=5.0)
        assert timings == []

    def test_ass_time_formatting(self):
        from storyfactory.services.caption_service import _format_ass_time

        assert _format_ass_time(0.0) == "0:00:00.00"
        assert _format_ass_time(1.5) == "0:00:01.50"
        assert _format_ass_time(65.25) == "0:01:05.25"
        assert _format_ass_time(3661.0) == "1:01:01.00"


class TestCaptionConfig:
    """Tests for caption configuration."""

    def test_safe_margins_defined(self):
        from storyfactory.services.caption_service import SHORTS_SAFE_MARGINS

        assert SHORTS_SAFE_MARGINS["top"] >= 100
        assert SHORTS_SAFE_MARGINS["bottom"] >= 200
        assert SHORTS_SAFE_MARGINS["left"] >= 20
        assert SHORTS_SAFE_MARGINS["right"] >= 20

    def test_default_config_values(self):
        from storyfactory.services.caption_service import DEFAULT_CAPTION_CONFIG

        assert DEFAULT_CAPTION_CONFIG["font_size"] > 0
        assert DEFAULT_CAPTION_CONFIG["max_words_per_line"] > 0
        assert DEFAULT_CAPTION_CONFIG["stroke_width"] >= 0
