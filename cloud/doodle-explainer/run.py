"""Entry point: python run.py --kind short|long [--count 2] [--question "..."] [--force] [--smoke] [--skip-upload]"""

import sys

import dx  # noqa: F401  — puts cloud/sleep-on-facts and cloud/common on sys.path
from dx.config import Config, parse_args
from dx.pipeline import run

if __name__ == "__main__":
    ns = parse_args()
    cfg = Config.from_env(kind=ns.kind, count=ns.count, question=ns.question or None, force=ns.force, privacy=ns.privacy,
                          smoke=ns.smoke, skip_upload=ns.skip_upload, work_dir=ns.work_dir)
    sys.exit(run(cfg))
