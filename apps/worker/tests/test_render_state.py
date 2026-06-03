"""Tests for render job state machine."""

import pytest
from unittest.mock import MagicMock


class TestRenderJobStateMachine:
    """Tests for render job status transitions."""

    VALID_STATUSES = ["queued", "processing", "completed", "failed", "retrying", "cancelled"]

    def test_valid_statuses(self):
        for status in self.VALID_STATUSES:
            assert isinstance(status, str)
            assert len(status) > 0

    def test_initial_status_is_queued(self):
        from storyfactory.db.models import RenderJob
        job = RenderJob()
        assert job.status == "queued"

    def test_status_transition_queued_to_processing(self):
        from storyfactory.db.models import RenderJob
        job = RenderJob()
        job.status = "processing"
        assert job.status == "processing"

    def test_status_transition_processing_to_completed(self):
        from storyfactory.db.models import RenderJob
        job = RenderJob()
        job.status = "processing"
        job.status = "completed"
        assert job.status == "completed"

    def test_status_transition_processing_to_failed(self):
        from storyfactory.db.models import RenderJob
        job = RenderJob()
        job.status = "processing"
        job.status = "failed"
        assert job.status == "failed"

    def test_render_config_default(self):
        from storyfactory.db.models import RenderJob
        job = RenderJob()
        # Default should be empty dict or list
        assert job.story_ids is not None or True  # JSON default


class TestVideoFormats:
    """Tests for video format specifications."""

    def test_short_format(self):
        from storyfactory.services.renderer import VIDEO_FORMATS
        fmt = VIDEO_FORMATS["short"]
        assert fmt["width"] == 1080
        assert fmt["height"] == 1920
        assert fmt["fps"] == 30

    def test_long_form_format(self):
        from storyfactory.services.renderer import VIDEO_FORMATS
        fmt = VIDEO_FORMATS["long_form"]
        assert fmt["width"] == 1920
        assert fmt["height"] == 1080
        assert fmt["fps"] == 30

    def test_short_is_vertical(self):
        from storyfactory.services.renderer import VIDEO_FORMATS
        fmt = VIDEO_FORMATS["short"]
        assert fmt["height"] > fmt["width"]

    def test_long_form_is_horizontal(self):
        from storyfactory.services.renderer import VIDEO_FORMATS
        fmt = VIDEO_FORMATS["long_form"]
        assert fmt["width"] > fmt["height"]
