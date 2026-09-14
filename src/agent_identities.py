"""Multi-identity registry with attached model profiles (MAD-929).

The installation identity in ``src.settings`` stays the public contract:
``src.agent_identity.agent_identity_status()`` and
``validate_agent_identity_setting`` are unchanged, and an installation with no
registry file keeps resolving from settings exactly as before.

This module layers a saved-identity registry on top:

* The first entry is migrated from the installation settings identity the
  first time the registry is persisted, so live behavior does not change.
* Each identity carries an attached model profile: a required chat lane
  (endpoint + model + reasoning level) and optional per-lane overrides that
  default to the identity's own profile.
* Sessions bind one identity (``sessions.identity_id``); the registry never
  rewrites another user's session.

The registry is a plain JSON file written through ``core.atomic_io`` — no new
dependencies, and the same validation/secret rules as the settings identity.
"""

from __future__ import annotations

import copy
import re
import time
from typing import Any, Mapping
from pathlib import Path

from src.constants import AGENT_IDENTITIES_FILE
from src.settings import DEFAULT_SETTINGS, load_settings

_IDENTITIES_FILE = AGENT_IDENTITIES_FILE

_IDENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DISPLAY_NAME_MAX = 80
_CONSTITUTION_MAX = 16_000
_MAX_IDENTITIES = 50
_MAX_ENDPOINT_ID = 128
_MAX_MODEL_ID = 256
_REASONING_LEVELS = ("low", "medium", "high")
_CHAT_LANE = "chat"
# Lanes an identity may override. ``voice`` keeps its existing display-name ->
# voice mapping elsewhere; it is stored here for future lane consumers only.
IDENTITY_LANES = ("utility", "vision", "research", "image", "voice")
_STORE_VERSION = 1


def _now() -> int:
    return int(time.time())


def _file_path() -> Path:
    return Path(_IDENTITIES_FILE)


def store_exists() -> bool:
    """True when the on-disk registry exists (not just the in-memory migration)."""
    try:
        return _file_path().is_file()
    except OSError:
        return False


def _fallback_identity_values() -> dict[str, str]:
    """Resolve installation settings field-by-field, mirroring agent_identity."""
    try:
        values = load_settings()
    except Exception:
        values = dict(DEFAULT_SETTINGS)
    resolved: dict[str, str] = {}
    for key in ("agent_id", "agent_display_name", "agent_constitution", "agent_constitution_version"):
        raw = values.get(key)
        try:
            resolved[key] = validate_identity_field(key, raw)
        except (AttributeError, ValueError):
            resolved[key] = str(DEFAULT_SETTINGS[key])
    return resolved


def validate_identity_field(key: str, value: Any) -> str:
    """Validate one identity field with the exact settings rules.

    Duplicated deliberately: importing ``src.agent_identity`` here would create
    an import cycle because ``agent_identity`` resolves the active registry
    entry. The accepted shapes are identical.
    """
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{key} must not be empty")
    if key == "agent_id":
        if not _IDENTITY_ID_RE.fullmatch(normalized):
            raise ValueError(
                "agent_id must start with a lowercase letter and contain only lowercase letters, numbers, _ or -"
            )
    elif key == "agent_display_name":
        if len(normalized) > _DISPLAY_NAME_MAX or any(ord(char) < 32 for char in normalized):
            raise ValueError("agent_display_name must be 80 printable characters or fewer")
    elif key == "agent_constitution":
        if len(normalized) > _CONSTITUTION_MAX:
            raise ValueError("agent_constitution must be 16000 characters or fewer")
    elif key == "agent_constitution_version":
        if not _VERSION_RE.fullmatch(normalized):
            raise ValueError("agent_constitution_version must be 64 letters, numbers, dots, _ or - or fewer")
    else:
        raise ValueError("unknown agent identity setting")
    return normalized


