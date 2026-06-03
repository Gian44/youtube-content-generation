"""Tests for YouTube upload request builder and quota guard."""

import pytest
from unittest.mock import MagicMock, patch


LEGACY_TITLE = "Reddit Stories That Will Keep You Up At Night | Family Drama"


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def all(self):
        return self.rows


def make_story(category="family_drama"):
    story = MagicMock()
    story.id = "story-1"
    story.title = "My sister exposed the family secret at dinner"
    story.hook = "The whole table went silent when she read the texts."
    story.category = category
    return story


def make_upload(
    *,
    title=LEGACY_TITLE,
    video_id="abc123def45",
    render_type="long_form",
    status="completed",
):
    render_job = MagicMock()
    render_job.type = render_type
    render_job.story_ids = ["story-1"]

    upload = MagicMock()
    upload.id = "upload-1"
    upload.title = title
    upload.youtube_video_id = video_id
    upload.status = status
    upload.render_job = render_job
    return upload


class TestUploadRequestBuilder:
    """Tests for YouTube upload metadata builder."""

    def test_short_metadata(self):
        from storyfactory.services.youtube_uploader import build_upload_metadata

        render_job = MagicMock()
        render_job.type = "short"

        story = MagicMock()
        story.title = "AITA for saying no to my mom?"
        story.hook = "My mom asked me something outrageous."
        story.category = "aita"

        metadata = build_upload_metadata(render_job, [story], "short")

        assert "title" in metadata
        assert "description" in metadata
        assert "tags" in metadata
        assert len(metadata["title"]) <= 100
        assert "#shorts" in metadata["description"]
        assert "fictional stories" not in metadata["description"].lower()
        assert "fiction" not in metadata["tags"]

    def test_long_form_metadata(self):
        from storyfactory.services.youtube_uploader import build_upload_metadata

        render_job = MagicMock()
        render_job.type = "long_form"

        stories = [MagicMock(title=f"Story {i}", category="revenge") for i in range(3)]

        metadata = build_upload_metadata(render_job, stories, "long_form")

        assert len(metadata["title"]) <= 200
        assert "fictional" not in metadata["description"].lower()
        assert len(metadata["tags"]) > 0
        assert "fiction" not in metadata["tags"]

    def test_long_form_metadata_uses_specific_title(self):
        from storyfactory.services.youtube_uploader import build_upload_metadata

        render_job = MagicMock()
        render_job.type = "long_form"

        story = MagicMock()
        story.title = "My sister exposed the family secret at dinner"
        story.hook = "The whole table went silent when she read the texts."
        story.category = "family_drama"

        metadata = build_upload_metadata(render_job, [story], "long_form")

        assert len(metadata["title"]) <= 100
        assert not metadata["title"].startswith("Reddit Stories That Will Keep You Up At Night")
        assert "Family Drama" in metadata["title"] or "Family Secret" in metadata["title"]

    def test_relationship_long_form_metadata_uses_category_title(self):
        from storyfactory.services.youtube_uploader import build_upload_metadata

        render_job = MagicMock()
        render_job.type = "long_form"

        story = MagicMock()
        story.title = "My boyfriend lied about the trip"
        story.hook = "I found out from one photo."
        story.category = "relationships"

        metadata = build_upload_metadata(render_job, [story], "long_form")

        assert len(metadata["title"]) <= 100
        assert "Relationship" in metadata["title"]
        assert "Reddit Stories That Will Keep You Up At Night" not in metadata["title"]

    def test_empty_stories_default_metadata(self):
        from storyfactory.services.youtube_uploader import build_upload_metadata

        render_job = MagicMock()
        render_job.type = "short"

        metadata = build_upload_metadata(render_job, [], "short")

        assert metadata["title"] == "Story Time"
        assert metadata["description"] == ""

    @patch("storyfactory.services.youtube_uploader.effective_config")
    def test_description_includes_disclosure(self, mock_effective_config):
        from storyfactory.services.youtube_uploader import _build_description

        # Disclosure now resolves through the active channel's effective config.
        mock_effective_config.return_value = "Test Disclosure Line"

        metadata = {"description": "Great stories!"}
        desc = _build_description(metadata)

        assert "Test Disclosure Line" in desc

    def test_contains_synthetic_media_flag(self):
        from storyfactory.services.youtube_uploader import build_upload_metadata

        render_job = MagicMock()
        render_job.type = "short"
        story = MagicMock(title="Test", hook="Test", category="aita")

        metadata = build_upload_metadata(render_job, [story], "short")
        assert metadata.get("contains_synthetic_media") is True


