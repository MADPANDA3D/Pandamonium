"""Package-local yt-dlp adapter; source and dependencies stay pinned with this package."""
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, "/package")
from yt_dlp import YoutubeDL
from yt_dlp.extractor.generic import GenericIE

name = sys.argv[1]
args = json.load(sys.stdin)
url = args["url"]
parsed = urlparse(url)
if parsed.scheme not in {"http", "https", "file"} or parsed.username or parsed.password:
    raise ValueError("Use a media URL without credentials")
if parsed.scheme == "file" and not Path(parsed.path).resolve().is_relative_to(Path("/runtime")):
    raise ValueError("Local inputs must be in this plugin's private runtime")
download = name == "yt_dlp__download_media"
if name not in {"yt_dlp__inspect_media", "yt_dlp__download_media"}:
    raise ValueError("Unknown tool")
out = Path("/runtime/artifacts")
out.mkdir(exist_ok=True)
with YoutubeDL({
    "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
    "socket_timeout": 8, "retries": 0, "extractor_retries": 0,
    "enable_file_urls": parsed.scheme == "file", "max_filesize": 16 * 1024 * 1024,
    "outtmpl": str(out / "%(id)s.%(ext)s"), "restrictfilenames": True,
}, auto_init=False) as client:
    # This package supports direct media; optional provider modules are excluded.
    client.add_info_extractor(GenericIE())
    info = client.extract_info(url, download=download)
    if not info or info.get("_type") == "playlist":
        raise ValueError("A single media item is required")
    result = {"id": str(info["id"]), "title": str(info.get("title") or ""), "extension": str(info.get("ext") or "")}
    if download:
        artifact = Path(client.prepare_filename(info)).resolve()
        if not artifact.is_relative_to(out) or not artifact.is_file():
            raise ValueError("Download did not produce the requested bounded artifact")
        result.update({"artifact": artifact.relative_to(Path("/runtime")).as_posix(), "bytes": artifact.stat().st_size})
    print(json.dumps(result))
