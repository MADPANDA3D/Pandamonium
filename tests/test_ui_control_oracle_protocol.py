import asyncio
import json

from src.ai_interaction import do_ui_control
from src.agent_tools import function_call_to_tool_block


def test_oracle_ui_control_reuses_the_existing_ui_event_bus():
    engaged = asyncio.run(do_ui_control("oracle_protocol engage"))
    assert engaged["ui_event"] == "oracle_protocol_engage"

    styled = asyncio.run(do_ui_control("oracle_protocol style FLIR"))
    assert styled == {
        "ui_event": "oracle_protocol_command",
        "tool": "set_visual_style",
        "arguments": {"style": "thermal"},
        "results": "Switching ORACLE to FLIR",
    }

    rejected = asyncio.run(do_ui_control("oracle_protocol style shell"))
    assert "error" in rejected


def test_oracle_ui_control_exposes_the_existing_bounded_bridge_commands():
    assert asyncio.run(do_ui_control("oracle_protocol globe")) == {
        "ui_event": "oracle_protocol_command",
        "tool": "zoom_to_globe",
        "arguments": {},
        "results": "Sending ORACLE to full-globe view",
    }
    assert asyncio.run(do_ui_control("oracle_protocol location United States")) == {
        "ui_event": "oracle_protocol_command",
        "tool": "fly_to_location",
        "arguments": {"query": "United States", "viewMode": "overview"},
        "results": "Sending ORACLE to an overview of United States",
    }
    assert asyncio.run(do_ui_control("oracle_protocol cockpit enter"))["arguments"] == {
        "action": "enter",
    }
    assert asyncio.run(do_ui_control("oracle_protocol cctv nearest"))["arguments"] == {
        "action": "nearest",
    }
    assert asyncio.run(do_ui_control("oracle_protocol layer live vessels on")) == {
        "ui_event": "oracle_protocol_command",
        "tool": "set_layer_visibility",
        "arguments": {"layerId": "live vessels", "enabled": True},
        "results": "Turning ORACLE layer live vessels on",
    }
    assert "error" in asyncio.run(do_ui_control("oracle_protocol layer traffic maybe"))


def test_oracle_structured_tool_calls_keep_the_subcommand_value():
    block = function_call_to_tool_block(
        "ui_control",
        json.dumps({
            "action": "oracle_protocol",
            "name": "location",
            "value": "United States",
        }),
    )
    assert block is not None
    assert block.content == "oracle_protocol location United States"
