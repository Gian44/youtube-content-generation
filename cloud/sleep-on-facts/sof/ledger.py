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


def git_commit_and_push(path: str, entry: dict) -> None:
    """Used in the workflow; fails loudly so the run goes red if the ledger can't be saved."""
    subprocess.run(["git", "config", "user.name", "sleep-on-facts-bot"], check=True)
    subprocess.run(["git", "config", "user.email", "actions@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", path], check=True)
    subprocess.run(["git", "commit", "-m", commit_message(entry)], check=True)
    subprocess.run(["git", "pull", "--rebase"], check=True)
    subprocess.run(["git", "push"], check=True)
