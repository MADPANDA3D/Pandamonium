"""Build the pinned PandaFlix CLI in its private, architecture-matched runtime."""

import hashlib
import io
import os
import pathlib
import platform
import shutil
import subprocess
import tarfile
import urllib.request

GO = {
    "x86_64": ("amd64", "aac1b08a0fb0c4e0a7c1555beb7b59180b05dfc5a3d62e40e9de90cd42f88235"),
    "aarch64": ("arm64", "bd03b743eb6eb4193ea3c3fd3956546bf0e3ca5b7076c8226334afe6b75704cd"),
}


def _extract(content, root):
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        for member in archive.getmembers():
            path = pathlib.Path(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir() or member.issym()):
                raise RuntimeError("Go archive contains an unsafe member")
            if member.issym() and (pathlib.Path(member.linkname).is_absolute() or ".." in pathlib.Path(member.linkname).parts):
                raise RuntimeError("Go archive contains an unsafe link")
        archive.extractall(root)


def main():
    try:
        arch, expected = GO[platform.machine().lower()]
    except KeyError as exc:
        raise RuntimeError("PandaFlix supports Linux x86_64 and aarch64 containers") from exc
    compiler = pathlib.Path("/runtime/toolchain/go/bin/go")
    if not compiler.exists():
        url = f"https://go.dev/dl/go1.26.0.linux-{arch}.tar.gz"
        with urllib.request.urlopen(url, timeout=45) as response:
            content = response.read(70 * 1024 * 1024)
        if hashlib.sha256(content).hexdigest() != expected:
            raise RuntimeError("Go archive digest mismatch")
        root = pathlib.Path("/runtime/toolchain")
        root.mkdir(parents=True, exist_ok=True)
        _extract(content, root)
    bin_dir = pathlib.Path("/runtime/bin")
    bin_dir.mkdir(parents=True, exist_ok=True)
    for source, name in (("selector.py", "pandamonium-select"), ("player.py", "mpv")):
        destination = bin_dir / name
        shutil.copy2(pathlib.Path("/package/.pandamonium", source), destination)
        destination.chmod(0o700)
    env = {**os.environ, "GOMAXPROCS": "2", "GOTOOLCHAIN": "local",
           "GOCACHE": "/runtime/cache/go-build", "GOMODCACHE": "/runtime/go-modules"}
    subprocess.run([str(compiler), "build", "-trimpath", "-p=2", "-o", str(bin_dir / "pandaflix"), "/package"],
                   env=env, check=True, timeout=300)
    print(f"PandaFlix v1.2.1 private linux-{arch} runtime ready")


if __name__ == "__main__":
    main()
