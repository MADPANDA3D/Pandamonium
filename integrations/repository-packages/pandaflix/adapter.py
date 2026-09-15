"""Noninteractive calls into PandaFlix's real Go core and provider interfaces."""
import json
import re
import subprocess
import sys

args = json.load(sys.stdin)
if sys.argv[1] == "pandaflix__episode_range" and not re.fullmatch(r"[0-9]{1,3}(?:-[0-9]{1,3})?", args["s"]):
    raise ValueError("Use one episode or a range from 0 to 999")
result = subprocess.run(["/runtime/bin/pandaflix-agent", sys.argv[1]],
                        input=json.dumps(args), capture_output=True,
                        text=True, check=True, timeout=20)
print(result.stdout, end="")
