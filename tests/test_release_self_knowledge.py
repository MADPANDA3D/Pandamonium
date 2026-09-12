"""Deterministic coverage for MAD-894 release/version self-knowledge.

A version/release/update question about this installation must answer from the
local release state (``/api/version`` data + updater state + curated notes)
before any web search, name the canonical repository, and fail precisely when
the release channel is unreachable.
"""

from __future__ import annotations

import json

import pytest

from src import agent_loop, update_status
from src.action_intents import classify_tool_intent, is_release_self_knowledge
from src.agent_tools import ToolBlock
from src.release_updater import REPOSITORY_URL
from src.tool_execution import execute_tool_block, format_tool_result
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


REPRO = "Search the web for the latest Pandamonium release notes and summarize them."


def _selected_tools(intent):
    tools = set()
    for domain in intent["domains"]:
        tools.update(agent_loop._DOMAIN_TOOL_MAP.get(domain, set()))
    return tools


def _write_notes(root, version, body="## Notes\n\n- fixture note\n"):
    notes_dir = root / ".github" / "releases"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / f"v{version}.md").write_text(body, encoding="utf-8")
    return body


def _release_candidate(version="9.9.9", *, current=False):
    return {
        "version": version,
        "tag": f"v{version}",
        "commit": "a" * 40,
        "channel": "stable",
        "release_url": f"{REPOSITORY_URL}/releases/tag/v{version}",
        "current": current,
        "compatibility": {"minimum_version": "1.0.0", "minimum_python": "3.10"},
    }


def test_release_questions_classify_as_local_release_intent():
    for message in (
        REPRO,
        "google pandamonium repo that should find the repo",
        "What version am I running?",
        "Which version is this installation on?",
        "Is there an update available?",
        "Is Pandamonium up to date?",
        "What's new in Pandamonium?",
        "Show me the release notes for the installed version",
    ):
        intent = classify_tool_intent(message)
        assert intent.needs_tools, message
        assert intent.category == "release", message
        assert is_release_self_knowledge(message) is True


def test_another_projects_release_notes_stay_on_the_web_path():
    for message in (
        "Search the web for the latest Django release notes and summarize them.",
        "What version of Python is installed?",
        "Find the Kubernetes changelog",
        "Check for updates to Home Assistant",
    ):
        intent = classify_tool_intent(message)
        assert intent.category != "release", message


def test_release_question_routes_to_platform_tools_without_web():
    intent = agent_loop._classify_agent_request([], REPRO)

    assert intent["release_self_knowledge"] is True
    assert "platform" in intent["domains"]
    assert "web" not in intent["domains"]
    assert "research" not in intent["domains"]
    selected = _selected_tools(intent)
    assert "get_runtime_status" in selected
    rules = agent_loop._domain_rules_for_tools(selected)
    assert any("canonical repository" in rule for rule in rules)
    assert any("channel unavailable" in rule for rule in rules)


def test_release_clamp_removes_retrieved_web_tools():
    retrieved = {"web_search", "web_fetch", "bash", "get_runtime_status"}

    clamped = agent_loop._clamp_release_self_knowledge_tools(True, retrieved)
    assert clamped == {"bash", "get_runtime_status"}
    assert agent_loop._clamp_release_self_knowledge_tools(False, retrieved) == retrieved


def test_release_intent_survives_a_short_continuation():
    messages = [
        {"role": "user", "content": REPRO},
        {"role": "assistant", "content": "Which version do you want the notes for?"},
        {"role": "user", "content": "yes"},
    ]

    intent = agent_loop._classify_agent_request(messages, "yes")

    assert intent["continuation"] is True
    assert intent["release_self_knowledge"] is True
    assert "platform" in intent["domains"]
    assert "web" not in intent["domains"]


