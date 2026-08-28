import asyncio

from src.ai_interaction import do_ui_control


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
