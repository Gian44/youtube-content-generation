"""Launcher for Windows Task Scheduler ("sylusnix daily"). Runs `run.py auto` with no window and appends everything
it prints to work/logs/auto-<date>.log. pythonw has no console; the child gets a hidden one, which ffmpeg, yt-dlp and
the Claude CLI then share, so nothing flashes on screen.

  pythonw auto_task.pyw            # the daily job
  pythonw auto_task.pyw check      # health check only (same log)"""
import datetime
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
logs = root / "work" / "logs"
logs.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:] or ["auto"]
with open(logs / f"auto-{datetime.date.today().isoformat()}.log", "a", encoding="utf-8") as log:
    log.write(f"\n=== {datetime.datetime.now().isoformat(timespec='seconds')}  run.py {' '.join(args)} ===\n")
    log.flush()
    code = subprocess.run([str(root / ".venv" / "Scripts" / "python.exe"), "-u", "run.py", *args], cwd=root,
                          stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                          creationflags=subprocess.CREATE_NO_WINDOW).returncode
    log.write(f"=== exit {code} at {datetime.datetime.now().isoformat(timespec='seconds')} ===\n")
sys.exit(code)
