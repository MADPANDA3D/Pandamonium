from routes.voice_routes import (
    _asks_current_business,
    _asks_runtime_status,
    _delegation_route,
    _workspace_for_text,
    VOICE_LONG_NUM_PREDICT,
    VOICE_NORMAL_NUM_PREDICT,
)
from routes.agent_task_routes import TaskCreate
from src.jarvis_agent import WORKERS, _parameter_value
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


def test_voice_runtime_and_business_intents_are_server_routed():
    assert _asks_runtime_status("What model are you running?")
    assert _asks_runtime_status("Give me the runtime architecture and quantization")
    assert _asks_current_business("What are our current business updates?")
    assert not _asks_current_business("Explain how a business works")


def test_leos_worker_names_route_without_confusing_nimbus_and_vps():
    assert _delegation_route("Ask my Codex to inspect the client files") == ("pc-codex", "business")
    assert _delegation_route("I need to talk to Hermes") == ("hermes", "home-lab")
    assert _delegation_route("Check my online server") == ("vps-codex", "home-lab")
    assert _delegation_route("Check Project Nimbus") == ("pc-codex", "home-lab")
    assert _workspace_for_text("Inspect Project Linux and Hyprland") == "project-linux"


def test_mark5_voice_budgets_and_runtime_parser():
    assert VOICE_NORMAL_NUM_PREDICT == 600
    assert VOICE_LONG_NUM_PREDICT == 1200
    assert _parameter_value("num_ctx 32768\ntemperature 0.35", "num_ctx") == 32768


def test_agent_tools_and_worker_defaults_are_narrow():
    names = {schema["function"]["name"] for schema in FUNCTION_TOOL_SCHEMAS}
    assert {"get_runtime_status", "start_agent_task", "read_agent_task", "search_jarvis_knowledge"} <= names
    assert WORKERS["pc-codex"]["enabled"] is False
    assert WORKERS["hermes"]["enabled"] is False
    assert WORKERS["vps-codex"]["enabled"] is False
    payload = TaskCreate(worker="pc-codex", session_id="s", workspace="home-lab", prompt="inspect")
    assert payload.permission_mode == "read_only"
    assert payload.approved is False
