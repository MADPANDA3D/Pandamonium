import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_security_tranche_evidence.py"
EVIDENCE_PATH = ROOT / "docs" / "security" / "mad-799-tranche-one-evidence.json"
SELECTION_PATH = ROOT / "docs" / "security" / "mad-799-tranche-one-selection.md"
THREAT_PATH = ROOT / "docs" / "security" / "mad-799-tranche-one-threat-model.md"


def load_module():
    spec = importlib.util.spec_from_file_location("verify_security_tranche_evidence", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def evidence():
    return json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


def test_committed_evidence_passes_structural_validation():
    inv = load_module()
    payload = evidence()

    assert inv.validate_evidence(payload) == []
    assert inv.scan_for_secrets(EVIDENCE_PATH.read_text(encoding="utf-8")) == []


def test_documents_cover_every_candidate_and_exclusion_class():
    inv = load_module()
    payload = evidence()

    failures = inv.validate_documents(
        payload,
        SELECTION_PATH.read_text(encoding="utf-8"),
        THREAT_PATH.read_text(encoding="utf-8"),
    )

    assert failures == []
    assert inv.validate_documents(payload, "", "") != []


def test_tranche_policy_rejects_mutating_or_privileged_candidates():
    inv = load_module()
    mutating = copy.deepcopy(evidence())
    mutating["candidates"][0]["classification"]["mutating"] = True
    privileged = copy.deepcopy(evidence())
    privileged["candidates"][0]["classification"]["privileged"] = True

    assert any("forbids mutating" in failure for failure in inv.validate_evidence(mutating))
    assert any("forbids privileged" in failure for failure in inv.validate_evidence(privileged))


def test_tranche_policy_rejects_credentials_and_bad_network_allowlist():
    inv = load_module()
    credentialed = copy.deepcopy(evidence())
    credentialed["candidates"][0]["manifest_plan"]["credentials_required"] = True
    bad_network = copy.deepcopy(evidence())
    bad_network["candidates"][0]["classification"]["network_active"] = True
    bad_network["candidates"][0]["manifest_plan"]["network_allowlist"] = ["http://insecure.example"]

    assert any("forbids credential" in failure for failure in inv.validate_evidence(credentialed))
    assert any("https URLs" in failure for failure in inv.validate_evidence(bad_network))


def test_missing_exclusion_class_is_rejected():
    inv = load_module()
    payload = copy.deepcopy(evidence())
    payload["exclusion_classes"] = [item for item in payload["exclusion_classes"] if item["class"] != "persistence"]

    failures = inv.validate_evidence(payload)

    assert any("missing exclusion classes" in failure for failure in failures)


def test_secret_scanner_flags_planted_credential():
    inv = load_module()

    assert inv.scan_for_secrets("token='ghp_" + "a" * 30 + "'")
    assert inv.scan_for_secrets("api_key: sk-" + "b" * 30)
    assert inv.scan_for_secrets("-----BEGIN RSA PRIVATE KEY-----")


def test_cli_verify_offline_passes():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--verify"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERIFY PASS" in result.stdout
    assert "evidence structure and tranche policy" in result.stdout
