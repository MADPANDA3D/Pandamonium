from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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


def test_voice_intent_separates_foreground_switch_from_background_delegation():
    assert _target_switch("Talk to PC Codex") == "pc-codex"
    assert _target_switch("Please switch me back to Jarvis") == "jarvis"
    assert _target_switch("Connect me to Jarvis") == "jarvis"
    assert _target_switch("Talk about the result Hermes found") is None
    assert _target_switch("Ask PC Codex to inspect Mark 5") is None
    assert _target_switch("I would like to now talk to Hermes") == "hermes"
    assert _target_switch("I'd like to talk to Hermes") == "hermes"
    assert _target_switch("Can you please talk to Hermes?") == "hermes"
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
    assert "Odysseus is the default destination" in calls[0][2]
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
    assert "PC Codex is opening the document in Odysseus" in events[-1]["assistant_text"]
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
        "status": "failed",
        "prompt": "Open Mark 6 in Odysseus with ODYSSEUS_ARTIFACT.",
    }
    calls = []

    monkeypatch.setattr(jarvis_agent, "get_task", lambda task_id: previous if task_id == previous["task_id"] else None)

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
    }
    assert hermes_task == {
        "type": "agent_task", "task_id": "hermes-task", "worker": "hermes",
        "workspace": "home-lab", "foreground": False,
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

    assert [event["type"] for event in events] == ["target_changed", "assistant_delta", "final"]
    assert events[0]["target"] == "jarvis"
    assert events[-1]["assistant_text"] == expected_reply


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
            "Talk to Hermes",
            "leo",
            {"target": "pc-codex", "workspace": "home-lab", "active_task_id": "pc-task"},
        )
    ]

    assert [event["type"] for event in events] == ["target_changed", "assistant_delta", "final"]
    assert events[0]["target"] == "hermes"
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

    assert [event["type"] for event in events] == ["target_changed", "assistant_delta", "final"]
    assert events[0]["target"] == "pc-codex"
    assert events[-1]["diagnostics"]["guard_reason"] == "target_switch_pc-codex"


@pytest.mark.asyncio
async def test_old_worker_question_does_not_capture_new_target_turn(monkeypatch):
    monkeypatch.setattr(jarvis_agent, "get_task", lambda _task_id: {
        "task_id": "pc-question",
        "worker": "pc-codex",
        "session_id": "chat-1",
        "status": "waiting",
        "owner": "leo",
    })
    monkeypatch.setattr(jarvis_agent, "find_active_task", lambda *_args: None)

    async def dispatch(_session, worker, workspace, prompt, _owner, _voice):
        assert (worker, workspace, prompt) == ("hermes", "home-lab", "Handle this with Hermes.")
        return {"task_id": "hermes-task", "worker": worker}, "started"

    async def must_not_reply(*_args, **_kwargs):
        raise AssertionError("the old PC question captured a Hermes turn")

    monkeypatch.setattr(voice_routes, "_dispatch_worker_request", dispatch)
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

    assert next(event for event in events if event["type"] == "agent_task")["worker"] == "hermes"


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
    assert calls == [
        ("find", "chat-1", "pc-codex", "business", "leo"),
        ("action", "task-1", "steer", {"prompt": "Add the CRM check."}, False, "leo"),
    ]


@pytest.mark.asyncio
async def test_new_pc_task_ignores_voice_global_thread_id(monkeypatch):
    captured = {}

    monkeypatch.setattr(jarvis_agent, "find_active_task", lambda *_args: None)

    async def start_task(worker, session_id, workspace, prompt, permission_mode, approved, owner, codex_thread_id=None):
        captured.update(
            worker=worker,
            session_id=session_id,
            workspace=workspace,
            prompt=prompt,
            permission_mode=permission_mode,
            approved=approved,
            owner=owner,
            codex_thread_id=codex_thread_id,
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
