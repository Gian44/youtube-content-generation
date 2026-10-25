import base64
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from sof import vision


def _imgs(tmp_path, n):
    out = []
    for i in range(n):
        p = tmp_path / f"p{i}.jpg"
        Image.new("RGB", (64, 48), (i * 20, 50, 90)).save(p)
        out.append(str(p))
    return out


def _done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


def test_contact_sheet_numbers_readable_photos_only(tmp_path):
    paths = _imgs(tmp_path, 3)
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    kept = vision.contact_sheet([paths[0], str(bad), *paths[1:]], str(tmp_path / "sheet.jpg"))
    assert kept == paths and Image.open(tmp_path / "sheet.jpg").size[0] > 384


def test_classify_falls_back_from_codex_to_claude(tmp_path):
    paths = _imgs(tmp_path, 3)
    seen = []

    def runner(cmd, *, cwd, timeout, stdin=None):
        seen.append(cmd[0])
        if cmd[0] == "codex":
            return _done(stderr="error: 401 Unauthorized", code=1)
        assert "--tools" in cmd and "Read" in stdin and "contact sheet of 3 photos" in stdin
        return _done(stdout=json.dumps({"result": '{"subject": [1, 3], "setting": [2], "reject": []}'}))

    v = vision.Vision(("codex", "claude"), runner=runner, check_available=False)
    labels = v.classify(paths, subject="The Moon")
    assert labels == {paths[0]: "subject", paths[1]: "setting", paths[2]: "subject"}
    assert "codex" in v.off and v.stats["claude"] == 1 and seen == ["codex", "claude"]
    v.classify(paths, subject="The Moon")                       # codex stays off: one failed call, not one per batch
    assert seen == ["codex", "claude", "claude"]


def test_codex_answer_is_read_from_the_last_message_file(tmp_path):
    def runner(cmd, *, cwd, timeout, stdin=None):
        assert cmd[:2] == ["codex", "exec"] and "-i" in cmd and "--ignore-user-config" in cmd
        Path(cmd[cmd.index("-o") + 1]).write_text('```json\n{"best": 2}\n```')
        return _done()

    paths = _imgs(tmp_path, 3)
    v = vision.Vision(("codex",), runner=runner, check_available=False)
    assert v.pick_iconic(paths, subject="X") == paths[1] and v.stats["codex"] == 1


def test_nobody_answers_keeps_photos_unknown(tmp_path):
    paths = _imgs(tmp_path, 2)
    v = vision.Vision(("claude",), runner=lambda *a, **k: _done(stderr="boom", code=1), check_available=False)
    for _ in range(3):
        assert set(v.classify(paths, subject="X").values()) == {"unknown"}
    assert "claude" in v.off and v.stats["unanswered"] == 3
    assert v.confirm_thumbnail(paths[0], subject="X") is None    # all providers off now


def _auth(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": int(exp.timestamp())}).encode()).decode().rstrip("=")
    return json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": f"h.{payload}.s", "refresh_token": "r"},
                       "last_refresh": "2026-10-01T00:00:00Z"})


def test_prepare_codex_home_refuses_a_login_about_to_expire(tmp_path):
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    assert "expires" in vision.prepare_codex_home(_auth(now + timedelta(hours=2)), str(tmp_path), now=now)
    assert vision.prepare_codex_home("", str(tmp_path)) == "no CODEX_AUTH_JSON"
    assert vision.prepare_codex_home(_auth(now + timedelta(days=5)), str(tmp_path / "h"), now=now) is None
    data = json.loads((tmp_path / "h" / "auth.json").read_text())
    assert data["last_refresh"].startswith("2026-10-25T12:00:00") and data["tokens"]["refresh_token"] == "r"
    assert vision.codex_login_problem(str(tmp_path / "h"), now=now) is None
    assert "left" in vision.codex_login_problem(str(tmp_path / "h"), now=now + timedelta(days=5))
