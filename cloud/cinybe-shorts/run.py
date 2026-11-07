"""Entry point: python run.py --minutes 10 [--film "Inception" --year 2010] [--privacy unlisted] [--skip-upload]"""

import sys

import cny  # noqa: F401  — puts cloud/sleep-on-facts on sys.path
from cny.config import Config, parse_args
from cny.pipeline import run

if __name__ == "__main__":
    ns = parse_args()
    cfg = Config.from_env(minutes=ns.minutes, film=ns.film or None, year=ns.year, privacy=ns.privacy,
                          skip_upload=ns.skip_upload, work_dir=ns.work_dir)
    sys.exit(run(cfg, shorts_enabled=not ns.no_shorts))
