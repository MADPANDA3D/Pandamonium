"""Regression coverage for constrained current-network inspection."""

import asyncio
import json

import pytest

from src import agent_loop
from src.action_protocol import _READ_ONLY
from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
from src.agent_tools import network_tools
from src.authority_protocol import action_effect_for
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.tool_security import NON_ADMIN_BLOCKED_TOOLS, PLAN_MODE_READONLY_TOOLS
from src.tool_execution import format_tool_result


REPRO = "Ok Jarvis make a diagram of my network please"


def _selected_tools(intent):
    tools = set()
    for domain in intent["domains"]:
        tools.update(agent_loop._DOMAIN_TOOL_MAP.get(domain, set()))
    return tools


def test_current_network_diagram_mounts_read_only_inspection_capability():
    intent = agent_loop._classify_agent_request([], REPRO)
    selected = _selected_tools(intent)

    assert intent["low_signal"] is False
    assert intent["domains"] == {"network_inspection"}
    assert selected == {"inspect_network"}
    assert any(
        "fixed, bounded, read-only probes" in rule
        for rule in agent_loop._domain_rules_for_tools(selected)
    )


def test_network_scope_followups_keep_the_original_inspection_intent():
    first_followup = "High‑level overview (e.g., LAN, Wi‑Fi, internet)"
    messages = [
        {"role": "user", "content": REPRO},
        {
            "role": "assistant",
            "content": (
                "To create an accurate network diagram, could you tell me "
                "what elements you'd like included?"
            ),
        },
        {"role": "user", "content": first_followup},
    ]
    first_intent = agent_loop._classify_agent_request(messages, first_followup)

    assert first_intent["continuation"] is True
    assert "network_inspection" in first_intent["domains"]
    assert "inspect_network" in _selected_tools(first_intent)

    correction = (
        "That is not the network you're on; you need to run commands to verify it."
    )
    second_followup = "Provide detailed components"
    messages.extend([
        {"role": "assistant", "content": "Here is a typical home network."},
        {"role": "user", "content": correction},
        {
            "role": "assistant",
            "content": (
                "Could you provide the specific components or confirm the "
                "high-level layout?"
            ),
        },
        {"role": "user", "content": second_followup},
    ])
    second_intent = agent_loop._classify_agent_request(messages, second_followup)

    assert second_intent["continuation"] is True
    assert "network_inspection" in second_intent["domains"]
    assert "inspect_network" in _selected_tools(second_intent)


def test_first_person_current_network_phrasing_mounts_only_inspection_tool():
    prompts = (
        "Map the network I'm on",
        "Show the Wi-Fi I am connected to",
        "Draw the LAN I’m using",
        "What network am I connected to?",
        "Map my Wi‑Fi",
    )

    for prompt in prompts:
        intent = agent_loop._classify_agent_request([], prompt)
        assert "network_inspection" in intent["domains"]
        assert "files" not in intent["domains"]
        selected = _selected_tools(intent)
        assert "inspect_network" in selected
        assert selected.isdisjoint(agent_loop._DOMAIN_TOOL_MAP["files"])


def test_descriptive_current_network_phrasing_mounts_inspection_tool():
    prompts = (
        "Describe my network",
        "How is my home network configured?",
        "Is my network secure?",
        "What is my IP address?",
        "What is my default gateway?",
        "Which DNS server am I using?",
        "What devices are on my home network?",
    )

    for prompt in prompts:
        intent = agent_loop._classify_agent_request([], prompt)
        assert "network_inspection" in intent["domains"]
        assert "inspect_network" in _selected_tools(intent)


def test_current_network_only_request_discards_generic_web_and_ui_matches():
    prompts = (
        "Map my current network",
        "Show the Wi-Fi I am connected to",
    )

    for prompt in prompts:
        intent = agent_loop._classify_agent_request([], prompt)
        assert intent["domains"] == {"network_inspection"}
        assert _selected_tools(intent) == {"inspect_network"}


def test_current_network_compound_request_keeps_explicit_extra_capability():
    intent = agent_loop._classify_agent_request(
        [], "Open the network panel and map my current network"
    )

    assert intent["domains"] == {"network_inspection", "ui"}


