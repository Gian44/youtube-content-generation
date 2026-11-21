"""Sleep On Facts — cloud pipeline package."""

import sys
from pathlib import Path

# cloud/common (package ``cc``): helpers shared by every channel, e.g. the on-screen text budget (cc.textbudget)
_COMMON = Path(__file__).resolve().parents[2] / "common"
if _COMMON.is_dir() and str(_COMMON) not in sys.path:
    sys.path.insert(0, str(_COMMON))
