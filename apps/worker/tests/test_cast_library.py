"""cast_library — inbox sorting, Flow fetch matching, manifest, coverage, grid."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from storyfactory.content_defaults import CAST_DEFAULTS, CAST_EMOTION_TAGS
from storyfactory.services import cast_library as cl

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)


def _make_clip(path: Path, seconds: float = 2.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30",
            "-t", str(seconds), "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _bank(tmp_path: Path, persona: str = "gina") -> tuple[Path, str]:
    root = tmp_path / "cast"
    (root / persona).mkdir(parents=True)
    return root, persona


# 1 ---------------------------------------------------------------------------

def test_parse_clip_name():
    assert cl.parse_clip_name("shocked_1.mp4") == ("shocked", 1, None)
    assert cl.parse_clip_name("neutral_listening_2_alt1.MOV") == ("neutral_listening", 2, 1)
    assert cl.parse_clip_name("whisper_secret_3.webm") == ("whisper_secret", 3, None)
    assert cl.parse_clip_name("shocked_1.txt") is None
    assert cl.parse_clip_name("unknown_1.mp4") is None
    assert cl.parse_clip_name("shocked.mp4") is None
    assert cl.parse_clip_name("flow_clips.json") is None
    assert cl.bank_name(1, None, ".mp4") == "1.mp4"
    assert cl.bank_name(1, 2, ".mov") == "1_alt2.mov"


# 2 ---------------------------------------------------------------------------

def test_ingest_inbox_moves_skips_and_ignores(tmp_path):
    root, persona = _bank(tmp_path)
    inbox = root / persona / "inbox"
    inbox.mkdir()
    (inbox / "shocked_1.mp4").write_bytes(b"a")
    (inbox / "shocked_1_alt1.mp4").write_bytes(b"b")
    (inbox / "sad_2.mov").write_bytes(b"c")
    (inbox / "notes.txt").write_bytes(b"n")
    (inbox / "flow_clips.json").write_text("[]")
    # duplicate: destination already exists in the bank
    (root / persona / "angry").mkdir()
    (root / persona / "angry" / "1.mp4").write_bytes(b"old")
    (inbox / "angry_1.mp4").write_bytes(b"new")

    res = cl.ingest_inbox(root, persona)

    assert set(res["moved"]) == {
        ("shocked_1.mp4", "shocked/1.mp4"),
        ("shocked_1_alt1.mp4", "shocked/1_alt1.mp4"),
        ("sad_2.mov", "sad/2.mov"),
    }
    assert ("notes.txt", "unrecognized name") in res["skipped"]
    assert ("angry_1.mp4", "exists") in res["skipped"]
    assert len(res["skipped"]) == 2  # flow_clips.json is silently ignored
    assert (root / persona / "shocked" / "1.mp4").read_bytes() == b"a"
    assert (root / persona / "shocked" / "1_alt1.mp4").read_bytes() == b"b"
    assert (root / persona / "sad" / "2.mov").read_bytes() == b"c"
    assert (root / persona / "angry" / "1.mp4").read_bytes() == b"old"
    assert (inbox / "angry_1.mp4").exists()
    assert (inbox / "flow_clips.json").exists()
    assert not (inbox / "shocked_1.mp4").exists()


def test_ingest_inbox_missing_persona_is_empty(tmp_path):
    assert cl.ingest_inbox(tmp_path / "cast", "nobody") == {"moved": [], "skipped": []}


# 3 ---------------------------------------------------------------------------

def test_fetch_flow_clips_matches_alts_and_unmatched(tmp_path):
    root, persona = _bank(tmp_path)
    prompts = {
        (tag, 1): f"{tag} reaction sentence number one. Continuous shot: phone propped on a desk."
        for tag in CAST_EMOTION_TAGS
    }
    entries = [
        {"id": "a1", "src": "https://flow/a1.mp4", "line": prompts[("shocked", 1)]},
        {"id": "a2", "src": "https://flow/a2.mp4", "line": prompts[("shocked", 1)]},
        {"id": "b1", "src": "https://flow/b1.mp4", "line": prompts[("whisper_secret", 1)]},
        {"id": "zz", "src": "https://flow/zz.mp4", "line": "completely unrelated nonsense here"},
    ]
    calls = []

    def fake_download(src, dest):
        calls.append(src)
        Path(dest).write_bytes(src.encode())

    res = cl.fetch_flow_clips(root, persona, entries, prompts, download=fake_download)

    inbox = root / persona / "inbox"
    assert ("a1", "shocked_1.mp4") in res["fetched"]
    assert ("a2", "shocked_1_alt1.mp4") in res["fetched"]
    assert ("b1", "whisper_secret_1.mp4") in res["fetched"]
    assert res["unmatched"] == ["zz"]
    assert (inbox / "shocked_1.mp4").read_bytes() == b"https://flow/a1.mp4"
    assert (inbox / "shocked_1_alt1.mp4").read_bytes() == b"https://flow/a2.mp4"
    assert (inbox / "unmatched_zz.mp4").exists()
    assert len(calls) == 4


def test_normalize_key():
    assert cl.normalize_key("  Hello,   WORLD! 42 ") == "hello world 42"
    assert len(cl.normalize_key("x" * 100)) == 40


# 4 ---------------------------------------------------------------------------

def test_build_manifest_probes_reuses_drops_and_reprobes(tmp_path, monkeypatch):
    root, persona = _bank(tmp_path)
    _make_clip(root / persona / "shocked" / "1.mp4", 2.0)
    _make_clip(root / persona / "angry" / "1.mp4", 1.0)

    manifest = cl.build_manifest(root, persona)

    assert set(manifest) == {"shocked/1.mp4", "angry/1.mp4"}
    assert manifest["shocked/1.mp4"]["duration"] == pytest.approx(2.0, abs=0.2)
    assert manifest["angry/1.mp4"]["duration"] == pytest.approx(1.0, abs=0.2)
    for entry in manifest.values():
        assert entry["width"] == 270
        assert entry["height"] == 480
        assert entry["size"] > 0 and entry["mtime"] > 0
    on_disk = json.loads((root / persona / "manifest.json").read_text())
    assert on_disk == manifest

    # Second call must not re-probe unchanged clips.
    def boom(path):
        raise AssertionError(f"re-probed {path}")

    monkeypatch.setattr(cl, "probe_clip", boom)
    assert cl.build_manifest(root, persona) == manifest
    monkeypatch.undo()

    # Deleting a clip drops its entry.
    (root / persona / "angry" / "1.mp4").unlink()
    m2 = cl.build_manifest(root, persona)
    assert set(m2) == {"shocked/1.mp4"}

    # Rewriting a clip (new size/mtime) re-probes it.
    _make_clip(root / persona / "shocked" / "1.mp4", 1.0)
    m3 = cl.build_manifest(root, persona)
    assert m3["shocked/1.mp4"]["duration"] == pytest.approx(1.0, abs=0.2)


# 5 ---------------------------------------------------------------------------

def test_coverage_missing_and_have_clips(tmp_path):
    root, persona = _bank(tmp_path)
    (root / persona / "shocked").mkdir()
    (root / persona / "angry").mkdir()
    (root / persona / "shocked" / "1.mp4").write_bytes(b"a")
    (root / persona / "angry" / "1.mp4").write_bytes(b"b")

    cov = cl.coverage(root, persona)
    assert set(cov) == set(CAST_EMOTION_TAGS)
    assert cov["shocked"] == 1 and cov["angry"] == 1
    assert sum(cov.values()) == 2

    missing = cl.missing_tags(cov)
    assert len(missing) == 8
    assert missing == [t for t in CAST_EMOTION_TAGS if t not in ("shocked", "angry")]

    assert cl.have_clips(root, persona) == {("shocked", 1), ("angry", 1)}
    (root / persona / "shocked" / "1_alt1.mp4").write_bytes(b"c")
    assert cl.have_clips(root, persona) == {("shocked", 1), ("angry", 1)}

    # from an explicit manifest
    cov2 = cl.coverage(root, persona, {"sad/3.mp4": {}, "sad/4.mp4": {}})
    assert cov2["sad"] == 2 and cov2["shocked"] == 0


# 6 ---------------------------------------------------------------------------

def test_write_first_frames_grid(tmp_path):
    from PIL import Image

    root, persona = _bank(tmp_path)
    _make_clip(root / persona / "shocked" / "1.mp4", 2.0)
    _make_clip(root / persona / "angry" / "1.mp4", 0.5)  # shorter than GRID_FRAME_SECONDS
    manifest = cl.build_manifest(root, persona)

    out = cl.write_first_frames_grid(root, persona, manifest)

    assert out == root / persona / "first_frames_grid.png"
    with Image.open(out) as im:
        assert im.size == (540, 480)

    assert cl.write_first_frames_grid(root, persona, {}) is None


# 7 ---------------------------------------------------------------------------

def test_library_root(tmp_path, monkeypatch):
    import storyfactory.config as cfg

    assert cl.library_root("main", {"library_path": "/x/y"}) == Path("/x/y")

    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(tmp_path / "storage"))
    cfg._settings = None
    try:
        assert cl.library_root("main", {"library_path": None}) == tmp_path / "storage" / "cast" / "main"
        assert cl.library_root("main", {}) == tmp_path / "storage" / "cast" / "main"
    finally:
        cfg._settings = None


# 8 ---------------------------------------------------------------------------

def test_merged_cast_config_defaults():
    merged = cl.merged_cast_config()
    assert merged == CAST_DEFAULTS
    assert merged["enabled"] is False


def test_personas_in(tmp_path):
    root = tmp_path / "cast"
    (root / "gina").mkdir(parents=True)
    (root / "gina" / "character.json").write_text("{}")
    (root / "max" / "shocked").mkdir(parents=True)
    (root / "hero").mkdir()
    (root / "hero" / "character.json").write_text("{}")
    (root / "empty").mkdir()
    assert cl.personas_in(root) == ["gina", "max"]
    assert cl.personas_in(tmp_path / "nope") == []
