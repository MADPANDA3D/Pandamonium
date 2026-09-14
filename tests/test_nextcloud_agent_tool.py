"""MAD-937: agent surface for read-only Nextcloud files.

The tool names an owner-scoped connection (no arbitrary server), returns
bounded content, and cites the node + path so the agent can report the exact
source.
"""

from __future__ import annotations

import asyncio
import json

from src import nextcloud_gallery as nc
from src.agent_tools.nextcloud_tools import NextcloudFilesTool


def _run(payload: dict, owner: str = "alice") -> dict:
    return asyncio.run(NextcloudFilesTool().execute(json.dumps(payload), {"owner": owner}))


def _patch(monkeypatch, name, value):
    monkeypatch.setattr(nc, name, value)


def test_list_action_returns_entries_and_citation(monkeypatch):
    async def fake_list(owner, path="", *, transport=None):
        return {
            "path": path,
            "entries": [
                {
                    "name": "q3.txt",
                    "path": "Reports/q3.txt",
                    "is_dir": False,
                    "size": 42,
                    "ref": "nextcloud:abc:Reports/q3.txt",
                }
            ],
            "truncated": False,
            "source_state": {"status": "healthy", "stale": False},
            "source": {
                "kind": "nextcloud_file",
                "connection_id": "abc",
                "node": "https://cloud.example.test",
                "path": path,
                "is_dir": True,
            },
            "citation": f'Nextcloud "https://cloud.example.test": /{path}'.rstrip("/"),
        }

    _patch(monkeypatch, "list_folder", fake_list)
    result = _run({"action": "list", "path": "Reports"})

    assert result["exit_code"] == 0
    assert "Reports/q3.txt" in result["output"]
    assert result["source"]["kind"] == "nextcloud_file"
    assert result["source"]["path"] == "Reports"
    assert "cloud.example.test" in result["citation"]


def test_read_action_returns_content_and_exact_citation(monkeypatch):
    async def fake_read(owner, path, *, max_bytes=None, transport=None):
        return {
            "path": path,
            "content": "quarterly numbers",
            "bytes": 17,
            "truncated": False,
            "media_type": "text/plain",
            "file_ref": f"nextcloud:abc:{path}",
            "source": {
                "kind": "nextcloud_file",
                "connection_id": "abc",
                "node": "https://cloud.example.test",
                "path": path,
            },
            "citation": f'Nextcloud "https://cloud.example.test": /{path}',
        }

    _patch(monkeypatch, "read_file", fake_read)
    result = _run({"action": "read", "path": "Reports/q3.txt"})

    assert result["exit_code"] == 0
    assert result["output"] == "quarterly numbers"
    assert result["source"]["path"] == "Reports/q3.txt"
    assert "cloud.example.test" in result["citation"]


def test_search_action_returns_matches(monkeypatch):
    async def fake_search(owner, query, *, path="", transport=None):
        return {
            "query": query,
            "results": [{"path": "Reports/q3.txt", "name": "q3.txt", "is_dir": False}],
            "truncated": False,
            "source": {
                "kind": "nextcloud_file",
                "connection_id": "abc",
                "node": "https://cloud.example.test",
                "query": query,
                "path": path,
            },
            "citation": f'Nextcloud "https://cloud.example.test" search "{query}"',
        }

    _patch(monkeypatch, "search_files", fake_search)
    result = _run({"action": "search", "query": "q3"})

    assert result["exit_code"] == 0
    assert "Reports/q3.txt" in result["output"]


def test_tool_requires_a_saved_connection(monkeypatch):
    async def fake_list(owner, path="", *, transport=None):
        raise nc.NextcloudError("unconfigured", "Nextcloud is not connected", status_code=404)

    _patch(monkeypatch, "list_folder", fake_list)
    result = _run({"action": "list"})

    assert result["exit_code"] == 1
    assert result["code"] == "unconfigured"
    assert "not connected" in result["error"].lower()


def test_tool_fails_closed_on_redacted_path(monkeypatch):
    async def fake_read(owner, path, *, max_bytes=None, transport=None):
        raise nc.NextcloudError(
            "redacted_path", "That path is excluded from read-only access.", status_code=403
        )

    _patch(monkeypatch, "read_file", fake_read)
    result = _run({"action": "read", "path": ".env"})

    assert result["exit_code"] == 1
    assert result["code"] == "redacted_path"


def test_tool_rejects_unknown_action(monkeypatch):
    result = _run({"action": "write"})
    assert result["exit_code"] == 1
    assert result["code"] == "invalid_action"


def test_tool_rejects_missing_query(monkeypatch):
    result = _run({"action": "search"})
    assert result["exit_code"] == 1
    assert result["code"] == "invalid_query"


def test_tool_requires_an_owner():
    result = _run({"action": "list"}, owner="")
    assert result["exit_code"] == 1
    assert result["code"] == "owner_required"


def test_authority_defaults_nextcloud_reads_to_read():
    from src.authority_protocol import action_effect_for

    call = {"name": "nextcloud_files", "target": "tool", "arguments": {"action": "read"}}
    assert action_effect_for(call) == "read"


def test_nextcloud_tool_is_admin_gated():
    from src.tool_security import is_public_blocked_tool

    assert is_public_blocked_tool("nextcloud_files") is True


def test_nextcloud_tool_is_registered_and_has_a_schema():
    from src.agent_tools import TOOL_HANDLERS, TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    assert "nextcloud_files" in TOOL_HANDLERS
    assert "nextcloud_files" in TOOL_TAGS
    names = {schema["function"]["name"] for schema in FUNCTION_TOOL_SCHEMAS}
    assert "nextcloud_files" in names
