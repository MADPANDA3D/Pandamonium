import pathlib
import shutil
import sys

PATH = '/runtime/bin:/usr/bin:/bin'
DEPENDENCIES = ('curl', 'sed', 'grep', 'cut', 'uname', 'mkdir', 'tr', 'head', 'tput', 'rm')


def main():
    missing = [name for name in DEPENDENCIES if shutil.which(name, path=PATH) is None]
    for executable in ('/bin/sh', '/bin/true'):
        if not pathlib.Path(executable).is_file():
            missing.append(executable)
    if missing:
        raise RuntimeError('Needs setup: provide approved isolated-runner dependencies, then rerun setup: ' + ', '.join(missing))
    if not pathlib.Path('/package/ani-cli').is_file():
        raise RuntimeError('Pinned source /package/ani-cli is missing')
    for name in ('home', 'cache', 'ani-cli'):
        pathlib.Path('/runtime', name).mkdir(parents=True, exist_ok=True)
    print('Dependencies located; real operation validation is still required.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
