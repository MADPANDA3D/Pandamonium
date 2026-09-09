#!/usr/bin/env python3
"""Run content-safe, disposable GPT-OSS acceptance for the native Portal route.

The report intentionally stores hashes and counts instead of downstream Discord
messages or Qdrant payloads.  It is suitable for release evidence, but it is not
a general chat client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DISCORD_PROMPT = (
    "Use MAD MCP Portal to read the last five messages from Discord channel #general."
)
QDRANT_PROMPT = (
    "Use MAD MCP Portal to sample ten payloads from the jarvis-knowledgebase "
    "Qdrant collection. Do not include vectors."
)
CONTEXT_FIRST_PROMPT = "Use MAD MCP Portal to list Qdrant collections."
CONTEXT_FOLLOWUP_PROMPT = (
    "What information is inside that collection? Show me ten examples."
)
FORBIDDEN_TOOLS = {
    "app_api", "api_call", "manage_mcp", "pipeline", "bash", "python",
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:7000")
    parser.add_argument("--session-token-file")
    parser.add_argument("--sessions-file")
    parser.add_argument("--username", default=os.getenv("PANDAMONIUM_ADMIN_USER", "admin"))
    parser.add_argument("--model-pattern", default=r"gpt[-_]?oss")
    parser.add_argument("--template-session-id")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _token(args: argparse.Namespace) -> str:
    supplied = (
        os.getenv("PANDAMONIUM_SESSION_TOKEN")
        or os.getenv("ODYSSEUS_SESSION_TOKEN", "")
    ).strip()
    if supplied:
        return supplied
    if args.session_token_file:
        return Path(args.session_token_file).read_text(encoding="utf-8").strip()
    if args.sessions_file:
        rows = json.loads(Path(args.sessions_file).read_text(encoding="utf-8"))
        valid = [
            key for key, row in rows.items()
            if row.get("username") == args.username
            and float(row.get("expiry") or 0) > time.time()
        ]
        if valid:
            return valid[0]
    raise RuntimeError("No valid Pandamonium session token was supplied")


class Client:
    def __init__(self, base_url: str, token: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Cookie": f"odysseus_session={token}"}

    def _request(self, path: str, *, method: str = "GET", form: dict | None = None):
        body = None
        headers = dict(self.headers)
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc

    def json(self, path: str, *, method: str = "GET", form: dict | None = None) -> Any:
        with self._request(path, method=method, form=form) as response:
            return json.load(response)

    def create_session(self, template: dict, name: str) -> str:
        row = self.json("/api/session", method="POST", form={
            "name": name,
            "endpoint_url": template["endpoint_url"],
            "model": template["model"],
            "rag": "false",
            "skip_validation": "true",
            "agent_target": "jarvis",
        })
        return str(row["id"])

    def delete_session(self, session_id: str) -> None:
        self.json(f"/api/session/{session_id}", method="DELETE")

    def chat(self, session_id: str, prompt: str) -> dict:
        started = time.monotonic()
        text_parts: list[str] = []
        metrics: dict[str, Any] = {}
        event_types: list[str] = []
        done = False
        with self._request("/api/chat_stream", method="POST", form={
            "message": prompt,
            "session": session_id,
            "mode": "adaptive",
            "agent_target": "jarvis",
            "use_rag": "false",
            "use_web": "false",
            "use_research": "false",
            "allow_bash": "false",
            "allow_web_search": "false",
        }) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    done = True
                    continue
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if isinstance(event.get("delta"), str) and not event.get("thinking"):
                    text_parts.append(event["delta"])
                event_type = str(event.get("type") or "")
                if event_type:
                    event_types.append(event_type)
                if event_type == "metrics" and isinstance(event.get("data"), dict):
                    metrics = event["data"]
        final_text = "".join(text_parts).strip()
        return {
            "http_complete": done,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "response_chars": len(final_text),
            "response_sha256": hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
            "event_types": sorted(set(event_types)),
            "metrics": metrics,
        }


def _schema_names(metrics: dict) -> tuple[list[str], int]:
    tools = ((metrics.get("context_manifest") or {}).get("tools") or {})
    names: set[str] = set()
    for key, value in tools.items():
        if key in {"schema_tokens", "total"} or not isinstance(value, dict):
            continue
        names.update(str(name) for name in value.get("names") or [])
    return sorted(names), int(tools.get("schema_tokens") or 0)


def _safe_tool_events(metrics: dict) -> list[dict]:
    rows: list[dict] = []
    for event in metrics.get("tool_events") or []:
        if not isinstance(event, dict):
            continue
        action = event.get("action_call") or {}
        result = event.get("action_result") or {}
        relay = event.get("portal_relay") or {}
        rows.append({
            "round": event.get("round"),
            "tool": event.get("tool"),
            "exit_code": event.get("exit_code"),
            "action_name": action.get("name"),
            "action_arguments": action.get("arguments") or {},
            "action_status": result.get("status"),
            "approval_present": bool(event.get("authority_decision")),
            "portal_relay": {
                "service_id": relay.get("service_id"),
                "tool_name": relay.get("tool_name"),
                "descriptor_hash": relay.get("descriptor_hash"),
                "catalog_version": relay.get("catalog_version"),
                "trace_id": relay.get("trace_id"),
                "arguments": relay.get("arguments") or {},
                "item_count": relay.get("item_count"),
            } if relay else {},
        })
    return rows


def _project_turn(raw: dict, expected_service: str, expected_tool: str,
                  expected_arguments: dict, expected_items: int | None) -> dict:
    metrics = raw.get("metrics") or {}
    schema_names, schema_tokens = _schema_names(metrics)
    events = _safe_tool_events(metrics)
    relays = [row for row in events if row.get("portal_relay")]
    target = next((
        row for row in relays
        if row["portal_relay"].get("service_id") == expected_service
        and row["portal_relay"].get("tool_name") == expected_tool
    ), None)
    visible_or_called = schema_names + [str(row.get("tool") or "") for row in events]
    forbidden = sorted({
        name for name in visible_or_called
        if name in FORBIDDEN_TOOLS
        or name.startswith("mcp__discord__")
        or name.startswith("mcp__qdrant__")
    })
    target_relay = (target or {}).get("portal_relay") or {}
    args_match = all(
        target_relay.get("arguments", {}).get(key) == value
        for key, value in expected_arguments.items()
    )
    bad_events = {
        "authority_approval_required", "ask_user", "fallback",
        "intent_nudge_exhausted", "rounds_exhausted",
    } & set(raw.get("event_types") or [])
    routing = metrics.get("portal_routing") or {}
    trace_events = [
        {
            "tool": row.get("tool"),
            "arguments": row.get("arguments") or {},
            "trace_id": row.get("trace_id"),
        }
        for row in routing.get("trace_events") or []
        if isinstance(row, dict)
    ]
    if target_relay:
        trace_events.append({
            "tool": "portal.call_read_tool",
            "arguments": {
                "serviceId": target_relay.get("service_id"),
                "toolName": target_relay.get("tool_name"),
                "arguments": target_relay.get("arguments") or {},
            },
            "trace_id": target_relay.get("trace_id"),
        })
    checks = {
        "stream_completed": bool(raw.get("http_complete")),
        "one_exact_model_schema": (
            len(schema_names) == 1
            and schema_names[0] == routing.get("model_visible_schema")
            and ".read." in schema_names[0]
        ),
        "no_full_catalog": len(schema_names) == 1 and schema_tokens < 2048,
        "no_generic_or_direct_provider_tools": not forbidden,
        "no_approval_or_control_failure": not bad_events
            and not any(row.get("approval_present") for row in events),
        "exact_portal_route": routing.get("service_id") == expected_service
            and routing.get("tool_name") == expected_tool,
        "successful_provider_read": bool(target)
            and target.get("exit_code") == 0
            and target.get("action_status") == "succeeded",
        "exact_arguments": args_match,
        "expected_item_count": (
            target_relay.get("item_count") == expected_items
            if expected_items is not None
            else isinstance(target_relay.get("item_count"), int)
            and target_relay["item_count"] > 0
        ),
        "user_facing_answer_after_read": raw.get("response_chars", 0) > 0 and bool(target),
        "no_completion_guard": not metrics.get("completion_guard"),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "elapsed_seconds": raw.get("elapsed_seconds"),
        "response_chars": raw.get("response_chars"),
        "response_sha256": raw.get("response_sha256"),
        "rounds": metrics.get("agent_rounds"),
        "tokens": {
            "input": metrics.get("input_tokens"),
            "output": metrics.get("output_tokens"),
            "total": metrics.get("total_tokens"),
        },
        "model_visible_schemas": schema_names,
        "schema_tokens": schema_tokens,
        "forbidden_visible_or_called": forbidden,
        "native_call_sequence": trace_events,
        "tool_events": events,
        "event_types": raw.get("event_types") or [],
    }


def _run_single(client: Client, template: dict, scenario: str, run: int) -> dict:
    session_id = client.create_session(template, f"MAD-842 {scenario} run {run}")
    cleanup = "pending"
    try:
        if scenario == "discord":
            raw = client.chat(session_id, DISCORD_PROMPT)
            result = _project_turn(
                raw, "discord", "read_messages", {"count": "5"}, 5
            )
        elif scenario == "qdrant":
            raw = client.chat(session_id, QDRANT_PROMPT)
            result = _project_turn(raw, "qdrant", "qdrant-list-points", {
                "collection_name": "jarvis-knowledgebase",
                "limit": 10,
                "include_payload": True,
                "include_vectors": False,
            }, 10)
        else:
            first = _project_turn(
                client.chat(session_id, CONTEXT_FIRST_PROMPT),
                "qdrant", "qdrant-list-collections", {}, None,
            )
            followup = _project_turn(
                client.chat(session_id, CONTEXT_FOLLOWUP_PROMPT),
                "qdrant", "qdrant-list-points", {
                    "collection_name": "jarvis-knowledgebase",
                    "limit": 10,
                }, 10,
            )
            result = {
                "passed": bool(first["passed"] and followup["passed"]),
                "first_turn": first,
                "followup_turn": followup,
            }
    finally:
        client.delete_session(session_id)
        cleanup = "deleted"
    return {"run": run, "cleanup": cleanup, **result}


def main() -> None:
    args = _args()
    if args.runs < 1:
        raise SystemExit("--runs must be positive")
    client = Client(args.base_url, _token(args), args.timeout)
    sessions = client.json("/api/sessions")
    candidates = [
        row for row in sessions
        if isinstance(row, dict)
        and row.get("endpoint_url") and row.get("model")
        and (not args.template_session_id or row.get("id") == args.template_session_id)
        and re.search(args.model_pattern, str(row.get("model") or ""), re.I)
    ]
    if not candidates:
        raise RuntimeError("No visible GPT-OSS template session matched")
    template = candidates[0]
    report: dict[str, Any] = {
        "contract": "MAD-842 native Portal GPT-OSS acceptance",
        "generated_at_unix": int(time.time()),
        "base_url": args.base_url,
        "model": template["model"],
        "template_session_id": template["id"],
        "runs_requested_per_scenario": args.runs,
        "prompts": {
            "discord": DISCORD_PROMPT,
            "qdrant": QDRANT_PROMPT,
            "context_first": CONTEXT_FIRST_PROMPT,
            "context_followup": CONTEXT_FOLLOWUP_PROMPT,
        },
        "scenarios": {},
    }
    all_passed = True
    for scenario in ("discord", "qdrant", "contextual_qdrant"):
        runs: list[dict] = []
        for run_number in range(1, args.runs + 1):
            row = _run_single(client, template, scenario, run_number)
            runs.append(row)
            status = "PASS" if row["passed"] else "FAIL"
            print(f"{scenario} {run_number}/{args.runs}: {status}", flush=True)
        passed = sum(bool(row["passed"]) for row in runs)
        rate = passed / args.runs
        report["scenarios"][scenario] = {
            "passed": passed,
            "total": args.runs,
            "pass_rate": rate,
            "meets_95_percent": rate >= 0.95,
            "runs": runs,
        }
        all_passed = all_passed and rate >= 0.95
    report["passed"] = all_passed
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": all_passed,
        "output": str(output),
        "rates": {
            name: row["pass_rate"] for name, row in report["scenarios"].items()
        },
    }, indent=2))
    if not all_passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
