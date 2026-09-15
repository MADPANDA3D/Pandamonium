"""Owner-scoped package setup using the existing encrypted integration store."""

from __future__ import annotations

import hashlib
import json

from core.database import Integration, ModelEndpoint, SessionLocal
from src.extension_installer import ExtensionLifecycleError
from src.secret_storage import decrypt, encrypt


def _id(manifest: dict, owner: str) -> str:
    return (
        "extension-"
        + hashlib.sha256(
            (
                owner
                + "\0"
                + manifest["extension_id"]
                + "\0"
                + manifest["source"]["url"]
            ).encode()
        ).hexdigest()
    )


def values(manifest: dict, owner: str, *, required: bool = True) -> dict[str, str]:
    if not manifest.get("configuration"):
        return {}
    with SessionLocal() as db:
        row = (
            db.query(Integration)
            .filter_by(id=_id(manifest, owner), owner=owner)
            .first()
        )
        stored = (row.config or {}) if row else {}
    result = {}
    for field in manifest.get("configuration", []):
        key = field["key"]
        raw = decrypt(stored.get(key, ""))
        value = json.loads(raw) if raw else ""
        if value:
            result[key] = value
        elif required and field.get("required"):
            raise ExtensionLifecycleError(
                "extension_needs_setup:" + key + " — " + field["description"]
            )
    if required and result.get("ENDPOINT_ID"):
        with SessionLocal() as db:
            endpoint = (
                db.query(ModelEndpoint)
                .filter_by(id=result["ENDPOINT_ID"], is_enabled=True)
                .first()
            )
            if endpoint is None or endpoint.owner not in {None, owner}:
                raise ExtensionLifecycleError(
                    "extension_needs_setup:Choose an enabled endpoint owned by this account."
                )
            result["PANDAMONIUM_ENDPOINT_URL"] = endpoint.base_url
            result["PANDAMONIUM_ENDPOINT_TOKEN"] = endpoint.api_key or ""
    return result


def fingerprint(config: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def public(manifest: dict, owner: str) -> dict:
    config = values(manifest, owner, required=False)
    result = {
        "fields": [
            {
                **field,
                "configured": field["key"] in config,
                **(
                    {"value": config.get(field["key"], "")}
                    if not field.get("secret")
                    else {}
                ),
            }
            for field in manifest.get("configuration", [])
        ]
    }
    if any(
        field["key"] == "ENDPOINT_ID" for field in manifest.get("configuration", [])
    ):
        with SessionLocal() as db:
            result["targets"] = [
                {
                    "id": endpoint.id,
                    "name": endpoint.name,
                    "kind": endpoint.model_type,
                    "base_url": endpoint.base_url,
                }
                for endpoint in db.query(ModelEndpoint).filter_by(is_enabled=True).all()
                if endpoint.owner in {None, owner} and endpoint.endpoint_kind != "agent"
            ]
    return result


def connect_voice(config: dict[str, str], model: str) -> dict:
    from src.settings import load_settings, save_settings

    endpoint_id = config.get("ENDPOINT_ID")
    if not endpoint_id:
        raise ExtensionLifecycleError(
            "extension_needs_setup:Select a TTS endpoint for voice."
        )
    with SessionLocal() as db:
        endpoint = (
            db.query(ModelEndpoint).filter_by(id=endpoint_id, is_enabled=True).first()
        )
        if endpoint is None or endpoint.model_type != "tts":
            raise ExtensionLifecycleError(
                "extension_needs_setup:Select a TTS endpoint for voice."
            )
    settings = load_settings()
    previous = {
        key: settings.get(key) for key in ("tts_provider", "tts_model", "tts_enabled")
    }
    settings.update(
        tts_provider="endpoint:" + endpoint_id, tts_model=model, tts_enabled=True
    )
    save_settings(settings)
    return previous


def save(manifest: dict, owner: str, updates: dict[str, str | None]) -> dict:
    fields = {field["key"] for field in manifest.get("configuration", [])}
    if set(updates) - fields or any(
        value is not None
        and (not isinstance(value, str) or len(value) > 8192 or "\0" in value)
        for value in updates.values()
    ):
        raise ExtensionLifecycleError("extension_configuration_invalid")
    with SessionLocal() as db:
        row = (
            db.query(Integration)
            .filter_by(id=_id(manifest, owner), owner=owner)
            .first()
        )
        if row is None:
            row = Integration(
                id=_id(manifest, owner),
                owner=owner,
                name=manifest["extension_id"],
                type="extension_runtime",
                config={},
            )
            db.add(row)
        config = dict(row.config or {})
        for key, value in updates.items():
            if value:
                # Encrypt a JSON string: values beginning with enc: are still plaintext input.
                config[key] = encrypt(json.dumps(value))
            else:
                config.pop(key, None)
        row.config = config
        db.commit()
    return public(manifest, owner)
