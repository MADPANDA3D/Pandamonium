from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import src.agent_loop as agent_loop
import src.agent_identity as agent_identity
import src.jarvis_agent as jarvis_agent
from routes import voice_routes
from routes.agent_task_routes import TaskApproval
from routes.voice_routes import (
    _approval_choice,
    _asks_current_business,
    _background_delegation,
    _background_delegations,
    _delegation_route,
    _explicit_reply_target,
    _foreground_command,
    _is_casual_greeting,
    _jarvis_vocative,
    _pending_task_accepts_turn,
    _selected_workspace,
    _server_routed_events,
    _target_switch,
    VoiceRespondRequest,
)
from src.agent_worker_adapters import HermesRunsAdapter, _hermes_instructions, _hermes_run_features
from src.extension_registry import ExtensionRegistry


def test_voice_intent_separates_foreground_switch_from_background_delegation():
    assert _target_switch("Talk to PC Codex") == "pc-codex"
    assert _target_switch("Please switch me back to Jarvis") == "jarvis"
    assert _target_switch("Connect me to Jarvis") == "jarvis"
    assert _target_switch("Talk about the result Hermes found") is None
    assert _target_switch("Ask PC Codex to inspect Mark 5") is None
    assert _target_switch("I would like to now talk to Hermes") == "hermes"
    assert _target_switch("I'd like to talk to Hermes") == "hermes"
    assert _target_switch("Can you please talk to Hermes?") == "hermes"
    assert _target_switch("Transfer me to Gordon") == "hermes"
    assert _target_switch("Put me on the phone with Gordon") == "hermes"
    assert _target_switch("Put me through to Gordon") == "hermes"
    assert _target_switch("I would like to be transferred to Gordon") == "hermes"
    assert _target_switch("Transfer me to Friday") == "pc-codex"
    assert _target_switch("Friday, transfer me back to Jarvis") == "jarvis"
    assert _target_switch("Friday, transfer me to Gordon") == "hermes"
    assert _target_switch("Do me a favor, can you transfer me back to Jarvis, please?") == "jarvis"
    assert _target_switch(
        "All right Jarvis great work can you do me a favor and transfer me to Gordon please?"
    ) == "hermes"
    assert _target_switch("Talk to my PC") == "pc-codex"
    assert _target_switch("Talk to PC Codex about Hermes") == "pc-codex"
    assert _target_switch("Talk to Hermes about the VPS") == "hermes"
    long_hermes_request = (
        "Can you also do me one single favor? I would like to ask Hermes how Hermes is doing, "
        "just a quick hey, and make sure that you're able to talk to Hermes as well."
    )
    assert _target_switch(long_hermes_request) is None
    assert _background_delegation(long_hermes_request) == ("hermes", "home-lab")
    assert _background_delegation("Ask PC Codex whether Hermes is reachable") == ("pc-codex", "madpanda3d")
    assert _background_delegation("Ask Hermes to review the VPS status") == ("hermes", "home-lab")
    compound = (
        "Pull up the Mark 7 document while you ask PC codecs to pull that up, then shoot a message "
        "over to Hermes and ask for an update."
    )
    assert _background_delegations(compound) == [
        ("pc-codex", "home-lab"),
        ("hermes", "home-lab"),
    ]
    assert _background_delegations("Ask PC Codex whether Hermes is reachable") == [
        ("pc-codex", "madpanda3d"),
    ]
    assert _background_delegations("Ask Hermes to open the Mark 7 document") == [
        ("hermes", "home-lab"),
    ]
    assert _delegation_route("Ask PC Codex to inspect Mark 5") == ("pc-codex", "home-lab")
    assert _delegation_route("Ask Codex on my PC for a client update") == ("pc-codex", "business")


def test_foreground_commands_are_narrow_and_client_state_rejects_unknown_fields():
    assert _foreground_command("Jarvis, open the Calendar") == ("open_view", "calendar")
    assert _foreground_command("Close this document") == ("close_view", "document")
    assert _foreground_command("Minimize the active document") == ("minimize_view", "document")
    assert _foreground_command("What view is open?") == ("report_view_state", None)
    assert _foreground_command("Open https://example.com") is None
    assert _foreground_command("Run this script in the page") is None

    with pytest.raises(ValidationError):
        VoiceRespondRequest.model_validate({
            "text": "Open Calendar",
            "client_state": {"active_view": "chat", "selector": "body"},
        })


@pytest.mark.asyncio
async def test_foreground_control_uses_allowlisted_ui_events_and_reported_state():
    state = {
        "target": "jarvis",
        "workspace": "home-lab",
        "_client_state": {
            "active_view": "calendar",
            "calendar": {"open": True, "minimized": False, "view": "week", "date": "2026-07-15"},
            "document": {"open": False, "minimized": True, "id": "doc-1"},
        },
    }
    opened = [
        event async for event in _server_routed_events(
            "chat-1", "Open Calendar", "leo", state,
        )
    ]
    assert opened[0] == {
        "type": "ui_control", "ui_event": "open_view", "view": "calendar",
    }
    assert opened[-1]["diagnostics"]["guard_reason"] == "foreground_open_view"

    reported = [
        event async for event in _server_routed_events(
            "chat-1", "What view is open?", "leo", state,
        )
    ]
    assert [event["type"] for event in reported] == ["assistant_delta", "final"]
    assert reported[-1]["assistant_text"] == (
        "Calendar is the active view in week view, centered on 2026-07-15."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Open the Mark 7 document",
        "Ask PC codecs to open the Mark 7 document",
    ],
)
async def test_odysseus_document_open_routes_to_background_pc_codex(text, monkeypatch):
    calls = []

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        calls.append((worker, workspace, prompt))
        return {"task_id": "pc-document", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    voice_session = {"target": "jarvis", "workspace": "home-lab", "active_task_id": None}
    events = [
        event async for event in _server_routed_events(
            "chat-1", text, "leo", voice_session,
        )
    ]

    assert calls[0][:2] == ("pc-codex", "home-lab")
    assert "Pandamonium is the default destination" in calls[0][2]
    assert "ODYSSEUS_ARTIFACT" in calls[0][2]
    task = next(event for event in events if event["type"] == "agent_task")
    assert task["foreground"] is False
    assert all(event["type"] != "target_changed" for event in events)
    assert voice_session["target"] == "jarvis"


@pytest.mark.asyncio
async def test_compound_voice_request_starts_pc_and_hermes_as_scoped_tasks(monkeypatch):
    calls = []

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        calls.append((worker, workspace, prompt))
        return {"task_id": f"{worker}-task", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    text = (
        "Jimmy a favor and pull up the Mark 7 document and while you ask PC codecs to pull that up "
        "go ahead and shoot a message over to Hermes and just ask for an update."
    )
    events = [
        event async for event in _server_routed_events(
            "chat-1", text, "leo", {"target": "jarvis", "workspace": "home-lab"},
        )
    ]

    assert [(worker, workspace) for worker, workspace, _ in calls] == [
        ("pc-codex", "home-lab"),
        ("hermes", "home-lab"),
    ]
    pc_prompt, hermes_prompt = calls[0][2], calls[1][2]
    assert "Handle only the work explicitly assigned to PC Codex" in pc_prompt
    assert "Handle only the work explicitly assigned to Hermes" in hermes_prompt
    assert "ODYSSEUS_ARTIFACT" in pc_prompt
    assert "ODYSSEUS_ARTIFACT" not in hermes_prompt
    assert [event["worker"] for event in events if event["type"] == "agent_task"] == [
        "pc-codex", "hermes",
    ]
    assert events[-1]["task_ids"] == ["pc-codex-task", "hermes-task"]
    assert events[-1]["diagnostics"]["guard_reason"] == (
        "delegation_multi_pc-codex_started_hermes_started"
    )
    assert "PC Codex is opening the document in Pandamonium" in events[-1]["assistant_text"]
    assert "Hermes is handling its part" in events[-1]["assistant_text"]


@pytest.mark.asyncio
async def test_compound_dispatch_failure_does_not_block_the_other_worker(monkeypatch):
    calls = []

    async def dispatch(_session, worker, _workspace, _prompt, _owner, _voice):
        calls.append(worker)
        if worker == "pc-codex":
            raise RuntimeError("pc bridge unavailable")
        return {"task_id": "hermes-task", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Ask PC Codex to open the Mark 7 document and shoot a message to Hermes for an update.",
            "leo",
            {"target": "jarvis", "workspace": "home-lab"},
        )
    ]

    assert calls == ["pc-codex", "hermes"]
    assert [event["task_id"] for event in events if event["type"] == "agent_task"] == ["hermes-task"]
    assert events[-1]["task_ids"] == ["hermes-task"]
    assert "PC Codex is not connected" in events[-1]["assistant_text"]
    assert "Hermes is handling its part" in events[-1]["assistant_text"]


@pytest.mark.asyncio
async def test_do_it_again_replays_the_latest_completed_worker_request(monkeypatch):
    previous = {
        "task_id": "old-document-task",
        "worker": "pc-codex",
        "workspace": "home-lab",
        "session_id": "chat-1",
        "owner": "leo",
        "status": "failed",
        "prompt": "Open Mark 6 in Pandamonium with ODYSSEUS_ARTIFACT.",
    }
    calls = []

    monkeypatch.setattr(
        jarvis_agent,
        "require_task_owner",
        lambda task_id, owner: previous
        if task_id == previous["task_id"] and owner == previous["owner"]
        else (_ for _ in ()).throw(PermissionError("task_owner_mismatch")),
    )

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        calls.append((worker, workspace, prompt))
        return {"task_id": "retry-task", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Okay, ask it to do it again",
            "leo",
            {
                "target": "jarvis",
                "workspace": "home-lab",
                "active_task_id": None,
                "tasks": [{"task_id": previous["task_id"]}],
            },
        )
    ]

    assert calls == [("pc-codex", "home-lab", previous["prompt"])]
    assert next(event for event in events if event["type"] == "agent_task")["foreground"] is False
    assert events[-1]["diagnostics"]["guard_reason"] == "delegation_started_pc-codex"


