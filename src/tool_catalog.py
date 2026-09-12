"""Built-in tool catalog for the agent surface (MAD-905).

`GET /api/tools` has always exposed the full toggle catalog, but per-turn
selection sent only a RAG-selected subset to the model, so the agent could not
see or invoke most of its own tools. This module gives the agent-facing
catalog one source of truth:

- names come from ``FUNCTION_TOOL_SCHEMAS`` plus the email MCP-backed
  ``BUILTIN_EMAIL_TOOLS`` (the same union ``TOOL_TAGS`` is required to equal);
- categories mirror the Built-in Tools admin panel so agent answers and UI
  answers agree;
- descriptions come from the native schema first, then the RAG descriptions.

Imports stay lazy so importing this module never pulls the embedding/Chroma
stack or the agent loop.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

from src.tool_security import BUILTIN_EMAIL_TOOLS

CATEGORY_ORDER = (
    "Code",
    "Search",
    "Documents",
    "Media",
    "Knowledge",
    "Multi-Agent",
    "Sessions",
    "System",
    "Other",
)

# Mirrors static/js/admin.js TOOL_META categories. Anything absent falls back
# to "Other", which is exactly how the admin panel groups unknown tools too.
TOOL_CATEGORIES: Dict[str, str] = {
    "bash": "Code",
    "python": "Code",
    "read_file": "Code",
    "write_file": "Code",
    "web_search": "Search",
    "search_chats": "Search",
    "create_document": "Documents",
    "update_document": "Documents",
    "edit_document": "Documents",
    "suggest_document": "Documents",
    "manage_documents": "Documents",
    "generate_image": "Media",
    "manage_memory": "Knowledge",
    "manage_skills": "Knowledge",
    "chat_with_model": "Multi-Agent",
    "pipeline": "Multi-Agent",
    "ask_teacher": "Multi-Agent",
    "send_to_session": "Sessions",
    "create_session": "Sessions",
    "list_sessions": "Sessions",
    "manage_session": "Sessions",
    "list_models": "System",
    "ui_control": "System",
    "manage_tasks": "System",
    "api_call": "System",
    "manage_endpoints": "System",
    "manage_mcp": "System",
    "manage_webhooks": "System",
    "manage_tokens": "System",
    "manage_settings": "System",
}

# Email tools run through the bundled email MCP server; the bare names have no
# native schema, so keep a short agent-facing description for catalog answers.
_FALLBACK_DESCRIPTIONS: Dict[str, str] = {
    "search_emails": "Search email messages across configured accounts.",
    "draft_email": "Create a new email draft without sending it.",
    "draft_email_reply": "Draft a reply to an existing email.",
    "ai_draft_email_reply": "Draft an AI-assisted reply to an existing email.",
    "download_attachment": "Download an attachment from an email.",
}


def _schema_map() -> Dict[str, dict]:
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    return {
        schema["function"]["name"]: schema
        for schema in FUNCTION_TOOL_SCHEMAS
        if schema.get("function", {}).get("name")
    }


def builtin_tool_names() -> Set[str]:
    """Every built-in toggle: native schemas plus email MCP-backed tools."""
    return set(_schema_map()) | set(BUILTIN_EMAIL_TOOLS)


def category_for(name: str) -> str:
    return TOOL_CATEGORIES.get(name, "Other")


def _one_line(text: str, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    for stop in (". ", ".\n"):
        index = text.find(stop)
        if 0 < index < limit:
            return text[: index + 1]
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _description_for(name: str, schema: Optional[dict]) -> str:
    description = ((schema or {}).get("function", {}) or {}).get("description") or ""
    if not description:
        try:
            from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS

            description = BUILTIN_TOOL_DESCRIPTIONS.get(name, "")
        except Exception:
            description = ""
    if not description:
        description = _FALLBACK_DESCRIPTIONS.get(name, "")
    return _one_line(description)


def catalog_entries(
    disabled: Iterable[str] = (),
    mounted: Optional[Set[str]] = None,
) -> List[dict]:
    """Return the complete built-in catalog, sorted for stable prompting."""
    disabled_set = set(disabled or ())
    schemas = _schema_map()
    mounted_set = set(mounted) if mounted is not None else None
    entries = []
    for name in sorted(builtin_tool_names()):
        schema = schemas.get(name)
        entry = {
            "id": name,
            "category": category_for(name),
            "description": _description_for(name, schema),
            "enabled": name not in disabled_set,
        }
        if mounted_set is not None:
            entry["mounted"] = name in mounted_set
        entries.append(entry)
    return entries
