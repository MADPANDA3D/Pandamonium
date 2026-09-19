#!/usr/bin/env python3
import json
import os
import pathlib
import sys

state_path = pathlib.Path(os.environ["PANDAMONIUM_SELECTOR_STATE"])
state = json.loads(state_path.read_text())
items = [line.rstrip("\n") for line in sys.stdin if line.rstrip("\n")]
index = state["index"]
if state.get("capture_at") == index:
    pathlib.Path(os.environ["PANDAMONIUM_SELECTOR_CAPTURE"]).write_text(json.dumps({"prompt": " ".join(sys.argv[1:]), "items": items}))
    raise SystemExit(1)
desired = state.get("plan", [])[index] if index < len(state.get("plan", [])) else None
state["index"] = index + 1
state_path.write_text(json.dumps(state))
if "playback" in " ".join(sys.argv[1:]).lower():
    desired = "Quit"
choice = next((item for item in items if item == str(desired)), items[0] if items else "")
if choice:
    print(choice)
else:
    raise SystemExit(1)
