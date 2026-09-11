"""Model-message regressions for MAD-842; synthetic data, no private records."""

import json

import pytest

from src.agent_loop import _append_tool_results
from src.authority_protocol import action_effect_for, redact_secrets, safe_preview
from src.context_compactor import trim_for_context
from src.tool_execution import format_tool_result


def descriptor():
    return {
        "ok": True,
        "data": {
            "descriptor": {
                "name": "search",
                "inputSchema": {
                    "$defs": {
                        "Filter": {
                            "type": "object",
                            "description": "Filter semantics. " * 700,
                        }
                    },
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "collection_name": {"type": "string"},
                        "top_k": {"type": "integer"},
                        "filter": {"$ref": "#/$defs/Filter"},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }
        },
        "pagination": None,
        "error": None,
    }


def model_message(payload, budget=32000):
    result = {"stdout": "", "stderr": "", "exit_code": 0, "structured_content": payload}
    text = format_tool_result("mcp: reference", result)
    messages = [
        {"role": "user", "content": "Read the collection using its declared tools."}
    ]
    _append_tool_results(
        messages,
        "",
        [{"id": "reference-1", "name": "mcp__fixture__reference", "arguments": "{}"}],
        [text],
        [text],
        True,
        0,
    )
    trimmed = trim_for_context(messages, budget, reserve_tokens=0)
    return next(m["content"] for m in trimmed if m["role"] == "tool")


def payload_from(text):
    return json.loads(text.split("```json\n", 1)[1].split("\n```", 1)[0])


def test_large_schema_reaches_next_model_message_complete():
    original = descriptor()
    text = model_message(original)
    assert "collection_name" in text
    assert "top_k" in text
    payload = payload_from(text)
    assert payload["structured_content"] == original


def test_context_limit_omits_whole_schema_instead_of_cutting_json():
    text = model_message(descriptor(), budget=2000)
    payload = payload_from(text)
    assert "inputSchema" not in text
    assert payload["_projection"]["truncated"] is True
    assert "schema" in payload["_projection"]["reason"].lower()


def test_large_page_keeps_complete_records_and_cursor_with_explicit_omission():
    original = {
        "ok": True,
        "data": {
            "items": [
                {
                    "id": f"record-{i}",
                    "text": "evidence " * 250,
                    "citation": f"source://{i}",
                }
                for i in range(12)
            ]
        },
        "pagination": {"next_cursor": "cursor-12"},
        "error": None,
        "nextAction": "Read another page.",
    }
    payload = payload_from(model_message(original))
    visible = payload["structured_content"]
    assert 0 < len(visible["data"]["items"]) < 12
    assert (
        visible["data"]["items"]
        == original["data"]["items"][: len(visible["data"]["items"])]
    )
    assert visible["pagination"] == original["pagination"]
    assert visible["nextAction"] == original["nextAction"]
    assert payload["_projection"]["truncated"] is True
    assert original["data"]["items"][-1]["id"] == "record-11"


def test_empty_catalog_search_stays_empty_not_content_evidence():
    original = {
        "ok": True,
        "data": {"total": 0, "items": []},
        "summary": "No tool matched.",
        "nextAction": "Try a simpler operation phrase.",
    }
    assert payload_from(model_message(original))["structured_content"] == original


@pytest.mark.parametrize(
    "key", ["max_context_tokens", "max_tokens", "max_output_tokens"]
)
def test_numeric_token_budget_is_not_a_credential(key):
    args = {"arguments": {key: 2048}}
    call = {
        "name": "mcp__fixture__read",
        "arguments": args,
        "capability_policy": {"action_effect": "read"},
    }
    assert action_effect_for(call) == "read"
    assert redact_secrets(args) == args
    assert safe_preview(args) == args


@pytest.mark.parametrize(
    "args",
    [
        {"access_token": "private-value"},
        {"max_context_tokens": "private-value"},
        {"max_tokens": {"api_key": "private-value"}},
    ],
)
def test_real_credentials_stay_gated_and_redacted(args):
    call = {
        "name": "mcp__fixture__read",
        "arguments": args,
        "capability_policy": {"action_effect": "read"},
    }
    assert action_effect_for(call) == "credential_or_auth_change"
    assert "private-value" not in json.dumps(redact_secrets(args))


def test_mounted_guidance_still_preserves_atomic_json_under_context_pressure():
    from src.agent_loop import _project_native_mcp_guidance_for_model
    from src.context_compactor import _truncate_message_to_token_budget

    original = descriptor()
    original["nextAction"] = "fixture.hidden"
    text = format_tool_result("reference", {"structured_content": original})
    text = _project_native_mcp_guidance_for_model(
        text,
        "mcp__fixture__fixture.reference",
        {"mcp__fixture__fixture.reference"},
        {"mcp__fixture__fixture.reference", "mcp__fixture__fixture.hidden"},
    )
    trimmed = _truncate_message_to_token_budget({"role": "tool", "content": text}, 200)
    payload = payload_from(trimmed["content"])
    assert payload["_projection"]["truncated"]
    assert "fixture.hidden" not in trimmed["content"]


def test_minimum_context_notice_fits_budget():
    from src.context_compactor import _truncate_message_to_token_budget
    from src.model_context import estimate_tokens

    trimmed = _truncate_message_to_token_budget(
        {
            "role": "tool",
            "content": format_tool_result(
                "reference", {"structured_content": descriptor()}
            ),
        },
        64,
    )
    assert estimate_tokens([trimmed]) <= 64
    assert payload_from(trimmed["content"])["_projection"]["truncated"]


def test_provider_projection_field_cannot_break_host_projection():
    result = {
        "structured_content": {"items": ["x" * 9000]},
        "_projection": "server value",
    }
    payload = payload_from(format_tool_result("reference", result))
    assert payload["_projection"]["truncated"]


def test_error_detail_survives_large_data_page():
    original = {
        "ok": False,
        "error": {
            "code": "INVALID_ARGUMENTS",
            "details": [{"path": "arguments.query", "message": "Required"}],
        },
        "data": {"items": ["x" * 5000 for _ in range(4)]},
    }
    payload = payload_from(model_message(original))
    assert payload["structured_content"]["error"] == original["error"]


@pytest.mark.asyncio
async def test_full_loop_preserves_schema_then_corrects_one_read(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import src.agent_loop as loop
    import src.tool_execution as execution
    from src.authority_protocol import AuthorityStore
    from src.mcp_manager import McpManager
    from tests.test_baton2_execution import _events

    manager = McpManager()
    manager._connections["fixture"] = {"name": "Fixture MCP", "status": "connected"}
    manager._tools["fixture"] = [
        {
            "name": "reference",
            "description": "Read the executable search reference.",
            "input_schema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "search",
            "description": "Read matching records.",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True},
        },
    ]
    wire_reads = []
    original = descriptor()
    page = {
        "items": [
            {
                "id": "record-1",
                "text": "Verified fixture content.",
                "citation": "source://record-1",
            }
        ]
    }

    class Session:
        async def call_tool(self, name, arguments):
            wire_reads.append((name, arguments))
            content = original if name == "reference" else page
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(content))],
                structuredContent=content,
                isError=False,
            )

    manager._sessions["fixture"] = Session()
    rounds = []

    async def model(*args, **kwargs):
        messages = args[1]
        rounds.append(messages)
        number = len(rounds)
        if number == 2:
            text = next(m["content"] for m in reversed(messages) if m["role"] == "tool")
            assert payload_from(text)["structured_content"] == original
        if number == 3:
            text = next(m["content"] for m in reversed(messages) if m["role"] == "tool")
            assert "arguments.query is required" in text
        if number == 4:
            text = next(m["content"] for m in reversed(messages) if m["role"] == "tool")
            assert payload_from(text)["structured_content"] == page
            yield (
                "data: "
                + json.dumps(
                    {"delta": "Verified fixture content. [record-1](source://record-1)"}
                )
                + "\n\n"
            )
        else:
            name = "reference" if number == 1 else "search"
            arguments = {"query": "evidence"} if number == 3 else {}
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "tool_calls",
                        "calls": [
                            {
                                "id": str(number),
                                "name": "mcp__fixture__" + name,
                                "arguments": json.dumps(arguments),
                            }
                        ],
                    }
                )
                + "\n\n"
            )
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(loop, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(execution, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(execution, "_owner_is_admin", lambda _: True)
    # Earlier tests reload tool_execution; bind the real dispatcher from the
    # same module whose manager/admin fixture is configured here.
    monkeypatch.setattr(loop, "execute_tool_block", execution.execute_tool_block)
    monkeypatch.setattr(loop, "blocked_tools_for_owner", lambda _: set())
    monkeypatch.setattr(
        loop, "authority_store", AuthorityStore(tmp_path / "authority.json")
    )
    monkeypatch.setattr(loop, "stream_llm_with_fallback", model)
    events = await _events(
        messages=[
            {
                "role": "user",
                "content": "Use Fixture MCP to reference and search records.",
            }
        ],
        owner="leo",
        session_id="projection-test",
        context_length=64000,
        max_rounds=6,
        max_tool_calls=6,
        max_tokens=2048,
    )
    assert wire_reads == [("reference", {}), ("search", {"query": "evidence"})]
    assert len(rounds) == 4
    assert not any(e.get("type") == "authority_approval_required" for e in events)


def test_duplicate_json_text_cannot_restore_redacted_structured_secret():
    original = {"items": [{"id": "record", "access_token": "private-credential"}]}
    result = redact_secrets(
        {
            "stdout": json.dumps(original),
            "stderr": "",
            "structured_content": original,
            "exit_code": 0,
        }
    )
    text = format_tool_result("mcp: read", result)
    assert "private-credential" not in text
    assert "stdout" not in payload_from(text)


def test_text_only_mcp_schema_uses_same_atomic_projection():
    original = descriptor()
    text = format_tool_result(
        "mcp: reference",
        {
            "stdout": json.dumps(original),
            "stderr": "",
            "exit_code": 0,
        },
    )
    assert payload_from(text)["stdout"] == original
