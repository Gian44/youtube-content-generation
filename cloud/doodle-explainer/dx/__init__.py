"""Doodle explainer channel (sylusnix, re-launched 2026-11-26): original stick-figure explainers answering questions
everyone has — long videos and Shorts, written by Claude, drawn as SVG, narrated by Kokoro, on GitHub Actions.

Reuses ``cloud/sleep-on-facts/sof`` (writer, voice, uploader, ledger) and ``cloud/common/cc`` by import."""

from __future__ import annotations

import sys
from pathlib import Path

_CLOUD = Path(__file__).resolve().parents[2]
for _p in (_CLOUD / "sleep-on-facts", _CLOUD / "common"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
