"""Governed Android emulator/ADB agent tool (MAD-838).

The agent names one explicit action and typed arguments; ``src.android_emulator``
validates them, builds the argv itself, and runs it under time/output bounds.
There is no arbitrary adb shell, no host shell, and no destructive device
operation in this surface. Every result carries a target citation and the
effective limits so the model can report evidence honestly.
"""

from __future__ import annotations

import json

from src import android_emulator as android


class AndroidDeviceTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        raw = str(content or "").strip()
        args = None
        if raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
        if not isinstance(args, dict):
            return {
                "error": (
                    "android_device arguments must be a JSON object, e.g. "
                    '{"action": "devices"} or '
                    '{"action": "install", "serial": "emulator-5554", "apk": "/abs/app.apk"}.'
                ),
                "exit_code": 1,
                "code": "invalid_arguments",
            }

        action = str(args.get("action") or "status").strip().lower()
        if action not in android.ACTIONS:
            return {
                "error": "The Android action must be one of: " + ", ".join(android.ACTIONS) + ".",
                "exit_code": 1,
                "code": "invalid_action",
            }

        ctx = ctx or {}
        owner = str(ctx.get("owner") or "") or None
        progress_cb = ctx.get("progress_cb")
        try:
            result = await android.execute_android_operation(
                action,
                args,
                owner=owner,
                progress_cb=progress_cb,
            )
        except android.AndroidAgentError as exc:
            return {"error": exc.message, "exit_code": 1, "code": exc.code}
        except Exception:
            return {
                "error": "The Android operation failed before it could complete.",
                "exit_code": 1,
                "code": "operation_failed",
            }

        payload = {
            "output": result.get("output", ""),
            "exit_code": 0,
            "state": result.get("state", "ok"),
            "source": result.get("source"),
            "citation": result.get("citation"),
            "limits": result.get("limits"),
            "truncated": bool(result.get("truncated")),
        }
        for key in (
            "sdk",
            "devices",
            "avds",
            "avd_allowlist",
            "avd",
            "pid",
            "log_path",
            "evidence",
            "cancelled",
            "boot_completed",
        ):
            if key in result:
                payload[key] = result[key]
        return payload
