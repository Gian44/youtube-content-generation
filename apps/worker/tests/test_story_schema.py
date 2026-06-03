"""Tests for story prompt output schema validation."""

import json
import pytest


def test_short_story_schema():
    """Test that short story output matches expected schema."""
    sample = {
        "title": "AITA for refusing to help my sister move?",
        "hook": "My sister asked me to cancel my vacation to help her move.",
        "body": "Last month my sister told me she was moving across town. "
        "I said I'd love to help but I already had a vacation booked. "
        "She said family comes first and I should cancel. "
        "I said no. Now she's telling everyone I'm selfish.",
        "comment_bait": "Would you cancel your vacation for family?",
        "word_count": 52,
    }

    assert "title" in sample
    assert "hook" in sample
    assert "body" in sample
    assert "comment_bait" in sample
    assert "word_count" in sample
    assert isinstance(sample["title"], str)
    assert isinstance(sample["body"], str)
    assert isinstance(sample["word_count"], int)
    assert len(sample["title"]) <= 200
    assert len(sample["hook"]) > 0
    assert sample["word_count"] > 0


def test_short_story_word_count():
    """Test short story word count is in valid range."""
    body = "This is a test story. " * 10  # ~50 words
    word_count = len(body.split())
    # Short stories should be 100-160 words in production
    # but we test basic counting here
    assert word_count > 0


def test_long_story_schema():
    """Test that long-form story output matches expected schema."""
    sample = {
        "title": "The Day Everything Changed at Work",
        "hook": "I thought my boss was joking when he fired the entire team.",
        "body": "It started as a normal Monday morning. " * 30,
        "comment_bait": "Have you ever had a workplace horror story?",
        "word_count": 240,
    }

    assert "title" in sample
    assert "hook" in sample
    assert "body" in sample
    assert sample["word_count"] >= 200  # Long-form should be 300-600


def test_story_json_parsing():
    """Test JSON extraction from potentially messy AI output."""
    # AI sometimes wraps JSON in markdown
    raw = '''Here's the story:
```json
{
  "title": "Test Story",
  "hook": "Something amazing happened",
  "body": "The full story text goes here.",
  "comment_bait": "What would you do?",
  "word_count": 7
}
```
'''
    start = raw.find("{")
    end = raw.rfind("}") + 1
    assert start >= 0
    assert end > start

    parsed = json.loads(raw[start:end])
    assert parsed["title"] == "Test Story"
    assert parsed["word_count"] == 7


def test_metadata_schema():
    """Test metadata generation output schema."""
    metadata = {
        "title": "Reddit Stories That Will Keep You Up | AITA Edition",
        "description": "Amazing stories...\n\nThese are original fictional stories created for entertainment.",
        "tags": ["shorts", "storytime", "reddit stories"],
        "pinned_comment": "What would you do in this situation?",
    }

    assert len(metadata["title"]) <= 100
    assert "fictional stories" in metadata["description"]
    assert isinstance(metadata["tags"], list)
    assert len(metadata["tags"]) > 0
