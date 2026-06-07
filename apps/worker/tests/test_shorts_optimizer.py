"""Optimizer integration: synthetic-flag default-off, metadata strip, upload throttle."""

from __future__ import annotations

import types
from datetime import datetime, timezone


# --------------------------------------------------------------------------
# Metadata strip (provenance / C2PA) in the recap render command
# --------------------------------------------------------------------------

def test_recap_short_cmd_strips_metadata():
    from storyfactory.services import renderer

    cmd = renderer._build_recap_short_cmd(
        clip_path="clip.mp4", music_path=None, caption_path=None,
        output_path="out.mp4", width=1080, height=1920, fps=30,
        music_volume=0.1, has_audio=True, duration=18.0,
    )
    # -map_metadata -1 must be present so no "generated with X" tag rides along.
    assert "-map_metadata" in cmd
    assert cmd[cmd.index("-map_metadata") + 1] == "-1"


# --------------------------------------------------------------------------
# Upload throttle: one Short per channel per day
# --------------------------------------------------------------------------

class _FakeQuery:
    def __init__(self, count):
        self._c = count

    def join(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def count(self):
        return self._c


class _FakeSession:
    def __init__(self, count):
        self._c = count

    def query(self, *a, **k):
        return _FakeQuery(self._c)


def _rj(rtype, ts):
    return types.SimpleNamespace(type=rtype, created_at=ts, id=f"{rtype}-{ts}")


def _jobs():
    base = datetime(2026, 6, 6, tzinfo=timezone.utc)
    return [
        _rj("short", base.replace(hour=1)),
        _rj("short", base.replace(hour=2)),
        _rj("short", base.replace(hour=3)),
        _rj("long_form", base.replace(hour=4)),
    ]


def test_throttle_passthrough_when_unlimited(monkeypatch):
    from storyfactory.pipeline import upload

    monkeypatch.setattr(upload, "effective_config", lambda k, d=None: 0)  # 0 = unlimited
    jobs = _jobs()
    out = upload._apply_short_upload_throttle(_FakeSession(0), types.SimpleNamespace(id="c1"), jobs)
    assert out == jobs


def test_throttle_holds_excess_shorts_but_keeps_long_form(monkeypatch):
    from storyfactory.pipeline import upload

    monkeypatch.setattr(upload, "effective_config", lambda k, d=None: 1)  # 1/day
    jobs = _jobs()
    out = upload._apply_short_upload_throttle(_FakeSession(0), types.SimpleNamespace(id="c1"), jobs)
    shorts = [j for j in out if j.type == "short"]
    longs = [j for j in out if j.type == "long_form"]
    assert len(shorts) == 1            # only one Short allowed today
    assert len(longs) == 1             # long-form never throttled
    assert shorts[0].created_at.hour == 1  # oldest first (FIFO drain)


def test_throttle_blocks_all_shorts_when_quota_used(monkeypatch):
    from storyfactory.pipeline import upload

    monkeypatch.setattr(upload, "effective_config", lambda k, d=None: 1)
    jobs = _jobs()
    # 1 Short already uploaded today → 0 remaining
    out = upload._apply_short_upload_throttle(_FakeSession(1), types.SimpleNamespace(id="c1"), jobs)
    assert all(j.type != "short" for j in out)
    assert len(out) == 1  # just the long-form


# --------------------------------------------------------------------------
# upload_video honors declare_synthetic_media (default OFF)
# --------------------------------------------------------------------------

def _dry_run_upload(monkeypatch, declare_env: str | None):
    monkeypatch.setenv("DRY_RUN", "true")
    if declare_env is not None:
        monkeypatch.setenv("DECLARE_SYNTHETIC_MEDIA", declare_env)
    import storyfactory.config as cfg
    cfg._settings = None  # re-read env

    from storyfactory.db.engine import init_db, get_session
    from storyfactory.db.models import DailyBatch, RenderJob
    from storyfactory.services.youtube_uploader import upload_video

    init_db()
    # Persist a real batch + render_job so the youtube_uploads FK is satisfied.
    s = get_session()
    s.add(DailyBatch(id="b1", channel_id=None, date="2026-06-06", category="x", status="rendered"))
    s.add(RenderJob(
        id="rj-test", channel_id=None, batch_id="b1", type="short",
        status="completed", output_path="x.mp4",
    ))
    s.commit()
    s.close()
    # A transient twin (same id) for attribute reads — avoids detached-instance access.
    rj_arg = RenderJob(
        id="rj-test", channel_id=None, batch_id="b1", type="short",
        output_path="x.mp4", thumbnail_path=None,
    )
    return upload_video(rj_arg, {})


def test_upload_defaults_to_no_synthetic_declaration(isolated_env, monkeypatch):
    up = _dry_run_upload(monkeypatch, declare_env=None)  # default
    assert up.contains_synthetic_media is False


def test_upload_declares_synthetic_when_configured(isolated_env, monkeypatch):
    up = _dry_run_upload(monkeypatch, declare_env="true")
    assert up.contains_synthetic_media is True
