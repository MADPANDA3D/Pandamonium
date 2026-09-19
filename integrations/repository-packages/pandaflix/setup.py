"""Build the pinned source with a checksum-verified private Go toolchain."""
import hashlib
import io
import os
import platform
import subprocess
import tarfile
import urllib.request
from pathlib import Path


def _extract_archive(archive, root):
    for member in archive.getmembers():
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
            raise RuntimeError("Go archive contains an unsafe member")
    archive.extractall(root)


def main():
    if platform.machine() != "x86_64":
        raise RuntimeError("This prepared package currently requires Linux x86_64")
    compiler = Path("/runtime/toolchain/go/bin/go")
    if not compiler.exists():
        with urllib.request.urlopen("https://go.dev/dl/go1.26.0.linux-amd64.tar.gz", timeout=45) as response:
            content = response.read(70 * 1024 * 1024)
        if hashlib.sha256(content).hexdigest() != "aac1b08a0fb0c4e0a7c1555beb7b59180b05dfc5a3d62e40e9de90cd42f88235":
            raise RuntimeError("Go archive digest mismatch")
        root = Path("/runtime/toolchain")
        root.mkdir(exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            _extract_archive(archive, root)
    env = {**os.environ, "GOMAXPROCS": "2", "GOTOOLCHAIN": "local", "GOCACHE": "/runtime/cache/go-build", "GOPATH": "/runtime/go-modules"}
    subprocess.run([str(compiler), "build", "-p=2", "-o", "/runtime/bin/pandaflix-agent", "/package/.pandamonium/main.go"],
                   env=env, check=True, timeout=240)


if __name__ == "__main__":
    main()
