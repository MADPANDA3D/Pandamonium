"""Read-only Nextcloud file tool (MAD-937).

Lists, searches, and reads files from the owner's Nextcloud connection. The
agent never names a server: the owner-scoped saved connection is the only
target, content is bounded, redacted paths are refused, and every result
carries a node + path citation. No write or upload path exists here.
"""

from __future__ import annotations

import json

from src import nextcloud_gallery as nc


def _format_listing(result: dict) -> str:
    lines = [f"folder: /{result.get('path') or ''}".rstrip("/")]
    for entry in result.get("entries") or []:
        if entry.get("is_dir"):
            lines.append(f"  {entry['path']}/")
        else:
            size = entry.get("size")
            suffix = f"  ({size} B)" if isinstance(size, int) else ""
            lines.append(f"  {entry['path']}{suffix}")
    if not result.get("entries"):
        lines.append("  (empty)")
    if result.get("truncated"):
        lines.append("... [list truncated; ask for a narrower folder]")
    return "\n".join(lines)


def _format_search(result: dict) -> str:
    lines = [f'search "{result.get("query") or ""}":']
    for entry in result.get("results") or []:
        lines.append(f"  {entry['path']}")
    if not result.get("results"):
        lines.append("  (no matches)")
    if result.get("truncated"):
        lines.append("... [search truncated; narrow the query or folder]")
    return "\n".join(lines)


class NextcloudFilesTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        owner = str((ctx or {}).get("owner") or "") or None
        raw = str(content or "").strip()
        args: dict = {}
        if raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
        if not isinstance(args, dict):
            return {
                "error": (
                    "nextcloud_files arguments must be a JSON object, e.g. "
                    '{"action": "read", "path": "Reports/q3.txt"}.'
                ),
                "exit_code": 1,
                "code": "invalid_arguments",
            }
        action = str(args.get("action") or "list").strip().lower()
        if action not in {"list", "read", "search"}:
            return {
                "error": "The Nextcloud action must be list, read, or search.",
                "exit_code": 1,
                "code": "invalid_action",
            }
        if not owner:
            return {
                "error": "Nextcloud file access needs an authenticated owner.",
                "exit_code": 1,
                "code": "owner_required",
            }

        try:
            if action == "list":
                result = await nc.list_folder(owner, str(args.get("path") or ""))
                output = _format_listing(result)
            elif action == "search":
                query = str(args.get("query") or "").strip()
                if len(query) < nc.MIN_SEARCH_QUERY_CHARS:
                    return {
                        "error": f"Enter at least {nc.MIN_SEARCH_QUERY_CHARS} characters to search.",
                        "exit_code": 1,
                        "code": "invalid_query",
                    }
                result = await nc.search_files(
                    owner, query, path=str(args.get("path") or "")
                )
                output = _format_search(result)
            else:
                path = str(args.get("path") or "").strip()
                if not path:
                    return {
                        "error": "Enter the file path to read.",
                        "exit_code": 1,
                        "code": "invalid_path",
                    }
                max_bytes = args.get("max_bytes")
                result = await nc.read_file(
                    owner,
                    path,
                    max_bytes=int(max_bytes) if max_bytes else nc.MAX_TEXT_READ_BYTES,
                )
                output = result["content"]
        except nc.NextcloudError as exc:
            return {"error": exc.message, "exit_code": 1, "code": exc.code}
        except Exception:
            return {
                "error": "The Nextcloud operation failed before it could complete.",
                "exit_code": 1,
                "code": "operation_failed",
            }

        return {
            "output": output,
            "exit_code": 0,
            "source": result.get("source"),
            "citation": result.get("citation"),
            "truncated": bool(result.get("truncated")),
            "source_state": result.get("source_state", {"status": "healthy", "stale": False}),
        }