def test_effective_current_network_tools_drop_retrieved_capabilities():
    retrieved = {
        "ask_user",
        "bash",
        "inspect_network",
        "python",
        "ui_control",
        "web_fetch",
    }

    network_only = agent_loop._clamp_network_inspection_tools(
        {"network_inspection"}, retrieved
    )
    compound = agent_loop._clamp_network_inspection_tools(
        {"network_inspection", "ui"}, retrieved
    )

    assert network_only == {"ask_user", "inspect_network"}
    assert compound == {"ask_user", "inspect_network", "ui_control"}


@pytest.mark.asyncio
async def test_live_catalog_clamps_retrieved_and_admin_keyword_tools(monkeypatch):
    captured = {}

    async def fake_stream(*args, **kwargs):
        captured["messages"] = args[1]
        captured["tools"] = kwargs.get("tools") or []
        yield 'data: {"delta":"Inspected."}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(agent_loop, "get_mcp_manager", lambda: None)
    monkeypatch.setattr(agent_loop, "blocked_tools_for_owner", lambda owner: set())
    monkeypatch.setattr(agent_loop, "stream_llm_with_fallback", fake_stream)

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": "How is my home network configured?"}],
        context_length=32_768,
        max_rounds=1,
        relevant_tools={"bash", "python", "ui_control", "web_fetch"},
    ):
        pass

    tool_names = {
        schema["function"]["name"]
        for schema in captured["tools"]
        if schema.get("function")
    }
    assert "inspect_network" in tool_names
    assert tool_names.isdisjoint({
        "bash",
        "python",
        "ui_control",
        "web_fetch",
        "manage_endpoints",
        "manage_settings",
    })
    assert all("manage_endpoints" not in message.get("content", "") for message in captured["messages"])

    compound_prompt = "Search the web and compare it with how my home network is configured"
    compound_intent = agent_loop._classify_agent_request([], compound_prompt)
    assert compound_intent["domains"] == {"network_inspection", "web"}

    async for _chunk in agent_loop.stream_agent_loop(
        "https://api.openai.com/v1/chat/completions",
        "gpt-4o",
        [{"role": "user", "content": compound_prompt}],
        context_length=32_768,
        max_rounds=1,
        relevant_tools={"inspect_network", "manage_settings", "web_fetch"},
    ):
        pass

    compound_tool_names = {
        schema["function"]["name"]
        for schema in captured["tools"]
        if schema.get("function")
    }
    assert {"inspect_network", "web_fetch"} <= compound_tool_names
    assert compound_tool_names.isdisjoint({
        "manage_endpoints",
        "manage_session",
        "manage_settings",
    })
    assert all(
        "manage_settings" not in message.get("content", "")
        for message in captured["messages"]
    )


def test_non_host_network_and_peripheral_questions_do_not_mount_inspection():
    prompts = (
        "Explain this neural network topology",
        "Show my social network connections",
        "Describe my application network graph",
        "Why can't my Bluetooth devices connect?",
        "Why is my network request failing?",
        "Why did my network response time out?",
        "Why is my device slow?",
        "Help me set up my device",
    )

    for prompt in prompts:
        intent = agent_loop._classify_agent_request([], prompt)
        assert "network_inspection" not in intent["domains"]
        assert "inspect_network" not in _selected_tools(intent)


def test_mixed_network_subject_keeps_explicit_current_host_inspection():
    prompt = "Map my home network and explain how it differs from a neural network"

    intent = agent_loop._classify_agent_request([], prompt)

    assert "network_inspection" in intent["domains"]
    assert "inspect_network" in _selected_tools(intent)


