"""`cast` CLI group — init writes the bible + pack, scan sorts the inbox, config merges."""

from __future__ import annotations

import json
import os
import shutil

import pytest
from click.testing import CliRunner

from storyfactory.content_defaults import CAST_EMOTION_TAGS

# ``storyfactory.__main__`` calls load_dotenv() at import, which would leak the
# repo's real .env into os.environ for every later test in the run. Import it
# once here and put the environment back exactly as it was.
_env_before = dict(os.environ)
from storyfactory import __main__ as main  # noqa: E402

os.environ.clear()
os.environ.update(_env_before)


def _fake_bible(variants: int = 3) -> dict:
    return {
        "name": "Mara",
        "age_range": "early 30s",
        "identity_lock": "The EXACT same woman: dark curly hair, olive skin, grey hoodie.",
        "setting": "a small lived-in kitchen with a window over the sink",
        "base_motion": (
            "Continuous shot: phone propped still at chest height, vertical 9:16. "
            "Small nods, real blinks, warm window light, quiet room tone. "
            "The character does not speak; lips stay closed. No captions, no on-screen "
            "text, no subtitles, no music."
        ),
        "master_frame_prompt": "The EXACT same woman ... neutral listening pose ... no watermark.",
        "emotion_actions": {
            tag: [f"{tag} reaction variant {n}: the expression develops in the first second."
                  for n in range(1, variants + 1)]
            for tag in CAST_EMOTION_TAGS
        },
    }


class _FakeGemini:
    def __init__(self, *args, **kwargs):
        pass

    def generate_json(self, model, prompt, system=None, temperature=0.6):
        return _fake_bible()


def _make_channel(slug: str, bank: str) -> str:
    from storyfactory.db.migrations import run_migrations

    run_migrations()
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import create_channel

    session = get_session()
    try:
        ch = create_channel(
            session, name=slug, slug=slug, niche="revenge stories",
            config={"cast": {"library_path": bank}}, commit=True,
        )
        return ch.slug
    finally:
        session.close()


def test_cast_init_writes_bible_and_pack(isolated_env, monkeypatch):
    import storyfactory.services.gemini_rest as gemini_rest

    monkeypatch.setattr(gemini_rest, "Gemini", _FakeGemini)
    bank = isolated_env / "bank"
    slug = _make_channel("fic", str(bank))

    result = CliRunner().invoke(main.cli, ["cast", "init", "--channel", slug, "--persona", "calm"])
    assert result.exit_code == 0, result.output

    bible = json.loads((bank / "calm" / "character.json").read_text(encoding="utf-8"))
    assert bible["persona"] == "calm"
    assert set(bible["emotion_actions"]) == set(CAST_EMOTION_TAGS)
    pack = (bank / "calm" / "outbox" / "prompt_pack.md").read_text(encoding="utf-8")
    assert pack.count("Save as: inbox/") == 30
    assert "lips stay closed" in pack
    checklist = (bank / "calm" / "outbox" / "flow_checklist.md").read_text(encoding="utf-8")
    assert "600 on Veo 3.1 Fast" in checklist and "300 on Veo 3.1 Lite" in checklist
    assert "600 credits on Veo 3.1 Fast" in result.output

    # A second init without --force keeps the existing bible.
    (bank / "calm" / "character.json").write_text(
        json.dumps({**bible, "name": "Kept"}), encoding="utf-8"
    )
    result = CliRunner().invoke(main.cli, ["cast", "init", "--channel", slug, "--persona", "calm"])
    assert result.exit_code == 0, result.output
    assert "exists" in result.output
    assert json.loads((bank / "calm" / "character.json").read_text())["name"] == "Kept"


def test_cast_init_reports_missing_gemini_key(isolated_env, monkeypatch):
    import storyfactory.services.gemini_rest as gemini_rest

    def _boom(*a, **k):
        raise gemini_rest.GeminiError("Gemini API key not configured.")

    monkeypatch.setattr(gemini_rest, "Gemini", _boom)
    slug = _make_channel("fic2", str(isolated_env / "bank"))
    result = CliRunner().invoke(main.cli, ["cast", "init", "--channel", slug, "--persona", "calm"])
    assert result.exit_code == 0
    assert "not configured" in result.output
    assert not (isolated_env / "bank" / "calm").exists()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_cast_scan_sorts_inbox_and_refreshes_pack(isolated_env):
    import subprocess

    from storyfactory.services import cast_bible

    bank = isolated_env / "bank"
    slug = _make_channel("fic3", str(bank))
    cast_bible.write_bible(bank, "calm", {**_fake_bible(), "persona": "calm"})
    inbox = bank / "calm" / "inbox"
    inbox.mkdir(parents=True)
    for name in ("shocked_1.mp4", "sad_2.mp4"):
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=270x480:rate=30",
             "-t", "1.5", "-pix_fmt", "yuv420p", str(inbox / name)],
            check=True,
        )
    (inbox / "notes.txt").write_text("x")

    result = CliRunner().invoke(main.cli, ["cast", "scan", "--channel", slug])
    assert result.exit_code == 0, result.output
    assert (bank / "calm" / "shocked" / "1.mp4").exists()
    assert (bank / "calm" / "sad" / "2.mp4").exists()
    assert (inbox / "notes.txt").exists()
    manifest = json.loads((bank / "calm" / "manifest.json").read_text())
    assert set(manifest) == {"shocked/1.mp4", "sad/2.mp4"}
    assert (bank / "calm" / "first_frames_grid.png").exists()
    assert "missing tags:" in result.output and "angry" in result.output
    assert "shocked" not in result.output.split("missing tags:")[1].splitlines()[0]
    # The pack now omits the two clips that are in the bank.
    pack = (bank / "calm" / "outbox" / "prompt_pack.md").read_text(encoding="utf-8")
    assert pack.count("Save as: inbox/") == 28
    assert "Save as: inbox/shocked_1.mp4" not in pack
    assert "Save as: inbox/sad_2.mp4" not in pack


