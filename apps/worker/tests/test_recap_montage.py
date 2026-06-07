"""Montage assembly: transcript rebasing + clip stitching (recap_service)."""

from __future__ import annotations

import os
import types


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def test_rebase_words_single_cut_offsets_to_zero():
    from storyfactory.services import recap_service

    words = [_w("a", 1.0, 1.4), _w("b", 2.0, 2.4), _w("c", 5.0, 5.4)]
    out = recap_service.rebase_words_to_montage(words, [[1.0, 3.0]])
    # a,b kept and rebased to start at 0; c (at 5.0) dropped — outside the cut.
    assert [w["word"] for w in out] == ["a", "b"]
    assert out[0]["start"] == 0.0
    assert out[1]["start"] == 1.0


def test_rebase_words_follows_reordered_cut_order():
    from storyfactory.services import recap_service

    words = [_w("a", 1.0, 1.4), _w("b", 2.0, 2.4), _w("c", 5.0, 5.4)]
    # hook-first: the later cut [4,6] is stitched FIRST, then [1,3]
    out = recap_service.rebase_words_to_montage(words, [[4.0, 6.0], [1.0, 3.0]])
    # c comes first (rebased into the first stitched segment), then a, b
    assert [w["word"] for w in out] == ["c", "a", "b"]
    assert out[0]["start"] == 1.0          # c: (5.0-4.0)+0 = 1.0
    assert out[1]["start"] == 2.0          # a: (1.0-1.0)+2.0 = 2.0 (offset = first cut len 2)


def test_build_montage_clip_dry_run_writes_placeholder(tmp_path):
    from storyfactory.services import recap_service

    out = recap_service.build_montage_clip(
        "missing.mp4", [[0, 5], [10, 15]], output_dir=str(tmp_path), dry_run=True
    )
    assert os.path.exists(out)
    assert open(out, "rb").read().startswith(b"DRY_RUN")


def test_build_montage_clip_single_cut_delegates_to_cut_clip(tmp_path, monkeypatch):
    from storyfactory.services import recap_service

    src = tmp_path / "src.mp4"
    src.write_bytes(b"video-bytes")
    calls = {}

    def fake_cut_clip(path, start, dur, out_dir, *, dry_run=False, keep_audio=False):
        calls.update(start=start, dur=dur, keep_audio=keep_audio)
        return str(tmp_path / "clip.mp4")

    monkeypatch.setattr(recap_service, "cut_clip", fake_cut_clip)
    recap_service.build_montage_clip(str(src), [[12.0, 30.0]], output_dir=str(tmp_path))
    assert calls["start"] == 12.0
    assert calls["dur"] == 18.0
    assert calls["keep_audio"] is True  # original audio preserved


def test_build_montage_clip_concats_multiple_cuts(tmp_path, monkeypatch):
    from storyfactory.services import recap_service

    src = tmp_path / "src.mp4"
    src.write_bytes(b"video-bytes")

    made = []

    def fake_cut_clip(path, start, dur, out_dir, *, dry_run=False, keep_audio=False):
        p = tmp_path / f"sub_{start}.mp4"
        p.write_bytes(b"sub")
        made.append(str(p))
        return str(p)

    captured = {}

    def fake_run(cmd, *a, **k):
        captured["cmd"] = cmd
        # emulate ffmpeg writing the concat output
        out = cmd[cmd.index("-i") + 2 if False else -1]
        open(out, "wb").write(b"montage")
        return types.SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(recap_service, "cut_clip", fake_cut_clip)
    monkeypatch.setattr(recap_service.subprocess, "run", fake_run)

    out = recap_service.build_montage_clip(
        str(src), [[0, 6], [20, 26]], output_dir=str(tmp_path)
    )
    assert os.path.exists(out)
    assert "concat" in captured["cmd"]
    assert len(made) == 2  # two sub-clips cut before concat
