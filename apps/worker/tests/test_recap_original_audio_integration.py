"""Non-dry-run wiring of the original_audio path.

Exercises _produce_original_audio_short end-to-end (through run_daily_pipeline)
with the heavy leaf calls stubbed, to lock in: one shared transcript feeds BOTH
captions and music mood; mood is derived even when subtitles are off; music
attribution flows into the render; and the segment is ledgered by render outcome.
"""

from __future__ import annotations

import types
import uuid


def _bootstrap():
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.seed import run_seed

    run_migrations()
    run_seed()


def _make_channel(slug: str, inbox: str, *, subtitles: bool, music: bool = True) -> str:
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import create_channel

    session = get_session()
    try:
        ch = create_channel(
            session,
            name=slug,
            slug=slug,
            config={
                "pipeline_mode": "recap_shorts",
                "enable_shorts": True,
                "enable_long_form": False,
                "recap": {
                    "inbox_path": inbox,
                    "max_shorts_per_run": 1,
                    "audio_mode": "original_audio",
                    "subtitles_from_dialogue": subtitles,
                    "music_enabled": music,
                },
            },
            commit=True,
        )
        return ch.id
    finally:
        session.close()


def _register_episode(channel_id: str, duration: float) -> None:
    from storyfactory.db.engine import get_session
    from storyfactory.services import recap_service
    from storyfactory.services.channel_service import get_channel

    session = get_session()
    try:
        ch = get_channel(session, channel_id)
        series = recap_service.ensure_series(
            session, ch, title="Test Show", slug="test-show", type="series", commit=False
        )
        session.flush()
        recap_service.set_active_series(session, ch, "test-show", commit=False)
        recap_service.register_episode(
            session, series, file_path="/fake/Test Show S01E01.mkv",
            season=1, episode=1, duration_seconds=duration, commit=True,
        )
    finally:
        session.close()


def _persist_completed_render(kwargs):
    """Stand-in for render_recap_short: persist a real (FK-valid) completed RenderJob."""
    from storyfactory.channel_context import get_current_channel_id
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import RenderJob

    session = get_session()
    try:
        rj = RenderJob(
            id=str(uuid.uuid4()),
            batch_id=kwargs["batch_id"],
            channel_id=get_current_channel_id(),
            type="short",
            status="completed",
            render_config={"music_attribution": kwargs.get("music_attribution")},
        )
        session.add(rj)
        session.commit()
        return types.SimpleNamespace(id=rj.id, status="completed", error=None)
    finally:
        session.close()


def _common_stubs(monkeypatch, captured, words):
    import storyfactory.pipeline.recap_shorts as rs
    from storyfactory.services import recap_service

    monkeypatch.setattr(recap_service, "cut_clip", lambda *a, **k: "fake_clip.mp4")
    monkeypatch.setattr(
        rs, "_generate_json",
        lambda prompt: {"title": "T", "hook": "H", "body": "B", "comment_bait": "C", "word_count": 5},
    )
    monkeypatch.setattr(rs, "transcribe_dialogue", lambda path: words)

    def fake_render(**kwargs):
        captured["render"] = kwargs
        return _persist_completed_render(kwargs)

    monkeypatch.setattr(rs, "render_recap_short", fake_render)
    return rs


def test_shared_transcript_feeds_captions_mood_and_music(isolated_env, monkeypatch):
    _bootstrap()
    import storyfactory.pipeline.recap_shorts as rs
    from storyfactory.services import music_service

    captured = {}
    words = [
        {"word": "hello", "start": 0.0, "end": 0.4, "confidence": 0.9},
        {"word": "world", "start": 0.4, "end": 0.9, "confidence": 0.9},
    ]
    _common_stubs(monkeypatch, captured, words)
    monkeypatch.setattr(
        rs, "build_dialogue_captions",
        lambda story, w, **k: types.SimpleNamespace(output_path="caps.ass"),
    )

    def fake_infer(transcript, fallback):
        captured["transcript"] = transcript
        return "tense suspenseful"

    monkeypatch.setattr(music_service, "infer_music_mood", fake_infer)

    def fake_fetch(mood, **k):
        captured["mood"] = mood
        return types.SimpleNamespace(
            id="mus1", local_path="music.mp3",
            attribution='Music: "X" by Y (CC BY 4.0) via Jamendo',
        )

    monkeypatch.setattr(music_service, "fetch_music_track", fake_fetch)

    from storyfactory.pipeline.daily import run_daily_pipeline

    cid = _make_channel("oaint1", str(isolated_env / "noinbox"), subtitles=True)
    _register_episode(cid, duration=55)
    run_daily_pipeline(channel_ref=cid, dry_run=False)

    assert captured["transcript"] == "hello world"  # mood derived from the transcript
    assert captured["mood"] == "tense suspenseful"
    r = captured["render"]
    assert r["music_path"] == "music.mp3"
    assert r["caption_path"] == "caps.ass"
    assert "Jamendo" in r["music_attribution"]

    from storyfactory.db.engine import get_session
    from storyfactory.db.models import RecapSegment

    session = get_session()
    try:
        segs = session.query(RecapSegment).filter_by(channel_id=cid).all()
        assert len(segs) == 1 and segs[0].status == "rendered"
    finally:
        session.close()


