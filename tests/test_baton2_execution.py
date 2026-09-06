import json
from types import SimpleNamespace

import pytest

import src.agent_loop as agent_loop
import src.jarvis_agent as jarvis_agent
import src.tool_execution as tool_execution
from core.constants import APP_VERSION
from src.agent_tools import ToolBlock
from src.agent_identity import configured_agent_id
from src.authority_protocol import AuthorityStore, argument_fingerprint
from src.mcp_manager import McpManager


async def _events(**kwargs):
    events = []
    async for chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1",
        "gpt-4o",
        kwargs.pop("messages", [{"role": "user", "content": "run the task"}]),
        **kwargs,
    ):
        if chunk.startswith("data: ") and chunk.strip() != "data: [DONE]":
            events.append(json.loads(chunk[6:]))
    return events


def _bash_call(command: str, *, request_id: str = "request-1", call_id: str = "call-1"):
    return {
        "request_id": request_id,
        "call_id": call_id,
        "agent_id": configured_agent_id(),
        "actor": "engine:gpt-4o",
        "capability_version": "jos-p4:test",
        "name": "bash",
        "target": "tool",
        "arguments": {"command": command},
        "authority_ref": None,
        "limits": {},
        "capability_policy": {},
    }


@pytest.mark.asyncio
async def test_nmap_approval_executes_retained_install_once_then_verifies_and_scans(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    store = AuthorityStore(tmp_path / "authority.json")
    target = "10.0.0.0/24"
    install = (
        "if ! command -v nmap >/dev/null; then "
        "command -v sudo >/dev/null || { echo sudo_unavailable; exit 70; }; "
        "sudo -n true || { echo sudo_unavailable; exit 72; }; "
        "if command -v apt-get >/dev/null; then sudo -n apt-get update && sudo -n apt-get install -y nmap; "
        "else echo unsupported_package_manager; exit 71; fi; fi && "
        f"command -v nmap && nmap -sn -- {target}"
    )
    executions = []

    async def initial_stream(*_args, **_kwargs):
        call = {"id": "install-nmap", "name": "bash", "arguments": json.dumps({"command": install})}
        yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, **_kwargs):
        executions.append(block.content)
        if block.content == install:
            return "bash", {
                "output": "nmap installed\n/usr/bin/nmap\nNmap scan report for 10.0.0.1\nHost is up",
                "exit_code": 0,
            }
        raise AssertionError(f"tampered command: {block.content}")

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "authority_store", store)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", initial_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    first = await _events(
        relevant_tools={"bash"},
        session_id="session-1",
        max_rounds=1,
        workspace=str(tmp_path),
    )
    pending = next(event["data"] for event in first if event.get("type") == "authority_approval_required")
    assert executions == []
    assert pending["preview"] == {"command": install}
    assert pending["execution"]["host"]
    assert pending["execution"]["user"]
    assert pending["execution"]["workspace"] == str(tmp_path)

    # Mirrors the card's authenticated API approval followed by its automatic
    # one-word continuation message.
    store.resolve(pending["decision_id"], operator_id="local-operator", choice="approve", scope="once")
    resume = store.resolve_natural_reply(
        "Approve", operator_id="local-operator", session_id="session-1"
    )
    assert resume["pending_action"]["call"]["arguments"] == {"command": install}

    async def continuation_stream(*_args, **_kwargs):
        yield f'data: {json.dumps({"delta": "nmap was installed and verified. The approved target has one live host."})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", continuation_stream)
    second = await _events(
        messages=[{"role": "user", "content": "Approve"}],
        relevant_tools={"bash"},
        forced_tools={"bash"},
        session_id="session-1",
        max_rounds=1,
        workspace=str(tmp_path),
        approved_action=resume["pending_action"],
    )

    assert executions == [install]
    assert install.index("sudo -n true") < install.index("apt-get install")
    assert install.index("apt-get install") < install.index("command -v nmap && nmap")
    assert install.endswith(f"nmap -sn -- {target}")
    install_output = next(event for event in second if event.get("type") == "tool_output")
    assert install_output["authority_ref"] == pending["decision_id"]
    assert install_output["status"] == "succeeded"
    assert any("one live host" in event.get("delta", "") for event in second)
    assert store.resolve_natural_reply(
        "Approve", operator_id="local-operator", session_id="session-1"
    )["choice"] == "repeat"
    assert store.resolve_natural_reply(
        "yes", operator_id="local-operator", session_id="session-1"
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "unsupported_package_manager",
        "sudo_unavailable",
        "nmap_installation_failed",
    ],
)
async def test_approved_install_failures_execute_once_and_reach_original_task(monkeypatch, tmp_path, failure):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    store = AuthorityStore(tmp_path / "authority.json")
    command = (
        "check-sudo-and-package-manager && sudo -n install-nmap && "
        "command -v nmap && nmap -sn -- 10.0.0.0/24"
    )
    pending = store.decide(
        _bash_call(command),
        operator_id="local-operator",
        session_id="session-1",
        configured_workspace=str(tmp_path),
    )
    resume = store.resolve_natural_reply(
        "Approve", operator_id="local-operator", session_id="session-1"
    )
    executions = []

    async def fake_execute(block, **_kwargs):
        executions.append(block.content)
        return "bash", {"output": failure, "exit_code": 1, "error": failure}

    async def final_stream(_candidates, messages, **_kwargs):
        assert failure in json.dumps(messages)
        yield f'data: {json.dumps({"delta": f"Installation stopped: {failure}. The scan did not run."})}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "authority_store", store)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", final_stream)

    events = await _events(
        relevant_tools={"bash"},
        forced_tools={"bash"},
        session_id="session-1",
        max_rounds=1,
        workspace=str(tmp_path),
        approved_action=resume["pending_action"],
    )

    assert pending["decision"] == "approval_required"
    assert executions == [command]
    assert any(failure in event.get("delta", "") for event in events)
    assert len(executions) == 1
    assert "nmap -sn -- 10.0.0.0/24" in executions[0]


