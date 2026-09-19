"""Prepare ani-cli's dependencies inside its private Pandamonium runtime."""

import pathlib
import shutil

DEPENDENCIES = ("curl", "sed", "grep", "cut", "uname", "mkdir", "tr", "head", "tail", "tput", "rm", "cat", "wc", "nl", "sort", "base64", "od", "cp", "mv", "sleep")


def main():
    missing = [name for name in DEPENDENCIES if shutil.which(name, path="/usr/bin:/bin") is None]
    if missing or not pathlib.Path("/package/ani-cli").is_file():
        raise RuntimeError("Missing pinned ani-cli or container dependencies: " + ", ".join(missing))
    target = pathlib.Path("/runtime/bin")
    target.mkdir(parents=True, exist_ok=True)
    for source, name in (("selector.py", "pandamonium-select"), ("player.py", "pandamonium-mpv")):
        destination = target / name
        shutil.copy2(pathlib.Path("/package/.pandamonium", source), destination)
        destination.chmod(0o700)
    for name in ("home", "cache"):
        pathlib.Path("/runtime", name).mkdir(parents=True, exist_ok=True)
    print("ani-cli 5.1.2 private runtime ready")


if __name__ == "__main__":
    main()
