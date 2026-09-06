"""Pinned, approval-gated extension installation and reversible lifecycle.

Only Git transport and managed-directory state live here. Native runtime start,
health, catalog resolution, and shutdown stay behind explicit adapters.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse, urlunparse

from core.atomic_io import atomic_write_json
from src.action_protocol import (
    build_action_result,
    compose_capability_catalog,
    normalize_action_call,
    utc_now,
    validate_action_call,
)
from src.agent_identity import configured_agent_id
from src.authority_protocol import AuthorityStore, authority_store
from src.constants import EXTENSIONS_DIR
from src.extension_registry import (
    IMMUTABLE_REVISION_PATTERN,
    ExtensionContractError,
    ExtensionRegistry,
    validate_extension_manifest,
)
from src.operational_protocol import record_operational_event
from src.runtime_paths import get_app_root
from src.url_security import validate_public_http_url

LIFECYCLE_VERSION = "jos-extension-lifecycle.v1"
MANIFEST_NAME = "jarvis-extension.json"
SUPPORTED_GIT_HOSTS = frozenset({"github.com", "gitlab.com", "codeberg.org"})
REF_PATTERN = re.compile(
    r"^(?:HEAD|[A-Za-z0-9][A-Za-z0-9._/-]{0,199}|[0-9a-f]{40}|[0-9a-f]{64})$"
)
MAX_MANIFEST_BYTES = 256 * 1024
MAX_REPOSITORY_FILES = 50_000
MAX_REPOSITORY_BYTES = 512 * 1024 * 1024


class ExtensionLifecycleError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def default_extensions_root() -> Path:
    return Path(EXTENSIONS_DIR)


def normalize_git_source_url(value: str, *, check_public: bool = True) -> str:
    text = str(value or "").strip()
    if len(text) > 2_048:
        raise ExtensionLifecycleError("extension_git_url_too_long")
    parsed = urlparse(text)
    host = str(parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ExtensionLifecycleError("extension_git_url_unsupported") from exc
    if (
        parsed.scheme != "https"
        or host not in SUPPORTED_GIT_HOSTS
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise ExtensionLifecycleError("extension_git_url_unsupported")
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) < 2
        or any(part in {".", ".."} for part in parts)
        or "%" in parsed.path
        or "\\" in parsed.path
    ):
        raise ExtensionLifecycleError("extension_git_url_unsupported")
    path = "/" + "/".join(parts).removesuffix(".git") + ".git"
    normalized = urlunparse(("https", host, path, "", "", ""))
    if check_public:
        try:
            validate_public_http_url(normalized)
        except ValueError as exc:
            raise ExtensionLifecycleError("extension_git_url_not_public") from exc
    return normalized


def validate_git_ref(value: str) -> str:
    ref = str(value or "HEAD").strip()
    if (
        not REF_PATTERN.fullmatch(ref)
        or ".." in ref
        or "//" in ref
        or "@{" in ref
        or ref.endswith(("/", ".", ".lock"))
    ):
        raise ExtensionLifecycleError("extension_git_ref_invalid")
    return ref


GitRunner = Callable[
    [list[str], Path | None, int, Mapping[str, str]], subprocess.CompletedProcess
]


def _default_git_runner(
    argv: list[str], cwd: Path | None, timeout: int, environment: Mapping[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


class GitSourceClient:
    def __init__(
        self,
        *,
        runner: GitRunner = _default_git_runner,
        check_public_urls: bool = True,
        timeout_seconds: int = 60,
    ):
        self.runner = runner
        self.check_public_urls = check_public_urls
        self.timeout_seconds = min(max(int(timeout_seconds), 5), 120)

    def _run(self, args: list[str], *, cwd: Path | None = None) -> str:
        environment = dict(os.environ)
        environment.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_LFS_SKIP_SMUDGE": "1",
            }
        )
        try:
            result = self.runner(["git", *args], cwd, self.timeout_seconds, environment)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExtensionLifecycleError("extension_git_command_failed") from exc
        if result.returncode != 0:
            raise ExtensionLifecycleError("extension_git_command_failed")
        return str(result.stdout or "")

    def resolve_revision(
        self, source_url: str, requested_ref: str = "HEAD"
    ) -> tuple[str, str, str]:
        source = normalize_git_source_url(
            source_url, check_public=self.check_public_urls
        )
        ref = validate_git_ref(requested_ref)
        rows = []
        for line in self._run(["ls-remote", source]).splitlines():
            revision, separator, name = line.partition("\t")
            if separator and IMMUTABLE_REVISION_PATTERN.fullmatch(revision):
                rows.append((revision, name))
        if IMMUTABLE_REVISION_PATTERN.fullmatch(ref):
            matches = {revision for revision, _name in rows if revision == ref}
        elif ref == "HEAD" or ref.startswith("refs/"):
            matches = {revision for revision, name in rows if name == ref}
        else:
            peeled = {
                revision for revision, name in rows if name == f"refs/tags/{ref}^{{}}"
            }
            matches = peeled or {
                revision
                for revision, name in rows
                if name in {f"refs/heads/{ref}", f"refs/tags/{ref}"}
            }
        if not matches:
            raise ExtensionLifecycleError("extension_git_ref_not_found")
        if len(matches) != 1:
            raise ExtensionLifecycleError("extension_git_ref_ambiguous")
        return source, ref, next(iter(matches))

    def checkout(
        self, source_url: str, requested_ref: str, revision: str, destination: Path
    ) -> None:
        if destination.exists():
            raise ExtensionLifecycleError("extension_staging_path_exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run(["init", str(destination)])
        self._run(["-C", str(destination), "remote", "add", "origin", source_url])
        self._run(
            [
                "-C",
                str(destination),
                "fetch",
                "--depth",
                "1",
                "--filter=blob:none",
                "origin",
                requested_ref,
            ]
        )
        self._run(["-C", str(destination), "checkout", "--detach", revision])
        observed = self._run(["-C", str(destination), "rev-parse", "HEAD"]).strip()
        if observed != revision:
            raise ExtensionLifecycleError("extension_git_checkout_revision_mismatch")
        self._validate_checkout_size(destination)

    @staticmethod
    def _validate_checkout_size(root: Path) -> None:
        file_count = 0
        total_bytes = 0
        for path in root.rglob("*"):
            try:
                stat = path.lstat()
            except OSError as exc:
                raise ExtensionLifecycleError("extension_checkout_unreadable") from exc
            if path.is_file() and not path.is_symlink():
                file_count += 1
                total_bytes += stat.st_size
            if file_count > MAX_REPOSITORY_FILES or total_bytes > MAX_REPOSITORY_BYTES:
                raise ExtensionLifecycleError("extension_checkout_too_large")


class ExtensionLifecycleAdapter(Protocol):
    def supports(self, manifest: Mapping[str, Any]) -> bool: ...
    def validate(
        self, install_path: Path, manifest: Mapping[str, Any], source_revision: str
    ) -> tuple[Mapping[str, Any] | None, bool]: ...
    def activate(self, install_path: Path, manifest: Mapping[str, Any]) -> None: ...
    def deactivate(self, install_path: Path, manifest: Mapping[str, Any]) -> None: ...


class InlineWebAdapter:
    """The only built-in adapter: static web source with inline schemas and no commands."""

    def supports(self, manifest: Mapping[str, Any]) -> bool:
        lifecycle = manifest.get("lifecycle") or {}
        return (
            (manifest.get("runtime") or {}).get("type") == "web"
            and ((manifest.get("capabilities") or {}).get("descriptor") or {}).get(
                "type"
            )
            == "inline"
            and all(
                not lifecycle.get(name)
                for name in ("install", "start", "stop", "remove")
            )
        )

    def validate(
        self, install_path: Path, manifest: Mapping[str, Any], source_revision: str
    ) -> tuple[None, bool]:
        validate_web_entrypoint(install_path, manifest)
        return None, True

    def activate(self, install_path: Path, manifest: Mapping[str, Any]) -> None:
        return None

    def deactivate(self, install_path: Path, manifest: Mapping[str, Any]) -> None:
        return None


def validate_web_entrypoint(install_path: Path, manifest: Mapping[str, Any]) -> Path:
    entrypoint = install_path / str(
        (manifest.get("runtime") or {}).get("entrypoint") or ""
    )
    if (
        not entrypoint.resolve().is_relative_to(install_path.resolve())
        or not entrypoint.is_file()
        or entrypoint.is_symlink()
    ):
        raise ExtensionLifecycleError("extension_entrypoint_unavailable")
    return entrypoint


_ACTION_NAMES = {
    "install": "install_extension",
    "upgrade": "upgrade_extension",
    "enable": "enable_extension",
    "disable": "disable_extension",
    "rollback": "rollback_extension",
    "uninstall": "uninstall_extension",
}
_ACTION_PARAMETERS = {
    "type": "object",
    "properties": {
        "plan_id": {"type": "string", "maxLength": 64},
        "operation": {"type": "string", "enum": sorted(_ACTION_NAMES)},
        "extension_id": {"type": "string", "maxLength": 64},
        "source_url": {"type": "string", "maxLength": 2048},
        "source_revision": {"type": "string", "maxLength": 64},
        "target_revision": {"type": "string", "maxLength": 64},
        "permissions": {"type": "object"},
        "lifecycle": {"type": "object"},
        "data_boundaries": {"type": "object"},
        "removal": {"type": "object"},
        "artifact_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "artifact_size_bytes": {"type": "integer", "minimum": 1},
        "current_version": {"type": ["string", "null"], "maxLength": 80},
        "target_version": {"type": "string", "maxLength": 80},
        "dependencies": {"type": "array", "items": {"type": "object"}},
        "configuration": {"type": "array", "items": {"type": "object"}},
        "restart_required": {
            "type": "string",
            "enum": ["none", "plugin", "pandamonium"],
        },
        "admitted_skills": {
            "type": "array",
            "items": {"type": "string", "maxLength": 64},
        },
    },
    "required": ["plan_id", "operation", "extension_id"],
    "additionalProperties": False,
}
_ACTION_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Apply the approved {operation} lifecycle to one managed extension.",
            "parameters": _ACTION_PARAMETERS,
        },
    }
    for operation, name in _ACTION_NAMES.items()
]
_ACTION_CATALOG = compose_capability_catalog(_ACTION_SCHEMAS)


class ExtensionLifecycleManager:
    def __init__(
        self,
        *,
        root: Path | str | None = None,
        registry: ExtensionRegistry | None = None,
        authority: AuthorityStore = authority_store,
        git_client: GitSourceClient | None = None,
        adapters: list[ExtensionLifecycleAdapter] | None = None,
    ):
        self.root = Path(root or default_extensions_root()).resolve()
        self.registry = registry or ExtensionRegistry()
        self.authority = authority
        self.git = git_client or GitSourceClient()
        self.adapters = list(adapters or [InlineWebAdapter()])
        self.state_file = self.root / "lifecycle.json"
        self._lock = threading.RLock()
        self._validate_root()

    def _validate_root(self) -> None:
        app_root = Path(get_app_root()).resolve()
        inside_source = self.root == app_root or self.root.is_relative_to(app_root)
        nested_mount = False
        candidate = self.root
        while inside_source and candidate != app_root:
            if candidate.is_mount():
                nested_mount = True
                break
            candidate = candidate.parent
        if inside_source and not nested_mount:
            raise ExtensionLifecycleError("extension_root_must_be_outside_source")
        if self.root == self.root.parent or self.root == Path.home().resolve():
            raise ExtensionLifecycleError("extension_root_too_broad")

    def _ensure_dirs(self) -> None:
        for name in ("staging", "installed", "failed", "removed"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def _read_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"schema_version": LIFECYCLE_VERSION, "extensions": {}, "plans": {}}
        if (
            state.get("schema_version") != LIFECYCLE_VERSION
            or not isinstance(state.get("extensions"), dict)
            or not isinstance(state.get("plans"), dict)
        ):
            return {"schema_version": LIFECYCLE_VERSION, "extensions": {}, "plans": {}}
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        self._ensure_dirs()
        atomic_write_json(str(self.state_file), state, indent=2)

    def _adapter_for(self, manifest: Mapping[str, Any]) -> ExtensionLifecycleAdapter:
        for adapter in self.adapters:
            if adapter.supports(manifest):
                return adapter
        runtime = str((manifest.get("runtime") or {}).get("type") or "unknown")
        descriptor = str(
            ((manifest.get("capabilities") or {}).get("descriptor") or {}).get("type")
            or "unknown"
        )
        raise ExtensionLifecycleError(
            f"extension_adapter_required:{runtime}:{descriptor}"
        )

    @staticmethod
    def _validate_adapter(
        adapter: ExtensionLifecycleAdapter,
        path: Path,
        manifest: Mapping[str, Any],
        revision: str,
        owner_scope: str,
    ) -> tuple[Mapping[str, Any] | None, bool]:
        owner_validator = getattr(adapter, "validate_for_owner", None)
        if owner_validator:
            return owner_validator(path, manifest, revision, owner_scope=owner_scope)
        return adapter.validate(path, manifest, revision)

    @staticmethod
    def _deactivate_adapter(
        adapter: ExtensionLifecycleAdapter,
        path: Path,
        manifest: Mapping[str, Any],
        owner_scope: str,
    ) -> None:
        owner_deactivator = getattr(adapter, "deactivate_for_owner", None)
        if owner_deactivator:
            owner_deactivator(path, manifest, owner_scope=owner_scope)
        else:
            adapter.deactivate(path, manifest)

    @staticmethod
    def _load_manifest(checkout: Path) -> dict[str, Any]:
        path = checkout / MANIFEST_NAME
        try:
            stat = path.lstat()
        except OSError as exc:
            raise ExtensionLifecycleError("extension_manifest_missing") from exc
        if path.is_symlink() or not path.is_file() or stat.st_size > MAX_MANIFEST_BYTES:
            raise ExtensionLifecycleError("extension_manifest_unsafe")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            return validate_extension_manifest(manifest)
        except (OSError, ValueError, ExtensionContractError) as exc:
            raise ExtensionLifecycleError("extension_manifest_invalid") from exc

    @staticmethod
    def _manifest_source_matches(
        manifest: Mapping[str, Any], source_url: str, revision: str
    ) -> None:
        declared_url = normalize_git_source_url(
            str((manifest.get("source") or {}).get("url") or ""), check_public=False
        )
        declared_revision = str((manifest.get("source") or {}).get("revision") or "")
        if declared_url != source_url:
            raise ExtensionLifecycleError("extension_manifest_source_mismatch")
        if declared_revision not in {"self", revision}:
            raise ExtensionLifecycleError("extension_manifest_revision_mismatch")

    @staticmethod
    def _signed_manifest_matches(
        manifest: Mapping[str, Any], expected_manifest: Mapping[str, Any], revision: str
    ) -> dict[str, Any]:
        signed_manifest = validate_extension_manifest(expected_manifest)
        observed_manifest = json.loads(json.dumps(manifest))
        if observed_manifest["source"]["revision"] == "self":
            observed_manifest["source"]["revision"] = revision
        if observed_manifest != signed_manifest:
            raise ExtensionLifecycleError("extension_signed_manifest_mismatch")
        return signed_manifest

    def _action_call(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        manifest = plan.get("manifest") or {}
        arguments = {
            "plan_id": plan["plan_id"],
            "operation": plan["operation"],
            "extension_id": plan["extension_id"],
        }
        for key in ("source_url", "source_revision", "target_revision"):
            if plan.get(key):
                arguments[key] = plan[key]
        if manifest:
            arguments["permissions"] = manifest.get("permissions") or {}
            arguments["lifecycle"] = manifest.get("lifecycle") or {}
            arguments["data_boundaries"] = manifest.get("data_boundaries") or {}
            arguments["removal"] = self._removal_scope(manifest)
        distribution = plan.get("distribution") or {}
        artifact = distribution.get("artifact") or {}
        if artifact:
            arguments["artifact_sha256"] = artifact.get("sha256")
            arguments["artifact_size_bytes"] = artifact.get("size_bytes")
        for key in (
            "dependencies",
            "configuration",
            "restart_required",
            "current_version",
            "target_version",
        ):
            if distribution.get(key) is not None:
                arguments[key] = distribution[key]
        catalog = plan.get("resolved_catalog") or {}
        if catalog.get("skills"):
            arguments["admitted_skills"] = [item["id"] for item in catalog["skills"]]
        call = normalize_action_call(
            request_id=plan["plan_id"],
            call_id=plan["call_id"],
            agent_id=configured_agent_id(),
            actor="odysseus:extension-installer",
            capability_version=_ACTION_CATALOG["version"],
            name=_ACTION_NAMES[plan["operation"]],
            arguments=arguments,
            target=f"extension:{plan['extension_id']}",
            authority_ref=None,
            capability_policy={"permission_mode": "external_side_effect"},
        )
        error = validate_action_call(call, _ACTION_CATALOG)
        if error:
            raise ExtensionLifecycleError(error["category"])
        return call

    def _request_approval(
        self, plan: dict[str, Any], operator_id: str
    ) -> dict[str, Any]:
        call = self._action_call(plan)
        decision = self.authority.decide(
            call, operator_id=operator_id, session_id="extension-control"
        )
        plan["authority_decision_id"] = decision["decision_id"]
        record_operational_event(
            request_id=plan["plan_id"],
            call_id=plan["call_id"],
            operator_id=operator_id,
            actor="odysseus:authority",
            component=f"extension:{plan['extension_id']}",
            event_type="approval",
            status={"allow": "succeeded", "deny": "denied"}.get(
                decision["decision"], "approval_required"
            ),
            evidence_refs=[{"decision_id": decision["decision_id"]}],
            metadata={"operation": plan["operation"]},
        )
        return decision

    def preview_source(
        self,
        operation: str,
        source_url: str,
        requested_ref: str,
        *,
        operator_id: str,
        expected_manifest: Mapping[str, Any] | None = None,
        distribution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if operation not in {"install", "upgrade"}:
            raise ExtensionLifecycleError("extension_source_operation_invalid")
        self._ensure_dirs()
        plan_id = str(uuid.uuid4())
        staging = self.root / "staging" / plan_id
        record_operational_event(
            request_id=plan_id,
            operator_id=operator_id,
            actor="odysseus:extension-installer",
            component="extension-installer",
            event_type="started",
            status="running",
            metadata={"operation": f"preview_{operation}"},
        )
        try:
            source, ref, revision = self.git.resolve_revision(source_url, requested_ref)
            self.git.checkout(source, ref, revision, staging)
            manifest = self._load_manifest(staging)
            self._manifest_source_matches(manifest, source, revision)
            signed_manifest = None
            if expected_manifest is not None:
                signed_manifest = self._signed_manifest_matches(
                    manifest, expected_manifest, revision
                )
            adapter = self._adapter_for(manifest)
            extension_id = manifest["extension_id"]
            with self._lock:
                state = self._read_state()
                existing = state["extensions"].get(extension_id)
                if operation == "install" and existing:
                    raise ExtensionLifecycleError(
                        "extension_already_installed_use_upgrade"
                    )
                if operation == "upgrade" and not existing:
                    raise ExtensionLifecycleError("extension_not_installed_use_install")
                if existing and existing.get("owner_scope") not in {None, operator_id}:
                    raise ExtensionLifecycleError("extension_owner_scope_mismatch")
                resolved_catalog = None
                if (manifest.get("runtime") or {}).get("type") in {"skills", "mcp"}:
                    resolved_catalog, healthy = self._validate_adapter(
                        adapter, staging, manifest, revision, operator_id
                    )
                    if not healthy:
                        raise ExtensionLifecycleError("extension_health_unavailable")
                plan = {
                    "plan_id": plan_id,
                    "call_id": str(uuid.uuid4()),
                    "operation": operation,
                    "extension_id": extension_id,
                    "source_url": source,
                    "requested_ref": ref,
                    "source_revision": revision,
                    "staging_path": str(staging),
                    "manifest": manifest,
                    "resolved_catalog": resolved_catalog,
                    "expected_manifest": signed_manifest,
                    "distribution": dict(distribution or {}),
                    "operator_id": operator_id,
                    "status": "pending_approval",
                    "created_at": utc_now(),
                }
                decision = self._request_approval(plan, operator_id)
                state["plans"][plan_id] = plan
                self._write_state(state)
            record_operational_event(
                request_id=plan_id,
                call_id=plan["call_id"],
                operator_id=operator_id,
                actor="odysseus:extension-installer",
                component=f"extension:{extension_id}",
                event_type="result",
                status="succeeded",
                evidence_refs=[{"source_revision": revision}],
                metadata={"operation": f"preview_{operation}"},
            )
            return self._public_plan(plan, decision=decision)
        except Exception as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            record_operational_event(
                request_id=plan_id,
                operator_id=operator_id,
                actor="odysseus:extension-installer",
                component="extension-installer",
                event_type="result",
                status="failed",
                error=exc,
                metadata={"operation": f"preview_{operation}"},
            )
            raise

    def preview_lifecycle(
        self,
        operation: str,
        extension_id: str,
        *,
        operator_id: str,
        target_revision: str | None = None,
    ) -> dict[str, Any]:
        if operation not in {"enable", "disable", "rollback", "uninstall"}:
            raise ExtensionLifecycleError("extension_lifecycle_operation_invalid")
        with self._lock:
            state = self._read_state()
            existing = state["extensions"].get(extension_id)
            if not existing and operation != "uninstall":
                raise ExtensionLifecycleError("extension_not_installed")
            if operation == "rollback":
                history = list((existing or {}).get("history") or [])
                target_revision = target_revision or (history[-1] if history else None)
                if (
                    not target_revision
                    or target_revision not in history
                    and target_revision != (existing or {}).get("active_revision")
                ):
                    raise ExtensionLifecycleError(
                        "extension_rollback_revision_unavailable"
                    )
            if existing and existing.get("owner_scope") not in {None, operator_id}:
                raise ExtensionLifecycleError("extension_owner_scope_mismatch")
            manifest = None
            if existing:
                manifest = self._load_manifest(
                    self._revision_path(extension_id, existing["active_revision"])
                )
            plan = {
                "plan_id": str(uuid.uuid4()),
                "call_id": str(uuid.uuid4()),
                "operation": operation,
                "extension_id": extension_id,
                "target_revision": target_revision,
                "operator_id": operator_id,
                "owner_scope": (existing or {}).get("owner_scope") or operator_id,
                "manifest": manifest,
                "available_revisions": list((existing or {}).get("history") or []),
                "status": "pending_approval",
                "created_at": utc_now(),
            }
            decision = self._request_approval(plan, operator_id)
            state["plans"][plan["plan_id"]] = plan
            self._write_state(state)
            return self._public_plan(plan, decision=decision)

    @staticmethod
    def _removal_scope(manifest: Mapping[str, Any]) -> dict[str, Any]:
        declared = manifest.get("removal") or {}
        removable = list(declared.get("remove_paths") or [])
        preserved = list(declared.get("preserve_paths") or [])
        return {
            "remove_paths": removable,
            "preserve_paths": preserved,
            "deleted_paths": [],
            "retained_paths": list(dict.fromkeys([*removable, *preserved])),
            "user_data_default": "preserve",
            "package_recoverable": True,
        }

    @staticmethod
    def _public_plan(
        plan: Mapping[str, Any], *, decision: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        manifest = plan.get("manifest") or {}
        result = {
            key: plan.get(key)
            for key in (
                "plan_id",
                "operation",
                "extension_id",
                "source_url",
                "requested_ref",
                "source_revision",
                "target_revision",
                "status",
            )
            if plan.get(key) is not None
        }
        if manifest:
            result["manifest"] = manifest
            result["requested_permissions"] = manifest.get("permissions") or {}
            result["lifecycle_commands"] = manifest.get("lifecycle") or {}
            result["data_boundaries"] = manifest.get("data_boundaries") or {}
            result["removal"] = ExtensionLifecycleManager._removal_scope(manifest)
            result["rollback"] = {
                **(manifest.get("rollback") or {}),
                "available_revisions": list(plan.get("available_revisions") or []),
            }
        if plan.get("distribution"):
            result["marketplace"] = dict(plan["distribution"])
        catalog = plan.get("resolved_catalog") or {}
        if catalog.get("skills"):
            result["admitted_skills"] = [
                {
                    "id": item["id"],
                    "source_path": item["source_path"],
                    "owner_scope": item["owner_scope"],
                    "platforms": item["platforms"],
                    "requires_toolsets": item["requires_toolsets"],
                }
                for item in catalog["skills"]
            ]
        if decision:
            result["authority_decision"] = dict(decision)
        if plan.get("result"):
            result["result"] = plan["result"]
        return result

    def execute_plan(self, plan_id: str, *, operator_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            plan = state["plans"].get(plan_id)
            if not plan:
                raise ExtensionLifecycleError("extension_plan_not_found")
            if plan.get("operator_id") not in {None, operator_id}:
                raise ExtensionLifecycleError("extension_plan_owner_mismatch")
            if plan.get("status") == "completed":
                return self._public_plan(plan)
            call = self._action_call(plan)
            if not plan.get("authorized"):
                decision = self.authority.decide(
                    call, operator_id=operator_id, session_id="extension-control"
                )
                if decision["decision"] != "allow":
                    raise ExtensionLifecycleError(
                        "extension_approval_required"
                        if decision["decision"] == "approval_required"
                        else "extension_action_denied"
                    )
                plan["authorized"] = True
                plan["authority_ref"] = decision["decision_id"]
            plan["operator_id"] = operator_id
            plan["status"] = "executing"
            state["plans"][plan_id] = plan
            self._write_state(state)

        started_at = utc_now()
        started = time.monotonic()
        record_operational_event(
            request_id=plan_id,
            call_id=plan["call_id"],
            operator_id=operator_id,
            actor=call["actor"],
            component=f"extension:{plan['extension_id']}",
            event_type="started",
            status="running",
            metadata={"operation": plan["operation"]},
        )
        try:
            raw_result = self._execute_operation(plan)
            action_result = build_action_result(
                call,
                {"success": True, **raw_result},
                started_at=started_at,
                finished_at=utc_now(),
                description=f"Extension {plan['operation']} completed",
            )
        except Exception as exc:
            action_result = build_action_result(
                call,
                {"success": False, "error": getattr(exc, "code", type(exc).__name__)},
                started_at=started_at,
                finished_at=utc_now(),
                description=f"Extension {plan['operation']} failed",
            )
            with self._lock:
                state = self._read_state()
                current = state["plans"].get(plan_id, plan)
                current["status"] = "failed"
                current["result"] = action_result
                state["plans"][plan_id] = current
                self._write_state(state)
            record_operational_event(
                request_id=plan_id,
                call_id=plan["call_id"],
                operator_id=operator_id,
                actor=call["actor"],
                component=f"extension:{plan['extension_id']}",
                event_type="result",
                status="failed",
                duration=time.monotonic() - started,
                error=exc,
                evidence_refs=[{"authority_ref": plan.get("authority_ref")}],
                metadata={"operation": plan["operation"]},
            )
            raise

        with self._lock:
            state = self._read_state()
            current = state["plans"].get(plan_id, plan)
            current["status"] = "completed"
            current["result"] = action_result
            state["plans"][plan_id] = current
            self._write_state(state)
        record_operational_event(
            request_id=plan_id,
            call_id=plan["call_id"],
            operator_id=operator_id,
            actor=call["actor"],
            component=f"extension:{plan['extension_id']}",
            event_type="result",
            status="succeeded",
            duration=time.monotonic() - started,
            evidence_refs=[{"authority_ref": plan.get("authority_ref")}],
            metadata={"operation": plan["operation"]},
        )
        return self._public_plan(current)

    def _execute_operation(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        operation = plan["operation"]
        if operation in {"install", "upgrade"}:
            return self._activate_source_plan(plan)
        if operation == "enable":
            return self._enable(plan["extension_id"], plan["owner_scope"])
        if operation == "disable":
            return self._disable(plan["extension_id"], plan["owner_scope"])
        if operation == "rollback":
            return self._rollback(
                plan["extension_id"], plan["target_revision"], plan["owner_scope"]
            )
        return self._uninstall(plan["extension_id"], plan["owner_scope"])

    def _revision_path(self, extension_id: str, revision: str) -> Path:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", extension_id):
            raise ExtensionLifecycleError("extension_id_invalid")
        if not IMMUTABLE_REVISION_PATTERN.fullmatch(revision):
            raise ExtensionLifecycleError("extension_source_revision_invalid")
        return self.root / "installed" / extension_id / "revisions" / revision

    def _activate_and_register(
        self,
        adapter: ExtensionLifecycleAdapter,
        path: Path,
        manifest: Mapping[str, Any],
        catalog: Mapping[str, Any] | None,
        revision: str,
        owner_scope: str,
    ) -> dict[str, Any]:
        owner_activator = getattr(adapter, "activate_for_owner", None)
        if owner_activator:
            owner_activator(path, manifest, catalog, revision, owner_scope=owner_scope)
        else:
            adapter.activate(path, manifest)
        try:
            record = self.registry.register(
                manifest,
                catalog,
                source_revision=revision,
                health_available=True,
            )
        except Exception:
            rollback = getattr(adapter, "rollback_activation", None)
            if rollback:
                rollback(manifest)
            else:
                self._deactivate_adapter(adapter, path, manifest, owner_scope)
            raise
        commit = getattr(adapter, "commit_activation", None)
        if commit:
            commit(manifest)
        return record

    def _activate_source_plan(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        extension_id = plan["extension_id"]
        revision = plan["source_revision"]
        staging = Path(plan["staging_path"]).resolve()
        expected_staging = (self.root / "staging").resolve()
        if not staging.is_relative_to(expected_staging):
            raise ExtensionLifecycleError("extension_staging_path_invalid")
        destination = self._revision_path(extension_id, revision)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            if not staging.is_dir():
                raise ExtensionLifecycleError("extension_staging_missing")
            os.replace(staging, destination)
        elif staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        manifest = self._load_manifest(destination)
        self._manifest_source_matches(manifest, plan["source_url"], revision)
        if plan.get("expected_manifest") is not None:
            self._signed_manifest_matches(manifest, plan["expected_manifest"], revision)
        with self._lock:
            previous = self._read_state()["extensions"].get(extension_id) or {}
        if plan["operation"] == "upgrade" and not previous:
            raise ExtensionLifecycleError("extension_upgrade_previous_revision_missing")
        adapter = self._adapter_for(manifest)
        owner_scope = str(previous.get("owner_scope") or plan["operator_id"])
        if owner_scope != plan["operator_id"]:
            raise ExtensionLifecycleError("extension_owner_scope_mismatch")
        if previous:
            old_path = self._revision_path(extension_id, previous["active_revision"])
            old_manifest = self._load_manifest(old_path)
            old_runtime = (old_manifest.get("runtime") or {}).get("type")
            old_descriptor = (
                (old_manifest.get("capabilities") or {}).get("descriptor") or {}
            ).get("type")
            if old_runtime != (manifest.get("runtime") or {}).get(
                "type"
            ) or old_descriptor != (
                (manifest.get("capabilities") or {}).get("descriptor") or {}
            ).get("type"):
                raise ExtensionLifecycleError("extension_adapter_change_unsupported")
        catalog, healthy = self._validate_adapter(
            adapter, destination, manifest, revision, owner_scope
        )
        if not healthy:
            raise ExtensionLifecycleError("extension_health_unavailable")
        if (
            plan.get("resolved_catalog") is not None
            and catalog != plan["resolved_catalog"]
        ):
            raise ExtensionLifecycleError("extension_resolved_catalog_changed")
        registry_record = self._activate_and_register(
            adapter, destination, manifest, catalog, revision, owner_scope
        )
        with self._lock:
            state = self._read_state()
            previous = state["extensions"].get(extension_id) or previous
            old_revision = previous.get("active_revision")
            history = list(previous.get("history") or [])
            if (
                old_revision
                and old_revision != revision
                and old_revision not in history
            ):
                history.append(old_revision)
            retain = registry_record["manifest"]["rollback"]["retain_revisions"]
            state["extensions"][extension_id] = {
                "source_url": plan["source_url"],
                "active_revision": revision,
                "enabled": True,
                "owner_scope": owner_scope,
                "admitted_skills": [
                    item["id"] for item in registry_record.get("admitted_skills", [])
                ],
                "history": history[-retain:],
                "installed_at": previous.get("installed_at") or utc_now(),
                "updated_at": utc_now(),
            }
            self._write_state(state)
        return {"extension_id": extension_id, "revision": revision, "enabled": True}

    def _active_record(
        self, extension_id: str
    ) -> tuple[dict[str, Any], Path, dict[str, Any]]:
        state = self._read_state()
        record = state["extensions"].get(extension_id)
        if not record:
            raise ExtensionLifecycleError("extension_not_installed")
        path = self._revision_path(extension_id, record["active_revision"])
        manifest = self._load_manifest(path)
        return record, path, manifest

    def _enable(self, extension_id: str, owner_scope: str) -> dict[str, Any]:
        with self._lock:
            record, path, manifest = self._active_record(extension_id)
            if (
                record.get("enabled")
                and extension_id in self.registry.snapshot()["extensions"]
            ):
                return {
                    "extension_id": extension_id,
                    "enabled": True,
                    "idempotent": True,
                }
            adapter = self._adapter_for(manifest)
            catalog, healthy = self._validate_adapter(
                adapter, path, manifest, record["active_revision"], owner_scope
            )
            if not healthy:
                raise ExtensionLifecycleError("extension_health_unavailable")
            self._activate_and_register(
                adapter, path, manifest, catalog, record["active_revision"], owner_scope
            )
            state = self._read_state()
            state["extensions"][extension_id]["enabled"] = True
            state["extensions"][extension_id]["updated_at"] = utc_now()
            self._write_state(state)
            return {"extension_id": extension_id, "enabled": True}

    def _disable(self, extension_id: str, owner_scope: str) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            record = state["extensions"].get(extension_id)
            if not record:
                return {
                    "extension_id": extension_id,
                    "enabled": False,
                    "idempotent": True,
                }
            if not record.get("enabled"):
                self.registry.disable(extension_id)
                return {
                    "extension_id": extension_id,
                    "enabled": False,
                    "idempotent": True,
                }
            path = self._revision_path(extension_id, record["active_revision"])
            manifest = self._load_manifest(path)
            self._deactivate_adapter(
                self._adapter_for(manifest), path, manifest, owner_scope
            )
            self.registry.disable(extension_id)
            record["enabled"] = False
            record["updated_at"] = utc_now()
            self._write_state(state)
            return {"extension_id": extension_id, "enabled": False}

    def _rollback(
        self, extension_id: str, target_revision: str, owner_scope: str
    ) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            current = state["extensions"].get(extension_id)
            if not current or (
                target_revision not in current.get("history", [])
                and target_revision != current.get("active_revision")
            ):
                raise ExtensionLifecycleError("extension_rollback_revision_unavailable")
            if current["active_revision"] == target_revision:
                return {
                    "extension_id": extension_id,
                    "revision": target_revision,
                    "idempotent": True,
                }
            path = self._revision_path(extension_id, target_revision)
            manifest = self._load_manifest(path)
            adapter = self._adapter_for(manifest)
            catalog, healthy = self._validate_adapter(
                adapter, path, manifest, target_revision, owner_scope
            )
            if not healthy:
                raise ExtensionLifecycleError("extension_health_unavailable")
            registry_record = self._activate_and_register(
                adapter, path, manifest, catalog, target_revision, owner_scope
            )
            old_revision = current["active_revision"]
            current["history"] = [
                item for item in current["history"] if item != target_revision
            ]
            current["history"].append(old_revision)
            current["active_revision"] = target_revision
            current["enabled"] = True
            current["admitted_skills"] = [
                item["id"] for item in registry_record.get("admitted_skills", [])
            ]
            current["updated_at"] = utc_now()
            self._write_state(state)
            return {
                "extension_id": extension_id,
                "revision": target_revision,
                "enabled": True,
            }

    def _uninstall(self, extension_id: str, owner_scope: str) -> dict[str, Any]:
        with self._lock:
            state = self._read_state()
            record = state["extensions"].get(extension_id)
            if not record:
                self.registry.unregister(extension_id)
                return {
                    "extension_id": extension_id,
                    "installed": False,
                    "idempotent": True,
                }
            source = self.root / "installed" / extension_id
            path = self._revision_path(extension_id, record["active_revision"])
            manifest = self._load_manifest(path)
            self._deactivate_adapter(
                self._adapter_for(manifest), path, manifest, owner_scope
            )
            self.registry.unregister(extension_id)
            if source.exists():
                destination = self.root / "removed" / extension_id / str(uuid.uuid4())
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
            del state["extensions"][extension_id]
            self._write_state(state)
            return {
                "extension_id": extension_id,
                "installed": False,
                "recoverable": True,
            }

    def snapshot(self) -> dict[str, Any]:
        state = self._read_state()
        return {
            "schema_version": state["schema_version"],
            "extensions": state["extensions"],
            "plans": {
                plan_id: self._public_plan(plan)
                for plan_id, plan in state["plans"].items()
            },
        }
