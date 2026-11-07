"""Entry point: python run.py [--count 3] [--category revenge] [--privacy unlisted] [--smoke] [--skip-upload]"""

import sys

import bqd  # noqa: F401  — puts cloud/sleep-on-facts and cloud/common on sys.path
from bqd.config import Config, parse_args
from bqd.pipeline import run

if __name__ == "__main__":
    ns = parse_args()
    cfg = Config.from_env(count=ns.count, category=ns.category or None, privacy=ns.privacy, smoke=ns.smoke,
                          skip_upload=ns.skip_upload, work_dir=ns.work_dir)
    sys.exit(run(cfg))
