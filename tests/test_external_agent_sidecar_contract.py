"""Conformance guards for the provider-neutral external-agent sidecar contract."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "specs" / "schemas" / "pandamonium-external-agent-sidecar-v1.schema.json"
DISCOVERY_SCHEMA_PATH = ROOT / "specs" / "schemas" / "pandamonium-discovery-v1.schema.json"
CONTRACT_PATH = ROOT / "specs" / "external-agent-sidecar-contract.md"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "external-agent-sidecar-v1.json"
MAX_WIRE_BYTES = 65_536


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _declared_properties(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value.get("properties", {})) | set().union(
            *(_declared_properties(child) for child in value.values()),
            set(),
        )
    if isinstance(value, list):
        return set().union(*(_declared_properties(child) for child in value), set())
    return set()


SCHEMA = _load(SCHEMA_PATH)
FIXTURES = _load(FIXTURE_PATH)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def _conformance_result(message: dict, context: dict) -> str:
    wire_size = int(context.get("wire_size") or len(json.dumps(message).encode()))
    if wire_size > MAX_WIRE_BYTES:
        return "oversized_payload"
    if next(VALIDATOR.iter_errors(message), None):
        return "malformed_envelope"
    if message["envelope"] == "request":
        now = datetime.fromisoformat(context["now"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(message["expires_at"].replace("Z", "+00:00"))
        if expires <= now:
            return "stale_request"
        if message["nonce"] in context.get("seen_nonces", []):
            return "replay_detected"
        if context.get("authorized") is not True:
            return "unauthorized"
        if message["owner_ref"] != context.get("owner_ref"):
            return "wrong_owner"
        if message["workspace_alias"] != context.get("workspace_alias"):
            return "wrong_workspace"
        if context.get("sidecar_available") is not True:
            return "sidecar_unavailable"
    return "ok"


def test_normative_schema_and_all_envelope_fixtures_validate():
    Draft202012Validator.check_schema(SCHEMA)
    envelopes = FIXTURES["valid_envelopes"]
    assert {item["envelope"] for item in envelopes} == {
        "request", "response", "event", "health", "capabilities", "error", "cancel"
    }
    for envelope in envelopes:
        VALIDATOR.validate(envelope)


def test_mocked_conformance_cases_fail_closed_with_stable_codes():
    envelopes = FIXTURES["valid_envelopes"]
    cases = FIXTURES["cases"]
    assert {case["name"] for case in cases} >= {
        "valid", "stale", "malformed", "unauthorized", "oversized",
        "wrong-owner", "wrong-Workspace", "unavailable-sidecar",
    }
    for case in cases:
        message = deepcopy(case.get("message") or envelopes[case["envelope_index"]])
        assert _conformance_result(message, case["context"]) == case["expected"], case["name"]


def test_contract_reuses_canonical_effects_and_is_provider_neutral():
    discovery = _load(DISCOVERY_SCHEMA_PATH)
    canonical_effects = set(discovery["$defs"]["action"]["properties"]["effect"]["enum"])
    assert set(SCHEMA["$defs"]["effect"]["enum"]) == canonical_effects

    schema_text = json.dumps(SCHEMA).lower()
    assert "cursor" not in schema_text
    assert _declared_properties(SCHEMA).isdisjoint(
        {"auth_ref", "token", "secret", "password", "credential"}
    )

    secret_event = deepcopy(FIXTURES["valid_envelopes"][2])
    secret_event["metadata"]["access_token"] = "not-allowed-on-wire"
    assert not VALIDATOR.is_valid(secret_event)


def test_contract_names_required_boundaries_threats_and_credit():
    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    normalized = contract.lower()
    for term in (
        "Model/Agent/Worker/Workspace/Connection",
        "destructive_or_difficult_to_recover",
        "external_publication_or_communication",
        "purchase",
        "credential_or_auth_change",
        "privilege_expansion",
        "outside_workspace_boundary",
        "localhost sidecar",
        "remote sidecar",
        "SSRF",
        "replay",
        "confused deputy",
        "event",
        "Tokens",
        "path",
        "symlink",
        "Oversized",
        "denial of service",
        "dependency",
        "No provider name is part of the protocol",
        "Twenty4SevenLabs/Pandamonium",
        "7220f7cc9cbee26cd94697bd7a6a3d0ef001b66d",
    ):
        assert term.lower() in normalized

    for forbidden in (
        "cursor-sdk==",
        "http://192.168.",
        "http://10.",
        "/home/",
        "read-write editor-home mount is enabled",
    ):
        assert forbidden not in contract
