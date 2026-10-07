"""The committed ledger of uploaded videos (ledger.json)."""

from __future__ import annotations

import json
import os
import subprocess


def load_ledger(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f) or []


def append_entry(path: str, entry: dict) -> dict:
    rows = load_ledger(path)
    rows.append(entry)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return entry


def commit_message(entry: dict) -> str:
    return f"ledger: {entry.get('date')} {entry.get('topic')} ({entry.get('video_id')})"


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def git_commit_and_push(path: str, entry: dict, *, attempts: int = 3, run=None) -> None:
    """Used in the workflow; fails loudly so the run goes red if the ledger can't be saved.

    The video is already public by the time this runs, so losing the row would make the topic
    picker repeat the subject tomorrow. If someone pushed meanwhile — including a conflicting
    edit of ledger.json — we take the remote ledger, re-append this entry on top, and push again.
    """
    git = run or _git
    git("config", "user.name", "sleep-on-facts-bot")
    git("config", "user.email", "actions@users.noreply.github.com")
    for attempt in range(attempts):
        git("add", path)
        git("commit", "-m", commit_message(entry))
        if git("pull", "--rebase", check=False).returncode != 0:
            git("rebase", "--abort", check=False)
            git("fetch", "origin")
            git("reset", "--hard", "origin/HEAD")
            rows = [r for r in load_ledger(path) if r.get("video_id") != entry.get("video_id")]
            rows.append(entry)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, ensure_ascii=False)
                f.write("\n")
            continue
        if git("push", check=False).returncode == 0:
            return
        git("reset", "--soft", "HEAD~1", check=False)      # keep the change, retry on fresh remote
    raise RuntimeError(f"ledger push failed after {attempts} attempts")
