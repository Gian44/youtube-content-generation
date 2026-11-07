"""CinybeShorts cloud pipeline: narrated movie recaps from the Wikipedia plot, on GitHub Actions.

The package reuses the Sleep On Facts modules (``cloud/sleep-on-facts/sof``: writer, voice, footage, vision
checks, no-AI-slop pass, uploader, ledger) and the shared cloud helpers (``cloud/common/cc``: b-roll selection,
segment composition) by importing them, not copying them, so one fix lands in every channel. Both are put on
``sys.path`` here; nothing in ``sof`` is modified.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CLOUD = Path(__file__).resolve().parents[2]
for _p in (_CLOUD / "sleep-on-facts", _CLOUD / "common"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
