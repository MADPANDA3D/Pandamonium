"""Versioned JOS protocol packs mounted into agent turns.

The constitution remains a light operator-editable system prompt. This module
supplies the separate operating-protocol layer: strict frontmatter manifests,
validated packs, and a compact rendered block for the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.settings import load_settings

PACKS_DIR = Path(__file__).resolve().parents[1] / "protocols"
PACK_SUFFIX = ".pack.md"
CORE_SCOPE = "core"
DUTY_SCOPE = "duty"
EXTENSION_SCOPE = "extension"
PROTOCOL_SCOPES = frozenset({CORE_SCOPE, DUTY_SCOPE, EXTENSION_SCOPE})
_REQUIRED_KEYS = ("id", "version", "scope", "title", "token_budget")
_LIST_KEYS = frozenset({"domains", "enforcement"})
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PROTOCOL_BEGIN = "[[JOS-PROTOCOLS]]"
PROTOCOL_END = "[[/JOS-PROTOCOLS]]"
_RENDER_HEADER = (
    "## Operating protocols\n\n"
    "The following versioned protocols are mounted for this turn. Follow them, "
    "and cite them accurately if asked which protocols you operate under.\n"
)


@dataclass(frozen=True)
class ProtocolPack:
    id: str
    version: str
    scope: str
    title: str
    token_budget: int
    body: str
    protocol: str = ""
    domains: tuple[str, ...] = ()
    enforcement: tuple[str, ...] = ()
    path: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def reference(self) -> dict[str, str]:
        return {"id": self.id, "version": self.version, "scope": self.scope}


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str, str | None]:
    if not text.startswith("---"):
        return {}, text, "missing frontmatter"
    lines = text.splitlines()
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return {}, text, "unterminated frontmatter"
    manifest: dict[str, Any] = {}
    current_list: str | None = None
    for raw in lines[1:end]:
        stripped = raw.strip()
        if not stripped:
            current_list = None
            continue
        if stripped.startswith("- "):
            if current_list is None:
                return {}, text, "list item without a key"
            manifest[current_list].append(_strip_quotes(stripped[2:]))
            continue
        if ":" not in stripped:
            return {}, text, f"invalid manifest line: {stripped[:60]}"
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        current_list = None
        if not key:
            return {}, text, "empty manifest key"
        if value == "":
            manifest[key] = []
            current_list = key
        elif value == "[]":
            manifest[key] = []
        else:
            manifest[key] = _strip_quotes(value)
    body = "\n".join(lines[end + 1:]).strip()
    return manifest, body, None


def _error_pack(path: Path, message: str) -> ProtocolPack:
    return ProtocolPack(
        id=path.name,
        version="",
        scope="unknown",
        title=path.name,
        token_budget=0,
        body="",
        path=str(path),
        error=message,
    )


def _build_pack(path: Path, manifest: Mapping[str, Any], body: str) -> ProtocolPack:
    missing = [key for key in _REQUIRED_KEYS if manifest.get(key) in (None, "", [])]
    if missing:
        return _error_pack(path, "missing required manifest key(s): " + ", ".join(missing))
    pack_id = str(manifest["id"]).strip()
    if not _ID_RE.fullmatch(pack_id):
        return _error_pack(path, f"invalid pack id: {pack_id[:60]}")
    if path.name != f"{pack_id}{PACK_SUFFIX}":
        return _error_pack(path, f"filename does not match manifest id '{pack_id}'")
    scope = str(manifest["scope"]).strip()
    if scope not in PROTOCOL_SCOPES:
        return _error_pack(path, f"invalid scope: {scope[:40]}")
    try:
        budget = int(manifest["token_budget"])
    except (TypeError, ValueError):
        return _error_pack(path, "token_budget must be an integer")
    if budget <= 0:
        return _error_pack(path, "token_budget must be positive")
    if not body:
        return _error_pack(path, "pack body is empty")
    for key in _LIST_KEYS:
        value = manifest.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return _error_pack(path, f"{key} must be a list of strings")
    return ProtocolPack(
        id=pack_id,
        version=str(manifest["version"]).strip(),
        scope=scope,
        title=str(manifest["title"]).strip(),
        token_budget=budget,
        body=body,
        protocol=str(manifest.get("protocol") or "").strip(),
        domains=tuple(manifest.get("domains") or ()),
        enforcement=tuple(manifest.get("enforcement") or ()),
        path=str(path),
    )


def _load_pack_file(path: Path) -> ProtocolPack:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _error_pack(path, f"unreadable pack: {exc}")
    manifest, body, error = _parse_frontmatter(text)
    if error:
        return _error_pack(path, error)
    return _build_pack(path, manifest, body)


@lru_cache(maxsize=8)
def _load_cached(root: str) -> tuple[ProtocolPack, ...]:
    directory = Path(root)
    if not directory.is_dir():
        return (_error_pack(directory, "protocol packs directory is missing"),)
    files = sorted(directory.glob(f"*{PACK_SUFFIX}"))
    if not files:
        return (_error_pack(directory, "no protocol packs found"),)
    return tuple(_load_pack_file(path) for path in files)


def load_protocol_packs(packs_dir: Path | str | None = None) -> list[ProtocolPack]:
    root = Path(packs_dir) if packs_dir is not None else PACKS_DIR
    return list(_load_cached(str(root)))


def clear_protocol_cache() -> None:
    _load_cached.cache_clear()


def protocol_layer_enabled(settings: Mapping[str, Any] | None = None) -> bool:
    values = settings
    if values is None:
        try:
            values = load_settings()
        except Exception:
            return True
    if not isinstance(values, Mapping):
        return True
    return bool(values.get("protocol_layer_enabled", True))


def select_protocol_packs(
    scope: str, *, packs_dir: Path | str | None = None
) -> list[ProtocolPack]:
    return [pack for pack in load_protocol_packs(packs_dir) if pack.ok and pack.scope == scope]


def render_protocol_block(packs: Iterable[ProtocolPack]) -> str:
    mounted = [pack for pack in packs if pack.ok]
    if not mounted:
        return ""
    sections = [_RENDER_HEADER]
    for pack in mounted:
        label = pack.protocol or pack.id
        sections.append(f"### {label} — {pack.title} (v{pack.version})\n{pack.body}")
    body = "\n\n".join(sections).strip()
    return f"{PROTOCOL_BEGIN}\n{body}\n{PROTOCOL_END}"


def strip_protocol_block(text: str) -> str:
    """Remove the mounted protocol section so evidence can win the budget."""
    if not isinstance(text, str) or PROTOCOL_BEGIN not in text:
        return text
    start = text.find(PROTOCOL_BEGIN)
    end = text.find(PROTOCOL_END, start)
    if end < 0:
        return text[:start].rstrip()
    end += len(PROTOCOL_END)
    return (text[:start].rstrip() + "\n\n" + text[end:].lstrip()).strip()


def core_protocol_block(
    *, settings: Mapping[str, Any] | None = None, packs_dir: Path | str | None = None
) -> str:
    if not protocol_layer_enabled(settings):
        return ""
    return render_protocol_block(select_protocol_packs(CORE_SCOPE, packs_dir=packs_dir))


def core_protocol_references(
    *, settings: Mapping[str, Any] | None = None, packs_dir: Path | str | None = None
) -> list[dict[str, str]]:
    if not protocol_layer_enabled(settings):
        return []
    return [
        pack.reference() for pack in select_protocol_packs(CORE_SCOPE, packs_dir=packs_dir)
    ]


def protocol_status(
    *, settings: Mapping[str, Any] | None = None, packs_dir: Path | str | None = None
) -> dict[str, Any]:
    packs = load_protocol_packs(packs_dir)
    errors = [{"path": pack.path, "error": pack.error} for pack in packs if not pack.ok]
    return {
        "enabled": protocol_layer_enabled(settings),
        "status": "degraded" if errors else "healthy",
        "core_ids": [pack.id for pack in packs if pack.ok and pack.scope == CORE_SCOPE],
        "packs": [
            {
                **pack.reference(),
                "protocol": pack.protocol,
                "title": pack.title,
                "token_budget": pack.token_budget,
                "domains": list(pack.domains),
                "enforcement": list(pack.enforcement),
                "path": pack.path,
                "status": "error" if pack.error else "loaded",
                "error": pack.error,
            }
            for pack in packs
        ],
        "errors": errors,
    }
