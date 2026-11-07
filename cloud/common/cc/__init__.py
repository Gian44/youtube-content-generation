"""Shared pieces for the per-channel cloud pipelines that are not sleep-specific: stock b-roll selection with a
vision check (``cc.broll``) and segment-based composition of clips, photos and cards under narration
(``cc.segments``). Everything else comes from ``cloud/sleep-on-facts/sof`` (put on ``sys.path`` here)."""

from __future__ import annotations

import sys
from pathlib import Path

SOF_ROOT = Path(__file__).resolve().parents[2] / "sleep-on-facts"
if str(SOF_ROOT) not in sys.path:
    sys.path.insert(0, str(SOF_ROOT))
