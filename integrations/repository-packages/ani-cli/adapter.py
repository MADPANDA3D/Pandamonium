"""Structured web operations over the pinned, unmodified ani-cli script."""

import json
import os
import pathlib
import pty
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

LIMIT = 64 * 1024
TOOLS = {
    "ani_cli__status", "ani_cli__search", "ani_cli__episodes",
    "ani_cli__resolve", "ani_cli__history", "ani_cli__continue",
}


def _json():
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    raw = sys.stdin.buffer.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Input exceeds limit")
    value = json.loads(raw or b"{}", object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def _text(value, name, maximum=300):
    if not isinstance(value, str) or value != value.strip() or not 1 <= len(value) <= maximum or "\0" in value:
        raise ValueError(f"Invalid {name}")
    return value


def _run(argv, env, timeout=25):
    master, slave = pty.openpty()
    process = subprocess.Popen(argv, stdin=slave, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd="/package", env=env,
                               shell=False, start_new_session=True)
    os.close(slave)
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for stream in streams:
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Upstream operation timed out")
                for key, _ in selector.select(0.2):
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        streams[key.fileobj].extend(chunk)
                        if sum(map(len, streams.values())) > LIMIT:
                            raise RuntimeError("Upstream output exceeded limit")
        return process.wait(timeout=1), streams[process.stdout].decode(errors="replace"), streams[process.stderr].decode(errors="replace")
    finally:
        os.close(master)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close(); process.stderr.close()


def _environment(work, plan=None, capture_at=None, hold="0"):
    selector = pathlib.Path(work, "selector.json")
    selector.write_text(json.dumps({"index": 0, "plan": plan or [], "capture_at": capture_at}))
    return {
        "PATH": "/runtime/bin:/usr/bin:/bin", "HOME": "/runtime/home",
        "XDG_CACHE_HOME": "/runtime/cache", "XDG_STATE_HOME": "/runtime/home/.local/state",
        "TMPDIR": work, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "dumb",
        "ANI_CLI_PLAYER": "/runtime/bin/pandamonium-mpv",
        "ANI_CLI_MENU": "/runtime/bin/pandamonium-select", "ANI_CLI_LOG": "0",
        "ANI_CLI_HIST_DIR": "/runtime/home/.local/state/ani-cli", "ANI_CLI_NO_DETACH": "1",
        "PANDAMONIUM_SELECTOR_STATE": str(selector),
        "PANDAMONIUM_SELECTOR_CAPTURE": str(pathlib.Path(work, "capture.json")),
        "PANDAMONIUM_PLAYBACK_CAPTURE": str(pathlib.Path(work, "playback.json")),
        "PANDAMONIUM_PLAYER_HOLD": hold,
    }


def _capture(work, detail=""):
    path = pathlib.Path(work, "capture.json")
    if not path.exists():
        raise RuntimeError("Upstream did not expose the requested selection: " + detail[-1000:])
    return json.loads(path.read_text())["items"][:5000]


def _playback(work):
    path = pathlib.Path(work, "playback.json")
    if not path.exists():
        raise RuntimeError("Upstream did not resolve playback")
    argv = json.loads(path.read_text())
    urls = [value for value in argv if value.startswith(("http://", "https://"))]
    if not urls:
        raise RuntimeError("Upstream returned no playback URL")
    def option(prefix):
        return next((value[len(prefix):] for value in argv if value.startswith(prefix)), "")
    referer = option("--referrer=")
    title = option("--force-media-title=").removeprefix("Playing ") or "Anime"
    return {
        "title": title[:300], "url": urls[-1], "headers": {"Referer": referer} if referer else {},
        "subtitles": [{"url": value[len("--sub-file="):], "label": "English", "language": "en"}
                      for value in argv if value.startswith("--sub-file=")],
    }


def _base_args(args, *, episode=False):
    allowed = {"query", "selection_index", "dub"} | ({"episode", "quality"} if episode else {"offset"})
    if set(args) != allowed:
        raise ValueError("Unexpected arguments")
    query = _text(args["query"], "query", 200)
    index, dub = args["selection_index"], args["dub"]
    if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= 50 or not isinstance(dub, bool):
        raise ValueError("Invalid selection")
    argv = ["/bin/sh", "/package/ani-cli", "-S", str(index)]
    if dub:
        argv.append("--dub")
    if episode:
        argv += ["-e", _text(args["episode"], "episode", 16), "-q", _text(args["quality"], "quality", 8)]
    argv.append(query)
    return argv


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in TOOLS:
        raise ValueError("Unknown tool name")
    tool, args = sys.argv[1], _json()
    if tool == "ani_cli__status":
        if args:
            raise ValueError("Unexpected arguments")
        code, output, _ = _run(["/bin/sh", "/package/ani-cli", "--version"], _environment("/runtime/cache"), 10)
        if code:
            raise RuntimeError("ani-cli version check failed")
        result = {"version": output.strip()[:40]}
    elif tool == "ani_cli__history":
        if args:
            raise ValueError("Unexpected arguments")
        rows = []
        root = pathlib.Path("/runtime/home/.local/state/ani-cli")
        for path in [root / "ani-hsts"] if root.exists() else []:
            if not path.is_file() or path.stat().st_size > LIMIT:
                continue
            for line in path.read_text(errors="replace").splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    rows.append({"index": len(rows) + 1, "episode": parts[0][:16], "title": parts[2][:300]})
        result = {"items": rows[:100]}
    else:
        pathlib.Path("/runtime/cache").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="ani-web-", dir="/runtime/cache") as work:
            if tool == "ani_cli__search":
                if set(args) != {"query", "dub"} or not isinstance(args["dub"], bool):
                    raise ValueError("Unexpected arguments")
                query = _text(args["query"], "query", 200)
                argv = ["/bin/sh", "/package/ani-cli", *(["--dub"] if args["dub"] else []), query]
                _, _, detail = _run(argv, _environment(work, capture_at=0))
                items = []
                for line in _capture(work, detail):
                    match = re.match(r"^(\d+)\s+(.+)$", line)
                    if match:
                        items.append({"id": int(match.group(1)), "title": match.group(2)[:300]})
                result = {"items": items[:50]}
            elif tool == "ani_cli__episodes":
                offset = args["offset"]
                if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 5000:
                    raise ValueError("Invalid episode offset")
                _, _, detail = _run(_base_args(args), _environment(work, capture_at=0))
                values = _capture(work, detail)
                page = values[offset:offset + 100]
                result = {"items": [{"number": value[:16], "label": f"Episode {value[:16]}"} for value in page],
                          "total": len(values), "next_offset": offset + 100 if offset + 100 < len(values) else -1}
            elif tool == "ani_cli__resolve":
                _run(_base_args(args, episode=True), _environment(work, plan=["quit"]))
                result = _playback(work)
            else:
                if set(args) != {"history_index", "dub", "quality"}:
                    raise ValueError("Unexpected arguments")
                index, dub = args["history_index"], args["dub"]
                if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= 100 or not isinstance(dub, bool):
                    raise ValueError("Invalid history selection")
                argv = ["/bin/sh", "/package/ani-cli", "--continue", "-S", str(index), "-q", _text(args["quality"], "quality", 8)]
                if dub:
                    argv.append("--dub")
                _run(argv, _environment(work, plan=["quit"]))
                result = _playback(work)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ani-cli adapter: " + str(exc), file=sys.stderr)
        sys.exit(1)
