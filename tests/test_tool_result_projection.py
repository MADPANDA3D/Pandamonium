"""MAD-895 regressions for the generic MCP result projection (unit level).

Coverage targets the dead-end failure mode: oversized list results must
name their recovery paths, behave identically for any provider shape, and
never silently collapse into an empty payload. The integration-level
projection behavior is covered by tests/test_mcp_result_projection.py.
"""

from src.tool_result_projection import (
    project_result,
    trim_mcp_result,
)


def test_truncation_notice_names_recovery_paths():
    payload = {"rows": [{"n": i, "pad": "y" * 60} for i in range(60)]}
    projected = project_result(payload, max_chars=1500)

    reason = projected["_projection"]["reason"]
    assert "Whole records omitted" in reason
    assert "smaller limit" in reason
    assert "name/ID resolver tool" in reason
    assert "do not infer omitted content" in reason


def test_projection_is_shape_driven_not_provider_specific():
    discord_style = {
        "items": [{"cid": i, "label": f"c{i}", "desc": "d" * 50} for i in range(40)]
    }
    generic_style = {
        "items": [{"uid": i, "label": f"n{i}", "body": "b" * 50} for i in range(40)]
    }
    a = project_result(discord_style, max_chars=900)
    b = project_result(generic_style, max_chars=900)

    assert a["_projection"]["omitted_items"] == b["_projection"]["omitted_items"]
    assert len(a["items"]) == len(b["items"])
    assert a["_projection"]["reason"] == b["_projection"]["reason"]


def test_result_omitted_not_empty_when_no_pages_remain():
    projected = project_result({"data": ["AAA", "BBB"]}, max_chars=10)

    assert projected["_projection"]["truncated"] is True
    assert "Result omitted, not empty" in projected["_projection"]["reason"]
    assert "data" not in projected


def test_trim_mcp_result_ignores_non_framed_text():
    assert trim_mcp_result("plain tool output text", 100) is None