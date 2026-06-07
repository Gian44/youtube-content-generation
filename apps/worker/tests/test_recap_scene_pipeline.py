"""Recap pipeline in scene mode — content-aware montage Shorts.

Runs the real daily pipeline (dry-run) for a recap channel configured with
segmentation_mode="scene". The transcription + planning are mocked (no network)
so the test asserts the montage branch: one Short per kept scene, built from the
scene's cut-list, ledgered by the scene's start, with original-audio render.
"""

from __future__ import annotations


def _bootstrap():
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.seed import run_seed

    run_migrations()
    run_seed()


def _make_scene_channel(slug: str, inbox: str) -> str:
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
                    "max_shorts_per_run": 25,
                    "segmentation_mode": "scene",
                    "audio_mode": "original_audio",
                },
            },
            commit=True,
        )
        return ch.id
    finally:
        session.close()


def _register_episode(channel_id: str, duration: float):
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
        ep, _ = recap_service.register_episode(
            session, series, file_path="/fake/Test Show S01E01.mkv",
            season=1, episode=1, duration_seconds=duration, commit=True,
        )
        return ep.id
    finally:
        session.close()


def test_scene_mode_produces_one_montage_short_per_kept_scene(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    _bootstrap()

    cid = _make_scene_channel("scenechan", str(isolated_env / "noinbox"))
    episode_id = _register_episode(cid, duration=600)

    from storyfactory.db.models import MediaScene
    import storyfactory.pipeline.recap_shorts as rs

    # Two kept scenes with montage cut-lists (filler dropped).
    scenes = [
        MediaScene(
            episode_id=episode_id, channel_id=cid, scene_index=0,
            start_seconds=30.0, end_seconds=70.0, importance=0.9, keep=True,
            cut_list=[[30.0, 40.0], [55.0, 63.0]], title="The Big Reveal",
            hook="Nobody saw this coming", comment_bait="Did you catch it?",
            tags=["test show", "test show recap"], mood="tense suspenseful",
            reason="major reveal",
        ),
        MediaScene(
            episode_id=episode_id, channel_id=cid, scene_index=1,
            start_seconds=120.0, end_seconds=150.0, importance=0.75, keep=True,
            cut_list=[[120.0, 138.0]], title="The Betrayal",
            hook="A friend turns", comment_bait="Whose side are you on?",
            tags=["test show betrayal"], mood="dark dramatic", reason="turning point",
        ),
    ]

    monkeypatch.setattr(rs.scene_planner, "plan_episode_scenes", lambda *a, **k: scenes)
    # Transcript words (used for subtitle slicing — skipped in dry-run anyway).
    monkeypatch.setattr(
        rs.episode_transcriber, "transcribe_episode",
        lambda *a, **k: [{"word": "hi", "start": 31.0, "end": 31.3}],
    )

    # build_montage_clip must be asked to stitch the scene's cuts.
    montage_calls = []
    real_build = rs.recap_service.build_montage_clip

    def spy_build(file_path, cut_list, *args, **kwargs):
        montage_calls.append(cut_list)
        return real_build(file_path, cut_list, *args, **kwargs)

    monkeypatch.setattr(rs.recap_service, "build_montage_clip", spy_build)

    from storyfactory.pipeline.daily import run_daily_pipeline

    run_daily_pipeline(channel_ref=cid, dry_run=True)

    from storyfactory.db.engine import get_session
    from storyfactory.db.models import RecapSegment, RenderJob, Story

    session = get_session()
    try:
        segments = session.query(RecapSegment).filter_by(channel_id=cid).all()
        renders = session.query(RenderJob).filter_by(channel_id=cid).all()
        stories = session.query(Story).filter_by(channel_id=cid).all()

        # One Short per kept scene (dynamic count = number of integral scenes).
        assert len(segments) == 2
        assert all(s.status == "rendered" for s in segments)
        # Ledgered by scene start (so dedupe + exhaustion match the plan).
        assert sorted(round(s.start_seconds) for s in segments) == [30, 120]
        # Montage built from the scene cut-lists, not raw windows.
        assert [[30.0, 40.0], [55.0, 63.0]] in montage_calls
        # Stories carry the planner's curiosity titles.
        titles = {s.title for s in stories}
        assert "The Big Reveal" in titles and "The Betrayal" in titles
        # Rendered via the original-audio (montage) path.
        assert all(r.render_config.get("audio_mode") == "original_audio" for r in renders)
    finally:
        session.close()
