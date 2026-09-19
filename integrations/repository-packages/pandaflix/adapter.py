"""Structured web operations over the pinned, unmodified PandaFlix CLI."""

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
    "pandaflix__status", "pandaflix__search", "pandaflix__seasons",
    "pandaflix__episodes", "pandaflix__resolve",
}
CONFIG = "fzf_path: /runtime/bin/pandamonium-select\nplayer: mpv\nprovider: cinejoy\nquality: best\nminimize_on_play: false\nauto_next: false\nprefetch: false\nsubtitle_source: auto\n"


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
    process = subprocess.Popen(argv, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd="/package", env=env, shell=False, start_new_session=True)
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


def _environment(work, plan=None, capture_at=None):
    config = pathlib.Path("/runtime/home/.config/pandaflix/config.yaml")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(CONFIG)
    selector = pathlib.Path(work, "selector.json")
    selector.write_text(json.dumps({"index": 0, "plan": plan or [], "capture_at": capture_at}))
    return {
        "PATH": "/runtime/bin:/usr/bin:/bin", "HOME": "/runtime/home",
        "XDG_CACHE_HOME": "/runtime/cache", "XDG_CONFIG_HOME": "/runtime/home/.config",
        "TMPDIR": work, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "xterm",
        "PANDAMONIUM_SELECTOR_STATE": str(selector),
        "PANDAMONIUM_SELECTOR_CAPTURE": str(pathlib.Path(work, "capture.json")),
        "PANDAMONIUM_PLAYBACK_CAPTURE": str(pathlib.Path(work, "playback.json")),
        "PANDAMONIUM_PLAYER_HOLD": "10",
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
    headers = {}
    for key, prefix in (("Referer", "--referrer="), ("User-Agent", "--user-agent=")):
        if option(prefix):
            headers[key] = option(prefix)
    origin = option("--http-header-fields=Origin: ")
    if origin:
        headers["Origin"] = origin
    title = option("--force-media-title=").removeprefix("Playing ") or "Movies & Shows"
    return {
        "title": title[:300], "url": urls[0], "headers": headers,
        "subtitles": [{"url": value[len("--sub-file="):], "label": "Subtitles", "language": "en"}
                      for value in argv if value.startswith("--sub-file=")],
    }


def _selection_args(args, allowed):
    if set(args) != allowed:
        raise ValueError("Unexpected arguments")
    return _text(args["query"], "query", 200), _text(args["selection"], "selection", 400)


def _number(value, name, maximum=1000):
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"Invalid {name}")
    return value


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in TOOLS:
        raise ValueError("Unknown tool name")
    tool, args = sys.argv[1], _json()
    if tool == "pandaflix__status":
        if args:
            raise ValueError("Unexpected arguments")
        code, output, _ = _run(["/runtime/bin/pandaflix", "--version"], _environment("/runtime/cache"), 10)
        if code:
            raise RuntimeError("PandaFlix version check failed")
        result = {"version": re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output).strip()[-80:]}
    else:
        pathlib.Path("/runtime/cache").mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pandaflix-web-", dir="/runtime/cache") as work:
            base = ["/runtime/bin/pandaflix", "--show-image=false"]
            if tool == "pandaflix__search":
                if set(args) != {"query"}:
                    raise ValueError("Unexpected arguments")
                query = _text(args["query"], "query", 200)
                _, _, detail = _run([*base, query], _environment(work, capture_at=0))
                items = []
                for label in _capture(work, detail):
                    match = re.match(r"^\[(movie|series)\]\s+(.+)$", label, re.I)
                    if match:
                        items.append({"selection": label[:400], "kind": match.group(1).lower(), "title": match.group(2)[:300]})
                result = {"items": items[:50]}
            elif tool == "pandaflix__seasons":
                query, selection = _selection_args(args, {"query", "selection"})
                _, _, detail = _run([*base, query], _environment(work, [selection], 1))
                result = {"items": [{"number": index + 1, "label": label[:300]} for index, label in enumerate(_capture(work, detail))]}
            elif tool == "pandaflix__episodes":
                query, selection = _selection_args(args, {"query", "selection", "season", "offset"})
                season = _number(args["season"], "season", 100)
                offset = args["offset"]
                if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 5000:
                    raise ValueError("Invalid episode offset")
                _, _, detail = _run([*base, "--season", str(season), query], _environment(work, [selection], 1))
                values = _capture(work, detail)
                page = values[offset:offset + 100]
                result = {"items": [{"number": offset + index + 1, "label": label[:300]} for index, label in enumerate(page)],
                          "total": len(values), "next_offset": offset + 100 if offset + 100 < len(values) else -1}
            else:
                query, selection = _selection_args(args, {"query", "selection", "kind", "season", "episode"})
                kind = args["kind"]
                if kind not in {"movie", "series"}:
                    raise ValueError("Invalid media kind")
                argv = [*base, "--action", "play", "--best"]
                if kind == "series":
                    argv += ["--season", str(_number(args["season"], "season", 100)),
                             "--episodes", str(_number(args["episode"], "episode"))]
                elif args["season"] != 0 or args["episode"] != 0:
                    raise ValueError("Movies do not have seasons or episodes")
                _run([*argv, query], _environment(work, [selection]))
                result = _playback(work)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("PandaFlix adapter: " + str(exc), file=sys.stderr)
        sys.exit(1)
