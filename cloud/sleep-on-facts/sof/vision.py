"""Who looks at the photos: label them subject / setting / reject, pick the thumbnail, confirm it.

Until 2026-10-25 this was gpt-4o-mini on the OpenAI API (sof/images.py). When the OpenAI keys ran out of
credits Gian chose Codex (his ChatGPT plan, no API billing) with Claude (his Claude plan, the same login that
writes the scripts) as the fallback; OpenAI stays last in line for when it has credits again.

Each question goes to the first provider that answers. A provider that reports a dead login or a usage limit
— or fails three times in a row — is switched off for the rest of the run. When nobody answers, the caller
gets ``None`` and keeps its old behaviour (labels stay "unknown": photos are used, not dropped).

To keep the number of calls small, a batch of photos is sent as ONE contact sheet: numbered tiles in a grid
(``contact_sheet``), so Codex and Claude each look at one image per question instead of eight.

Codex in CI: the workflow writes Gian's ``auth.json`` (secret CODEX_AUTH_JSON, copied from his PC) into
``CODEX_HOME``. ``prepare_codex_home`` refuses a login that expires within ``min_hours`` and stamps
``last_refresh`` so Codex never refreshes the login on the runner — a refresh there would rotate the refresh
token and could sign Gian's PC out of Codex. His PC keeps the login fresh; the secret is re-copied from it.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from sof.images import CLASSIFY_PROMPT, CONFIRM_PROMPT, PICK_PROMPT, VISION_URL, _b64_small

log = logging.getLogger("sof.vision")

SHEET_NOTE = ("The image is a contact sheet of {n} photos in a grid, each tile numbered 1 to {n} in its top-left "
              "corner (left to right, top to bottom). Judge each tile on its own; ignore the numbers and the grid.\n")
JSON_ONLY = "\nReply with the JSON object only: no prose before or after it, no code fences."
_FATAL = ("usage limit", "rate limit", "limit reached", "quota", "insufficient_quota", "credit", "401", "403",
          "unauthorized", "authentication", "not logged in", "please run /login", "login", "sign in",
          "refresh token", "token expired", "invalid api key")


class _Fatal(Exception):
    pass


def _jsonish(raw: str) -> dict:
    from sof.llm import parse_json

    return parse_json(raw)


# ------------------------------------------------------------------ contact sheets

def contact_sheet(paths: list[str], out: str, *, cols: int = 4, tile: int = 384) -> list[str]:
    """Numbered grid of ``paths`` → ``out`` (JPEG). Returns the paths that made it onto the sheet, in tile
    order (unreadable files are left out)."""
    from PIL import Image, ImageDraw, ImageFont

    ims, kept = [], []
    for p in paths:
        try:
            with Image.open(p) as im:
                im = im.convert("RGB")
                im.thumbnail((tile, tile))
                ims.append(im.copy())
                kept.append(p)
        except Exception as exc:  # noqa: BLE001 — a broken download is simply not shown
            log.warning("contact sheet: cannot read %s (%s)", p, exc)
    if not ims:
        return []
    cols = max(1, min(cols, len(ims)))
    rows = (len(ims) + cols - 1) // cols
    gap = 8
    sheet = Image.new("RGB", (cols * (tile + gap) + gap, rows * (tile + gap) + gap), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 34)
    except OSError:
        font = ImageFont.load_default()
    for k, im in enumerate(ims):
        x = gap + (k % cols) * (tile + gap) + (tile - im.width) // 2
        y = gap + (k // cols) * (tile + gap) + (tile - im.height) // 2
        sheet.paste(im, (x, y))
        tx, ty = gap + (k % cols) * (tile + gap) + 6, gap + (k // cols) * (tile + gap) + 6
        draw.rectangle([tx, ty, tx + 54, ty + 44], fill=(0, 0, 0))
        draw.text((tx + 8, ty + 3), str(k + 1), fill=(255, 220, 60), font=font)
    sheet.save(out, "JPEG", quality=85)
    return kept


def _small_copy(path: str, out: str, max_side: int) -> str:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        im.save(out, "JPEG", quality=85)
    return out


# ------------------------------------------------------------------ Codex login on the runner

def prepare_codex_home(auth_json: str, home: str, *, min_hours: float = 6.0, now: datetime | None = None) -> str | None:
    """Write ``auth.json`` into ``home`` for ``codex exec``. Returns None when ready, else why not."""
    if not auth_json.strip():
        return "no CODEX_AUTH_JSON"
    try:
        data = json.loads(auth_json)
        token = str((data.get("tokens") or {}).get("access_token") or "")
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = datetime.fromtimestamp(int(claims["exp"]), timezone.utc)
    except Exception as exc:  # noqa: BLE001
        return f"CODEX_AUTH_JSON unreadable ({type(exc).__name__})"
    now = now or datetime.now(timezone.utc)
    hours_left = (exp - now).total_seconds() / 3600
    if hours_left < min_hours:
        return f"Codex login expires {exp:%Y-%m-%d %H:%M} UTC; copy a fresh one from the PC"
    data["last_refresh"] = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")      # no refresh on the runner (see module doc)
    Path(home).mkdir(parents=True, exist_ok=True)
    f = Path(home) / "auth.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(f, 0o600)
    log.info("codex login ready (%.0f h left)", hours_left)
    return None


def codex_login_problem(home: str | None = None, *, min_hours: float = 6.0, now: datetime | None = None) -> str | None:
    """None if ``CODEX_HOME``/auth.json holds a ChatGPT login that is valid for ``min_hours`` more."""
    home = home or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    f = Path(home) / "auth.json"
    if not f.exists():
        return "no Codex login on this machine"
    try:
        token = json.loads(f.read_text(encoding="utf-8"))["tokens"]["access_token"]
        payload = token.split(".")[1]
        exp = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
    except Exception:  # noqa: BLE001
        return "Codex login unreadable"
    left = (exp - (now or datetime.now(timezone.utc)).timestamp()) / 3600
    return None if left >= min_hours else f"Codex login has {left:.0f} h left"


# ------------------------------------------------------------------ providers

def _run(cmd: list[str], *, cwd: str, timeout: float, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", stdin=None if stdin is not None else subprocess.DEVNULL)


class Vision:
    def __init__(self, providers=("codex", "claude", "openai"), *, openai_key: str = "", openai_model: str = "gpt-4o-mini",
                 codex_model: str = "", codex_effort: str = "low", claude_model: str = "haiku", claude_ok: bool = True,
                 runner=None, transport=None, timeout: float = 300.0, check_available: bool = True):
        self.openai_key, self.openai_model = openai_key, openai_model
        self.codex_model, self.codex_effort, self.claude_model = codex_model, codex_effort, claude_model
        self._run = runner or _run
        self._transport, self.timeout = transport, timeout
        self.order = [p for p in providers if p in ("codex", "claude", "openai")]
        self.off: dict[str, str] = {}
        self._fails: dict[str, int] = {}
        self.stats: dict[str, int] = {p: 0 for p in self.order} | {"unanswered": 0}
        if check_available:
            for p in self.order:
                why = self._unavailable(p, claude_ok)
                if why:
                    self.off[p] = why

    @classmethod
    def from_config(cls, cfg) -> "Vision":
        if not cfg.images_vision_check:
            return cls(providers=())
        return cls(cfg.vision_providers, openai_key=cfg.openai_api_key, openai_model=cfg.images_vision_model,
                   codex_model=cfg.codex_model, codex_effort=cfg.codex_effort, claude_model=cfg.claude_vision_model,
                   claude_ok=cfg.claude_enabled)

    def _unavailable(self, p: str, claude_ok: bool) -> str | None:
        if p == "codex":
            if not shutil.which("codex"):
                return "codex CLI not installed"
            return codex_login_problem()
        if p == "claude":
            if not claude_ok:
                return "no CLAUDE_CODE_OAUTH_TOKEN"
            return None if shutil.which("claude") else "claude CLI not installed"
        if p == "openai":
            return None if self.openai_key else "no OPENAI_API_KEY"
        return "unknown provider"

    @property
    def active(self) -> list[str]:
        return [p for p in self.order if p not in self.off]

    def describe(self) -> str:
        on = self.active
        off = [f"{p}: {why}" for p, why in self.off.items()]
        return (" → ".join(on) if on else "none (photos are not checked)") + (f" · off: {'; '.join(off)}" if off else "")

    # -- one question ---------------------------------------------------------

    def ask(self, prompt: str, image: str) -> dict | None:
        """``prompt`` about the single image file ``image`` (a contact sheet or one photo) → parsed JSON."""
        for p in self.active:
            try:
                data = getattr(self, f"_ask_{p}")(prompt + JSON_ONLY, image)
                self.stats[p] += 1
                self._fails[p] = 0
                return data
            except _Fatal as exc:
                self.off[p] = str(exc)[:200]
                log.warning("vision: %s switched off for this run (%s)", p, self.off[p])
            except Exception as exc:  # noqa: BLE001 — try the next provider
                self._fails[p] = self._fails.get(p, 0) + 1
                log.warning("vision: %s failed (%s)", p, str(exc)[:200])
                if self._fails[p] >= 3:
                    self.off[p] = f"3 failures in a row (last: {str(exc)[:120]})"
        self.stats["unanswered"] += 1
        return None

    def _ask_codex(self, prompt: str, image: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="sof-codex-") as td:
            out = Path(td) / "answer.txt"
            img = shutil.copy(image, Path(td) / Path(image).name)
            cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                   "--sandbox", "read-only", "--color", "never", "-C", td,
                   "-c", f'model_reasoning_effort="{self.codex_effort}"', "-o", str(out)]
            if self.codex_model:
                cmd += ["-m", self.codex_model]
            cmd += ["-i", str(img), "--", prompt + "\nThe image is attached. Do not run any commands."]
            try:
                r = self._run(cmd, cwd=td, timeout=self.timeout)
            except (subprocess.TimeoutExpired, OSError) as exc:
                raise RuntimeError(f"codex did not answer: {exc}")
            text = out.read_text(encoding="utf-8").strip() if out.exists() else ""
            if r.returncode != 0 or not text:
                detail = ((r.stderr or "") + (r.stdout or ""))[-400:]
                if any(k in detail.lower() for k in _FATAL):
                    raise _Fatal(f"codex: {detail.strip()[-200:]}")
                raise RuntimeError(f"codex exit {r.returncode}: {detail.strip()[-200:]}")
            return _jsonish(text)

    def _ask_claude(self, prompt: str, image: str) -> dict:
        with tempfile.TemporaryDirectory(prefix="sof-claude-") as td:
            img = shutil.copy(image, Path(td) / Path(image).name)
            cmd = ["claude", "-p", "--output-format", "json", "--model", self.claude_model, "--tools", "Read",
                   "--allowedTools", "Read", "--strict-mcp-config", "--disable-slash-commands",
                   "--no-session-persistence", "--max-turns", "4", "--add-dir", td,
                   "--system-prompt", "You check photos for a documentary video pipeline. Use the Read tool to look "
                                      "at the image file you are given, then answer with the requested JSON only."]
            text = f"Look at the image file {img} with the Read tool.\n\n{prompt}"
            try:
                r = self._run(cmd, cwd=td, timeout=self.timeout, stdin=text)
            except (subprocess.TimeoutExpired, OSError) as exc:
                raise RuntimeError(f"claude did not answer: {exc}")
            out = (r.stdout or "").strip()
            try:
                data = json.loads(out) if out else {}
            except json.JSONDecodeError:
                data = {}
            if r.returncode != 0 or not isinstance(data, dict) or data.get("is_error") or not data.get("result"):
                detail = str((data.get("result") if isinstance(data, dict) else "") or (r.stderr or out)[-300:])
                if any(k in detail.lower() for k in _FATAL):
                    raise _Fatal(f"claude: {detail[-200:]}")
                raise RuntimeError(f"claude exit {r.returncode}: {detail[-200:]}")
            return _jsonish(str(data["result"]))

    def _ask_openai(self, prompt: str, image: str) -> dict:
        content = [{"type": "text", "text": prompt},
                   {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64_small(image, max_side=1536)}",
                                                       "detail": "high"}}]
        with httpx.Client(timeout=120.0, transport=self._transport,
                          headers={"Authorization": f"Bearer {self.openai_key}"}) as c:
            r = c.post(VISION_URL, json={"model": self.openai_model, "messages": [{"role": "user", "content": content}],
                                         "response_format": {"type": "json_object"}, "max_tokens": 300})
        if r.status_code in (401, 403) or (r.status_code == 429 and "quota" in r.text.lower()):
            raise _Fatal(f"openai HTTP {r.status_code}: {r.text[:160]}")
        r.raise_for_status()
        return _jsonish(r.json()["choices"][0]["message"]["content"])

    # -- the pipeline's three questions ---------------------------------------

    def classify(self, paths: list[str], *, subject: str, batch: int = 12) -> dict[str, str]:
        """path → "subject" | "setting" | "reject" | "unknown" (unknown: nobody could look; the photo is kept)."""
        labels = {p: "unknown" for p in paths}
        if not paths or not self.active:
            return labels
        t0 = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="sof-sheet-") as td:
            for i in range(0, len(paths), batch):
                group = paths[i:i + batch]
                sheet = str(Path(td) / f"sheet{i:04d}.jpg")
                kept = contact_sheet(group, sheet)
                for p in group:
                    if p not in kept:
                        labels[p] = "reject"
                if not kept:
                    continue
                data = self.ask(SHEET_NOTE.format(n=len(kept)) + CLASSIFY_PROMPT.format(n=len(kept), subject=subject),
                                sheet)
                if data is None:
                    continue

                def idx(key: str) -> set[int]:
                    return {int(k) - 1 for k in (data.get(key) or []) if str(k).lstrip("-").isdigit()}

                subj, sett = idx("subject") | idx("keep"), idx("setting")
                for j, p in enumerate(kept):
                    labels[p] = "subject" if j in subj else "setting" if j in sett else "reject"
        counts = {k: sum(1 for v in labels.values() if v == k) for k in ("subject", "setting", "reject", "unknown")}
        log.info("vision labels (%s): %s in %.0f s", subject, counts, time.monotonic() - t0)
        return labels

    def pick_iconic(self, paths: list[str], *, subject: str) -> str | None:
        if not paths or not self.active:
            return None
        group = paths[:12]
        with tempfile.TemporaryDirectory(prefix="sof-pick-") as td:
            sheet = str(Path(td) / "pick.jpg")
            kept = contact_sheet(group, sheet, cols=4, tile=384)
            if not kept:
                return None
            data = self.ask(SHEET_NOTE.format(n=len(kept)) + PICK_PROMPT.format(n=len(kept), subject=subject), sheet)
        try:
            best = int((data or {}).get("best"))
        except (TypeError, ValueError):
            return None
        return kept[best - 1] if 1 <= best <= len(kept) else None

    def confirm_thumbnail(self, path: str, *, subject: str) -> dict | None:
        if not path or not self.active:
            return None
        with tempfile.TemporaryDirectory(prefix="sof-confirm-") as td:
            small = _small_copy(path, str(Path(td) / "thumb.jpg"), 1024)
            data = self.ask(CONFIRM_PROMPT.format(subject=subject), small)
        if data is None:
            return None
        where = str(data.get("where") or "center").lower()
        out = {"ok": bool(data.get("ok")), "where": where if where in ("left", "center", "right") else "center"}
        log.info("thumbnail check %s: %s, subject %s (%s)", Path(path).name, "ok" if out["ok"] else "rejected",
                 out["where"], data.get("why"))
        return out