@pytest.mark.asyncio
async def test_do_it_again_never_replays_another_owners_task(monkeypatch):
    monkeypatch.setattr(
        jarvis_agent,
        "require_task_owner",
        lambda _task_id, _owner: (_ for _ in ()).throw(PermissionError("task_owner_mismatch")),
    )

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("cross-owner retry must not dispatch")

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-alice",
            "Ask it to do it again",
            "alice",
            {
                "target": "jarvis",
                "workspace": "home-lab",
                "tasks": [{"task_id": "task-owned-by-bob"}],
            },
        )
    ]

    assert [event["type"] for event in events] == ["assistant_delta", "final"]
    assert events[-1]["diagnostics"]["guard_reason"] == "retry_task_missing"


@pytest.mark.asyncio
async def test_do_it_again_without_a_recent_task_asks_for_context(monkeypatch):
    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("an unbound retry must not invent a worker task")

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Ask it to do it again",
            "leo",
            {"target": "jarvis", "workspace": "home-lab", "active_task_id": None, "tasks": []},
        )
    ]

    assert [event["type"] for event in events] == ["assistant_delta", "final"]
    assert events[-1]["diagnostics"]["guard_reason"] == "retry_task_missing"


@pytest.mark.parametrize("band", ["morning", "afternoon", "evening"])
def test_explicit_greeting_etiquette_matches_leos_words(band):
    text = f"Good {band}, Jarvis."
    assert _is_casual_greeting(text)
    assert voice_routes._casual_greeting_reply(text, {"turns": []}) == (
        f"Good {band}, Leo. What are we working on?"
    )


def test_casual_greeting_and_approval_guards_are_deterministic():
    assert _is_casual_greeting("Hey Jarvis, how you doing?")
    assert _is_casual_greeting("What's up y'alls?")
    reply = voice_routes._casual_greeting_reply("Hey Jarvis, how you doing?", {"turns": []})
    assert "time" not in reply.lower()
    assert "london" not in reply.lower()
    assert _jarvis_vocative("Beautiful Jarvis. Great work.")
    assert _jarvis_vocative("Good evening, Jarvis.")
    assert not _jarvis_vocative("Tell Hermes what Jarvis said.")
    assert _approval_choice("Yes, approve it once") == "once"
    assert _approval_choice("No, deny it") == "deny"
    assert _approval_choice("Yes, no, wait") is None


def test_background_question_requires_foreground_or_explicit_reply_association():
    waiting = {"worker": "pc-codex", "status": "waiting"}
    assert _pending_task_accepts_turn(waiting, "Use the Acme account", "pc-codex")
    assert not _pending_task_accepts_turn(waiting, "Tell me a joke", "jarvis")
    assert _explicit_reply_target("Reply to PC Codex: use the Acme account") == "pc-codex"
    assert _pending_task_accepts_turn(waiting, "Reply to PC Codex: use Acme", "jarvis")


def test_background_approval_requires_a_clear_choice_when_jarvis_is_foreground():
    waiting = {"worker": "hermes", "status": "waiting_approval"}
    assert _pending_task_accepts_turn(waiting, "Approve it once", "jarvis")
    assert not _pending_task_accepts_turn(waiting, "Tell me what this approval means", "jarvis")


def test_selected_workspace_changes_only_when_the_turn_names_one():
    assert _selected_workspace("Keep checking that", "business") == "business"
    assert _selected_workspace("Inspect Project Linux and Hyprland", "home-lab") == "project-linux"
    assert _selected_workspace("Review the client CRM", "home-lab") == "business"
    assert _selected_workspace("Review this across all projects", "home-lab") == "madpanda3d"


def test_pc_codex_uses_company_root_for_cross_domain_work():
    assert _delegation_route("Ask PC Codex to inspect Charter") == ("pc-codex", "madpanda3d")
    assert _delegation_route("Ask PC Codex to review an Academic file") == ("pc-codex", "madpanda3d")
    assert _delegation_route("Ask PC Codex to inspect Mark 7") == ("pc-codex", "home-lab")
    assert _delegation_route("Ask PC Codex to review the client CRM") == ("pc-codex", "business")


@pytest.mark.parametrize("text", [
    "What's up with the business?",
    "Whats up with the business",
    "What is up with the business?",
    "What’s up with the business?",
    "How are things running with the business, with my clients right now? Just a quick rundown, nothing extensive.",
    "How are my clients doing?",
    "Give me a quick rundown on the business.",
    "What's up with Mad Panda 3D?",
])
def test_business_update_phrase_is_deterministic(text):
    assert _asks_current_business(text)


@pytest.mark.parametrize("text", [
    "Explain how a business works",
    "How are business taxes handled?",
])
def test_business_explanations_do_not_trigger_a_live_status_task(text):
    assert not _asks_current_business(text)


@pytest.mark.parametrize("text", [
    "What business tasks should I automate today?",
    "Give me a client update on the website code.",
    "How should I keep my business running?",
    "The business status page is broken.",
    "Build me a business status dashboard.",
    "Update on the business website code.",
    "What is happening with business taxes?",
    "Give me a rundown on the business status page implementation.",
])
def test_specific_business_work_does_not_trigger_a_portfolio_status_task(text):
    assert not _asks_current_business(text)


@pytest.mark.asyncio
async def test_yalls_greeting_does_not_dispatch_or_steer_an_active_worker(monkeypatch):
    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("a casual greeting must not dispatch or steer a worker")

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "What's up y'alls?",
            "leo",
            {"target": "jarvis", "workspace": "business", "active_task_id": "pc-task"},
        )
    ]

    assert [event["type"] for event in events] == ["assistant_delta", "final"]
    assert events[-1]["diagnostics"]["guard_reason"] == "casual_greeting"


@pytest.mark.asyncio
async def test_business_then_hermes_runs_as_distinct_background_tasks_with_jarvis_foreground(monkeypatch):
    prompts = []

    async def dispatch(_session, worker, workspace, _prompt, _owner, _voice):
        prompts.append((worker, workspace, _prompt))
        return {"task_id": f"{worker}-task", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    voice_session = {"target": "jarvis", "workspace": "home-lab", "active_task_id": None}

    business = [
        event async for event in _server_routed_events(
            "chat-1", "What's up with the business?", "leo", voice_session,
        )
    ]
    hermes = [
        event async for event in _server_routed_events(
            "chat-1", "While he works, ask Hermes to check the architecture.", "leo", voice_session,
        )
    ]

    business_task = next(event for event in business if event["type"] == "agent_task")
    hermes_task = next(event for event in hermes if event["type"] == "agent_task")
    assert business_task == {
        "type": "agent_task", "task_id": "pc-codex-task", "worker": "pc-codex",
        "workspace": "business", "foreground": False,
        "presenter": voice_routes.configured_agent_name(),
    }
    assert hermes_task == {
        "type": "agent_task", "task_id": "hermes-task", "worker": "hermes",
        "workspace": "home-lab", "foreground": False,
        "presenter": voice_routes.configured_agent_name(),
    }
    assert "not current enough" in business[-1]["assistant_text"]
    assert all(event["type"] != "target_changed" for event in business + hermes)
    assert voice_session["target"] == "jarvis"
    business_prompt = prompts[0][2]
    assert len(business_prompt) < 1_000
    assert "at most three verified priorities" in business_prompt
    assert "250 words or fewer" in business_prompt
    assert "Retrieved background" not in business_prompt
    assert "any connected read-only systems" not in business_prompt


@pytest.mark.asyncio
async def test_long_hermes_request_stays_background_and_does_not_switch(monkeypatch):
    calls = []

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        calls.append((worker, workspace, prompt))
        return {"task_id": "hermes-ping", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    text = (
        "Can you also do me one single favor? I would like to ask Hermes how Hermes is doing, "
        "just a quick hey, and make sure that you're able to talk to Hermes as well."
    )
    events = [
        event async for event in _server_routed_events(
            "chat-1", text, "leo", {"target": "jarvis", "workspace": "business"},
        )
    ]

    assert calls[0][:2] == ("hermes", "home-lab")
    assert next(event for event in events if event["type"] == "agent_task")["foreground"] is False
    assert all(event["type"] != "target_changed" for event in events)


@pytest.mark.asyncio
async def test_selected_hermes_talks_directly_to_gordon_without_broker_task(monkeypatch):
    calls = []

    async def direct(session_id, text, *, owner, workspace):
        calls.append((session_id, text, owner, workspace))
        return "Good evening, Leo. This is Gordon."

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("a direct Gordon turn must not create a broker task")

    monkeypatch.setattr(jarvis_agent, "direct_hermes_turn", direct)
    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Is this Gordon?",
            "leo",
            {"target": "hermes", "workspace": "home-lab"},
        )
    ]

    assert calls == [("chat-1", "Is this Gordon?", "leo", "home-lab")]
    assert [event["type"] for event in events] == ["assistant_delta", "final"]
    assert events[0] == {
        "type": "assistant_delta",
        "text": "Good evening, Leo. This is Gordon.",
        "model": "Hermes",
    }
    assert events[-1]["task_ids"] == []
    assert events[-1]["diagnostics"]["guard_reason"] == "direct_hermes"
    assert events[-1]["diagnostics"]["direct_target"] == "hermes"
    assert events[-1]["diagnostics"]["character_name"] == "Hermes"


@pytest.mark.asyncio
async def test_selected_hermes_greeting_reaches_gordon(monkeypatch):
    calls = []

    async def direct(session_id, text, *, owner, workspace):
        calls.append((session_id, text, owner, workspace))
        return "Good evening, Leo. Gordon here."

    def must_not_use_jarvis_greeting(*_args, **_kwargs):
        raise AssertionError("Jarvis greeting handling captured a direct Gordon turn")

    monkeypatch.setattr(jarvis_agent, "direct_hermes_turn", direct)
    monkeypatch.setattr(voice_routes, "_casual_greeting_reply", must_not_use_jarvis_greeting)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Good evening, how are you?",
            "leo",
            {"target": "hermes", "workspace": "home-lab"},
        )
    ]

    assert calls == [("chat-1", "Good evening, how are you?", "leo", "home-lab")]
    assert events[-1]["assistant_text"] == "Good evening, Leo. Gordon here."
    assert events[-1]["diagnostics"]["guard_reason"] == "direct_hermes"


