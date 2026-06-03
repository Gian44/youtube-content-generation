"""Tests for the policy checker module."""

import pytest
from unittest.mock import patch, MagicMock


class TestLocalKeywordCheck:
    """Tests for local keyword-based policy screening."""

    def test_clean_story_passes(self):
        from storyfactory.services.policy_checker import _local_keyword_check

        text = "My neighbor threw a party and I asked them to keep it down."
        flags = _local_keyword_check(text)
        assert len(flags) == 0

    def test_block_keywords_detected(self):
        from storyfactory.services.policy_checker import _local_keyword_check

        text = "This story contains child porn references."
        flags = _local_keyword_check(text)
        assert len(flags) > 0
        assert any(f["severity"] == "block" for f in flags)
        assert any(f["type"] == "minor_sexual" for f in flags)

    def test_self_harm_blocked(self):
        from storyfactory.services.policy_checker import _local_keyword_check

        text = "The character described how to kill yourself in detail."
        flags = _local_keyword_check(text)
        assert len(flags) > 0
        assert any(f["type"] == "self_harm" for f in flags)

    def test_excessive_profanity_flagged(self):
        from storyfactory.services.policy_checker import _local_keyword_check

        text = "That fuck was a shit person who acted like a bitch all day."
        flags = _local_keyword_check(text)
        assert len(flags) > 0
        assert any(f["type"] == "excessive_profanity" for f in flags)
        assert any(f["severity"] == "rewrite" for f in flags)

    def test_mild_profanity_passes(self):
        from storyfactory.services.policy_checker import _local_keyword_check

        text = "I was so damn annoyed at the situation."
        flags = _local_keyword_check(text)
        # Only 1 instance of profanity, threshold is 3
        profanity_flags = [f for f in flags if f["type"] == "excessive_profanity"]
        assert len(profanity_flags) == 0

    def test_graphic_violence_flagged(self):
        from storyfactory.services.policy_checker import _local_keyword_check

        text = "The villain tried to decapitate the hero."
        flags = _local_keyword_check(text)
        assert any(f["type"] == "graphic_violence" for f in flags)

    def test_crime_instructions_blocked(self):
        from storyfactory.services.policy_checker import _local_keyword_check

        text = "Here's how to make a bomb using household items."
        flags = _local_keyword_check(text)
        assert any(f["severity"] == "block" for f in flags)


class TestPolicyCheckIntegration:
    """Integration tests for the full policy check workflow."""

    @patch("storyfactory.services.policy_checker.get_session")
    @patch("storyfactory.services.policy_checker._ai_policy_check")
    def test_clean_story_passes_full_check(self, mock_ai, mock_session):
        from storyfactory.services.policy_checker import check_story_policy

        mock_ai.return_value = []
        mock_session.return_value = MagicMock()

        story = MagicMock()
        story.id = "test-id"
        story.body = "A wholesome story about helping a neighbor."
        story.hook = "You won't believe what my neighbor did."

        result = check_story_policy(story)
        assert result["is_clean"] is True
        assert result["action"] == "pass"

    @patch("storyfactory.services.policy_checker.get_session")
    def test_blocked_story_fails(self, mock_session):
        from storyfactory.services.policy_checker import check_story_policy

        mock_session.return_value = MagicMock()

        story = MagicMock()
        story.id = "test-id"
        story.body = "This contains child porn material."
        story.hook = "Terrible content."

        result = check_story_policy(story)
        assert result["is_clean"] is False
        assert result["action"] == "block"
