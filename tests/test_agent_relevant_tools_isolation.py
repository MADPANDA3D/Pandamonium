from __future__ import annotations

import pytest

from src.agent_loop import stream_agent_loop


@pytest.mark.asyncio
async def test_agent_loop_does_not_mutate_caller_relevant_tools():
    relevant_tools = {"read_calendar"}
    stream = stream_agent_loop(
        "http://example.invalid/v1/chat/completions",
        "jarvis",
        [{"role": "user", "content": "Check my calendar."}],
        relevant_tools=relevant_tools,
        disabled_tools=set(),
        max_rounds=1,
        context_length=32768,
    )

    try:
        async for chunk in stream:
            if '"type": "agent_prep"' in chunk:
                break
    finally:
        await stream.aclose()

    assert relevant_tools == {"read_calendar"}
