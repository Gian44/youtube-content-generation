"""StoryFactory Worker - Automated YouTube content generation engine."""

import sys

# Reconfigure stdout/stderr to UTF-8 on Windows to prevent UnicodeEncodeError in legacy consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

__version__ = "1.0.0"