def test_mood_inferred_even_when_subtitles_disabled(isolated_env, monkeypatch):
    _bootstrap()
    import storyfactory.pipeline.recap_shorts as rs
    from storyfactory.services import music_service

    captured = {}
    words = [{"word": "quiet", "start": 0.0, "end": 0.5, "confidence": 1.0}]
    _common_stubs(monkeypatch, captured, words)

    def _no_captions(*a, **k):
        raise AssertionError("subtitles disabled — build_dialogue_captions must not run")

    monkeypatch.setattr(rs, "build_dialogue_captions", _no_captions)

    def fake_infer(transcript, fallback):
        captured["transcript"] = transcript
        return "calm ambient"

    monkeypatch.setattr(music_service, "infer_music_mood", fake_infer)
    # No usable track found → graceful fallback to original audio only.
    monkeypatch.setattr(music_service, "fetch_music_track", lambda mood, **k: None)

    from storyfactory.pipeline.daily import run_daily_pipeline

    cid = _make_channel("oaint2", str(isolated_env / "noinbox"), subtitles=False)
    _register_episode(cid, duration=55)
    run_daily_pipeline(channel_ref=cid, dry_run=False)

    # Mood is still derived from the dialogue despite subtitles being off.
    assert captured["transcript"] == "quiet"
    r = captured["render"]
    assert r["caption_path"] is None  # no subtitles
    assert r["music_path"] is None    # no track → original audio only


def test_failed_render_is_ledgered_failed_not_rendered(isolated_env, monkeypatch):
    _bootstrap()
    import storyfactory.pipeline.recap_shorts as rs
    from storyfactory.channel_context import get_current_channel_id
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import RenderJob
    from storyfactory.services import music_service, recap_service

    monkeypatch.setattr(recap_service, "cut_clip", lambda *a, **k: "fake_clip.mp4")
    monkeypatch.setattr(
        rs, "_generate_json",
        lambda prompt: {"title": "T", "hook": "H", "body": "B", "comment_bait": "C", "word_count": 5},
    )
    monkeypatch.setattr(rs, "transcribe_dialogue", lambda path: [])
    monkeypatch.setattr(rs, "build_dialogue_captions", lambda s, w, **k: None)
    monkeypatch.setattr(music_service, "infer_music_mood", lambda t, f: f)
    monkeypatch.setattr(music_service, "fetch_music_track", lambda m, **k: None)

    def fake_failed_render(**kwargs):
        session = get_session()
        try:
            rj = RenderJob(
                id=str(uuid.uuid4()), batch_id=kwargs["batch_id"],
                channel_id=get_current_channel_id(), type="short",
                status="failed", error="ffmpeg boom", render_config={},
            )
            session.add(rj)
            session.commit()
            return types.SimpleNamespace(id=rj.id, status="failed", error="ffmpeg boom")
        finally:
            session.close()

    monkeypatch.setattr(rs, "render_recap_short", fake_failed_render)

    from storyfactory.pipeline.daily import run_daily_pipeline

    cid = _make_channel("oaint3", str(isolated_env / "noinbox"), subtitles=False, music=False)
    _register_episode(cid, duration=55)
    run_daily_pipeline(channel_ref=cid, dry_run=False)

    from storyfactory.db.models import RecapSegment

    session = get_session()
    try:
        segs = session.query(RecapSegment).filter_by(channel_id=cid).all()
        assert len(segs) == 1
        assert segs[0].status == "failed", "a failed render must not be ledgered as rendered"
    finally:
        session.close()
