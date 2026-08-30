"""JOS-P2A observes mounted context without changing provider messages."""

import json

import pytest

from src import agent_loop
from src.llm_core import _sanitize_llm_messages
from src.model_context import annotate_context_messages, build_context_manifest
from src.prompt_security import untrusted_context_message


def _messages():
    return [
        {
            "role": "system",
            "content": "Jarvis identity",
            "metadata": {
                "jos_context": {
                    "class": "identity_policy",
                    "source": "odysseus.identity",
                    "trust": "system_authority",
                }
            },
        },
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        untrusted_context_message("saved memory: retrieved context", "private memory text"),
        untrusted_context_message("retrieved documents", "secret document text"),
        {
            "role": "user",
            "content": "current time",
            "metadata": {
                "jos_context": {
                    "class": "time",
                    "source": "odysseus.current_time",
                    "trust": "system_authority",
                }
            },
        },
        {"role": "user", "content": "current request"},
    ]


def test_manifest_reports_classes_trust_sources_and_omissions_without_content():
    manifest = build_context_manifest(
        _messages(),
        32768,
        omissions=["skills_disabled"],
        extensions={"oracle": {"engaged": True, "state_mounted": True, "tool_count": 1}},
        tool_catalog={
            "extension": [{
                "type": "function",
                "function": {"name": "fly_to_location", "parameters": {"type": "object"}},
            }],
        },
    )

    assert manifest["version"] == "jos-p2a.1"
    assert set(manifest["mounted"]["classes"]) >= {
        "identity_policy", "conversation", "operator_intent",
        "recalled_memory", "retrieved_knowledge", "time",
    }
    assert manifest["mounted"]["classes"]["operator_intent"]["messages"] == 1
    assert manifest["mounted"]["trust"]["untrusted_data"]["messages"] == 2
    assert {row["source"] for row in manifest["mounted"]["sources"]} >= {
        "memory.recalled", "documents.rag", "operator.current_turn",
    }
    assert manifest["extensions"]["oracle"] == {
        "engaged": True, "state_mounted": True, "tool_count": 1,
    }
    assert manifest["tools"]["extension"]["names"] == ["fly_to_location"]
    assert manifest["omissions"] == ["skills_disabled"]
    serialized = str(manifest)
    assert "private memory text" not in serialized
    assert "secret document text" not in serialized
    assert "current request" not in serialized


def test_manifest_reports_class_level_trimming():
    before = _messages()
    after = [before[0], before[-1]]
    manifest = build_context_manifest(after, 2048, before_messages=before)

    assert manifest["trimming"]["ran"] is True
    assert manifest["trimming"]["dropped_by_class"]["recalled_memory"]["messages"] == 1
    assert manifest["trimming"]["dropped_by_class"]["retrieved_knowledge"]["messages"] == 1
    assert manifest["mounted"]["classes"]["operator_intent"]["messages"] == 1


def test_internal_tags_do_not_change_provider_payload():
    raw = _messages()
    annotated = annotate_context_messages(raw)

    assert _sanitize_llm_messages(annotated) == _sanitize_llm_messages(raw)
    assert all("metadata" not in message for message in _sanitize_llm_messages(annotated))


@pytest.mark.asyncio
async def test_agent_metrics_carry_manifest_on_direct_path(monkeypatch):
    async def fake_stream(*args, **kwargs):
        yield 'data: {"delta":"Hello."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    events = []
    async for chunk in agent_loop.stream_agent_loop(
        "http://model.test/v1/chat/completions",
        "test-model",
        [{"role": "user", "content": "hello"}],
        context_length=4096,
    ):
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
            events.append(json.loads(chunk[6:]))

    metrics = next(event["data"] for event in events if event.get("type") == "metrics")
    manifest = metrics["context_manifest"]
    assert manifest["mounted"]["classes"]["operator_intent"]["messages"] == 1
    assert manifest["omissions"] == ["agent_context_reduced_low_signal"]
