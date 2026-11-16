"""支援 `python -m fb_robot`，預設等同 `fb-robot run`（持續同步）。"""

import sys

from fb_robot.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["run"]))