@pytest.mark.asyncio
async def test_direct_gordon_failure_does_not_fall_back_to_jarvis_broker(monkeypatch):
    async def fail_direct(*_args, **_kwargs):
        raise RuntimeError("Hermes direct endpoint unavailable")

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("direct failure must not become a background Hermes task")

    monkeypatch.setattr(jarvis_agent, "direct_hermes_turn", fail_direct)
    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Are you there, Gordon?",
            "leo",
            {"target": "hermes", "workspace": "home-lab"},
        )
    ]

    assert [event["type"] for event in events] == ["assistant_delta", "final"]
    assert all(event["type"] != "agent_task" for event in events)
    assert events[-1]["task_ids"] == []
    assert events[-1]["diagnostics"]["guard_reason"] == "direct_hermes_unavailable"
    assert events[-1]["diagnostics"]["character_name"] == "Pandamonium"
    assert "did not send that through Jarvis" in events[-1]["assistant_text"]


@pytest.mark.asyncio
async def test_jarvis_selected_ask_hermes_stays_background_brokered(monkeypatch):
    calls = []

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        calls.append((worker, workspace, prompt))
        return {"task_id": "hermes-background", "worker": worker}, "started"

    async def must_not_call_direct(*_args, **_kwargs):
        raise AssertionError("a Jarvis delegation must not enter direct Gordon chat")

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    monkeypatch.setattr(jarvis_agent, "direct_hermes_turn", must_not_call_direct)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Ask Hermes for an update.",
            "leo",
            {"target": "jarvis", "workspace": "home-lab"},
        )
    ]

    assert calls[0][:2] == ("hermes", "home-lab")
    task = next(event for event in events if event["type"] == "agent_task")
    assert task == {
        "type": "agent_task",
        "task_id": "hermes-background",
        "worker": "hermes",
        "workspace": "home-lab",
        "foreground": False,
        "presenter": voice_routes.configured_agent_name(),
    }
    assert events[-1]["diagnostics"]["guard_reason"] == "delegation_started_hermes"
    assert "direct_target" not in events[-1]["diagnostics"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_reply"),
    [
        ("Beautiful Jarvis. Great work.", "You’re back with Jarvis."),
        ("Good evening, Jarvis.", "Good evening, Leo. What are we working on?"),
    ],
)
async def test_direct_jarvis_address_returns_from_selected_worker_without_task(text, expected_reply, monkeypatch):
    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("a direct Jarvis address must not create a worker task")

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1", text, "leo", {"target": "hermes", "workspace": "home-lab"},
        )
    ]

    assert [event["type"] for event in events] == [
        "target_changed", "assistant_delta", "handoff_greeting", "final",
    ]
    assert events[0]["target"] == "jarvis"
    assert events[-1]["assistant_text"] == expected_reply


@pytest.mark.asyncio
async def test_polite_gordon_return_request_switches_before_the_model_sees_it():
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Do me a favor, can you transfer me back to Jarvis, please?",
            "leo",
            {"target": "hermes", "workspace": "home-lab"},
        )
    ]

    assert events[0] == {
        "type": "target_changed", "target": "jarvis", "workspace": "home-lab",
    }
    assert events[2] == {
        "type": "handoff_greeting", "target": "jarvis", "workspace": "home-lab",
    }
    assert events[-1]["diagnostics"]["guard_reason"] == "target_switch_jarvis"


@pytest.mark.asyncio
async def test_direct_jarvis_address_can_launch_background_task_in_same_turn(monkeypatch):
    calls = []

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        calls.append((worker, workspace, prompt))
        return {"task_id": "pc-background", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Jarvis, ask PC Codex to inspect the Mark 6 documentation.",
            "leo",
            {"target": "hermes", "workspace": "home-lab"},
        )
    ]

    assert calls[0][:2] == ("pc-codex", "home-lab")
    assert events[0] == {
        "type": "target_changed", "target": "jarvis", "workspace": "home-lab",
    }
    task = next(event for event in events if event["type"] == "agent_task")
    assert task["foreground"] is False
    assert events[-1]["diagnostics"]["guard_reason"] == "delegation_started_pc-codex"


@pytest.mark.asyncio
async def test_named_pc_business_update_uses_the_bounded_business_route(monkeypatch):
    calls = []

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        calls.append((worker, workspace, prompt))
        return {"task_id": "pc-business", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    voice_session = {"target": "jarvis", "workspace": "home-lab", "active_task_id": None}
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Ask Codex on my PC for a quick update on my clients.",
            "leo",
            voice_session,
        )
    ]

    assert calls[0][:2] == ("pc-codex", "business")
    assert "at most three verified priorities" in calls[0][2]
    assert events[-1]["diagnostics"]["guard_reason"] == "current_business_started"
    assert next(event for event in events if event["type"] == "agent_task")["foreground"] is False
    assert all(event["type"] != "target_changed" for event in events)
    assert voice_session["target"] == "jarvis"


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "expected_worker", "expected_workspace"), [
    ("Ask Hermes for the latest status on our clients.", "hermes", "home-lab"),
    ("Ask VPS Codex for the latest status on our clients.", "vps-codex", "vps-ops"),
])
async def test_named_non_pc_worker_is_not_overridden_by_business_guard(
    text,
    expected_worker,
    expected_workspace,
    monkeypatch,
):
    calls = []

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        calls.append((worker, workspace, prompt))
        return {"task_id": f"{worker}-task", "worker": worker}, "started"

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            text,
            "leo",
            {"target": "jarvis", "workspace": "home-lab", "active_task_id": None},
        )
    ]

    assert calls[0][:2] == (expected_worker, expected_workspace)
    assert events[-1]["diagnostics"]["guard_reason"] == f"delegation_started_{expected_worker}"


@pytest.mark.asyncio
async def test_target_switch_precedes_active_worker_dispatch(monkeypatch):
    async def statuses():
        return {"hermes": {"enabled": True}}

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("target switch must not dispatch or steer a task")

    monkeypatch.setattr(jarvis_agent, "worker_statuses", statuses)
    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)

    events = [
        event
        async for event in _server_routed_events(
            "chat-1",
            "All right Jarvis great work can you do me a favor and transfer me to Gordon please?",
            "leo",
            {"target": "pc-codex", "workspace": "home-lab", "active_task_id": "pc-task"},
        )
    ]

    assert [event["type"] for event in events] == [
        "assistant_delta", "target_changed", "handoff_greeting", "final",
    ]
    assert events[0]["text"] == "Transferring you to Hermes now—one moment, please."
    assert events[1]["target"] == "hermes"
    assert events[-1]["task_ids"] == []


@pytest.mark.asyncio
async def test_explicit_switch_wins_over_jarvis_vocative(monkeypatch):
    async def statuses():
        return {"pc-codex": {"enabled": True}}

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("an explicit switch must not dispatch a task")

    monkeypatch.setattr(jarvis_agent, "worker_statuses", statuses)
    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Jarvis, talk to PC Codex.",
            "leo",
            {"target": "hermes", "workspace": "home-lab"},
        )
    ]

    assert [event["type"] for event in events] == [
        "assistant_delta", "target_changed", "handoff_greeting", "final",
    ]
    assert events[1]["target"] == "pc-codex"
    assert events[-1]["diagnostics"]["guard_reason"] == "target_switch_pc-codex"


@pytest.mark.asyncio
async def test_friday_handoff_greeting_does_not_launch_a_deep_codex_task():
    greeting = await voice_routes._handoff_greeting(
        "pc-codex", "chat-1", "leo", "home-lab",
    )

    assert greeting["text"] == "PC Codex here, Leo. What are we working on?"
    assert greeting["target"] == "pc-codex"
    assert greeting["diagnostics"]["character_name"] == "PC Codex"
    assert greeting["diagnostics"]["model"] == "odysseus-router"


@pytest.mark.asyncio
async def test_foreground_friday_result_becomes_the_spoken_reply(monkeypatch):
    async def dispatch(*_args, **_kwargs):
        return {"task_id": "friday-task"}, "started"

    async def foreground(task_id, owner):
        assert (task_id, owner) == ("friday-task", "leo")
        return "completed", "Good evening, Leo. I’m ready."

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    monkeypatch.setattr(voice_routes, "_foreground_worker_result", foreground)
    monkeypatch.setattr(
        jarvis_agent,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(owner="leo")),
    )
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Friday, inspect the active project configuration.",
            "leo",
            {"target": "pc-codex", "workspace": "home-lab"},
        )
    ]

    assert events[-1]["assistant_text"] == "Good evening, Leo. I’m ready."
    assert events[-1]["diagnostics"]["guard_reason"] == "selected_completed_pc-codex"
    assert events[-1]["diagnostics"]["task_delivery_pending"] is False
    assert any(event.get("type") == "agent_task" and event.get("foreground") for event in events)


@pytest.mark.asyncio
async def test_timed_out_friday_task_is_released_for_later_delivery(monkeypatch):
    async def dispatch(*_args, **_kwargs):
        return {"task_id": "friday-task"}, "started"

    async def foreground(_task_id, _owner):
        return "timeout", ""

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    monkeypatch.setattr(voice_routes, "_foreground_worker_result", foreground)
    monkeypatch.setattr(
        jarvis_agent,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(owner="leo")),
    )
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Friday, inspect the active project configuration.",
            "leo",
            {"target": "pc-codex", "workspace": "home-lab"},
        )
    ]

    assert events[-1]["assistant_text"] == "PC Codex is still working. I’ll deliver the result here when it finishes."
    assert events[-1]["diagnostics"]["task_delivery_pending"] is True


