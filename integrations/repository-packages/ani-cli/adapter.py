import json
import os
import pathlib
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

TOOL = 'ani_cli__nextep_countdown'
PATH = '/runtime/bin:/usr/bin:/bin'
LIMIT = 131072
TIMEOUT = 45


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def run_bounded(argv, env):
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd='/package', env=env,
                               shell=False, start_new_session=True)
    selector = selectors.DefaultSelector()
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    total = 0
    deadline = time.monotonic() + TIMEOUT
    try:
        selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
        selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Upstream operation timed out')
            for key, _ in selector.select(min(remaining, 0.2)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > LIMIT:
                    raise RuntimeError('Upstream output exceeded limit')
                buffers[key.data].extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('Upstream operation timed out')
        code = process.wait(timeout=remaining)
        if code != 0:
            raise RuntimeError('Upstream operation failed with exit code ' + str(code))
        return bytes(buffers['stdout']).decode('utf-8', errors='replace')
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
        process.stdout.close()
        process.stderr.close()


def main():
    if len(sys.argv) != 2 or sys.argv[1] != TOOL:
        raise ValueError('Unknown tool name')
    raw = sys.stdin.buffer.read(4097)
    if len(raw) > 4096:
        raise ValueError('Input exceeds limit')
    args = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(args, dict) or set(args) != {'query'}:
        raise ValueError('Expected exactly the query argument')
    query = args['query']
    if not isinstance(query, str) or not 1 <= len(query) <= 200:
        raise ValueError('query must be a string of 1 to 200 characters')
    if query != query.strip() or not query[0].isalnum():
        raise ValueError('query must start with a letter or digit and have no outer whitespace')
    if any(not (char.isalnum() or char in " .:'!?()-") for char in query):
        raise ValueError('query contains unsupported characters')
    root = pathlib.Path('/runtime/ani-cli')
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='check-', dir=root) as state:
        home = pathlib.Path(state, 'home')
        cache = pathlib.Path(state, 'cache')
        home.mkdir()
        cache.mkdir()
        env = {
            'PATH': PATH,
            'HOME': str(home),
            'XDG_CACHE_HOME': str(cache),
            'XDG_STATE_HOME': str(pathlib.Path(state, 'state')),
            'TMPDIR': state,
            'LANG': 'C.UTF-8',
            'LC_ALL': 'C.UTF-8',
            'TERM': 'dumb',
            'ANI_CLI_PLAYER': 'debug',
            'ANI_CLI_MENU': '/bin/true',
            'ANI_CLI_LOG': '0',
            'ANI_CLI_HIST_DIR': str(pathlib.Path(state, 'history')),
            'ANI_CLI_DEFAULT_SOURCE': 'search',
        }
        output = run_bounded(['/bin/sh', '/package/ani-cli', '--nextep-countdown', query], env)
    output = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', output)
    output = ''.join(char for char in output if char in '\n\t' or (ord(char) >= 32 and not 127 <= ord(char) <= 159))
    output = output.strip()
    if not all(re.search(r'^' + field + r': .+', output, flags=re.MULTILINE) for field in ('Eng', 'Jpn', 'Status')):
        raise RuntimeError('No recognizable release information returned; inspect provider availability and upstream parsing')
    if len(output) > LIMIT:
        raise RuntimeError('Result exceeds schema limit')
    print(json.dumps({'text': output}, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ani-cli adapter: ' + str(exc), file=sys.stderr)
        sys.exit(1)