def test_stale_approval_cannot_execute_after_process_restart(tmp_path):
    store = AuthorityStore(tmp_path / "authority.json")
    original = _bash_call("sudo -n apt-get install -y nmap && nmap -sn -- 10.0.0.0/24")
    pending = store.decide(
        original,
        operator_id="leo",
        session_id="session-1",
        configured_workspace=str(tmp_path),
    )

    restarted = AuthorityStore(store.path)
    stale = restarted.resolve_natural_reply("Approve", operator_id="leo", session_id="session-1")
    assert stale["choice"] == "stale"



@pytest.mark.parametrize(
    "tampered_command",
    [
        "sudo -n dnf install -y nmap && nmap -sn -- 10.0.0.0/24",
        "sudo -n apt-get install -y nmap && nmap -sn -- 10.0.1.0/24",
    ],
)
def test_changed_command_or_target_requires_a_new_decision(tmp_path, tampered_command):
    original = _bash_call("sudo -n apt-get install -y nmap && nmap -sn -- 10.0.0.0/24")
    store = AuthorityStore(tmp_path / "authority.json")
    decision = store.decide(original, operator_id="leo", session_id="session-1")
    store.resolve(decision["decision_id"], operator_id="leo", choice="approve")
    tampered = _bash_call(tampered_command, request_id="request-2", call_id="call-2")

    assert argument_fingerprint(original) != argument_fingerprint(tampered)
    assert store.decide(tampered, operator_id="leo", session_id="session-1")["decision"] == "approval_required"


@pytest.mark.parametrize("binding", ["host", "user"])
def test_changed_host_or_user_rejects_approval(monkeypatch, tmp_path, binding):
    store = AuthorityStore(tmp_path / "authority.json")
    pending = store.decide(
        _bash_call("sudo -n apt-get install -y nmap"),
        operator_id="leo",
        session_id="session-1",
    )
    if binding == "host":
        monkeypatch.setattr("src.authority_protocol.socket.gethostname", lambda: "different-host")
    else:
        monkeypatch.setattr("src.authority_protocol.getpass.getuser", lambda: "different-user")

    with pytest.raises(ValueError, match="authority_execution_context_changed"):
        store.resolve(pending["decision_id"], operator_id="leo", choice="approve")