def test_selected_friday_only_dispatches_explicit_work_requests():
    assert not voice_routes._selected_pc_codex_task_request(
        "Are you actually up and running, Friday?",
    )
    assert not voice_routes._selected_pc_codex_task_request(
        "We're getting there one piece at a time. It's all teamwork, wouldn't you say?",
    )
    assert not voice_routes._selected_pc_codex_task_request(
        "Check my Books library and list every title.",
    )
    assert not voice_routes._selected_pc_codex_task_request(
        "Find the book in my library that still needs OCR.",
    )
    assert voice_routes._selected_pc_codex_task_request(
        "Friday, inspect the active project's protocol configuration.",
    )
    assert voice_routes._selected_pc_codex_task_request(
        "Review the Books service source code.",
    )
    assert voice_routes._selected_pc_codex_task_request(
        "Check the server configuration.",
    )
    assert voice_routes._selected_pc_codex_task_request(
        "Read the repository file.",
    )
    assert voice_routes._selected_pc_codex_task_request("Friday, fix the tests.")
    assert voice_routes._selected_pc_codex_task_request("Review these files.")
    assert voice_routes._selected_pc_codex_task_request("Inspect the containers.")
    assert voice_routes._selected_pc_codex_task_request("Create a repository script.")
    assert voice_routes._selected_pc_codex_task_request("Start the project server.")
    assert voice_routes._selected_pc_codex_task_request("Stop the project service.")
    assert voice_routes._selected_pc_codex_task_request("Compare these files.")
    assert voice_routes._selected_pc_codex_task_request("Fix the authentication bug.")
    assert voice_routes._selected_pc_codex_task_request("Debug the API.")
    assert not voice_routes._selected_pc_codex_task_request("Update this scheduled task.")
    assert not voice_routes._selected_pc_codex_task_request("Fix that todo.")
    assert not voice_routes._selected_pc_codex_task_request("Change this reminder.")


@pytest.mark.asyncio
async def test_selected_friday_contextual_followup_steers_active_task(monkeypatch):
    dispatched = []

    monkeypatch.setattr(
        jarvis_agent,
        "find_active_task",
        lambda *_args, **_kwargs: {"task_id": "friday-task", "status": "running"},
    )
    monkeypatch.setattr(jarvis_agent, "list_active_tasks", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        voice_routes,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(owner="leo")),
    )

    async def dispatch(*args, **_kwargs):
        dispatched.append(args)
        return {"task_id": "friday-task"}, "steered"

    async def foreground(_task_id, _owner):
        return "completed", "I applied that follow-up."

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
    monkeypatch.setattr(voice_routes, "_foreground_worker_result", foreground)
    events = [
        event async for event in voice_routes._jarvis_events(
            "chat-1",
            "Fix that.",
            "leo",
            {"target": "pc-codex", "origin_target": "jarvis", "workspace": "home-lab"},
        )
    ]

    assert dispatched
    assert events[-1]["assistant_text"] == "I applied that follow-up."
    assert events[-1]["diagnostics"]["guard_reason"] == "selected_completed_pc-codex"


@pytest.mark.asyncio
async def test_selected_friday_conversation_uses_voice_model_without_task_tools(monkeypatch):
    captured = {}

    async def model_stream(_endpoint_url, _model, messages, **kwargs):
        captured["messages"] = messages
        captured["disabled_tools"] = kwargs["disabled_tools"]
        captured["relevant_tools"] = kwargs["relevant_tools"]
        yield 'data: {"delta":"I am up and running, Leo."}'
        yield 'data: {"type":"metrics","data":{}}'
        yield "data: [DONE]"

    monkeypatch.setattr(voice_routes, "stream_agent_loop", model_stream)
    monkeypatch.setattr(
        voice_routes,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(
            endpoint_url="http://jarvis.test/v1/chat/completions",
            model="jarvis-model",
            headers={},
            get_context_messages=lambda: [{"role": "user", "content": "Are you there?"}],
        )),
    )

    events = [
        event async for event in voice_routes._jarvis_events(
            "chat-1",
            "Are you actually up and running, Friday?",
            "leo",
            {"target": "pc-codex", "origin_target": "jarvis", "workspace": "home-lab"},
        )
    ]

    assert events[-1]["assistant_text"] == "I am up and running, Leo."
    assert events[-1]["diagnostics"]["guard_reason"] == "friday_conversation"
    assert events[-1]["diagnostics"]["character_name"] == "PC Codex"
    assert captured["messages"][0]["content"] == voice_routes.FRIDAY_VOICE_SYSTEM_PROMPT
    assert "start_agent_task" not in captured["relevant_tools"]
    assert "read_agent_task" not in captured["relevant_tools"]


@pytest.mark.asyncio
async def test_voice_forwards_the_canonical_authority_decision_and_speaks_one_summary(monkeypatch):
    decision = {
        "decision_id": "decision-voice-1",
        "decision": "approval_required",
        "action_effect": "destructive_or_difficult_to_recover",
        "gate_reason": "destructive_or_difficult_to_recover",
        "capability": {"name": "delete_email", "target": "tool"},
        "preview": {"uid": "7"},
    }

    async def model_stream(*_args, **_kwargs):
        yield "data: " + json.dumps({"type": "authority_approval_required", "data": decision})
        yield 'data: {"delta":"A second confirmation should not be spoken."}'
        yield 'data: {"type":"metrics","data":{}}'
        yield "data: [DONE]"

    monkeypatch.setattr(voice_routes, "stream_agent_loop", model_stream)
    monkeypatch.setattr(
        voice_routes,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(
            endpoint_url="http://jarvis.test/v1/chat/completions",
            model="jarvis-model",
            headers={},
            get_context_messages=lambda: [],
        )),
    )

    events = [
        event async for event in voice_routes._jarvis_events(
            "chat-1", "Delete email seven", "leo", {"target": "jarvis"},
        )
    ]

    assert events[0] == {"type": "authority_approval_required", "data": decision}
    assert events[1]["text"] == "Approval required for delete_email. I opened the exact decision in chat."
    assert events[-1]["assistant_text"] == events[1]["text"]
    assert events[-1]["diagnostics"]["guard_reason"] == "authority_approval_required"


@pytest.mark.asyncio
async def test_voice_oracle_language_uses_the_shared_ui_control_action(monkeypatch):
    captured = {"calls": 0}

    async def model_stream(*_args, **_kwargs):
        captured["calls"] += 1
        yield 'data: {"type":"ui_control","data":{"ui_event":"oracle_protocol_engage"}}'
        yield 'data: {"delta":"ORACLE protocol engaged."}'
        yield 'data: {"type":"metrics","data":{}}'
        yield "data: [DONE]"

    monkeypatch.setattr(voice_routes, "stream_agent_loop", model_stream)
    monkeypatch.setattr(
        voice_routes,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(
            endpoint_url="http://jarvis.test/v1/chat/completions",
            model="jarvis-model",
            headers={},
            get_context_messages=lambda: [],
        )),
    )
    monkeypatch.setattr(
        voice_routes,
        "_oracle_protocol_intent",
        lambda *_args: (_ for _ in ()).throw(AssertionError("legacy phrase shortcut was used")),
    )
    session = {"target": "jarvis"}

    events = [
        event async for event in voice_routes._jarvis_events(
            "chat-1", "Engage the ORACLE protocol", "leo", session,
        )
    ]

    assert captured["calls"] == 1
    assert events[0] == {"type": "ui_control", "ui_event": "oracle_protocol_engage"}
    assert events[-1]["assistant_text"] == "ORACLE protocol engaged."
    assert session["oracle_protocol_active"] is True


@pytest.mark.asyncio
async def test_old_worker_question_does_not_capture_direct_gordon_turn(monkeypatch):
    monkeypatch.setattr(jarvis_agent, "get_task", lambda _task_id: {
        "task_id": "pc-question",
        "worker": "pc-codex",
        "session_id": "chat-1",
        "status": "waiting",
        "owner": "leo",
    })
    monkeypatch.setattr(jarvis_agent, "find_active_task", lambda *_args: None)
    monkeypatch.setattr(jarvis_agent, "list_active_tasks", lambda *_args, **_kwargs: [])

    async def direct(session_id, text, *, owner, workspace):
        assert (session_id, text, owner, workspace) == (
            "chat-1", "Handle this with Hermes.", "leo", "home-lab",
        )
        return "I have this, Leo."

    async def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("a selected Hermes turn must not create a broker task")

    async def must_not_reply(*_args, **_kwargs):
        raise AssertionError("the old PC question captured a Hermes turn")

    monkeypatch.setattr(jarvis_agent, "direct_hermes_turn", direct)
    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", must_not_dispatch)
    monkeypatch.setattr(jarvis_agent, "task_action", must_not_reply)

    events = [
        event
        async for event in _server_routed_events(
            "chat-1",
            "Handle this with Hermes.",
            "leo",
            {"target": "hermes", "workspace": "home-lab", "active_task_id": "pc-question"},
        )
    ]

    assert all(event["type"] != "agent_task" for event in events)
    assert events[-1]["assistant_text"] == "I have this, Leo."
    assert events[-1]["diagnostics"]["guard_reason"] == "direct_hermes"


@pytest.mark.asyncio
async def test_selected_codex_followup_steers_without_duplicate_persistence(monkeypatch):
    active = {
        "task_id": "task-1",
        "worker": "pc-codex",
        "session_id": "chat-1",
        "workspace": "business",
        "status": "running",
        "owner": "leo",
    }
    calls = []

    def find_active(session_id, worker, workspace=None, owner=None):
        calls.append(("find", session_id, worker, workspace, owner))
        return active

    async def action(task_id, name, payload, *, persist_user_message=True, owner=None):
        calls.append(("action", task_id, name, payload, persist_user_message, owner))
        return active

    monkeypatch.setattr(jarvis_agent, "find_active_task", find_active)
    monkeypatch.setattr(jarvis_agent, "task_action", action)

    task, result = await voice_routes._dispatch_worker_request(
        "chat-1",
        "pc-codex",
        "business",
        "Add the CRM check.",
        "leo",
        {},
    )

    assert task == active
    assert result == "steered"
    assert calls[0] == ("find", "chat-1", "pc-codex", "business", "leo")
    assert calls[1][:3] == ("action", "task-1", "steer")
    assert calls[1][3]["prompt"].startswith("[JARVIS_CONTEXT v1]")
    assert "exact_request(<=4000):\nAdd the CRM check." in calls[1][3]["prompt"]
    assert calls[1][4:] == (False, "leo")


