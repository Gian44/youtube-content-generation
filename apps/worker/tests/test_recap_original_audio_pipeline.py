"""Recap pipeline in original_audio mode — keeps source audio, skips TTS.

Runs the real daily pipeline (dry-run) for a recap channel configured with
audio_mode="original_audio" and asserts the new path is taken: no TTS, the clip
is cut WITH its audio, and the render is the clip-native recap render.
"""

from __future__ import annotations


def _bootstrap():
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.seed import run_seed

    run_migrations()
    run_seed()


def _make_original_audio_channel(slug: str, inbox: str, *, max_per_run: int = 1) -> str:
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
                    "max_shorts_per_run": max_per_run,
                    "audio_mode": "original_audio",
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
            session,
            series,
            file_path="/fake/Test Show S01E01.mkv",
            season=1,
            episode=1,
            duration_seconds=duration,
            commit=True,
        )
    finally:
        session.close()


def test_original_audio_mode_keeps_audio_and_skips_tts(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")

    _bootstrap()

    # Spy on cut_clip to confirm the source audio is preserved.
    from storyfactory.services import recap_service

    captured = {}
    real_cut = recap_service.cut_clip

    def spy_cut(*args, **kwargs):
        captured["keep_audio"] = kwargs.get("keep_audio", False)
        return real_cut(*args, **kwargs)

    monkeypatch.setattr(recap_service, "cut_clip", spy_cut)

    # TTS must never be invoked in original_audio mode.
    import storyfactory.pipeline.recap_shorts as rs

    def no_tts(*args, **kwargs):
        captured["tts_called"] = True
        raise AssertionError("generate_tts must not run in original_audio mode")

    monkeypatch.setattr(rs, "generate_tts", no_tts)

    from storyfactory.pipeline.daily import run_daily_pipeline

    inbox = str(isolated_env / "noinbox")
    cid = _make_original_audio_channel("oachan", inbox, max_per_run=1)
    _register_episode(cid, duration=170)

    run_daily_pipeline(channel_ref=cid, dry_run=True)

    assert captured.get("tts_called") is None, "TTS should never run"
    assert captured.get("keep_audio") is True, "clip must be cut with original audio"

    from storyfactory.db.engine import get_session
    from storyfactory.db.models import RecapSegment, RenderJob

    session = get_session()
    try:
        segments = session.query(RecapSegment).filter_by(channel_id=cid).all()
        renders = session.query(RenderJob).filter_by(channel_id=cid).all()
        assert len(segments) == 1
        assert segments[0].status == "rendered"
        assert len(renders) == 1
        assert renders[0].render_config.get("audio_mode") == "original_audio"
    finally:
        session.close()