class TestQuotaGuard:
    """Tests for YouTube API quota management."""

    @patch("storyfactory.services.api_tracker.get_session")
    def test_quota_check_with_remaining(self, mock_session):
        from storyfactory.services.api_tracker import check_youtube_quota

        session = MagicMock()
        session.query.return_value.filter.return_value.all.return_value = []
        mock_session.return_value = session

        assert check_youtube_quota() is True

    @patch("storyfactory.services.api_tracker.get_youtube_quota_used")
    def test_quota_exceeded(self, mock_used):
        from storyfactory.services.api_tracker import check_youtube_quota

        mock_used.return_value = 9500  # Over threshold
        result = check_youtube_quota()
        # Should be False since 10000 - 9500 = 500 < 1600
        assert result is False


class TestLegacyLongFormRetitle:
    """Tests for safely retitling already uploaded legacy long-form videos."""

    @patch("storyfactory.services.youtube_uploader._update_youtube_video_title")
    @patch("storyfactory.services.youtube_uploader.get_session")
    def test_dry_run_only_reports_matching_legacy_uploads(self, mock_get_session, mock_update):
        from storyfactory.services.youtube_uploader import retitle_legacy_long_form_uploads

        matching = make_upload()
        custom = make_upload(title="Reddit Family Drama Stories That Get Worse Every Minute")
        short = make_upload(render_type="short")
        failed = make_upload(status="failed")
        dry_run_video = make_upload(video_id="DRY_RUN_12345678")

        session = MagicMock()
        session.query.side_effect = [
            FakeQuery([matching, custom, short, failed, dry_run_video]),
            FakeQuery([make_story()]),
        ]
        mock_get_session.return_value = session

        results = retitle_legacy_long_form_uploads(dry_run=True)

        assert len(results) == 1
        assert results[0]["status"] == "dry_run"
        assert results[0]["upload_id"] == "upload-1"
        assert results[0]["old_title"] == LEGACY_TITLE
        assert not results[0]["new_title"].startswith("Reddit Stories That Will Keep You Up At Night")
        assert matching.title == LEGACY_TITLE
        mock_update.assert_not_called()
        session.commit.assert_not_called()

    @patch("storyfactory.services.youtube_uploader._update_youtube_video_title")
    @patch("storyfactory.services.youtube_uploader.get_session")
    def test_retitle_updates_youtube_before_database_commit(self, mock_get_session, mock_update):
        from storyfactory.services.youtube_uploader import retitle_legacy_long_form_uploads

        upload = make_upload()
        session = MagicMock()
        session.query.side_effect = [
            FakeQuery([upload]),
            FakeQuery([make_story()]),
        ]
        events = []
        mock_update.side_effect = lambda *args: events.append("youtube")
        session.commit.side_effect = lambda: events.append("commit")
        mock_get_session.return_value = session

        results = retitle_legacy_long_form_uploads()

        assert results[0]["status"] == "updated"
        assert events == ["youtube", "commit"]
        assert upload.title == results[0]["new_title"]
        mock_update.assert_called_once()

    @patch("storyfactory.services.youtube_uploader._update_youtube_video_title")
    @patch("storyfactory.services.youtube_uploader.get_session")
    def test_failed_youtube_update_leaves_database_title_unchanged(self, mock_get_session, mock_update):
        from storyfactory.services.youtube_uploader import retitle_legacy_long_form_uploads

        upload = make_upload()
        session = MagicMock()
        session.query.side_effect = [
            FakeQuery([upload]),
            FakeQuery([make_story()]),
        ]
        mock_update.side_effect = RuntimeError("youtube unavailable")
        mock_get_session.return_value = session

        results = retitle_legacy_long_form_uploads()

        assert results[0]["status"] == "failed"
        assert "youtube unavailable" in results[0]["error"]
        assert upload.title == LEGACY_TITLE
        session.commit.assert_not_called()
        session.rollback.assert_called_once()