@pytest.mark.asyncio
async def test_new_pc_task_ignores_voice_global_thread_id(monkeypatch):
    captured = {}
    operational_events = []

    class Authority:
        def decide(self, call, **_kwargs):
            captured["action_call"] = call
            return {
                "decision_id": "decision-1",
                "decision": "allow",
                "permission_mode": "bounded_write",
                "action_effect": "reversible_write",
                "gate_reason": None,
                "policy_basis": "authenticated_explicit_request",
            }

    monkeypatch.setattr(jarvis_agent, "find_active_task", lambda *_args: None)
    monkeypatch.setattr(voice_routes, "authority_store", Authority())
    monkeypatch.setattr(
        voice_routes,
        "record_operational_event",
        lambda **values: operational_events.append(values) or {"event_id": f"event-{len(operational_events)}"},
    )

    async def start_task(
        worker,
        session_id,
        workspace,
        prompt,
        permission_mode,
        approved,
        owner,
        codex_thread_id=None,
        presenter=None,
    ):
        captured.update(
            worker=worker,
            session_id=session_id,
            workspace=workspace,
            prompt=prompt,
            permission_mode=permission_mode,
            approved=approved,
            owner=owner,
            codex_thread_id=codex_thread_id,
            presenter=presenter,
        )
        return {"task_id": "business-task", "status": "queued"}

    monkeypatch.setattr(jarvis_agent, "start_task", start_task)

    task, result = await voice_routes._dispatch_worker_request(
        "chat-1",
        "pc-codex",
        "business",
        "Inspect the CRM.",
        "leo",
        {"codex_thread_id": "019f5022-a520-7de0-9208-018cd2d4d222"},
    )

    assert task["task_id"] == "business-task"
    assert result == "started"
    assert captured["workspace"] == "business"
    assert captured["codex_thread_id"] is None
    assert captured["presenter"] == voice_routes.configured_agent_name()
    assert captured["action_call"]["target"] == "worker"
    assert captured["action_call"]["agent_id"] == "assistant"
    assert [event["event_type"] for event in operational_events] == [
        "started", "approval", "progress", "result",
    ]
    assert [event["status"] for event in operational_events] == [
        "requested", "authorized", "executed", "succeeded",
    ]
    assert operational_events[0]["request_id"] == operational_events[1]["request_id"]


def test_active_task_lookup_is_workspace_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(
        jarvis_agent,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(owner="leo")),
    )
    for task_id, workspace, updated in (
        ("home", "home-lab", 10),
        ("business", "business", 5),
        ("done", "business", 20),
    ):
        jarvis_agent._save_task({
            "task_id": task_id,
            "worker": "pc-codex",
            "session_id": "chat-1",
            "workspace": workspace,
            "status": "completed" if task_id == "done" else "running",
            "owner": "leo",
            "created_at": updated,
            "updated_at": updated,
        })

    assert jarvis_agent.find_active_task("chat-1", "pc-codex", "business", "leo")["task_id"] == "business"
    assert jarvis_agent.find_active_task("chat-1", "pc-codex", "project-linux", "leo") is None


def test_worker_approval_choices_are_narrow():
    assert TaskApproval(choice="once", spoken_text="Yes, approve that once.").choice == "once"
    with pytest.raises(ValidationError):
        TaskApproval(choice="everything")


def test_hermes_native_events_are_normalized_without_speaking_tools(tmp_path):
    adapter = HermesRunsAdapter("http://hermes", tmp_path / "token", enabled=False)
    tool = adapter._normalize({"event": "tool.started", "tool": "terminal"})
    progress = adapter._normalize({"event": "reasoning.available", "text": "Checking the service."})
    milestone = adapter._normalize({
        "event": "reasoning.available",
        "text": "[[ODYSSEUS_MILESTONE]] The service health check passed.",
    })
    approval = adapter._normalize({"event": "approval.request", "description": "Restart service?"})
    result = adapter._normalize({"event": "run.completed", "output": "Done."})
    assert tool["type"] == "tool_activity"
    assert progress == {
        "type": "progress",
        "text": "Checking the service.",
        "metadata": {"source_event": "reasoning.available"},
    }
    assert milestone == {
        "type": "progress",
        "text": "The service health check passed.",
        "metadata": {"source_event": "reasoning.available", "milestone": True},
    }
    assert approval == {
        "type": "approval_required",
        "text": "Restart service?",
        "metadata": {"event": "approval.request", "description": "Restart service?"},
    }
    assert result["type"] == "result"


def test_hermes_capability_names_match_current_runs_contract():
    assert _hermes_run_features({
        "run_submission": True,
        "run_events_sse": True,
        "run_stop": True,
        "run_approval_response": True,
    }) == {"runs": True, "stop": True, "approvals": True}
    assert _hermes_run_features({
        "run_submission": True,
        "run_events_sse": True,
        "run_stop": True,
        "run_approval": True,
    })["approvals"] is True


def test_hermes_instructions_preserve_broker_and_native_approval_boundaries():
    read_only = _hermes_instructions({"approved": False})
    approved = _hermes_instructions({"approved": True})

    assert "This run is read-only" in read_only
    assert "Do not attempt file changes" in read_only
    assert approved == read_only
    assert "mutation" not in approved
    assert "[[ODYSSEUS_MILESTONE]] <one completed-subtask update>" in read_only


@pytest.mark.asyncio
async def test_codex_thread_binding_is_scoped_to_session_and_workspace(tmp_path, monkeypatch):
    class FakeAdapter:
        worker = "pc-codex"
        enabled = True

        def __init__(self):
            self.started_with = []

        async def start(self, task):
            self.started_with.append(task.get("codex_thread_id"))
            return {"remote_task_id": f"remote-{len(self.started_with)}", "status": "queued"}

        async def events(self, _task):
            yield {
                "type": "tool_activity",
                "text": "Thread opened.",
                "metadata": {"codex_thread_id": "019f5022-a520-7de0-9208-018cd2d4d222"},
            }
            yield {"type": "result", "text": "Done.", "metadata": {}}

        async def status(self, _task):
            return {"status": "completed", "result": "Done."}

        async def reply(self, _task, _payload):
            return {}

        async def approve(self, _task, _payload):
            return {}

        async def cancel(self, _task):
            return {}

        async def health(self):
            return {"state": "connected"}

    adapter = FakeAdapter()
    monkeypatch.setattr(jarvis_agent, "TASKS_FILE", tmp_path / "agent_tasks.json")
    monkeypatch.setattr(
        jarvis_agent,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(owner="leo")),
    )
    monkeypatch.setattr(jarvis_agent, "_MIRRORS", {})
    monkeypatch.setattr(jarvis_agent, "adapters", lambda: {"pc-codex": adapter})
    monkeypatch.setattr(
        jarvis_agent,
        "worker_catalog",
        lambda: {
            "pc-codex": {
                "enabled": True,
                "machine": "workstation",
                "workspaces": ["home-lab"],
            }
        },
    )

    await jarvis_agent.start_task("pc-codex", "session-a", "home-lab", "first", owner="leo")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await jarvis_agent.start_task("pc-codex", "session-a", "home-lab", "second", owner="leo")
    assert adapter.started_with == [None, "019f5022-a520-7de0-9208-018cd2d4d222"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("opens calendar", ("open_view", "calendar")),
        ("what view is open right now", ("report_view_state", None)),
        ("do me a favor and minimize the document", ("minimize_view", "document")),
        (
            "I need you to close the document that is showing you know this is right now so that it goes away please",
            ("close_view", "document"),
        ),
    ],
)
def test_whisper_foreground_variants_are_bounded_and_deterministic(text, expected):
    assert voice_routes._foreground_command(text) == expected


def test_near_voice_controls_never_execute_or_fall_through():
    for text in (
        "Do not open Calendar",
        "Open your eyes and describe what you see",
        "Open https://example.test",
        "Run this script in the page",
    ):
        assert voice_routes._foreground_command(text) is None
        assert voice_routes._media_command(text) is None
        assert voice_routes._unsupported_voice_control(text)
    assert voice_routes._media_command("I want you to open your eyes") == "camera_open"
    assert voice_routes._media_command("need something motivational") == "media_motivation"
    assert not voice_routes._unsupported_voice_control("Tell me about the Calendar design")
    assert voice_routes._task_control_intent("Hermes mentioned that approve means yes") is None
    assert voice_routes._task_control_intent("Don't cancel the Hermes task")[0] == "rejected"


def test_oracle_protocol_lifecycle_language_is_bounded_and_state_aware():
    session = {}
    assert voice_routes._oracle_protocol_intent(
        "Hey buddy, I might need some eyes in the sky",
        session,
    ) == "suggest"
    assert voice_routes._oracle_protocol_intent("yes", session) is None
    assert voice_routes._oracle_protocol_intent("engage the Oracle protocol", session) == "engage"
    assert voice_routes._oracle_protocol_intent("shutdown the protocol", session) is None
    assert voice_routes._oracle_protocol_intent(
        "shutdown the protocol",
        {"oracle_protocol_active": True},
    ) == "shutdown"
    assert voice_routes._oracle_protocol_intent("do not engage Oracle", session) is None


def _oracle_voice_session(*tool_names):
    return {
        "id": "voice-1",
        "target": "jarvis",
        "oracle_protocol_active": True,
        "_client_state": {
            "oracle": {
                "ready": True,
                "style": "normal",
                "layers": [],
                "capabilities": {
                    "protocol": "oracle",
                    "version": "1",
                    "tools": [
                        {
                            "type": "function",
                            "name": name,
                            "description": f"Native {name} capability",
                            "parameters": {"type": "object", "properties": {}},
                        }
                        for name in tool_names
                    ],
                },
            },
        },
    }