def test_cast_status_lists_personas_without_bank(isolated_env):
    from storyfactory.services import cast_bible

    bank = isolated_env / "bank"
    slug = _make_channel("fic4", str(bank))
    cast_bible.write_bible(bank, "calm", {**_fake_bible(), "persona": "calm"})
    result = CliRunner().invoke(main.cli, ["cast", "status", "--channel", slug])
    assert result.exit_code == 0, result.output
    assert "cast.enabled = False" in result.output
    assert "calm" in result.output
    assert "no bank yet" in result.output and "dramatic" in result.output


def test_cast_config_merges_into_nested_block(isolated_env):
    from storyfactory.db.engine import get_session
    from storyfactory.services.channel_service import get_channel

    slug = _make_channel("fic5", str(isolated_env / "bank"))
    result = CliRunner().invoke(
        main.cli, ["cast", "config", "--channel", slug, "--enabled", "--planner-model", "gemini-x"]
    )
    assert result.exit_code == 0, result.output
    session = get_session()
    try:
        cfg = get_channel(session, slug).config["cast"]
        assert cfg["enabled"] is True
        assert cfg["planner_model"] == "gemini-x"
        assert cfg["library_path"] == str(isolated_env / "bank")  # preserved (deep merge)
    finally:
        session.close()


def test_merged_cast_config_applies_channel_override(isolated_env):
    from storyfactory.channel_context import use_channel
    from storyfactory.db.engine import get_session
    from storyfactory.services import cast_library
    from storyfactory.services.channel_service import get_channel

    slug = _make_channel("fic6", "/bank/root")
    session = get_session()
    try:
        ch = get_channel(session, slug)
        with use_channel(session, ch):
            cfg = cast_library.merged_cast_config()
        assert cfg["enabled"] is False
        assert cfg["library_path"] == "/bank/root"
        assert cfg["variants_per_tag"] == 3
        assert cast_library.library_root(ch.slug, cfg).as_posix() == "/bank/root"
    finally:
        session.close()


def test_cast_scan_consumes_flow_clips_json(isolated_env, monkeypatch):
    from storyfactory.services import cast_bible, cast_library

    bank = isolated_env / "bank"
    slug = _make_channel("fic7", str(bank))
    bible = {**_fake_bible(), "persona": "calm"}
    cast_bible.write_bible(bank, "calm", bible)
    inbox = bank / "calm" / "inbox"
    inbox.mkdir(parents=True)
    entries = [
        {"id": "aaa", "src": "https://flow.example/1", "line": cast_bible.clip_prompt(bible, "shocked", 1)},
        {"id": "bbb", "src": "https://flow.example/2", "line": "nothing like any prompt"},
    ]
    (inbox / "flow_clips.json").write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(cast_library, "_http_download", lambda src, dest: dest.write_bytes(b"x"))

    result = CliRunner().invoke(main.cli, ["cast", "scan", "--channel", slug, "--persona", "calm"])
    assert result.exit_code == 0, result.output
    assert "fetched 1 clip(s) from flow_clips.json, 1 unmatched" in result.output
    assert (bank / "calm" / "shocked" / "1.mp4").exists()          # matched → bank (placeholder bytes)
    assert (inbox / "unmatched_bbb.mp4").exists()                  # unmatched kept in the inbox
    assert not (inbox / "flow_clips.json").exists()
    assert (inbox / "flow_clips.done.json").exists()               # consumed, not re-downloaded next time

    (inbox / "flow_clips.json").write_text("{not json", encoding="utf-8")
    result = CliRunner().invoke(main.cli, ["cast", "scan", "--channel", slug, "--persona", "calm"])
    assert result.exit_code == 0, result.output
    assert "not valid JSON" in result.output
