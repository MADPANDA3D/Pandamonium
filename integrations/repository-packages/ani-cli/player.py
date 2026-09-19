#!/usr/bin/env python3
import json
import os
import pathlib
import sys
import time

pathlib.Path(os.environ["PANDAMONIUM_PLAYBACK_CAPTURE"]).write_text(json.dumps(sys.argv[1:]))
time.sleep(float(os.environ.get("PANDAMONIUM_PLAYER_HOLD", "0")))
