from __future__ import annotations

import json

from src.tool_execution import format_tool_result


def test_format_tool_result_serializes_structured_results_as_text():
    results = [{"text": "handoff guidance", "source": "home-lab"}]

    formatted = format_tool_result("search_jarvis_knowledge", {"results": results})

    assert isinstance(formatted, str)
    assert json.dumps(results, indent=2, ensure_ascii=False) in formatted
