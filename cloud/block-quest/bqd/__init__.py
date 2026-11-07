"""Block Quest Daily cloud pipeline: original Reddit-style story Shorts over stock b-roll, on GitHub Actions.

Reuses ``cloud/sleep-on-facts/sof`` (writer, voice, footage, vision checks, no-AI-slop pass, uploader, ledger)
and ``cloud/common/cc`` (b-roll selection, segment composition) by import; both go on ``sys.path`` here.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CLOUD = Path(__file__).resolve().parents[2]
for _p in (_CLOUD / "sleep-on-facts", _CLOUD / "common"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