@pytest.mark.asyncio
async def test_selected_friday_enforces_workspace_owns_voice_and_suppresses_raw_duplicate(monkeypatch):
    captured = {}

    async def fake_start_task(**kwargs):
        captured.update(kwargs)
        return {"task_id": "task-1", "status": "queued", "workspace": kwargs["workspace"]}

    monkeypatch.setattr(jarvis_agent, "start_task", fake_start_task)
    _desc, result = await tool_execution.execute_tool_block(
        ToolBlock(
            "start_agent_task",
            json.dumps({"worker": "pc-codex", "workspace": "business", "prompt": "Inspect Pandamonium"}),
        ),
        session_id="session-1",
        owner="leo",
        presenter="Jarvis",
        persist_worker_result=False,
        worker_workspace="home-lab",
    )

    assert result["exit_code"] == 0
    assert captured["workspace"] == "home-lab"
    assert captured["presenter"] == "Jarvis"
    assert captured["persist_result"] is False


@pytest.mark.asyncio
async def test_final_round_web_search_gets_one_reserved_synthesis_pass(monkeypatch):
    calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            call = {"id": "search-1", "name": "web_search", "arguments": json.dumps({"query": "Pandamonium GitHub"})}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield f'data: {json.dumps({"delta": "The GitHub result identifies the Pandamonium repository."})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(_block, **_kwargs):
        return "web search", {"output": "MADPANDA3D/Pandamonium", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    events = await _events(
        relevant_tools={"web_search"},
        session_id="session-1",
        owner="leo",
        max_rounds=1,
    )

    assert calls == 2
    assert any("identifies the Pandamonium repository" in event.get("delta", "") for event in events)
    assert not any(event.get("type") == "rounds_exhausted" for event in events)


@pytest.mark.asyncio
async def test_readonly_portal_health_call_reaches_executor_instead_of_unclassified_denial(monkeypatch, tmp_path):
    manager = McpManager()
    manager._tools["portal"] = [{
        "name": "get_health",
        "description": "Read Portal health",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    }]
    manager._connections["portal"] = {"name": "MAD MCP Portal", "status": "connected"}
    store = AuthorityStore(tmp_path / "authority.json")
    calls = 0
    executions = []

    async def fake_stream(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            call = {"id": "portal-health", "name": "mcp__portal__get_health", "arguments": "{}"}
            yield f'data: {json.dumps({"type": "tool_calls", "calls": [call]})}\n\n'
        else:
            yield f'data: {json.dumps({"delta": "Portal health is connected."})}\n\n'
        yield "data: [DONE]\n\n"

    async def fake_execute(block, **_kwargs):
        executions.append(block.tool_type)
        return "portal health", {"stdout": "connected", "stderr": "", "exit_code": 0}

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: manager)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda _owner: set())
    monkeypatch.setattr(agent_loop, "authority_store", store)
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)
    monkeypatch.setattr(agent_loop, "execute_tool_block", fake_execute)

    events = await _events(
        relevant_tools={"mcp__portal__get_health"},
        session_id="session-1",
        owner="leo",
        max_rounds=2,
    )

    assert executions == ["mcp__portal__get_health"]
    output = next(event for event in events if event.get("type") == "tool_output")
    assert output["status"] == "succeeded"
    assert output["output"] == "connected"


@pytest.mark.asyncio
async def test_runtime_status_reports_running_version_and_only_reported_cache_evidence(monkeypatch):
    monkeypatch.setattr(
        jarvis_agent,
        "_jarvis_runtime",
        lambda _payload: ("http://runtime.invalid/v1", "model-a", {}),
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [{
                    "id": "model-a",
                    "architecture": "qwen3moe",
                    "max_model_len": 8208,
                    "kv_cache_type": "q8_0",
                    "sliding_window": 4096,
                    "n_cpu_moe": 20,
                    "unverified_embedding_matrix": "ignore-me",
                }]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(jarvis_agent.httpx, "AsyncClient", lambda **_kwargs: Client())

    status = await jarvis_agent.runtime_status(owner="leo")

    assert status["application_version"] == APP_VERSION
    assert status["context"] == 8208
    assert status["model_memory_evidence"] == {
        "kv_cache_type": "q8_0",
        "sliding_window": 4096,
        "n_cpu_moe": 20,
    }
    assert "embedding" not in json.dumps(status).lower()