class TestYouTubeTitleUpdate:
    """Tests for updating a live YouTube video's title."""

    @patch("storyfactory.services.youtube_uploader.track_api_call")
    @patch("googleapiclient.discovery.build")
    def test_update_youtube_video_title_preserves_existing_snippet(self, mock_build, mock_track):
        from storyfactory.services.youtube_uploader import _update_youtube_video_title

        settings = MagicMock()
        settings.youtube_refresh_token = "refresh-token"
        settings.youtube_client_id = "client-id"
        settings.youtube_client_secret = "client-secret"

        youtube = MagicMock()
        videos = youtube.videos.return_value
        videos.list.return_value.execute.return_value = {
            "items": [
                {
                    "id": "abc123def45",
                    "snippet": {
                        "title": "Old Title",
                        "description": "Existing description",
                        "tags": ["reddit stories", "drama"],
                        "categoryId": "24",
                    },
                }
            ]
        }
        videos.update.return_value.execute.return_value = {"id": "abc123def45"}
        mock_build.return_value = youtube

        _update_youtube_video_title(settings, "abc123def45", "New Strong Title")

        videos.list.assert_called_once_with(part="snippet", id="abc123def45")
        videos.update.assert_called_once()
        update_kwargs = videos.update.call_args.kwargs
        assert update_kwargs["part"] == "snippet"
        assert update_kwargs["body"]["id"] == "abc123def45"
        assert update_kwargs["body"]["snippet"] == {
            "title": "New Strong Title",
            "description": "Existing description",
            "tags": ["reddit stories", "drama"],
            "categoryId": "24",
        }
        assert mock_track.call_count == 2

    @patch("storyfactory.services.youtube_uploader.track_api_call")
    @patch("googleapiclient.discovery.build")
    def test_update_youtube_video_title_raises_when_video_is_missing(self, mock_build, mock_track):
        from storyfactory.services.youtube_uploader import _update_youtube_video_title

        settings = MagicMock()
        settings.youtube_refresh_token = "refresh-token"
        settings.youtube_client_id = "client-id"
        settings.youtube_client_secret = "client-secret"

        youtube = MagicMock()
        videos = youtube.videos.return_value
        videos.list.return_value.execute.return_value = {"items": []}
        mock_build.return_value = youtube

        with pytest.raises(RuntimeError, match="not found"):
            _update_youtube_video_title(settings, "missing-video", "New Strong Title")

        videos.update.assert_not_called()
        mock_track.assert_called_once()


class TestDuplicateStoryDetection:
    """Tests for story deduplication."""

    def test_originality_hash_consistency(self):
        import hashlib

        body1 = "This is the same story content."
        body2 = "This is the same story content."
        body3 = "This is different content."

        hash1 = hashlib.sha256(body1.encode()).hexdigest()
        hash2 = hashlib.sha256(body2.encode()).hexdigest()
        hash3 = hashlib.sha256(body3.encode()).hexdigest()

        assert hash1 == hash2
        assert hash1 != hash3

    def test_different_stories_have_different_hashes(self):
        import hashlib

        stories = [
            "Story about a neighbor dispute.",
            "Story about a workplace conflict.",
            "Story about a family reunion.",
        ]

        hashes = [hashlib.sha256(s.encode()).hexdigest() for s in stories]
        assert len(set(hashes)) == len(hashes)
