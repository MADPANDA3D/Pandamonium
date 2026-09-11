"""Generic, atomic JSON projection for model-facing tool results."""

import json
from copy import deepcopy
from typing import Any

from src.authority_protocol import redact_secrets

MCP_RESULT_PREFIX = "**MCP result:**\n```json\n"
MCP_RESULT_SUFFIX = "\n```"
_SCHEMA_KEYS = {
    "inputSchema",
    "input_schema",
    "outputSchema",
    "output_schema",
    "$schema",
    "$defs",
    "definitions",
}
# Envelope control fields are never treated as data pages.
_CONTROL_KEYS = {
    "error",
    "errors",
    "pagination",
    "nextAction",
    "next_action",
    "recovery",
    "citations",
    "sources",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def contains_schema(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(_SCHEMA_KEYS.intersection(value)) or any(
            contains_schema(v) for v in value.values()
        )
    if isinstance(value, list):
        return any(contains_schema(v) for v in value)
    return False


def project_result(value: dict, max_chars: int) -> dict:
    """Retain complete schemas/records or explicitly report what did not fit.

    Never change a server cursor to pretend omitted records were consumed. A
    shortened page must be reread with a smaller limit before advancing it.
    """
    if len(_json(value)) <= max_chars:
        return value
    if contains_schema(value):
        return {
            "_projection": {
                "truncated": True,
                "reason": "Schema omitted: request a narrower reference or larger context.",
            }
        }
    projected = deepcopy(value)
    notice = projected["_projection"] = {}
    notice.update(
        truncated=True,
        omitted_items={},
        reason="Whole records omitted to fit the model result budget. Reread this page with a "
        "smaller limit before advancing its original cursor; do not infer omitted content.",
    )

    def pages(node: Any, path: str = "") -> list:
        found = []
        if isinstance(node, dict):
            for key, child in node.items():
                if key in _CONTROL_KEYS or key == "_projection":
                    continue
                found.extend(pages(child, path + "/" + key))
        elif isinstance(node, list) and node:
            # Remove complete suffix items; never shorten fields inside a record.
            found.append((len(_json(node)), path, node))
        return found

    # ponytail: repeated serialization is quadratic in page length; batch suffix
    # removal if bounded MCP pages become large enough to make this measurable.
    while len(_json(projected)) > max_chars:
        candidates = pages(projected)
        if not candidates:
            return {
                "_projection": {
                    "truncated": True,
                    "reason": "Result omitted, not empty: request a narrower result.",
                }
            }
        _, path, page = max(candidates, key=lambda entry: entry[0])
        page.pop()
        notice["omitted_items"][path] = notice["omitted_items"].get(path, 0) + 1
    return projected


def format_mcp_result(result: dict, max_chars: int | None = None) -> str:
    # Standard MCP structured content is authoritative. Text blocks can add
    # useful details, but a duplicate JSON text block need not consume context.
    value = redact_secrets(dict(result))
    structured = value.get("structured_content")
    for key in ("stdout", "stderr"):
        text = value.get(key)
        if isinstance(text, str):
            try:
                parsed = redact_secrets(json.loads(text))
                if "structured_content" in value and parsed == structured:
                    value.pop(key)
                else:
                    value[key] = parsed
            except (ValueError, TypeError):
                pass
    if max_chars is None:
        max_chars = len(_json(value)) if contains_schema(value) else 8000
    return (
        MCP_RESULT_PREFIX + _json(project_result(value, max_chars)) + MCP_RESULT_SUFFIX
    )


def trim_mcp_result(text: str, max_chars: int) -> str | None:
    """Reproject our complete JSON frame; do not slice schema or record text."""
    start = text.find(MCP_RESULT_PREFIX)
    if start < 0 or not text.endswith(MCP_RESULT_SUFFIX):
        return None
    try:
        value = json.loads(
            text[start + len(MCP_RESULT_PREFIX) : -len(MCP_RESULT_SUFFIX)]
        )
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    prefix = text[:start]
    budget = max(0, max_chars - len(MCP_RESULT_PREFIX) - len(MCP_RESULT_SUFFIX))
    projected = _json(project_result(value, max(0, budget - len(prefix))))
    if len(prefix) + len(projected) > budget:
        prefix = ""
        projected = _json(project_result(value, budget))
    return prefix + MCP_RESULT_PREFIX + projected + MCP_RESULT_SUFFIX