def test_oracle_native_catalog_and_jarvis_prompt_are_authoritative():
    session = _oracle_voice_session(
        "fly_to_location",
        "set_visual_style",
        "control_cctv",
        "analyst_query",
        "control_cockpit",
    )
    specs = voice_routes._extension_tool_specs(session)
    assert [tool["name"] for tool in specs] == [
        "fly_to_location",
        "set_visual_style",
        "control_cctv",
        "analyst_query",
        "control_cockpit",
    ]
    assert [schema["function"]["name"] for schema in voice_routes._extension_tool_schemas(specs)] == [
        tool["name"] for tool in specs
    ]
    prompt = voice_routes._voice_system_prompt(session)
    assert "sole intelligence, identity, memory, and voice" in prompt
    assert "what you can do in ORACLE mode" in prompt
    assert "satellite over Tel Aviv and enable CCTV" in prompt
    assert "find a flight heading to Miami" in prompt
    assert "Moons out, Goons out" in prompt


@pytest.mark.asyncio
async def test_text_chat_bridge_mounts_and_executes_real_oracle_native_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_routes, "VOICE_STATE_FILE", tmp_path / "voice_sessions.json")
    monkeypatch.setattr(voice_routes, "ORACLE_PROTOCOL_URL", "https://oracle.example.test/")
    monkeypatch.setattr(voice_routes, "extension_registry", SimpleNamespace(
        snapshot=lambda: {"extensions": {}},
        effective_capabilities=lambda _engaged: {},
    ))
    voice_routes._save_state({
        "sessions": {
            "text-bridge-1": {
                "id": "text-bridge-1",
                "owner": "leo",
                "chat_session_id": "chat-1",
                "oracle_protocol_active": False,
                "engaged_extensions": [],
            },
        },
        "actions": {},
    })
    client_state = {
        "active_view": "chat",
        "extensions": {
            "oracle": {
                "ready": True,
                "updated_at_ms": 1,
                "state": {"map": "current"},
                "capabilities": {
                    "protocol": "oracle",
                    "version": "1",
                    "tools": [{
                        "type": "function",
                        "name": "get_current_view_state",
                        "description": "Read the current map view",
                        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                    }],
                },
            },
        },
    }

    bridge = voice_routes.prepare_text_extension_bridge(
        "text-bridge-1", "chat-1", "leo", "oracle", client_state,
    )

    assert bridge["tool_names"] == {"get_current_view_state"}
    assert bridge["extra_tool_schemas"][0]["function"]["name"] == "get_current_view_state"
    assert bridge["extension_capabilities"] == {
        "get_current_view_state": {
            "extension_id": "oracle",
            "permission_mode": "read_only",
        }
    }
    persisted = voice_routes._load_state()["sessions"]["text-bridge-1"]
    assert persisted["oracle_protocol_active"] is True
    assert persisted["engaged_extensions"] == ["oracle"]

    progress = []

    async def receive_call(payload):
        progress.append(payload)
        call_id = payload["extension_call"]["call_id"]
        voice_routes._EXTENSION_TOOL_CALLS[("text-bridge-1", "oracle", call_id)]["future"].set_result({
            "ok": True,
            "action": "get_current_view_state",
            "camera": {"latitude": 25.76, "longitude": -80.19},
        })

    description, result = await bridge["tool_executor"](
        SimpleNamespace(tool_type="get_current_view_state", content="{}"),
        receive_call,
    )
    assert description == "ORACLE get_current_view_state"
    assert result["ok"] is True
    assert result["camera"]["latitude"] == 25.76
    assert progress[0]["extension_call"]["tool"] == "get_current_view_state"


def test_registered_extension_catalog_is_authoritative_and_disable_removes_it(tmp_path, monkeypatch):
    manifest = json.loads((
        Path(__file__).parent / "fixtures" / "extensions" / "atlas.manifest.json"
    ).read_text())
    revision = "a" * 40
    tool = {
        "name": "create_mesh",
        "description": "Create a mesh",
        "parameters": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
            "additionalProperties": False,
        },
    }
    registry = ExtensionRegistry(tmp_path / "extensions.json")
    registry.register(
        manifest,
        {
            "protocol_version": "jos-extension.v1",
            "extension_id": "atlas",
            "version": "1",
            "source_revision": revision,
            "tools": [tool],
        },
        source_revision=revision,
        health_available=True,
    )
    monkeypatch.setattr(voice_routes, "extension_registry", registry)
    session = {
        "id": "voice-1",
        "target": "jarvis",
        "engaged_extensions": ["atlas"],
        "_client_state": {
            "extensions": {
                "atlas": {
                    "ready": True,
                    "capabilities": {"protocol": "atlas", "version": "1", "tools": [tool]},
                },
            },
        },
    }

    specs = voice_routes._extension_tool_specs(session)

    assert [(item["name"], item["extension_id"], item["permission_mode"]) for item in specs] == [
        ("create_mesh", "atlas", "bounded_write")
    ]
    assert voice_routes._extension_context(session, specs) == {
        "atlas": {
            "engaged": True,
            "state_mounted": True,
            "tool_count": 1,
            "tool_names": ["create_mesh"],
        }
    }
    registry.disable("atlas")
    assert voice_routes._extension_tool_specs(session) == []
    assert voice_routes._extension_context(session, []) == {}


def test_primary_voice_keeps_configured_identity_across_oracle_lifecycle(monkeypatch):
    monkeypatch.setattr(agent_identity, "load_settings", lambda: {
        "agent_id": "atlas",
        "agent_display_name": "Atlas",
        "agent_constitution": "Stay accurate across every surface.",
        "agent_constitution_version": "2026.1",
    })
    offline = voice_routes._voice_system_prompt({"target": "jarvis", "oracle_protocol_active": False})
    online = voice_routes._voice_system_prompt(_oracle_voice_session("fly_to_location"))

    for prompt in (offline, online):
        assert "persistent agent identity is Atlas" in prompt
        assert "stable agent id: atlas" in prompt
        assert "Stay accurate across every surface." in prompt
    assert "ORACLE protocol is offline" in offline
    assert "ORACLE protocol is active" in online


def test_oracle_dynamic_native_calls_convert_without_a_static_tool_tag(caplog):
    blocks, used_native, converted = agent_loop._resolve_tool_blocks(
        "",
        [{"name": "set_visual_style", "arguments": '{"style":"thermal"}', "id": "call-1"}],
        round_num=1,
        is_api_model=True,
        extra_tool_names={"set_visual_style"},
    )
    assert used_native is True
    assert blocks == [agent_loop.ToolBlock("set_visual_style", '{"style":"thermal"}')]
    assert converted[0]["id"] == "call-1"
    assert "Unknown function call" not in caplog.text


@pytest.mark.asyncio
async def test_oracle_protocol_confirmation_engages_and_shutdown_hides(monkeypatch):
    monkeypatch.setattr(voice_routes, "ORACLE_PROTOCOL_URL", "https://oracle.example.test/")
    session = {"target": "jarvis"}

    suggested = [
        event async for event in _server_routed_events(
            "chat-1",
            "Hey buddy, I might need some eyes in the sky",
            "leo",
            session,
        )
    ]
    assert suggested[-1]["diagnostics"]["guard_reason"] == "oracle_protocol_confirmation"
    assert suggested[-1]["assistant_text"] == "Did you mean the ORACLE protocol, sir?"
    assert session["oracle_protocol_pending"] is True

    engaged = [
        event async for event in _server_routed_events(
            "chat-1", "Yes, I'm talking about the Oracle Protocol", "leo", session,
        )
    ]
    assert engaged[0] == {"type": "ui_control", "ui_event": "oracle_protocol_engage"}
    assert engaged[-1]["diagnostics"]["guard_reason"] == "oracle_protocol_engaged"
    assert engaged[-1]["diagnostics"]["inference"] is False
    assert session["oracle_protocol_active"] is True
    assert session["oracle_protocol_pending"] is False

    shutdown = [
        event async for event in _server_routed_events(
            "chat-1", "shutdown the protocol", "leo", session,
        )
    ]
    assert shutdown[0] == {"type": "ui_control", "ui_event": "oracle_protocol_shutdown"}
    assert shutdown[-1]["diagnostics"]["guard_reason"] == "oracle_protocol_shutdown"
    assert session["oracle_protocol_active"] is False


@pytest.mark.asyncio
async def test_oracle_native_executor_waits_for_the_real_browser_result(monkeypatch):
    monkeypatch.setattr(voice_routes, "ORACLE_PROTOCOL_URL", "https://oracle.example.test/")
    session = _oracle_voice_session("set_visual_style")
    executor = voice_routes._extension_tool_executor(
        session,
        "leo",
        voice_routes._extension_tool_specs(session),
    )
    progress = []

    async def receive_call(payload):
        progress.append(payload)
        call_id = payload["extension_call"]["call_id"]
        voice_routes._EXTENSION_TOOL_CALLS[("voice-1", "oracle", call_id)]["future"].set_result({
            "ok": True,
            "action": "set_visual_style",
            "style": "surveillance",
        })

    description, result = await executor(
        SimpleNamespace(tool_type="set_visual_style", content='{"style":"surveillance"}'),
        receive_call,
    )
    assert description == "ORACLE set_visual_style"
    assert result["ok"] is True
    assert progress[0]["extension_call"]["arguments"] == {"style": "surveillance"}
    assert progress[0]["extension_call"]["extension_id"] == "oracle"
    assert voice_routes._EXTENSION_TOOL_CALLS == {}


