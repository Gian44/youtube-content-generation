"""Recap Shorts pipeline (CinybeShorts) — dry-run + dedupe-ledger tests.

Runs the real daily pipeline in recap_shorts mode (dry-run: no API calls, no
FFmpeg, placeholder clips/renders), then asserts which Shorts and ledger rows
were produced and that re-runs never duplicate a window.
"""

from __future__ import annotations


def _bootstrap():
    from storyfactory.db.migrations import run_migrations
    from storyfactory.db.seed import run_seed

    run_migrations()
    run_seed()  # seeds recap_short_script + the other prompts


def _make_recap_channel(slug: str, inbox: str, *, max_per_run: int = 1) -> str:
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
                "recap": {"inbox_path": inbox, "max_shorts_per_run": max_per_run},
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


def _counts(channel_id: str):
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import RecapSegment, RenderJob, Story

    session = get_session()
    try:
        stories = session.query(Story).filter_by(channel_id=channel_id).all()
        renders = session.query(RenderJob).filter_by(channel_id=channel_id).all()
        segments = session.query(RecapSegment).filter_by(channel_id=channel_id).all()
        return (
            [s.type for s in stories],
            [r.type for r in renders],
            [(round(s.start_seconds, 1), s.status) for s in segments],
        )
    finally:
        session.close()


def test_recap_pipeline_makes_only_shorts_and_ledgers(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    inbox = str(isolated_env / "noinbox")  # absent → inbox scan is a graceful no-op
    cid = _make_recap_channel("recapchan", inbox, max_per_run=1)
    _register_episode(cid, duration=170)  # plans 3 windows at 52s

    run_daily_pipeline(channel_ref=cid, dry_run=True)

    story_types, render_types, segments = _counts(cid)
    assert set(story_types) == {"short"}
    assert set(render_types) == {"short"}
    assert "long_form" not in render_types
    assert len(segments) == 1  # max_shorts_per_run=1
    assert segments[0][0] == 0.0 and segments[0][1] == "rendered"


def test_recap_ledger_prevents_duplicate_windows_across_runs(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    inbox = str(isolated_env / "noinbox")
    cid = _make_recap_channel("recapchan2", inbox, max_per_run=1)
    _register_episode(cid, duration=170)  # 3 planned windows

    # Run more times than there are windows — the ledger must cap production.
    for _ in range(5):
        run_daily_pipeline(channel_ref=cid, dry_run=True)

    _, render_types, segments = _counts(cid)
    starts = sorted({s for s, _ in segments})
    assert starts == [0.0, 52.0, 104.0]  # exactly the planned windows
    assert len(segments) == 3  # no duplicate rows despite 5 runs
    assert set(render_types) == {"short"}


def test_recap_episode_completes_then_idles(isolated_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    from storyfactory.db.engine import get_session
    from storyfactory.db.models import MediaEpisode
    from storyfactory.pipeline.daily import run_daily_pipeline

    _bootstrap()
    inbox = str(isolated_env / "noinbox")
    cid = _make_recap_channel("recapchan3", inbox, max_per_run=10)  # exhaust in one run
    _register_episode(cid, duration=170)

    run_daily_pipeline(channel_ref=cid, dry_run=True)

    session = get_session()
    try:
        episode = session.query(MediaEpisode).filter_by(channel_id=cid).first()
        assert episode.status == "completed"
    finally:
        session.close()

    # A further run finds nothing to do and creates no new segments.
    run_daily_pipeline(channel_ref=cid, dry_run=True)
    _, _, segments = _counts(cid)
    assert len(segments) == 3
