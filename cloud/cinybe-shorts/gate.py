"""Workflow gate (standard library only, runs before any install): prints ``go=true|false`` for $GITHUB_OUTPUT.

Scheduled triggers run several times a day; only the first one after 09:00 UTC on a day without a recap goes on to
render (cny/daily.py). Manual runs always go on (the pipeline itself still refuses a second recap the same day unless
a film is forced).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cny import daily  # noqa: E402  (cny/__init__ only edits sys.path)

scheduled = os.environ.get("GITHUB_EVENT_NAME", "") == "schedule"
why = daily.hold(daily.load_rows(Path(__file__).resolve().parent / "ledger.json"), scheduled=True,
                 post_from_hour=int(os.environ.get("POST_FROM_HOUR", "9"))) if scheduled else None
print(f"::notice title=cinybe: nothing to do::{why}" if why else "go: today's recap is due", flush=True)
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"go={'false' if why else 'true'}\n")