def test_inspection_tool_is_parameterless_owner_scoped_and_read_only():
    schema = next(
        item["function"]
        for item in FUNCTION_TOOL_SCHEMAS
        if item["function"]["name"] == "inspect_network"
    )

    assert schema["parameters"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert "inspect_network" in TOOL_TAGS
    assert "inspect_network" in TOOL_HANDLERS
    assert "inspect_network" in PLAN_MODE_READONLY_TOOLS
    assert "inspect_network" in NON_ADMIN_BLOCKED_TOOLS
    assert "inspect_network" in _READ_ONLY
    assert action_effect_for({"name": "inspect_network", "arguments": {}}) == "read"


def test_inspection_ignores_supplied_commands_and_executes_only_fixed_probes(monkeypatch):
    calls = []

    class FakeStream:
        def __init__(self, content):
            self.content = content

        async def read(self, size):
            chunk = self.content[:size]
            self.content = self.content[size:]
            return chunk

    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.stdout = FakeStream(b"verified output")
            self.stderr = FakeStream(b"")

        async def wait(self):
            return self.returncode

        def kill(self):
            raise AssertionError("a successful fixed probe must not be killed")

    async def fake_create_subprocess_exec(*argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr(
        network_tools,
        "_resolve_executable",
        lambda name, platform: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(
        network_tools.NetworkInspectionTool().execute(
            '{"command":"touch /tmp/should-not-run","path":"/etc/shadow"}',
            {"owner": "leo"},
        )
    )

    expected = [
        (f"/usr/bin/{argv[0]}", *argv[1:])
        for _name, argv in network_tools._platform_probes("linux")
    ]
    assert [argv for argv, _kwargs in calls] == expected
    assert all(kwargs["stdout"] == asyncio.subprocess.PIPE for _argv, kwargs in calls)
    snapshot = json.loads(result["output"])
    assert snapshot["inspection_available"] is True
    assert result["exit_code"] == 0
    assert "touch" not in result["output"]


def test_probe_pipe_reader_retains_only_the_fixed_prefix():
    class LargeStream:
        def __init__(self):
            self.remaining = network_tools._PROBE_OUTPUT_CHARS * 5

        async def read(self, size):
            if self.remaining <= 0:
                return b""
            amount = min(size, self.remaining)
            self.remaining -= amount
            return b"x" * amount

    retained, total = asyncio.run(network_tools._read_probe_stream(LargeStream()))
    rendered = network_tools._decode_probe_stream(retained, total)

    assert len(retained) == network_tools._PROBE_OUTPUT_CHARS
    assert total == network_tools._PROBE_OUTPUT_CHARS * 5
    assert len(rendered) <= network_tools._PROBE_OUTPUT_CHARS
    assert f"{total} bytes total" in rendered


def test_cancelled_probe_terminates_process_and_reader_tasks(monkeypatch):
    class HangingStream:
        def __init__(self):
            self.cancelled = False

        async def read(self, size):
            del size
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    class HangingProcess:
        def __init__(self):
            self.returncode = None
            self.stdout = HangingStream()
            self.stderr = HangingStream()
            self.stopped = asyncio.Event()
            self.killed = False

        async def wait(self):
            await self.stopped.wait()
            self.returncode = -9
            return self.returncode

        def kill(self):
            self.killed = True
            self.stopped.set()

    process = HangingProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        del args, kwargs
        return process

    monkeypatch.setattr(network_tools, "_resolve_executable", lambda *args: "/usr/bin/ip")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def cancel_probe():
        task = asyncio.create_task(
            network_tools._run_probe(("ip", "route", "show"), "linux")
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_probe())

    assert process.killed is True
    assert process.returncode == -9
    assert process.stdout.cancelled is True
    assert process.stderr.cancelled is True


def test_fixed_linux_table_read_is_bounded(monkeypatch):
    observed = {}

    class LargeTable:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            observed["size"] = size
            return "x" * size

    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: LargeTable())

    rendered = network_tools._read_linux_kernel_table("/proc/net/route")

    assert observed["size"] == network_tools._LINUX_KERNEL_TABLE_CHARS + 1
    assert len(rendered) <= network_tools._LINUX_KERNEL_TABLE_CHARS
    assert rendered.endswith("... (truncated at fixed table budget)")


def test_supported_native_platforms_have_fixed_read_only_probes():
    assert network_tools._platform_family("linux") == "linux"
    assert network_tools._platform_family("darwin") == "macos"
    assert network_tools._platform_family("win32") == "windows"

    expected_commands = {
        "linux": {"ip", "tailscale"},
        "macos": {"ifconfig", "route", "scutil", "arp", "tailscale"},
        "windows": {"ipconfig", "route", "arp", "tailscale"},
    }
    for platform, commands in expected_commands.items():
        probes = network_tools._platform_probes(platform)
        trusted = network_tools._trusted_executables(platform)
        assert {argv[0] for _name, argv in probes} == commands
        assert commands <= trusted.keys()
        assert all("shell" not in argv for _name, argv in probes)

    mac_paths = network_tools._trusted_executables("darwin")["tailscale"]
    assert "/opt/homebrew/bin/tailscale" in mac_paths
    windows_paths = network_tools._trusted_executables("win32")
    assert all(
        any(path.lower().endswith(f"\\system32\\{command}.exe") for path in paths)
        for command, paths in windows_paths.items()
        if command != "tailscale"
    )


def test_linux_container_uses_internal_network_fallback_without_iproute2(monkeypatch):
    async def unavailable_probe(argv, platform):
        return {
            "available": False,
            "command": " ".join(argv),
            "error": f"{argv[0]} is unavailable",
        }

    monkeypatch.setattr(network_tools, "_platform_family", lambda platform=None: "linux")
    monkeypatch.setattr(network_tools, "_run_probe", unavailable_probe)
    monkeypatch.setattr(network_tools.socket, "if_nameindex", lambda: [(1, "lo"), (2, "eth0")])
    monkeypatch.setattr(
        network_tools.socket,
        "getaddrinfo",
        lambda hostname, port: [(2, 1, 6, "", ("172.17.0.2", 0))],
    )
    monkeypatch.setattr(
        network_tools,
        "_read_linux_kernel_table",
        lambda path: {
            "/proc/net/route": "Iface Destination Gateway",
            "/etc/resolv.conf": "nameserver 127.0.0.11",
        }.get(path, ""),
    )

    result = asyncio.run(network_tools.NetworkInspectionTool().execute("", {}))

    snapshot = json.loads(result["output"])
    assert snapshot["inspection_available"] is True
    assert snapshot["successful_probes"] == ["kernel_network"]
    assert snapshot["probes"]["kernel_network"]["exit_code"] == 0
    assert "eth0" in result["output"]
    assert "172.17.0.2" in result["output"]
    assert "resolver" in result["output"]


def test_combined_network_snapshot_has_one_bounded_formatter_payload(monkeypatch):
    async def large_probe(argv, platform):
        return {
            "available": True,
            "command": " ".join(argv),
            "exit_code": 0,
            "output": "x" * 8_000,
            "error": "e" * 8_000,
        }

    monkeypatch.setattr(network_tools, "_platform_family", lambda platform=None: "linux")
    monkeypatch.setattr(network_tools, "_run_probe", large_probe)
    monkeypatch.setattr(
        network_tools,
        "_linux_kernel_probe",
        lambda: {"available": True, "exit_code": 0, "output": "k" * 8_000},
    )

    result = asyncio.run(network_tools.NetworkInspectionTool().execute("", {}))
    formatted = format_tool_result("inspect_network", result)
    snapshot = json.loads(result["output"])

    assert set(result) == {"output", "exit_code"}
    assert len(result["output"]) <= 9_000
    assert len(formatted) < 9_200
    assert set(snapshot["probes"]) == {
        "kernel_network",
        "interfaces",
        "ipv4_routes",
        "ipv6_routes",
        "neighbors",
        "tailscale",
    }
    assert all(probe["output"] for probe in snapshot["probes"].values())
    assert snapshot["probes"]["tailscale"]["error"]
    assert "**data:**" not in formatted
    assert formatted.count("```\n") == 1


def test_unavailable_inspection_requires_an_honest_limitation():
    requirement = "If inspection is unavailable or fails"

    assert requirement in agent_loop._AGENT_RULES
    assert requirement in agent_loop._API_AGENT_RULES
    assert "do not fill gaps with a typical setup or memory" in agent_loop._AGENT_RULES


def test_conceptual_network_question_does_not_mount_shell_tools():
    intent = agent_loop._classify_agent_request([], "Explain what a network topology is")

    assert "files" not in intent["domains"]
    assert "bash" not in _selected_tools(intent)
    assert "network_inspection" not in intent["domains"]