def _clean_text(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("must be a string")
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise ValueError(f"must be {limit} characters or fewer")
    if any(ord(char) < 32 for char in cleaned):
        raise ValueError("must not contain control characters")
    return cleaned


def _sanitize_lane(value: Any, *, lane: str) -> dict[str, str] | None:
    """Normalize one model lane. Empty lanes return None (= inherit chat lane)."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{lane} override must be an object")
    endpoint_id = _clean_text(value.get("endpoint_id"), limit=_MAX_ENDPOINT_ID)
    model = _clean_text(value.get("model"), limit=_MAX_MODEL_ID)
    if not endpoint_id and not model:
        return None
    return {"endpoint_id": endpoint_id, "model": model}


def sanitize_model_profile(value: Any) -> dict[str, Any]:
    """Validate and normalize an attached model profile.

    The chat lane is always present (fields may be empty until an operator
    picks a model). Per-lane overrides default to ``None`` — "same as the
    identity profile" — so callers never need a sentinel string.
    """
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValueError("model_profile must be an object")
    chat_raw = value.get(_CHAT_LANE)
    if chat_raw is None:
        chat_raw = {}
    if not isinstance(chat_raw, Mapping):
        raise ValueError("chat must be an object")
    reasoning_level = _clean_text(chat_raw.get("reasoning_level"), limit=16).lower()
    if reasoning_level and reasoning_level not in _REASONING_LEVELS:
        raise ValueError("reasoning_level must be low, medium, or high")
    chat = {
        "endpoint_id": _clean_text(chat_raw.get("endpoint_id"), limit=_MAX_ENDPOINT_ID),
        "model": _clean_text(chat_raw.get("model"), limit=_MAX_MODEL_ID),
        "reasoning_level": reasoning_level,
    }
    lanes_raw = value.get("lanes")
    if lanes_raw is None:
        lanes_raw = {}
    if not isinstance(lanes_raw, Mapping):
        raise ValueError("lanes must be an object")
    lanes: dict[str, Any] = {}
    for lane in IDENTITY_LANES:
        lanes[lane] = _sanitize_lane(lanes_raw.get(lane), lane=lane)
    return {_CHAT_LANE: chat, "lanes": lanes}


def empty_model_profile() -> dict[str, Any]:
    return sanitize_model_profile({})


def _normalize_entry(raw: Any) -> dict[str, Any] | None:
    """Coerce one stored row into the canonical identity shape (or None)."""
    if not isinstance(raw, Mapping):
        return None
    try:
        identity_id = validate_identity_field("agent_id", raw.get("id"))
        display_name = validate_identity_field("agent_display_name", raw.get("display_name"))
        constitution = validate_identity_field("agent_constitution", raw.get("constitution"))
        version = validate_identity_field("agent_constitution_version", raw.get("constitution_version"))
        profile = sanitize_model_profile(raw.get("model_profile"))
    except (AttributeError, ValueError):
        return None
    created_at = raw.get("created_at")
    updated_at = raw.get("updated_at")
    return {
        "id": identity_id,
        "display_name": display_name,
        "constitution": constitution,
        "constitution_version": version,
        "model_profile": profile,
        "created_at": int(created_at) if isinstance(created_at, (int, float)) else _now(),
        "updated_at": int(updated_at) if isinstance(updated_at, (int, float)) else _now(),
    }


def migrated_identity() -> dict[str, Any]:
    """Build the first registry entry from the installation settings identity."""
    values = _fallback_identity_values()
    return {
        "id": values["agent_id"],
        "display_name": values["agent_display_name"],
        "constitution": values["agent_constitution"],
        "constitution_version": values["agent_constitution_version"],
        "model_profile": empty_model_profile(),
        "created_at": _now(),
        "updated_at": _now(),
    }


def load_store() -> dict[str, Any]:
    """Load the registry, migrating the settings identity in memory when absent.

    Reads are side-effect free: a clean install never materializes a file just
    because something asked for the list. ``ensure_migrated`` persists the
    migrated first entry explicitly.
    """
    fallback = migrated_identity()
    raw: Any = None
    try:
        import json

        with open(_file_path(), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        raw = None
    identities: list[dict[str, Any]] = []
    active_id = ""
    if isinstance(raw, Mapping):
        for item in raw.get("identities") or []:
            entry = _normalize_entry(item)
            if entry and all(existing["id"] != entry["id"] for existing in identities):
                identities.append(entry)
        active_id = str(raw.get("active_id") or "")
    if not identities:
        identities = [fallback]
        active_id = fallback["id"]
    if not any(entry["id"] == active_id for entry in identities):
        active_id = identities[0]["id"]
    return {"version": _STORE_VERSION, "active_id": active_id, "identities": identities}


def save_store(store: Mapping[str, Any]) -> None:
    from core.atomic_io import atomic_write_json

    payload = {
        "version": _STORE_VERSION,
        "active_id": str(store.get("active_id") or ""),
        "identities": [
            {
                "id": entry["id"],
                "display_name": entry["display_name"],
                "constitution": entry["constitution"],
                "constitution_version": entry["constitution_version"],
                "model_profile": entry["model_profile"],
                "created_at": entry["created_at"],
                "updated_at": entry["updated_at"],
            }
            for entry in store.get("identities") or []
        ],
    }
    atomic_write_json(str(_file_path()), payload, indent=2)


def ensure_migrated() -> None:
    """Persist the migrated installation identity once, without changing it.

    Called at startup. Leaves an existing registry untouched.
    """
    if store_exists():
        return
    try:
        store = load_store()
        save_store(store)
    except Exception:
        # Registry migration must never block app startup; the in-memory
        # fallback keeps identity behavior working.
        pass


def list_identities() -> list[dict[str, Any]]:
    return load_store()["identities"]


def active_id() -> str:
    return str(load_store()["active_id"])


def get_identity(identity_id: str) -> dict[str, Any] | None:
    wanted = str(identity_id or "").strip()
    if not wanted:
        return None
    for entry in list_identities():
        if entry["id"] == wanted:
            return entry
    return None


def active_identity_entry() -> dict[str, Any] | None:
    store = load_store()
    for entry in store["identities"]:
        if entry["id"] == store["active_id"]:
            return entry
    return None


def identity_prompt_values(identity_id: str | None = None) -> dict[str, str] | None:
    """Map a saved identity onto the settings-shaped identity fields.

    Returns None when the registry has no usable entry, so callers fall back
    to the installation settings identity.
    """
    if identity_id:
        entry = get_identity(identity_id)
    else:
        if not store_exists():
            return None
        entry = active_identity_entry()
    if not entry:
        return None
    return {
        "agent_id": entry["id"],
        "agent_display_name": entry["display_name"],
        "agent_constitution": entry["constitution"],
        "agent_constitution_version": entry["constitution_version"],
    }


def resolve_model_profile(identity_id: str | None = None) -> dict[str, Any]:
    """Return the attached profile, or an empty profile when none is saved."""
    entry = get_identity(identity_id) if identity_id else active_identity_entry()
    if not entry:
        return empty_model_profile()
    try:
        return sanitize_model_profile(entry.get("model_profile"))
    except ValueError:
        return empty_model_profile()


def resolve_lane_profile(identity_id: str | None, lane: str) -> dict[str, str] | None:
    """Resolve one lane: the override when set, else the identity's chat lane.

    ``None`` means the caller should keep its existing default resolution.
    """
    if lane not in IDENTITY_LANES:
        raise ValueError("unknown model lane")
    profile = resolve_model_profile(identity_id)
    override = profile["lanes"].get(lane)
    if override:
        return dict(override)
    chat = profile.get(_CHAT_LANE) or {}
    if chat.get("endpoint_id") or chat.get("model"):
        return {"endpoint_id": chat.get("endpoint_id") or "", "model": chat.get("model") or ""}
    return None


def _slugify(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:64]
    if not slug or not _IDENTITY_ID_RE.fullmatch(slug):
        slug = "identity"
    return slug


def _unique_id(store: Mapping[str, Any], base: str) -> str:
    taken = {entry["id"] for entry in store.get("identities") or []}
    candidate = base
    suffix = 2
    while candidate in taken:
        marker = f"-{suffix}"
        candidate = f"{base[: 64 - len(marker)]}{marker}"
        suffix += 1
    return candidate


def _validate_new_entry(payload: Mapping[str, Any], *, identity_id: str | None = None) -> dict[str, Any]:
    store = load_store()
    if len(store["identities"]) >= _MAX_IDENTITIES:
        raise ValueError(f"at most {_MAX_IDENTITIES} identities can be saved")
    display_name = validate_identity_field("agent_display_name", payload.get("display_name"))
    constitution = validate_identity_field("agent_constitution", payload.get("constitution"))
    version = validate_identity_field("agent_constitution_version", payload.get("constitution_version"))
    profile = sanitize_model_profile(payload.get("model_profile"))
    raw_id = str(identity_id if identity_id is not None else (payload.get("id") or "")).strip()
    if not raw_id:
        raw_id = _slugify(display_name)
    identity_id_value = validate_identity_field("agent_id", raw_id)
    now = _now()
    return {
        "id": identity_id_value,
        "display_name": display_name,
        "constitution": constitution,
        "constitution_version": version,
        "model_profile": profile,
        "created_at": now,
        "updated_at": now,
    }


def create_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("identity payload must be an object")
    store = load_store()
    entry = _validate_new_entry(payload)
    entry["id"] = _unique_id(store, entry["id"])
    store["identities"].append(entry)
    if not store.get("active_id") or not any(e["id"] == store["active_id"] for e in store["identities"]):
        store["active_id"] = entry["id"]
    save_store(store)
    return entry


def update_identity(identity_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("identity payload must be an object")
    store = load_store()
    wanted = validate_identity_field("agent_id", identity_id)
    current = next((e for e in store["identities"] if e["id"] == wanted), None)
    if current is None:
        raise KeyError(identity_id)
    updated = _validate_new_entry(
        {
            "display_name": payload.get("display_name", current["display_name"]),
            "constitution": payload.get("constitution", current["constitution"]),
            "constitution_version": payload.get("constitution_version", current["constitution_version"]),
            "model_profile": payload.get("model_profile", current["model_profile"]),
        },
        identity_id=wanted,
    )
    updated["created_at"] = current["created_at"]
    updated["updated_at"] = _now()
    store["identities"] = [updated if e["id"] == wanted else e for e in store["identities"]]
    save_store(store)
    return updated


def duplicate_identity(identity_id: str) -> dict[str, Any]:
    store = load_store()
    wanted = validate_identity_field("agent_id", identity_id)
    current = next((e for e in store["identities"] if e["id"] == wanted), None)
    if current is None:
        raise KeyError(identity_id)
    if len(store["identities"]) >= _MAX_IDENTITIES:
        raise ValueError(f"at most {_MAX_IDENTITIES} identities can be saved")
    copy_entry = {
        "display_name": f"{current['display_name']} (copy)"[:_DISPLAY_NAME_MAX],
        "constitution": current["constitution"],
        "constitution_version": current["constitution_version"],
        "model_profile": copy.deepcopy(current["model_profile"]),
    }
    new_entry = _validate_new_entry(copy_entry, identity_id=_unique_id(store, f"{wanted}-copy"))
    store["identities"].append(new_entry)
    save_store(store)
    return new_entry


def delete_identity(identity_id: str) -> dict[str, Any]:
    store = load_store()
    wanted = validate_identity_field("agent_id", identity_id)
    remaining = [e for e in store["identities"] if e["id"] != wanted]
    if len(remaining) == len(store["identities"]):
        raise KeyError(identity_id)
    if not remaining:
        raise ValueError("at least one identity must remain")
    if store["active_id"] == wanted:
        store["active_id"] = remaining[0]["id"]
    store["identities"] = remaining
    save_store(store)
    return {"id": wanted, "active_id": store["active_id"]}


def set_active_identity(identity_id: str) -> dict[str, Any]:
    store = load_store()
    wanted = validate_identity_field("agent_id", identity_id)
    entry = next((e for e in store["identities"] if e["id"] == wanted), None)
    if entry is None:
        raise KeyError(identity_id)
    store["active_id"] = wanted
    save_store(store)
    return entry


def sync_installation_identity(fields: Mapping[str, Any]) -> None:
    """Mirror legacy settings writes into the active registry entry.

    The settings API stays backward compatible; a write there must not be
    shadowed by an existing registry file. A fresh installation without a
    registry keeps the pure settings path.
    """
    if not store_exists():
        return
    store = load_store()
    entry = next((e for e in store["identities"] if e["id"] == store["active_id"]), None)
    if entry is None:
        return
    try:
        for settings_key, entry_key in (
            ("agent_display_name", "display_name"),
            ("agent_constitution", "constitution"),
            ("agent_constitution_version", "constitution_version"),
        ):
            if settings_key in fields:
                entry[entry_key] = validate_identity_field(settings_key, fields[settings_key])
        new_id = fields.get("agent_id")
        if new_id:
            normalized = validate_identity_field("agent_id", new_id)
            if normalized != entry["id"] and not any(e["id"] == normalized for e in store["identities"]):
                entry["id"] = normalized
                store["active_id"] = normalized
        entry["updated_at"] = _now()
        save_store(store)
    except (KeyError, ValueError):
        # Settings validation already rejected the write; never corrupt the
        # registry because of a partial sync.
        return


def identity_id_for_session(session_id: str) -> str:
    """Return the identity bound to a session, or "" when unbound.

    Session rows are the authority (MAD-929): a browser cannot pass an
    arbitrary identity per turn.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    try:
        from core.database import Session as DbSession, SessionLocal

        db = SessionLocal()
        try:
            row = db.query(DbSession.identity_id).filter(DbSession.id == sid).first()
        finally:
            db.close()
        return str(row[0] or "") if row else ""
    except Exception:
        return ""


def session_reasoning_level(session_id: str) -> str:
    """Return the reasoning level bound to a session, or "" when unset."""
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    try:
        from core.database import Session as DbSession, SessionLocal

        db = SessionLocal()
        try:
            row = db.query(DbSession.reasoning_level).filter(DbSession.id == sid).first()
        finally:
            db.close()
        level = str(row[0] or "").strip().lower() if row else ""
        return level if level in _REASONING_LEVELS else ""
    except Exception:
        return ""


def public_identity(entry: Mapping[str, Any], *, include_constitution: bool = False) -> dict[str, Any]:
    """Project one saved identity for API/UI use.

    The constitution is a hidden system prompt (same rule as settings): it is
    only ever included for an authenticated admin caller.
    """
    projection = {
        "id": entry.get("id", ""),
        "display_name": entry.get("display_name", ""),
        "constitution_version": entry.get("constitution_version", ""),
        "model_profile": sanitize_model_profile(entry.get("model_profile")),
        "constitution_present": bool(entry.get("constitution")),
        "created_at": entry.get("created_at"),
        "updated_at": entry.get("updated_at"),
    }
    if include_constitution:
        projection["constitution"] = entry.get("constitution", "")
    return projection
