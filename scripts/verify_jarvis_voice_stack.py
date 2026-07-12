#!/usr/bin/env python3
"""Read-only health verification for the deployed Jarvis voice stack."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:7000")
    parser.add_argument("--session-token-file")
    parser.add_argument("--sessions-file")
    parser.add_argument("--username", default="leo")
    parser.add_argument("--require-workers", action="store_true")
    return parser.parse_args()


def _token(args: argparse.Namespace) -> str:
    supplied = os.getenv("ODYSSEUS_SESSION_TOKEN", "").strip()
    if supplied:
        return supplied
    if args.session_token_file:
        return Path(args.session_token_file).read_text(encoding="utf-8").strip()
    if args.sessions_file:
        sessions = json.loads(Path(args.sessions_file).read_text(encoding="utf-8"))
        valid = [
            token
            for token, row in sessions.items()
            if row.get("username") == args.username and float(row.get("expiry") or 0) > time.time()
        ]
        if valid:
            return valid[0]
    raise RuntimeError("No valid Odysseus session token was supplied")


def _get(base_url: str, path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Cookie": f"odysseus_session={token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{path} returned HTTP {exc.code}") from exc


def main() -> None:
    args = _args()
    token = _token(args)
    voice = _get(args.base_url, "/api/voice/status", token)
    stt = _get(args.base_url, "/api/stt/stats", token)
    tts = _get(args.base_url, "/api/tts/stats", token)
    runtime = _get(args.base_url, "/api/runtime/status", token)
    workers = _get(args.base_url, "/api/agent-workers", token)

    checks = {
        "raw_audio_disabled": voice.get("stores_raw_audio") is False,
        "stt_ready": bool(stt.get("available")),
        "tts_ready": bool(tts.get("ready") or tts.get("available")),
        "brain_identified": bool(runtime.get("brain_model")),
        "workers_connected": all(
            row.get("enabled") and (row.get("connection") or {}).get("state") == "connected"
            for row in workers.values()
        ),
    }
    required = ["raw_audio_disabled", "stt_ready", "tts_ready", "brain_identified"]
    if args.require_workers:
        required.append("workers_connected")
    failed = [name for name in required if not checks[name]]
    print(json.dumps({
        "ok": not failed,
        "checks": checks,
        "voice_model": voice.get("voice_model"),
        "stt": {"provider": stt.get("provider"), "model": stt.get("model"), "language": stt.get("language")},
        "brain": {
            "model": runtime.get("brain_model"),
            "architecture": runtime.get("architecture"),
            "quantization": runtime.get("quantization"),
            "context": runtime.get("context"),
        },
        "tts": {"provider": tts.get("provider"), "model": tts.get("model"), "voice": tts.get("voice")},
        "workers": {
            name: {"enabled": row.get("enabled"), "state": (row.get("connection") or {}).get("state")}
            for name, row in workers.items()
        },
        "failed": failed,
    }, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