@pytest.mark.asyncio
async def test_extension_executor_rejects_bad_input_unknown_tools_and_times_out(monkeypatch):
    monkeypatch.setattr(voice_routes, "ORACLE_PROTOCOL_URL", "https://oracle.example.test/")
    session = _oracle_voice_session("set_visual_style")
    specs = voice_routes._extension_tool_specs(session)
    executor = voice_routes._extension_tool_executor(session, "leo", specs)

    assert await executor(SimpleNamespace(tool_type="shell", content="{}"), lambda _payload: None) is None
    description, malformed = await executor(
        SimpleNamespace(tool_type="set_visual_style", content="[]"),
        lambda _payload: None,
    )
    assert description == "ORACLE set_visual_style"
    assert malformed["ok"] is False
    assert "JSON object" in malformed["error"]

    monkeypatch.setattr(voice_routes, "EXTENSION_TOOL_TIMEOUT_SECONDS", 0.001)

    async def accept(_payload):
        return None

    description, timed_out = await executor(
        SimpleNamespace(tool_type="set_visual_style", content='{"style":"thermal"}'),
        accept,
    )
    assert description == "ORACLE set_visual_style"
    assert "time" in timed_out["error"]
    assert voice_routes._EXTENSION_TOOL_CALLS == {}


@pytest.mark.asyncio
async def test_oracle_requests_use_jarvis_native_tool_loop_and_actual_reply(monkeypatch):
    calls = []

    async def model_stream(_endpoint_url, _model, messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        if kwargs["context_extensions"].get("oracle", {}).get("engaged"):
            yield "data: " + json.dumps({
                "type": "tool_progress",
                "tool": "set_visual_style",
                "extension_call": {
                    "call_id": "oracle_call_1",
                    "extension_id": "oracle",
                    "tool": "set_visual_style",
                    "arguments": {"style": "surveillance"},
                },
            })
            yield "data: " + json.dumps({
                "type": "tool_output",
                "tool": "set_visual_style",
                "output": '{"ok":true,"style":"surveillance"}',
            })
            yield "data: " + json.dumps({
                "type": "tool_progress",
                "tool": "fly_to_location",
                "extension_call": {
                    "call_id": "oracle_call_2",
                    "extension_id": "oracle",
                    "tool": "fly_to_location",
                    "arguments": {"query": "Tel Aviv"},
                },
            })
            yield "data: " + json.dumps({
                "type": "tool_output",
                "tool": "fly_to_location",
                "output": '{"ok":true,"label":"Tel Aviv"}',
            })
            yield 'data: {"delta":"Night vision is active."}'
        else:
            yield 'data: {"delta":"ORACLE is offline."}'
        yield 'data: {"type":"metrics","data":{"rounds":2}}'
        yield "data: [DONE]"

    monkeypatch.setattr(voice_routes, "stream_agent_loop", model_stream)
    monkeypatch.setattr(
        voice_routes,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(
            endpoint_url="http://jarvis.test/v1/chat/completions",
            model="jarvis-model",
            headers={},
            get_context_messages=lambda: [],
        )),
    )
    session = _oracle_voice_session(
        "set_visual_style",
        "fly_to_location",
        "control_cctv",
        "analyst_query",
        "control_cockpit",
    )

    events = [
        event async for event in voice_routes._jarvis_events(
            "chat-1", "Moons out, Goons out", "leo", session,
        )
    ]

    assert events[0] == {
        "type": "ui_control",
        "ui_event": "extension_protocol_command",
        "call_id": "oracle_call_1",
        "extension_id": "oracle",
        "tool": "set_visual_style",
        "arguments": {"style": "surveillance"},
        "server_managed": True,
    }
    assert events[1]["extension_id"] == "oracle"
    assert events[1]["tool"] == "fly_to_location"
    assert events[-1]["assistant_text"] == "Night vision is active."
    assert events[-1]["diagnostics"]["inference"] is True
    assert events[-1]["diagnostics"]["guard_reason"] == "extension_native_tools"
    assert callable(calls[0]["kwargs"]["tool_executor"])
    assert set(calls[0]["kwargs"]["relevant_tools"]) >= {
        "set_visual_style", "fly_to_location", "control_cctv", "analyst_query", "control_cockpit",
    }
    assert [schema["function"]["name"] for schema in calls[0]["kwargs"]["extra_tool_schemas"]] == [
        "set_visual_style", "fly_to_location", "control_cctv", "analyst_query", "control_cockpit",
    ]
    assert calls[0]["kwargs"]["context_extensions"]["oracle"] == {
        "engaged": True,
        "state_mounted": True,
        "tool_count": 5,
        "tool_names": [
            "set_visual_style", "fly_to_location", "control_cctv",
            "analyst_query", "control_cockpit",
        ],
    }
    assert "Moons out, Goons out" in calls[0]["messages"][0]["content"]

    session["oracle_protocol_active"] = False
    offline_events = [
        event async for event in voice_routes._jarvis_events(
            "chat-1", "Are we still in ORACLE?", "leo", session,
        )
    ]
    assert offline_events[-1]["assistant_text"] == "ORACLE is offline."
    assert calls[1]["kwargs"]["extra_tool_schemas"] == []
    assert calls[1]["kwargs"]["context_extensions"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "I do not want you to cancel the Hermes task",
        "Cancel none of the tasks",
        "Cancel zero Hermes tasks",
        "Approve zero Hermes requests",
        "Do nothing to the Hermes task",
        "Cancel neither Hermes nor PC Codex",
        "I want nothing cancelled for Hermes",
    ],
)
async def test_negative_task_controls_fail_closed_without_broker_action(text, monkeypatch):
    monkeypatch.setattr(
        jarvis_agent,
        "list_active_tasks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("negative control reached broker lookup")),
    )
    events = [
        event async for event in _server_routed_events(
            "chat-1", text, "leo", {"target": "jarvis", "workspace": "home-lab"},
        )
    ]
    assert events[-1]["diagnostics"]["guard_reason"] == "worker_control_rejected"
    assert events[-1]["task_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Always approve Hermes",
        "Approve Hermes for this session",
        "Approve every Hermes request",
        "Approve all requests for Hermes",
        "Approve all future Hermes requests",
        "Approve Hermes until further notice",
        "Approve them all for Hermes",
        "For this session approve the Hermes request",
        "From now on approve the Hermes request",
        "Until further notice approve the Hermes request",
    ],
)
async def test_persistent_voice_approval_is_deterministically_refused(text, monkeypatch):
    monkeypatch.setattr(
        jarvis_agent,
        "list_active_tasks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("persistent approval reached broker lookup")),
    )
    events = [
        event async for event in _server_routed_events(
            "chat-1", text, "leo", {"target": "jarvis"},
        )
    ]
    assert events[-1]["diagnostics"]["guard_reason"] == "worker_approval_persistent_refused"
    assert "did not grant persistent approval" in events[-1]["assistant_text"]


@pytest.mark.asyncio
async def test_oversized_and_compound_task_controls_are_rejected_before_lookup(monkeypatch):
    monkeypatch.setattr(
        jarvis_agent,
        "list_active_tasks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unsafe control reached broker lookup")),
    )
    oversized = "Cancel the Hermes task " + ("x" * voice_routes.VOICE_CONTROL_MAX_CHARS)
    assert _approval_choice("Approve " + ("x" * voice_routes.VOICE_CONTROL_MAX_CHARS)) is None
    for text, reason in (
        (oversized, "worker_control_rejected"),
        ("Cancel Hermes or PC Codex task", "worker_control_compound"),
        ("Cancel Hermes, PC Codex task", "worker_control_compound"),
        ("Cancel the Hermes task; open Calendar", "worker_control_compound"),
        ("Cancel the Hermes task. Open Calendar", "worker_control_compound"),
        ("Cancel the Hermes task plus open Calendar", "worker_control_compound"),
    ):
        events = [
            event async for event in _server_routed_events(
                "chat-1", text, "leo", {"target": "jarvis"},
            )
        ]
        assert events[-1]["diagnostics"]["guard_reason"] == reason
        assert events[-1]["task_ids"] == []


@pytest.mark.asyncio
async def test_unsafe_task_control_bypasses_llm_fallback(monkeypatch):
    monkeypatch.setattr(
        voice_routes,
        "_SESSION_MANAGER",
        SimpleNamespace(get_session=lambda _session_id: SimpleNamespace(get_context_messages=lambda: [])),
    )

    async def must_not_call_model(*_args, **_kwargs):
        raise AssertionError("unsafe task control reached the LLM")
        yield ""  # pragma: no cover

    monkeypatch.setattr(voice_routes, "stream_agent_loop", must_not_call_model)
    events = [
        event async for event in voice_routes._jarvis_events(
            "chat-1",
            "I do not want you to cancel the Hermes task",
            "leo",
            {"target": "jarvis"},
        )
    ]
    assert events[-1]["diagnostics"]["guard_reason"] == "worker_control_rejected"


def test_question_reply_grammar_does_not_capture_ordinary_delegation():
    assert voice_routes._task_control_intent("Tell Hermes that the service is healthy") is None
    assert voice_routes._background_delegation("Tell Hermes that the service is healthy") == (
        "hermes",
        "home-lab",
    )
    assert voice_routes._task_control_intent("Tell Hermes the answer is yes")[0] == "reply"
    assert voice_routes._task_control_intent("Tell Hermes use the Acme account")[0] == "reply"
    assert voice_routes._task_control_intent("Reply to PC Codex: use Acme")[0] == "reply"
    assert voice_routes._task_control_intent("Reply later") is None


def test_task_control_allows_polite_comma_but_rejects_multi_worker_compound():
    assert voice_routes._task_control_intent("Cancel the Hermes task, please.") == (
        "cancel",
        "hermes",
        None,
    )
    assert voice_routes._task_control_intent("Cancel the Hermes task.") == (
        "cancel",
        "hermes",
        None,
    )
    assert voice_routes._task_control_intent("Cancel Hermes, PC Codex task")[0] == "invalid"


def test_descriptive_worker_prose_stays_outside_task_control_routing():
    assert voice_routes._task_control_intent("Hermes found nothing wrong with the task") is None
    assert voice_routes._task_control_intent("Why does Hermes always ask me to approve requests?") is None


