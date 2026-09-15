#!/bin/sh
# Disposable mount namespace; production uses a persistent dedicated filesystem.
set -eu
cd "$(dirname "$0")/.."
exec unshare --user --map-root-user --mount sh -c '
  set -eu
  runtime_dir=$(mktemp -d /tmp/pandamonium-runtime-check.XXXXXX)
  trap '\''umount "$runtime_dir"; rmdir "$runtime_dir"'\'' EXIT
  mount -t tmpfs -o size=2g,nr_inodes=100000 tmpfs "$runtime_dir"
  export ODYSSEUS_EXTENSION_RUNTIME_ROOT="$runtime_dir"
  "$@"
' sh "${@:-.venv/bin/python}"