def test_curated_release_notes_read_exact_version_file(tmp_path):
    body = _write_notes(tmp_path, "9.9.9")

    notes = update_status.curated_release_notes("9.9.9", root=tmp_path)

    assert notes["available"] is True
    assert notes["version"] == "9.9.9"
    assert notes["markdown"] == body
    assert notes["source"] == ".github/releases/v9.9.9.md"
    assert notes["truncated"] is False


def test_curated_release_notes_fail_precisely_for_bad_or_missing_versions(tmp_path):
    _write_notes(tmp_path, "9.9.9")

    invalid = update_status.curated_release_notes("../../etc/passwd", root=tmp_path)
    missing = update_status.curated_release_notes("8.8.8", root=tmp_path)

    assert invalid["available"] is False
    assert "not a valid release version" in invalid["reason"]
    assert missing["available"] is False
    assert "not installed" in missing["reason"]


def test_curated_release_notes_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(update_status, "_NOTES_MAX_CHARS", 64)
    _write_notes(tmp_path, "9.9.9", body="x" * 500)

    notes = update_status.curated_release_notes("9.9.9", root=tmp_path)

    assert notes["available"] is True
    assert notes["truncated"] is True
    assert len(notes["markdown"]) < 200


@pytest.mark.asyncio
async def test_release_facts_prefers_local_state_and_canonical_repository(
    monkeypatch, tmp_path
):
    installed = update_status.APP_VERSION
    monkeypatch.setattr(update_status, "ROOT", tmp_path)
    monkeypatch.setattr(update_status, "current_revision", lambda _root: "b" * 40)
    monkeypatch.setattr(
        update_status, "discover_release", lambda: _release_candidate()
    )
    monkeypatch.setattr(
        update_status,
        "public_update_state",
        lambda: {
            "status": "succeeded",
            "phase": "complete",
            "progress": 100,
            "message": "Updated to v9.9.9",
            "target_version": "9.9.9",
            "target_commit": "a" * 40,
            "previous_release": "/opt/pandamonium/releases/1.0.0-deadbeef",
            "rollback_available": True,
            "auto_rolled_back": False,
            "updated_at": "2026-09-12T00:00:00+00:00",
        },
    )
    _write_notes(tmp_path, installed, body=f"# Pandamonium v{installed}\n\ninstalled notes\n")
    update_status._CACHE.update(expires_at=0.0, payload=None)

    facts = await update_status.release_facts()

    assert facts["repository"] == REPOSITORY_URL
    assert facts["installed_version"] == installed
    assert facts["installed_commit"] == "b" * 40
    assert facts["update_status"] == "available"
    assert facts["latest_version"] == "9.9.9"
    assert facts["notes_version"] == installed
    assert facts["notes"]["available"] is True
    assert "installed notes" in facts["notes"]["markdown"]
    assert facts["updater"]["status"] == "succeeded"
    assert facts["updater"]["previous_release"] == "1.0.0-deadbeef"


