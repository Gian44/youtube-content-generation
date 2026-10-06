"""Entry point: python run.py --minutes 180 [--topic "Whales"] [--privacy public] [--skip-upload]"""

import sys

from sof.config import Config, parse_args
from sof.pipeline import run

if __name__ == "__main__":
    ns = parse_args()
    cfg = Config.from_env(minutes=ns.minutes, topic=ns.topic, privacy=ns.privacy,
                          skip_upload=ns.skip_upload, work_dir=ns.work_dir)
    sys.exit(run(cfg))
