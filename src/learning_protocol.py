"""JOS-P6 learning evaluation, promotion, monitoring, and rollback.

Learning artifacts are data until this module records sufficient evaluation
evidence and an authorized promotion.  Producer confidence is retained for
review, but never counts as proof and never changes runtime policy by itself.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR
from src.authority_protocol import redact_secrets


LEARNING_FILE = Path(DATA_DIR) / "learning_candidates.json"
CANDIDATE_STATUSES = frozenset(
    {"draft", "evaluating", "approved", "rejected", "demoted", "superseded"}
)
EVALUATION_VERDICTS = frozenset(
    {"pass", "fail", "evaluator_failure", "unavailable", "inconclusive", "unknown"}
)
RISK_LEVELS = ("read_only_guidance", "bounded_write", "authority_security", "infrastructure")

_ABSOLUTE_USER_PATH = re.compile(
    r"(?:(?:/home|/Users)/[^\s/'\"`]+(?:/[^\s'\"`]*)?|[A-Za-z]:\\Users\\[^\s\\'\"`]+(?:\\[^\s'\"`]*)?)"
)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)
_PRIVATE_ENDPOINT = re.compile(
    r"\b(?:localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?::\d{2,5})?\b",
    re.I,
)
_UNSAFE_PATTERNS = {
    "prompt_injection": re.compile(
        r"\b(ignore|disregard|override)\b.{0,80}\b(previous|prior|system|developer|instruction|prompt)\b",
        re.I | re.S,
    ),
    "authority_bypass": re.compile(
        r"\b(bypass|skip|disable|forge|fake|never ask)\b.{0,80}\b(approval|authority|permission|receipt|policy)\b",
        re.I | re.S,
    ),
}
_READ_ONLY_CAPABILITIES = frozenset(
    {
        "read_file", "grep", "glob", "ls", "search_chats", "search_documents",
        "web_search", "list_emails", "manage_research", "get_workspace",
    }
)
_AUTHORITY_CAPABILITIES = frozenset(
    {
        "manage_settings", "manage_users", "manage_tokens", "manage_endpoints",
        "manage_mcp", "manage_webhooks", "manage_skills", "sudo", "admin",
    }
)
_INFRASTRUCTURE_CAPABILITIES = frozenset(
    {"bash", "shell", "ssh", "docker", "proxmox", "deploy", "systemctl", "kubectl"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _ephemeral_default(path: Path) -> bool:
    return path == LEARNING_FILE and (
        os.getenv("DATABASE_URL", "").lower() == "sqlite:///:memory:" or "pytest" in sys.modules
    )


def _normalise_text(value: str) -> str:
    safe = str(redact_secrets(value))
    safe = _ABSOLUTE_USER_PATH.sub("<DISCOVER_PATH>", safe)
    safe = _PRIVATE_ENDPOINT.sub("<DISCOVER_ENDPOINT>", safe)
    safe = _UUID.sub("<DISCOVER_ID>", safe)
    return safe


def normalize_artifact(value: Any) -> Any:
    """Return a host-neutral, credential-free copy suitable for evaluation."""
    if isinstance(value, Mapping):
        redacted = redact_secrets(dict(value))
        return {str(key): normalize_artifact(item) for key, item in redacted.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize_artifact(item) for item in value]
    if isinstance(value, str):
        return _normalise_text(value)
    return copy.deepcopy(value)


def artifact_conflicts(artifact: Any, *, operator_id: str | None = None) -> list[str]:
    text = json.dumps(artifact, ensure_ascii=False, default=str)
    # Safety procedures commonly say "do not bypass approval".  Preserve the
    # prohibition while preventing that quoted boundary from looking like an
    # instruction to bypass it.
    text = re.sub(
        r"\b(?:do not|don't|never)\s+(?:bypass|skip|disable|forge|fake)\b",
        "prohibit",
        text,
        flags=re.I,
    )
    conflicts = [name for name, pattern in _UNSAFE_PATTERNS.items() if pattern.search(text)]
    try:
        from src.agent_identity import resolve_agent_identity
        identity = resolve_agent_identity()
        protected = {
            "identity", "operator", "owner",
            str(identity.get("agent_id") or ""),
            str(identity.get("agent_display_name") or ""),
            str(operator_id or ""),
        }
    except Exception:
        protected = {"identity", "operator", "owner", str(operator_id or "")}
    terms = sorted(
        (re.escape(value) for value in protected if 1 < len(value) <= 80),
        key=len,
        reverse=True,
    )
    if terms and re.search(
        rf"\b(change|replace|pretend|impersonate|become)\b.{{0,80}}\b(?:{'|'.join(terms)})\b",
        text,
        re.I | re.S,
    ):
        conflicts.append("identity_override")
    return conflicts


def classify_risk(artifact: Mapping[str, Any], capabilities: Sequence[str] | None = None) -> str:
    caps = {str(item).strip().lower() for item in (capabilities or []) if str(item).strip()}
    caps.update(
        str(item).strip().lower()
        for item in (artifact.get("requires_toolsets") or [])
        if str(item).strip()
    )
    text = json.dumps(artifact, ensure_ascii=False, default=str).lower()
    procedure_text = "\n".join(
        str(item) for item in (
            list(artifact.get("procedure") or artifact.get("steps") or [])
            + list(artifact.get("verification") or [])
        )
    ).lower()
    if caps & _INFRASTRUCTURE_CAPABILITIES or re.search(r"\b(deploy|firewall|database schema|root|sudo)\b", text):
        return "infrastructure"
    if caps & _AUTHORITY_CAPABILITIES or re.search(r"\b(authentication|authorization|credential|approval policy)\b", text):
        return "authority_security"
    if caps - _READ_ONLY_CAPABILITIES or re.search(
        r"(?m)^\s*(?:\d+[.)]\s*)?(?:send|create|update|delete|write|publish|execute|run)\b",
        procedure_text,
    ):
        return "bounded_write"
    return "read_only_guidance"


def _producer_record(producer: Mapping[str, Any] | str, confidence: float | None) -> dict[str, Any]:
    if isinstance(producer, Mapping):
        record = normalize_artifact(dict(producer))
    else:
        record = {"id": _normalise_text(str(producer or "unknown")), "kind": "model"}
    if confidence is not None:
        try:
            record["confidence"] = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            record["confidence"] = 0.0
    record.setdefault("kind", "model")
    return record


def structural_evaluation_cases(
    artifact: Mapping[str, Any],
    *,
    original_verdict: str = "pass",
    original_evidence_kind: str = "native_test",
    evaluator_id: str = "odysseus-skill-audit",
) -> list[dict[str, Any]]:
    """Build the original/boundary/negative evidence contract for a skill."""
    procedure = artifact.get("procedure") or artifact.get("steps") or artifact.get("body_extra") or []
    trigger = artifact.get("when_to_use") or artifact.get("problem") or artifact.get("description")
    boundary_ok = bool(procedure and trigger)
    negative_ok = not artifact_conflicts(artifact)
    return [
        {
            "role": "original",
            "verdict": original_verdict,
            "evidence_kind": original_evidence_kind,
            "evaluator_id": evaluator_id,
            "summary": "Representative procedure run",
        },
        {
            "role": "boundary",
            "verdict": "pass" if boundary_ok else "fail",
            "evidence_kind": "deterministic",
            "evaluator_id": "jos-p6-schema",
            "summary": "Procedure and trigger boundary are explicit",
        },
        {
            "role": "negative",
            "verdict": "pass" if negative_ok else "fail",
            "evidence_kind": "deterministic",
            "evaluator_id": "jos-p6-policy",
            "summary": "No identity, prompt, or authority override was admitted",
        },
    ]


class LearningCandidateStore:
    """Atomic owner-scoped candidate, promotion, and monitoring ledger."""

    def __init__(self, path: Path | str = LEARNING_FILE):
        self.path = Path(path)
        self._ephemeral = _ephemeral_default(self.path)
        self._state: dict[str, Any] = {"candidates": {}, "promotions": [], "monitoring": {}}
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if self._ephemeral:
            return copy.deepcopy(self._state)
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                value.setdefault("candidates", {})
                value.setdefault("promotions", [])
                value.setdefault("monitoring", {})
                return value
        except (OSError, ValueError, TypeError):
            pass
        return {"candidates": {}, "promotions": [], "monitoring": {}}

    def _write(self, state: dict[str, Any]) -> None:
        if self._ephemeral:
            self._state = copy.deepcopy(state)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(str(self.path), state, indent=2)

    @staticmethod
    def _owner_matches(candidate: Mapping[str, Any], owner_scope: str | None) -> bool:
        return owner_scope is None or candidate.get("owner_scope") == owner_scope

    def create_candidate(
        self,
        *,
        candidate_type: str,
        owner_scope: str,
        artifact: Mapping[str, Any],
        source_refs: Sequence[Any] | None,
        producer: Mapping[str, Any] | str,
        capabilities: Sequence[str] | None = None,
        confidence: float | None = None,
        version: str = "1.0.0",
        parent_candidate_id: str | None = None,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        if not str(owner_scope or "").strip():
            raise ValueError("candidate_owner_scope_required")
        normalized = normalize_artifact(dict(artifact or {}))
        conflicts = artifact_conflicts(normalized, operator_id=owner_scope)
        caps = sorted({str(item) for item in (capabilities or normalized.get("requires_toolsets") or []) if str(item)})
        risk = classify_risk(normalized, caps)
        candidate = {
            "candidate_id": candidate_id or str(uuid.uuid4()),
            "type": str(candidate_type or "procedure")[:80],
            "owner_scope": str(owner_scope),
            "source_refs": normalize_artifact(list(source_refs or [])[:50]),
            "producer": _producer_record(producer, confidence),
            "capabilities": caps,
            "risk": risk,
            "status": "rejected" if conflicts else "draft",
            "version": str(version or "1.0.0"),
            "parent_candidate_id": parent_candidate_id,
            "artifact": normalized,
            "artifact_fingerprint": _fingerprint(normalized),
            "conflicts": conflicts,
            "evaluation": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._lock:
            state = self._read()
            if candidate["candidate_id"] in state["candidates"]:
                raise ValueError("candidate_id_exists")
            state["candidates"][candidate["candidate_id"]] = candidate
            self._write(state)
        return copy.deepcopy(candidate)

    def get(self, candidate_id: str, *, owner_scope: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            candidate = self._read()["candidates"].get(candidate_id)
        if not candidate or not self._owner_matches(candidate, owner_scope):
            return None
        return copy.deepcopy(candidate)

    def list(self, *, owner_scope: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._read()["candidates"].values())
        rows = [
            copy.deepcopy(row) for row in rows
            if self._owner_matches(row, owner_scope) and (status is None or row.get("status") == status)
        ]
        return sorted(rows, key=lambda row: row.get("created_at") or "")

    def latest_for_artifact(
        self, *, owner_scope: str, candidate_type: str, artifact_name: str
    ) -> dict[str, Any] | None:
        matches = [
            row for row in self.list(owner_scope=owner_scope)
            if row.get("type") == candidate_type
            and str((row.get("artifact") or {}).get("name") or "") == artifact_name
        ]
        return matches[-1] if matches else None

    def evaluate(
        self,
        candidate_id: str,
        cases: Sequence[Mapping[str, Any]],
        *,
        evaluator: Mapping[str, Any] | str,
        runtime_version: str,
        owner_scope: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            candidate = state["candidates"].get(candidate_id)
            if not candidate or not self._owner_matches(candidate, owner_scope):
                raise KeyError("candidate_not_found")
            normalized_cases = []
            for raw in cases:
                row = normalize_artifact(dict(raw))
                verdict = str(row.get("verdict") or "unknown").lower()
                if verdict in {"needs_work", "error"}:
                    verdict = "fail" if verdict == "needs_work" else "evaluator_failure"
                if verdict not in EVALUATION_VERDICTS:
                    verdict = "unknown"
                row["verdict"] = verdict
                row["role"] = str(row.get("role") or "original").lower()
                normalized_cases.append(row)

            required_roles = {"original", "boundary", "negative"}
            roles = {row["role"] for row in normalized_cases if row["verdict"] == "pass"}
            all_passed = bool(normalized_cases) and all(row["verdict"] == "pass" for row in normalized_cases)
            action_original = any(
                row.get("role") == "original"
                and row.get("verdict") == "pass"
                and row.get("evidence_kind") in {"native_test", "integration_test"}
                for row in normalized_cases
            )
            corroborated = any(
                row.get("evidence_kind") in {"deterministic", "native_test", "integration_test"}
                and row.get("verdict") == "pass"
                for row in normalized_cases
            )
            producer_id = str((candidate.get("producer") or {}).get("id") or "")
            independent = any(
                str(row.get("evaluator_id") or "") != producer_id
                and row.get("verdict") == "pass"
                for row in normalized_cases
            )
            passed = sum(1 for row in normalized_cases if row["verdict"] == "pass")
            verdict = "pass" if (
                all_passed
                and required_roles <= roles
                and corroborated
                and independent
                and (candidate.get("risk") == "read_only_guidance" or action_original)
                and not candidate.get("conflicts")
            ) else "fail"
            if any(row["verdict"] == "evaluator_failure" for row in normalized_cases):
                verdict = "evaluator_failure"
            elif any(row["verdict"] == "unavailable" for row in normalized_cases):
                verdict = "unavailable"
            elif any(row["verdict"] == "unknown" for row in normalized_cases):
                verdict = "unknown"
            elif any(row["verdict"] == "inconclusive" for row in normalized_cases):
                verdict = "inconclusive"

            evaluation = {
                "verdict": verdict,
                "cases": normalized_cases,
                "evaluator": normalize_artifact(dict(evaluator)) if isinstance(evaluator, Mapping) else {"id": str(evaluator)},
                "runtime_version": str(runtime_version),
                "metrics": {
                    "sample_size": len(normalized_cases),
                    "passed": passed,
                    "pass_rate": round(passed / len(normalized_cases), 4) if normalized_cases else 0.0,
                    "corroborated": corroborated,
                    "independent_review": independent,
                },
                "evaluated_at": _now(),
            }
            candidate["evaluation"] = evaluation
            candidate["status"] = "evaluating" if verdict == "pass" else "rejected"
            candidate["updated_at"] = _now()
            self._write(state)
            return copy.deepcopy(evaluation)

    @staticmethod
    def _learning_enabled() -> bool:
        try:
            from src.settings import get_setting
            return bool(get_setting("learning_enabled", True))
        except Exception:
            return True

    def promote(
        self,
        candidate_id: str,
        *,
        operator_id: str,
        owner_scope: str | None = None,
        automatic: bool = False,
        policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._learning_enabled():
            raise ValueError("learning_disabled")
        policy = dict(policy or {})
        with self._lock:
            state = self._read()
            candidate = state["candidates"].get(candidate_id)
            if not candidate or not self._owner_matches(candidate, owner_scope):
                raise KeyError("candidate_not_found")
            evaluation = candidate.get("evaluation") or {}
            metrics = evaluation.get("metrics") or {}
            if evaluation.get("verdict") != "pass":
                raise ValueError("evaluation_pass_required")
            if candidate.get("conflicts"):
                raise ValueError("candidate_policy_conflict")
            producer = candidate.get("producer") or {}
            if producer.get("kind") != "operator" and str(producer.get("id") or "") == str(operator_id):
                raise ValueError("producer_cannot_self_approve")
            min_cases = max(3, int(policy.get("minimum_cases", 3)))
            min_rate = max(0.0, min(1.0, float(policy.get("minimum_pass_rate", 1.0))))
            if int(metrics.get("sample_size") or 0) < min_cases or float(metrics.get("pass_rate") or 0.0) < min_rate:
                raise ValueError("promotion_evidence_below_policy")
            if not metrics.get("corroborated") or not metrics.get("independent_review"):
                raise ValueError("independent_corroboration_required")
            if automatic and candidate.get("risk") != "read_only_guidance":
                raise ValueError("risk_requires_operator_review")

            artifact = candidate.get("artifact") or {}
            name = str(artifact.get("name") or candidate["candidate_id"])
            for other in state["candidates"].values():
                if (
                    other.get("candidate_id") != candidate_id
                    and other.get("owner_scope") == candidate.get("owner_scope")
                    and other.get("type") == candidate.get("type")
                    and str((other.get("artifact") or {}).get("name") or other.get("candidate_id")) == name
                    and other.get("status") == "approved"
                ):
                    other["status"] = "superseded"
                    other["updated_at"] = _now()
            record = {
                "promotion_id": str(uuid.uuid4()),
                "candidate_id": candidate_id,
                "owner_scope": candidate.get("owner_scope"),
                "artifact_type": candidate.get("type"),
                "artifact_name": name,
                "artifact": copy.deepcopy(artifact),
                "artifact_fingerprint": candidate.get("artifact_fingerprint"),
                "version": candidate.get("version"),
                "source_refs": copy.deepcopy(candidate.get("source_refs") or []),
                "evaluation": copy.deepcopy(evaluation),
                "operator_id": str(operator_id),
                "automatic": bool(automatic),
                "risk": candidate.get("risk"),
                "promoted_at": _now(),
                "status": "active",
            }
            for prior in state["promotions"]:
                if (
                    prior.get("owner_scope") == record["owner_scope"]
                    and prior.get("artifact_type") == record["artifact_type"]
                    and prior.get("artifact_name") == name
                    and prior.get("status") == "active"
                ):
                    prior["status"] = "superseded"
                    record["previous_promotion_id"] = prior.get("promotion_id")
            state["promotions"].append(record)
            candidate["status"] = "approved"
            candidate["updated_at"] = _now()
            self._write(state)
        self._operational_event(candidate, record, "promotion")
        return copy.deepcopy(record)

    def demote(
        self, candidate_id: str, *, operator_id: str, reason: str, owner_scope: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            candidate = state["candidates"].get(candidate_id)
            if not candidate or not self._owner_matches(candidate, owner_scope):
                raise KeyError("candidate_not_found")
            candidate["status"] = "demoted"
            candidate["demotion"] = {
                "operator_id": str(operator_id),
                "reason": _normalise_text(reason)[:500],
                "demoted_at": _now(),
            }
            candidate["updated_at"] = _now()
            for promotion in state["promotions"]:
                if promotion.get("candidate_id") == candidate_id and promotion.get("status") == "active":
                    promotion["status"] = "demoted"
            self._write(state)
        self._operational_event(candidate, candidate["demotion"], "rollback")
        return copy.deepcopy(candidate)

    def rollback(
        self,
        *,
        owner_scope: str,
        artifact_type: str,
        artifact_name: str,
        target_promotion_id: str,
        operator_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            candidates = [
                row for row in state["promotions"]
                if row.get("owner_scope") == owner_scope
                and row.get("artifact_type") == artifact_type
                and row.get("artifact_name") == artifact_name
            ]
            target = next((row for row in candidates if row.get("promotion_id") == target_promotion_id), None)
            active = next((row for row in candidates if row.get("status") == "active"), None)
            if not target:
                raise KeyError("rollback_target_not_found")
            if active:
                active["status"] = "rolled_back"
            restored = copy.deepcopy(target)
            restored.update(
                {
                    "promotion_id": str(uuid.uuid4()),
                    "operator_id": str(operator_id),
                    "automatic": False,
                    "promoted_at": _now(),
                    "status": "active",
                    "rollback_of": active.get("promotion_id") if active else None,
                    "restored_from": target_promotion_id,
                }
            )
            state["promotions"].append(restored)
            restored_candidate = state["candidates"].get(restored.get("candidate_id"))
            if restored_candidate:
                restored_candidate["status"] = "approved"
                restored_candidate["updated_at"] = _now()
            self._write(state)
        self._operational_event(restored_candidate or {}, restored, "rollback")
        return copy.deepcopy(restored)

    def record_outcome(
        self,
        candidate_id: str,
        *,
        succeeded: bool,
        latency_seconds: float | None = None,
        regression: bool = False,
        owner_scope: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            candidate = state["candidates"].get(candidate_id)
            if not candidate or not self._owner_matches(candidate, owner_scope):
                raise KeyError("candidate_not_found")
            metrics = state["monitoring"].setdefault(
                candidate_id,
                {"uses": 0, "successes": 0, "failures": 0, "latency_total": 0.0, "regressions": 0},
            )
            metrics["uses"] += 1
            metrics["successes" if succeeded else "failures"] += 1
            metrics["latency_total"] += max(float(latency_seconds or 0.0), 0.0)
            metrics["regressions"] += int(bool(regression))
            metrics["success_rate"] = round(metrics["successes"] / metrics["uses"], 4)
            metrics["average_latency"] = round(metrics["latency_total"] / metrics["uses"], 6)
            metrics["updated_at"] = _now()
            self._write(state)
            return copy.deepcopy(metrics)

    def snapshot(self, *, owner_scope: str | None = None) -> dict[str, Any]:
        with self._lock:
            state = self._read()
        if owner_scope is None:
            return state
        candidate_ids = {
            key for key, row in state["candidates"].items() if row.get("owner_scope") == owner_scope
        }
        return {
            "candidates": {key: row for key, row in state["candidates"].items() if key in candidate_ids},
            "promotions": [row for row in state["promotions"] if row.get("owner_scope") == owner_scope],
            "monitoring": {key: row for key, row in state["monitoring"].items() if key in candidate_ids},
        }

    @staticmethod
    def _operational_event(candidate: Mapping[str, Any], record: Mapping[str, Any], event_type: str) -> None:
        try:
            from src.operational_protocol import record_operational_event
            record_operational_event(
                operator_id=str(record.get("operator_id") or "") or None,
                actor="learning_protocol",
                component="learning_promotion",
                event_type=event_type,
                status="succeeded",
                evidence_refs=[record.get("promotion_id"), candidate.get("candidate_id")],
                metadata={"risk": candidate.get("risk"), "version": candidate.get("version")},
            )
        except Exception:
            pass


learning_candidates = LearningCandidateStore()
