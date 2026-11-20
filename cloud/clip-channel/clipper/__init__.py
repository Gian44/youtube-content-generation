"""Clip channel pipeline: finder + editor.

The shared cloud helpers (``cloud/common/cc``: on-screen text budget, quiet sound effects, emoji icons) are put on
``sys.path`` here and imported, not copied, so one fix lands in every channel."""
import sys
from pathlib import Path

_COMMON = Path(__file__).resolve().parents[2] / "common"
if str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))
