#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
from urllib.parse import urlencode

SOCKET_PATH = "/run/jarvis-vps-observer/observer.sock"


def request(action: str, target: str = "", lines: int = 80) -> dict:
    query = urlencode({key: value for key, value in {"target": target, "lines": lines}.items() if value})
    path = f"/v1/observe/{action}" + (f"?{query}" if query else "")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(15)
        connection.connect(SOCKET_PATH)
        connection.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
        chunks = []
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    _headers, _separator, body = raw.partition(b"\r\n\r\n")
    return json.loads(body or b"{}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Jarvis VPS observer")
    parser.add_argument("action", choices=("health", "resources", "ports", "services", "journal", "containers", "nginx", "deployments"))
    parser.add_argument("--target", default="")
    parser.add_argument("--lines", type=int, default=80)
    args = parser.parse_args()
    print(json.dumps(request(args.action, args.target, args.lines), indent=2))


if __name__ == "__main__":
    main()