@pytest.mark.asyncio
async def test_release_facts_reports_unreachable_channel_without_fabricating(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(update_status, "ROOT", tmp_path)
    monkeypatch.setattr(update_status, "current_revision", lambda _root: "b" * 40)
    monkeypatch.setattr(update_status, "public_update_state", lambda: {"status": "idle"})
    _write_notes(tmp_path, update_status.APP_VERSION)

    def unavailable():
        raise update_status.UpdateError("GitHub release metadata is unavailable")

    monkeypatch.setattr(update_status, "discover_release", unavailable)
    update_status._CACHE.update(expires_at=0.0, payload=None)

    facts = await update_status.release_facts()

    assert facts["update_status"] == "unavailable"
    assert facts["latest_version"] is None
    assert facts["update_available"] is False
    assert facts["release_check"]["message"] == "GitHub release metadata is unavailable"
    assert facts["notes"]["available"] is True


@pytest.mark.asyncio
async def test_get_runtime_status_execution_carries_release_state(monkeypatch, tmp_path):
    installed = update_status.APP_VERSION
    monkeypatch.setattr(update_status, "ROOT", tmp_path)
    monkeypatch.setattr(update_status, "current_revision", lambda _root: "b" * 40)
    monkeypatch.setattr(update_status, "discover_release", lambda: _release_candidate(current=True))
    monkeypatch.setattr(update_status, "public_update_state", lambda: {"status": "succeeded"})
    _write_notes(tmp_path, installed, body=f"# Pandamonium v{installed}\n\nfixture\n")
    update_status._CACHE.update(expires_at=0.0, payload=None)

    async def fake_runtime_status(**_kwargs):
        return {"application_version": installed, "brain_model": "fixture-model"}

    monkeypatch.setattr("src.jarvis_agent.runtime_status", fake_runtime_status)

    _desc, result = await execute_tool_block(
        ToolBlock("get_runtime_status", json.dumps({"release": True})),
        owner="leo",
    )

    assert result["release"]["repository"] == REPOSITORY_URL
    assert result["release"]["installed_version"] == installed
    assert result["release"]["notes"]["available"] is True
    assert "fixture-model" in result["output"]
    formatted = format_tool_result("get_runtime_status", result)
    assert REPOSITORY_URL in formatted


@pytest.mark.asyncio
async def test_get_runtime_status_release_false_skips_release_path(monkeypatch):
    async def fake_runtime_status(**_kwargs):
        return {"application_version": update_status.APP_VERSION}

    async def forbidden_release_facts(**_kwargs):
        raise AssertionError("release facts must not be read when release=false")

    monkeypatch.setattr("src.jarvis_agent.runtime_status", fake_runtime_status)
    monkeypatch.setattr(update_status, "release_facts", forbidden_release_facts)

    _desc, result = await execute_tool_block(
        ToolBlock("get_runtime_status", json.dumps({"release": False})),
        owner="leo",
    )

    assert "release" not in result
    assert result["application_version"] == update_status.APP_VERSION


@pytest.mark.asyncio
async def test_get_runtime_status_release_notes_version_passthrough(monkeypatch):
    observed = {}

    async def fake_runtime_status(**_kwargs):
        return {"application_version": update_status.APP_VERSION}

    async def fake_release_facts(**kwargs):
        observed.update(kwargs)
        return {"repository": REPOSITORY_URL, "notes": {"available": True}}

    monkeypatch.setattr("src.jarvis_agent.runtime_status", fake_runtime_status)
    monkeypatch.setattr(update_status, "release_facts", fake_release_facts)

    await execute_tool_block(
        ToolBlock(
            "get_runtime_status",
            json.dumps({"release": True, "release_notes_version": "1.0.46"}),
        ),
        owner="leo",
    )

    assert observed == {"notes_version": "1.0.46", "include_notes": True}


@pytest.mark.asyncio
async def test_release_turn_schema_catalog_excludes_web_and_includes_release_tool(
    monkeypatch,
):
    captured = {}

    async def fake_stream(*args, **kwargs):
        captured["messages"] = args[1]
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"Loaded."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": REPRO}],
        context_length=32_768,
        max_rounds=1,
        relevant_tools={"bash", "web_search", "web_fetch", "get_runtime_status"},
    ):
        pass

    tool_names = {
        schema["function"]["name"]
        for schema in captured["tools"]
        if schema.get("function")
    }
    assert "get_runtime_status" in tool_names
    assert tool_names.isdisjoint({"web_search", "web_fetch"})
    assert any(
        "canonical repository" in str(message.get("content") or "")
        for message in captured["messages"]
    )


def test_get_runtime_status_schema_keeps_release_args_optional():
    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "get_runtime_status"
    )

    parameters = schema["parameters"]
    assert set(parameters["properties"]) == {"release", "release_notes_version"}
    assert parameters.get("required", []) == []
    assert parameters["additionalProperties"] is False
    assert "release" in schema["description"].lower()
