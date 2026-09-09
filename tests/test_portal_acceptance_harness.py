from scripts.verify_portal_native_acceptance import _project_turn, _safe_tool_events


def _tool_event(decision: str) -> dict:
    return {
        "round": 1,
        "tool": "mcp__portal__portal.read.discord.read_messages",
        "exit_code": 0,
        "action_call": {"name": "portal read", "arguments": {"count": "5"}},
        "action_result": {"status": "succeeded"},
        "authority_decision": {"decision": decision},
        "portal_relay": {
            "service_id": "discord",
            "tool_name": "read_messages",
            "trace_id": "trace-read",
            "arguments": {"count": "5"},
            "item_count": 5,
        },
    }


def test_acceptance_harness_does_not_treat_allow_audit_as_approval_prompt():
    assert _safe_tool_events({"tool_events": [_tool_event("allow")]})[0][
        "approval_present"
    ] is False
    assert _safe_tool_events({
        "tool_events": [_tool_event("approval_required")]
    })[0]["approval_present"] is True


def test_acceptance_harness_derives_rounds_from_recorded_tool_rounds():
    schema = "mcp__portal__portal.read.discord.read_messages"
    metrics = {
        "context_manifest": {
            "tools": {
                "mcp": {"names": [schema]},
                "schema_tokens": 200,
            },
        },
        "portal_routing": {
            "service_id": "discord",
            "tool_name": "read_messages",
            "model_visible_schema": schema,
        },
        "tool_events": [_tool_event("allow")],
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    projected = _project_turn({
        "metrics": metrics,
        "event_types": ["metrics"],
        "http_complete": True,
        "response_chars": 10,
        "response_sha256": "response-hash",
        "elapsed_seconds": 1.0,
    }, "discord", "read_messages", {"count": "5"}, 5)

    assert projected["passed"] is True
    assert projected["rounds"] == 2
