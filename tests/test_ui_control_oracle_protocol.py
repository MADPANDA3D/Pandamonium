import asyncio

from src.ai_interaction import do_ui_control
from src.agent_tools import function_call_to_tool_block


def test_oracle_ui_control_only_manages_protocol_lifecycle():
    engaged = asyncio.run(do_ui_control("oracle_protocol engage"))
    assert engaged["ui_event"] == "oracle_protocol_engage"

    shutdown = asyncio.run(do_ui_control("oracle_protocol shutdown"))
    assert shutdown["ui_event"] == "oracle_protocol_shutdown"
    assert "native tools" in asyncio.run(do_ui_control("oracle_protocol style FLIR"))["error"]


def test_oracle_structured_tool_calls_keep_lifecycle_command():
    block = function_call_to_tool_block(
        "ui_control",
        '{"action":"oracle_protocol","name":"engage"}',
    )
    assert block is not None
    assert block.content == "oracle_protocol engage"
