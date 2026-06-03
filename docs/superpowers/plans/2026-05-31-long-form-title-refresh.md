# Long-Form Title Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace generic long-form upload titles with stronger category-aware titles and provide a safe command to update matching live YouTube videos plus local database rows.

**Architecture:** Keep title generation in `youtube_uploader.py` beside the existing metadata builder. Add a small YouTube metadata update flow that fetches existing snippets, changes only the title, and commits the database update only after the YouTube API succeeds. Expose the one-time cleanup through the existing Click worker CLI.

**Tech Stack:** Python 3.11, SQLAlchemy, Click, pytest, google-api-python-client.

---

## Files

- Modify: `apps/worker/tests/test_youtube_upload.py`
  - Add TDD coverage for long-form title generation and legacy retitle behavior.
- Modify: `apps/worker/storyfactory/services/youtube_uploader.py`
  - Add title helper, legacy retitle query/update service, and YouTube snippet update helper.
- Modify: `apps/worker/storyfactory/__main__.py`
  - Add `retitle-legacy-long-form` CLI command.

## Task 1: Title Generation Tests and Helper

- [ ] **Step 1: Write failing tests**

Add these tests to `TestUploadRequestBuilder` in `apps/worker/tests/test_youtube_upload.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd apps\worker
python -m pytest tests/test_youtube_upload.py::TestUploadRequestBuilder::test_long_form_metadata_uses_specific_title tests/test_youtube_upload.py::TestUploadRequestBuilder::test_relationship_long_form_metadata_uses_category_title -v
```

Expected: both fail because the current long-form title starts with the old generic phrase.

- [ ] **Step 3: Implement minimal title helper**

Add `LEGACY_LONG_FORM_TITLE_PREFIX`, `YOUTUBE_TITLE_LIMIT`, `_category_label()`, and `build_long_form_title()` in `youtube_uploader.py`. Update the long-form branch of `build_upload_metadata()` to call `build_long_form_title(stories, category)`.

- [ ] **Step 4: Run tests to verify green**

Run:

```powershell
cd apps\worker
python -m pytest tests/test_youtube_upload.py::TestUploadRequestBuilder -v
```

Expected: all metadata tests pass.

## Task 2: Legacy Retitle Service Tests and Implementation

- [ ] **Step 1: Write failing tests**

Add a new `TestLegacyLongFormRetitle` class to `apps/worker/tests/test_youtube_upload.py` with tests for:

```python
@patch("storyfactory.services.youtube_uploader._update_youtube_video_title")
@patch("storyfactory.services.youtube_uploader.get_session")
def test_dry_run_does_not_call_youtube_or_commit(self, mock_get_session, mock_update):
    ...

@patch("storyfactory.services.youtube_uploader._update_youtube_video_title")
@patch("storyfactory.services.youtube_uploader.get_session")
def test_retitle_updates_youtube_before_database_commit(self, mock_get_session, mock_update):
    ...

@patch("storyfactory.services.youtube_uploader._update_youtube_video_title")
@patch("storyfactory.services.youtube_uploader.get_session")
def test_retitle_leaves_database_unchanged_when_youtube_update_fails(self, mock_get_session, mock_update):
    ...
```

Use fake upload/render/story objects and a fake query object so the tests exercise selection filtering and commit behavior without a real database.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd apps\worker
python -m pytest tests/test_youtube_upload.py::TestLegacyLongFormRetitle -v
```

Expected: import or attribute failures because `retitle_legacy_long_form_uploads()` does not exist.

- [ ] **Step 3: Implement service**

Add:

```python
def retitle_legacy_long_form_uploads(dry_run: bool = False, limit: int | None = None) -> list[dict]:
    ...
```

The service should query completed long-form uploads with the legacy prefix, skip missing or `DRY_RUN_` video IDs, rebuild stories from `render_job.story_ids`, call `_update_youtube_video_title()` first, then set `upload.title` and commit.

- [ ] **Step 4: Run retitle tests to verify green**

Run:

```powershell
cd apps\worker
python -m pytest tests/test_youtube_upload.py::TestLegacyLongFormRetitle -v
```

Expected: all retitle tests pass.

## Task 3: YouTube Snippet Update Helper and CLI

- [ ] **Step 1: Write failing tests**

Add tests for `_update_youtube_video_title()` that patch `googleapiclient.discovery.build` and verify:

- it calls `videos().list(part="snippet", id=video_id)`,
- it raises when the list response has no items,
- it calls `videos().update(part="snippet", body={...})` with the same snippet fields and replaced title.

Add a CLI smoke test if Click's test runner is already present; otherwise validate the CLI through `python -m storyfactory --help`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd apps\worker
python -m pytest tests/test_youtube_upload.py::TestYouTubeTitleUpdate -v
```

Expected: failures because `_update_youtube_video_title()` does not exist.

- [ ] **Step 3: Implement helper and CLI command**

Implement `_build_youtube_client(settings)` and `_update_youtube_video_title(settings, youtube_video_id, title)`. Add `retitle_legacy_long_form` to `apps/worker/storyfactory/__main__.py` with `--dry-run` and `--limit`.

- [ ] **Step 4: Verify helper tests and CLI help**

Run:

```powershell
cd apps\worker
python -m pytest tests/test_youtube_upload.py::TestYouTubeTitleUpdate -v
python -m storyfactory --help
```

Expected: helper tests pass and help includes `retitle-legacy-long-form`.

## Task 4: Full Verification and Live Retitle

- [ ] **Step 1: Run focused worker tests**

Run:

```powershell
cd apps\worker
python -m pytest tests/test_youtube_upload.py -v
```

Expected: all YouTube upload tests pass.

- [ ] **Step 2: Run full worker tests**

Run:

```powershell
npm run test:worker
```

Expected: all worker tests pass.

- [ ] **Step 3: Preview matching uploads**

Run:

```powershell
cd apps\worker
python -m storyfactory retitle-legacy-long-form --dry-run
```

Expected: command lists only completed uploaded long-form videos whose title starts with `Reddit Stories That Will Keep You Up At Night`.

- [ ] **Step 4: Update live YouTube titles and DB**

Run only after the dry run looks correct:

```powershell
cd apps\worker
python -m storyfactory retitle-legacy-long-form
```

Expected: command updates each live YouTube video title, then commits the matching local `youtube_uploads.title`.

## Self-Review

- Spec coverage: future title generation, legacy-only filtering, live YouTube update before DB commit, dry-run, and tests are all covered.
- Placeholder scan: no TBD or deferred implementation requirements remain.
- Type consistency: planned functions live in `youtube_uploader.py`, CLI imports the retitle service, and tests target those same names.
