"""caption_service.generate_captions_from_audio — subtitles from real dialogue.

In original_audio mode there is no TTS job; captions are transcribed from the
clip's own audio with whisper-1 (word timestamps). When transcription yields
nothing the Short must render without captions (return None).
"""

from __future__ import annotations

import os
import uuid


def _make_story():
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import DailyBatch, Story

    session = get_session()
    try:
        batch = DailyBatch(
            id=str(uuid.uuid4()), date="2026-06-05", category="test", status="stories_generated"
        )
        session.add(batch)
        session.commit()
        story = Story(
            id=str(uuid.uuid4()),
            batch_id=batch.id,
            category="test",
            type="short",
            title="Test",
            hook="hook",
            body="body",
            comment_bait="",
            word_count=1,
            estimated_duration_seconds=5.0,
            voice_persona="dramatic",
            originality_hash=uuid.uuid4().hex,
            prompt_used="x",
            raw_output="{}",
        )
        session.add(story)
        session.commit()
        return story
    finally:
        session.close()


def test_captions_from_audio_builds_ass_from_dialogue(isolated_env, monkeypatch, tmp_path):
    from storyfactory.db.migrations import run_migrations

    run_migrations()
    from storyfactory.services import caption_service

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-clip-with-audio")

    monkeypatch.setattr(
        caption_service,
        "_openai_transcription",
        lambda audio_path: [
            {"word": "hello", "start": 0.0, "end": 0.4, "confidence": 0.9},
            {"word": "world", "start": 0.4, "end": 0.9, "confidence": 0.9},
        ],
    )

    story = _make_story()
    job = caption_service.generate_captions_from_audio(
        story, str(clip), output_dir=str(tmp_path / "captions")
    )

    assert job is not None
    assert job.status == "completed"
    assert job.alignment_method == "transcription"
    assert job.tts_job_id is None
    assert os.path.exists(job.output_path)
    content = open(job.output_path, encoding="utf-8").read()
    assert "hello" in content and "world" in content


def test_captions_from_audio_returns_none_when_no_transcript(isolated_env, monkeypatch, tmp_path):
    from storyfactory.db.migrations import run_migrations

    run_migrations()
    from storyfactory.services import caption_service

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-clip")

    monkeypatch.setattr(caption_service, "_openai_transcription", lambda audio_path: [])

    story = _make_story()
    job = caption_service.generate_captions_from_audio(
        story, str(clip), output_dir=str(tmp_path / "captions")
    )
    assert job is None


def test_transcribe_dialogue_returns_words(isolated_env, monkeypatch, tmp_path):
    from storyfactory.db.migrations import run_migrations

    run_migrations()
    from storyfactory.services import caption_service

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    words = [{"word": "hi", "start": 0.0, "end": 0.3, "confidence": 0.9}]
    monkeypatch.setattr(caption_service, "_openai_transcription", lambda p: words)

    assert caption_service.transcribe_dialogue(str(clip)) == words


def test_transcribe_dialogue_empty_on_missing_or_error(isolated_env, monkeypatch, tmp_path):
    from storyfactory.services import caption_service

    # Missing file → [].
    assert caption_service.transcribe_dialogue("/does/not/exist.mp4") == []

    # Transcription error → [] (never raises).
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")

    def _boom(p):
        raise RuntimeError("whisper down")

    monkeypatch.setattr(caption_service, "_openai_transcription", _boom)
    assert caption_service.transcribe_dialogue(str(clip)) == []


def test_build_dialogue_captions_from_prefetched_words(isolated_env, tmp_path):
    from storyfactory.db.migrations import run_migrations

    run_migrations()
    from storyfactory.services import caption_service

    story = _make_story()
    job = caption_service.build_dialogue_captions(
        story,
        [
            {"word": "hi", "start": 0.0, "end": 0.3, "confidence": 0.9},
            {"word": "there", "start": 0.3, "end": 0.7, "confidence": 0.9},
        ],
        output_dir=str(tmp_path / "captions"),
    )
    assert job is not None
    assert job.status == "completed"
    assert os.path.exists(job.output_path)


def test_build_dialogue_captions_none_on_empty_words(isolated_env):
    from storyfactory.db.migrations import run_migrations

    run_migrations()
    from storyfactory.services import caption_service

    story = _make_story()
    assert caption_service.build_dialogue_captions(story, []) is None
