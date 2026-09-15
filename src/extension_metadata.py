"""Public package information, shared by intake, Plugins and agent discovery.

Only descriptive metadata crosses this boundary. Schemas stay in the native
inventory and are mounted on demand; configuration values never enter it.
"""

from collections.abc import Mapping
from typing import Any


def package_metadata(
    manifest: Mapping[str, Any], inventory: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    metadata = manifest.get("metadata") or {}
    descriptor = (manifest.get("capabilities") or {}).get("descriptor") or {}
    skills = descriptor.get("type") == "skill_bundle"
    schemas = (manifest.get("capabilities") or {}).get("schemas") or []
    capabilities = []
    if inventory:
        for item in inventory.get("capabilities") or []:
            function = (item.get("schema") or {}).get("function") or {}
            capabilities.append(
                {
                    "name": item["name"],
                    "kind": item["kind"],
                    "description": function.get("description")
                    or metadata.get("skill_descriptions", {}).get(item["name"], ""),
                }
            )
    elif skills:
        capabilities = [
            {
                "name": name,
                "kind": "skill",
                "description": metadata.get("skill_descriptions", {}).get(name, ""),
            }
            for name in descriptor.get("include", [])
        ]
    else:
        capabilities = [
            {
                "name": schema["function"]["name"],
                "kind": "tool",
                "description": schema["function"].get("description", ""),
            }
            for schema in schemas
        ]
    summary = metadata.get("summary") or "; ".join(
        item["description"] for item in capabilities if item["description"]
    )
    if not summary:
        summary = (
            ("Guidance for " + ", ".join(item["name"] for item in capabilities))
            if skills
            else "Open this integration to inspect its available capabilities and setup."
        )
    examples = (
        metadata.get("examples")
        or [
            (
                f"Use the {item['name']} skill."
                if skills
                else f"Ask to: {item['description']}"
            )
            for item in capabilities
            if skills or item["description"]
        ][:3]
    )
    return {
        "summary": summary[:1000],
        "icon": metadata.get("icon", "◈"),
        "categories": metadata.get("categories")
        or ["skills" if skills else "integrations"],
        "capability_summaries": capabilities,
        "examples": examples,
        "requirements": metadata.get("requirements", []),
        "configuration": manifest.get("configuration", []),
    }