def test_worker_context_envelope_is_bounded_and_logical_only():
    envelope = voice_routes._worker_context_envelope(
        "pc-codex",
        "home-lab",
        "read_only",
        "x" * 5000,
        {
            "turns": [
                {"role": "user", "text": "u" * 1500},
                {"role": "assistant", "text": "a" * 1500},
            ],
            "_client_state": {
                "active_view": "document",
                "document": {"open": True, "minimized": False, "id": "doc-1", "selector": "body"},
            },
            "_frame": {"bytes": b"private-frame"},
            "tasks": [{"events": [{"text": "private-event"}]}],
        },
    )

    request = envelope.split("exact_request(<=4000):\n", 1)[1].split("\nprior_exchange", 1)[0]
    prior = envelope.split("prior_exchange(<=2000):\n", 1)[1].split("\nclient_state", 1)[0]
    assert len(request) == 4000
    assert len(prior) <= 2000
    assert '"active_view":"document"' in envelope
    assert "selector" not in envelope
    assert "private-frame" not in envelope
    assert "private-event" not in envelope


@pytest.mark.asyncio
async def test_voice_cancel_uses_named_broker_task_not_browser_task_id(monkeypatch):
    hermes = {
        "task_id": "hermes-live",
        "worker": "hermes",
        "workspace": "home-lab",
        "status": "running",
        "permission_mode": "read_only",
        "owner": "leo",
    }
    calls = []

    def active(_session, _owner, worker=None, workspace=None, statuses=None):
        return [hermes] if worker == "hermes" and hermes["status"] in statuses else []

    async def action(task_id, name, payload=None, *, persist_user_message=True, owner=None):
        calls.append((task_id, name, payload, persist_user_message, owner))
        return hermes

    monkeypatch.setattr(jarvis_agent, "list_active_tasks", active)
    monkeypatch.setattr(jarvis_agent, "task_action", action)
    events = [
        event async for event in _server_routed_events(
            "chat-1",
            "Cancel the Hermes task",
            "leo",
            {"target": "jarvis", "workspace": "home-lab", "active_task_id": "stale-pc-task"},
        )
    ]

    assert calls == [("hermes-live", "cancel", None, False, "leo")]
    assert events[-1]["diagnostics"]["guard_reason"] == "worker_cancel_requested"
    assert events[-1]["assistant_text"].startswith("Cancellation requested for Hermes")


@pytest.mark.asyncio
async def test_voice_approval_obeys_broker_permission_and_named_task(monkeypatch):
    task = {
        "task_id": "hermes-approval",
        "worker": "hermes",
        "workspace": "home-lab",
        "status": "waiting_approval",
        "permission_mode": "read_only",
        "approved": False,
        "owner": "leo",
    }
    calls = []

    monkeypatch.setattr(jarvis_agent, "list_active_tasks", lambda *_args, **_kwargs: [task])

    async def action(task_id, name, payload=None, *, persist_user_message=True, owner=None):
        calls.append((task_id, name, payload, persist_user_message, owner))
        return task

    monkeypatch.setattr(jarvis_agent, "task_action", action)
    denied_write = [
        event async for event in _server_routed_events(
            "chat-1", "Approve the Hermes request once", "leo", {"target": "jarvis"},
        )
    ]
    assert calls == []
    assert denied_write[-1]["diagnostics"]["guard_reason"] == "worker_approval_not_authorized"

    denied = [
        event async for event in _server_routed_events(
            "chat-1", "Deny the Hermes request", "leo", {"target": "jarvis"},
        )
    ]
    assert calls[0][:3] == (
        "hermes-approval",
        "approval",
        {"choice": "deny", "spoken_text": "Deny the Hermes request"},
    )
    assert denied[-1]["diagnostics"]["guard_reason"] == "worker_approval_deny"


@pytest.mark.asyncio
async def test_named_worker_question_routes_through_broker(monkeypatch):
    task = {
        "task_id": "pc-question",
        "worker": "pc-codex",
        "workspace": "business",
        "status": "waiting",
        "owner": "leo",
        "events": [{"type": "question", "metadata": {"questions": [{"id": "account"}]}}],
    }
    calls = []
    monkeypatch.setattr(jarvis_agent, "list_active_tasks", lambda *_args, **_kwargs: [task])

    async def action(task_id, name, payload=None, *, persist_user_message=True, owner=None):
        calls.append((task_id, name, payload, persist_user_message, owner))
        return task

    monkeypatch.setattr(jarvis_agent, "task_action", action)
    events = [
        event async for event in _server_routed_events(
            "chat-1", "Reply to PC Codex: use the Acme account", "leo", {"target": "jarvis"},
        )
    ]

    assert calls[0][0:2] == ("pc-question", "reply")
    assert calls[0][2]["answers"] == {"account": "use the Acme account"}
    assert events[-1]["diagnostics"]["guard_reason"] == "worker_question_reply"


@pytest.mark.asyncio
async def test_unnamed_task_control_reports_ambiguity(monkeypatch):
    tasks = [
        {"task_id": "a", "worker": "hermes", "workspace": "home-lab", "status": "waiting_approval"},
        {"task_id": "b", "worker": "pc-codex", "workspace": "business", "status": "waiting_approval"},
    ]

    def active(_session, _owner, worker=None, workspace=None, statuses=None):
        return [
            task for task in tasks
            if (worker is None or task["worker"] == worker)
            and (workspace is None or task["workspace"] == workspace)
            and task["status"] in statuses
        ]

    monkeypatch.setattr(jarvis_agent, "list_active_tasks", active)

    async def action(task_id, *_args, **_kwargs):
        return next(task for task in tasks if task["task_id"] == task_id)

    monkeypatch.setattr(jarvis_agent, "task_action", action)
    events = [
        event async for event in _server_routed_events(
            "chat-1", "Deny that request", "leo", {"target": "jarvis", "workspace": "home-lab"},
        )
    ]
    assert events[-1]["diagnostics"]["guard_reason"] == "worker_approval_ambiguous"
    assert events[-1]["task_ids"] == []

    explicit = [
        event async for event in _server_routed_events(
            "chat-1", "Deny the Home Lab request", "leo", {"target": "jarvis", "workspace": "business"},
        )
    ]
    assert explicit[-1]["diagnostics"]["guard_reason"] == "worker_approval_deny"
    assert explicit[-1]["task_ids"] == ["a"]


@pytest.mark.asyncio
async def test_named_worker_with_multiple_tasks_requires_spoken_workspace(monkeypatch):
    tasks = [
        {"task_id": "home", "worker": "pc-codex", "workspace": "home-lab", "status": "running"},
        {"task_id": "business", "worker": "pc-codex", "workspace": "business", "status": "running"},
    ]
    calls = []

    def active(_session, _owner, worker=None, workspace=None, statuses=None):
        return [
            task for task in tasks
            if (worker is None or task["worker"] == worker)
            and (workspace is None or task["workspace"] == workspace)
            and task["status"] in statuses
        ]

    async def action(task_id, name, *_args, **_kwargs):
        calls.append((task_id, name))
        return next(task for task in tasks if task["task_id"] == task_id)

    monkeypatch.setattr(jarvis_agent, "list_active_tasks", active)
    monkeypatch.setattr(jarvis_agent, "task_action", action)
    ambiguous = [
        event async for event in _server_routed_events(
            "chat-1", "Cancel the PC Codex task", "leo", {"target": "jarvis", "workspace": "business"},
        )
    ]
    assert ambiguous[-1]["diagnostics"]["guard_reason"] == "worker_cancel_ambiguous"
    assert calls == []

    explicit = [
        event async for event in _server_routed_events(
            "chat-1", "Cancel the PC Codex task in Business", "leo", {"target": "jarvis", "workspace": "home-lab"},
        )
    ]
    assert explicit[-1]["task_ids"] == ["business"]
    assert calls == [("business", "cancel")]

    natural_order = [
        event async for event in _server_routed_events(
            "chat-1", "Cancel the PC Codex business task", "leo", {"target": "jarvis", "workspace": "home-lab"},
        )
    ]
    assert natural_order[-1]["task_ids"] == ["business"]
    assert calls == [("business", "cancel"), ("business", "cancel")]


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Close this document", "Minimize the active document"])
async def test_document_control_without_client_state_emits_no_ui_event(text):
    events = [
        event async for event in _server_routed_events(
            "chat-1", text, "leo", {"target": "jarvis"},
        )
    ]
    assert [event["type"] for event in events] == ["assistant_delta", "final"]
    assert "cannot confirm an active document" in events[-1]["assistant_text"]


@pytest.mark.asyncio
async def test_cancellation_failure_warns_task_may_still_run(monkeypatch):
    task = {
        "task_id": "hermes-live",
        "worker": "hermes",
        "workspace": "home-lab",
        "status": "running",
    }
    monkeypatch.setattr(jarvis_agent, "list_active_tasks", lambda *_args, **_kwargs: [task])

    async def fail(*_args, **_kwargs):
        raise RuntimeError("remote stop failed")

    monkeypatch.setattr(jarvis_agent, "task_action", fail)
    events = [
        event async for event in _server_routed_events(
            "chat-1", "Cancel the Hermes task", "leo", {"target": "jarvis"},
        )
    ]
    assert events[-1]["diagnostics"]["guard_reason"] == "worker_cancel_failed"
    assert "may still be running" in events[-1]["assistant_text"]


def test_voice_system_prompt_assigns_workers_without_inference():
    assert "configured PC Codex worker owns local project, code, and document inspection" in voice_routes.VOICE_SYSTEM_PROMPT
    assert "VPS Codex worker is only for work that explicitly names the VPS" in voice_routes.VOICE_SYSTEM_PROMPT
    assert "Hermes worker is explicit-only" in voice_routes.VOICE_SYSTEM_PROMPT
    assert "Ambiguous follow-ups refer to the preceding conversation" in voice_routes.VOICE_SYSTEM_PROMPT


def test_vps_worker_schema_exposes_its_only_valid_workspace():
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    start = next(row for row in FUNCTION_TOOL_SCHEMAS if row["function"]["name"] == "start_agent_task")
    assert "vps-ops" in start["function"]["parameters"]["properties"]["workspace"]["enum"]
