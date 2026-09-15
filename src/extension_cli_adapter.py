"""Generated CLI packages in a private Linux runtime, using native extension tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import selectors
import shutil
import signal
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from core.atomic_io import atomic_write_json
from src.extension_installer import ExtensionLifecycleError, default_extensions_root
from src.extension_package import package_tree_digest

MAX_IO = 64 * 1024
# ponytail: serialize lifecycle/calls; per-package locks if throughput requires it.
_LOCK = threading.RLock()


def is_cli(manifest: Mapping[str, Any]) -> bool:
    return (
        manifest.get("runtime", {}).get("type") == "service"
        and manifest.get("capabilities", {}).get("descriptor", {}).get("type")
        == "inline"
    )


def cli_revision(extension_id: str) -> str:
    state = json.loads((default_extensions_root() / "lifecycle.json").read_text())
    return state["extensions"][extension_id]["active_revision"]


def validate_cli_execution(manifest: dict, integration: dict) -> dict:
    """Validate data only; never execute a package at preview/intake time."""
    from src.extension_intake import CliExecution, _check_schema

    execution = CliExecution.model_validate(integration.get("execution"))
    tools = {
        s["function"]["name"]: s["function"]
        for s in manifest["capabilities"]["schemas"]
    }
    interfaces = {
        i["name"]: i for i in integration["interfaces"] if i["kind"] == "tool"
    }
    prefix = manifest["extension_id"].replace("-", "_") + "__"
    if (
        not tools
        or set(tools) != set(interfaces)
        or any(not n.startswith(prefix) for n in tools)
    ):
        raise ExtensionLifecycleError("extension_cli_namespaced_schemas_required")
    if any(manifest["lifecycle"].values()):
        raise ExtensionLifecycleError("extension_cli_lifecycle_must_be_empty")
    modes = {
        manifest["permissions"]["default"],
        *manifest["permissions"]["capabilities"].values(),
    }
    if modes - {"external_side_effect", "controlled_administrative", "destructive"}:
        raise ExtensionLifecycleError("extension_cli_effectful_authority_required")
    if {c.name for c in execution.checks} != set(tools):
        raise ExtensionLifecycleError("extension_cli_operation_checks_required")
    for argv in execution.install:
        if (
            not argv
            or len(argv) > 32
            or any(not a or len(a) > 500 or "\x00" in a for a in argv)
        ):
            raise ExtensionLifecycleError("extension_cli_install_argv_invalid")
    for name, tool in tools.items():
        if interfaces[name]["tool_schema"] != {"type": "function", "function": tool}:
            raise ExtensionLifecycleError("extension_cli_schema_mismatch")
        for schema in (tool["parameters"], interfaces[name]["output_schema"]):
            _check_schema(schema)
            Draft202012Validator.check_schema(schema)
    for check in execution.checks:
        Draft202012Validator(tools[check.name]["parameters"]).validate(check.arguments)
        if check.expected is not None:
            Draft202012Validator(interfaces[check.name]["output_schema"]).validate(
                check.expected
            )
    return {
        "execution": execution.model_dump(),
        "interfaces": interfaces,
        "tools": tools,
        "source_exclusions": integration.get("source_exclusions", []),
    }


def _read_contract(path: Path, manifest: dict) -> dict:
    entry = path / manifest["runtime"]["entrypoint"]
    metadata = path / ".pandamonium/integration.json"
    if (
        not entry.is_file()
        or entry.suffix != ".py"
        or not metadata.is_file()
        or metadata.stat().st_size > 256 * 1024
    ):
        raise ExtensionLifecycleError("extension_cli_execution_recipe_required")
    integration = json.loads(metadata.read_text())
    try:
        return validate_cli_execution(manifest, integration)
    except Exception as exc:
        raise ExtensionLifecycleError("extension_cli_execution_recipe_invalid") from exc


def _sandbox(
    path: Path, runtime: Path, network: bool, validation: bool = False
) -> list[str]:
    bwrap, prlimit = shutil.which("bwrap"), shutil.which("prlimit")
    if sys.platform != "linux" or not bwrap or not prlimit:
        raise ExtensionLifecycleError(
            "extension_cli_needs_setup:Linux bubblewrap and prlimit required"
        )
    python = Path(sys.executable).resolve()
    base = Path(sys.base_prefix).resolve()
    command = [
        prlimit,
        "--as=8589934592",
        "--fsize=268435456",
        "--nofile=256",
        "--cpu=300",
        "--",
        bwrap,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
    ]
    if network:
        command += ["--share-net"]
    command += ["--ro-bind", "/usr", "/usr"]
    for name in ("/bin", "/lib", "/lib64"):
        if Path(name).exists():
            command += ["--ro-bind", name, name]
    if not base.is_relative_to(Path("/usr")):
        command += ["--ro-bind", str(base), str(base)]
    if not python.is_relative_to(base) and not python.is_relative_to(Path("/usr")):
        raise ExtensionLifecycleError("extension_cli_python_runtime_unsupported")
    for name in ("/etc/resolv.conf", "/etc/ssl/certs", "/etc/hosts"):
        if Path(name).exists():
            command += ["--ro-bind", name, name]
    cafile = ssl.get_default_verify_paths().cafile
    if cafile:
        command += ["--ro-bind", str(Path(cafile).resolve()), "/etc/ssl/cert.pem"]
    command += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--ro-bind",
        str(path),
        "/package",
        "--bind",
        str(runtime),
        "/runtime",
    ]
    for folder in ("home", "artifacts"):
        if validation:
            command += ["--tmpfs", "/runtime/" + folder]
        else:
            data = runtime.parent / "data" / folder
            data.mkdir(parents=True, exist_ok=True, mode=0o700)
            command += ["--bind", str(data), "/runtime/" + folder]
    command += ["--clearenv"]
    for key, value in {
        "PATH": "/runtime/bin:/usr/bin:/bin",
        "HOME": "/runtime/home",
        "XDG_CACHE_HOME": "/runtime/cache",
        "XDG_CONFIG_HOME": "/runtime/home/.config",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "LANG": "C.UTF-8",
    }.items():
        command += ["--setenv", key, value]
    command += ["--chdir", "/package", "--"]
    return command


def _run(
    path: Path,
    runtime: Path,
    manifest: dict,
    argv: list[str],
    *,
    arguments: dict | None = None,
    timeout: int = 30,
    cancel: threading.Event | None = None,
    validation: bool = False,
) -> bytes:
    python = str(Path(sys.executable).resolve())
    argv = [python if argv[0] == "python" else argv[0], *argv[1:]]
    command = (
        _sandbox(
            path, runtime, bool(manifest["data_boundaries"]["network"]), validation
        )
        + argv
    )
    payload = (
        json.dumps(arguments, allow_nan=False).encode()
        if arguments is not None
        else b""
    )
    if len(payload) > MAX_IO:
        raise ExtensionLifecycleError("extension_cli_arguments_too_large")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    output = {"stdout": bytearray(), "stderr": bytearray()}
    started = time.monotonic()
    try:
        with selectors.DefaultSelector() as selector:
            for name in output:
                selector.register(getattr(process, name), selectors.EVENT_READ, name)
            assert process.stdin is not None
            if payload:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            while selector.get_map():
                if cancel is not None and cancel.is_set():
                    raise ExtensionLifecycleError("extension_cli_cancelled")
                if time.monotonic() - started > timeout:
                    raise ExtensionLifecycleError("extension_cli_timeout")
                for key, _ in selector.select(0.1):
                    if key.data == "stdin":
                        try:
                            payload = payload[os.write(key.fd, payload[:4096]) :]
                        except BrokenPipeError:
                            payload = b""
                        if not payload:
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                        continue
                    data = os.read(key.fd, 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    output[key.data].extend(data)
                    if sum(map(len, output.values())) > MAX_IO:
                        raise ExtensionLifecycleError("extension_cli_output_limit")
        if process.wait(timeout=1) != 0:
            # The private process cannot access host secrets; retain bounded diagnostics.
            detail = bytes(output["stderr"]).decode(errors="replace")[-2000:]
            raise ExtensionLifecycleError("extension_cli_failed:" + detail)
        return bytes(output["stdout"])
    finally:
        # Kill the namespace supervisor; --die-with-parent removes its children too.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def _invoke(
    path: Path,
    runtime: Path,
    manifest: dict,
    contract: dict,
    name: str,
    arguments: dict,
    cancel: threading.Event | None = None,
    validation: bool = False,
) -> dict:
    if name not in contract["tools"]:
        raise ExtensionLifecycleError("extension_cli_capability_unavailable")
    Draft202012Validator(contract["tools"][name]["parameters"]).validate(arguments)
    raw = _run(
        path,
        runtime,
        manifest,
        ["python", "/package/" + manifest["runtime"]["entrypoint"], name],
        arguments=arguments,
        timeout=manifest["health"]["timeout_seconds"],
        cancel=cancel,
        validation=validation,
    )
    result = json.loads(raw)
    Draft202012Validator(contract["interfaces"][name]["output_schema"]).validate(result)
    return result


class GeneratedCliAdapter:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or default_extensions_root()).resolve()

    supports = staticmethod(is_cli)

    def preview(self, path: Path, manifest: dict) -> dict:
        contract = _read_contract(path, manifest)
        return {
            **contract["execution"],
            "source_exclusions": contract["source_exclusions"],
        }

    def _runtime(self, manifest: dict, digest: str, owner: str) -> Path:
        scope = hashlib.sha256(owner.encode()).hexdigest()[:24]
        return self.root / "runtimes" / scope / manifest["extension_id"] / digest

    def validate(self, path: Path, manifest: dict, revision: str) -> tuple[None, bool]:
        raise ExtensionLifecycleError("extension_owner_scope_required")

    def validate_for_owner(
        self, path: Path, manifest: dict, revision: str, owner_scope: str
    ) -> tuple[None, bool]:
        with _LOCK:
            digest = package_tree_digest(path)
            contract = _read_contract(path, manifest)
            required = [
                c["key"] for c in manifest.get("configuration", []) if c.get("required")
            ]
            if required:
                raise ExtensionLifecycleError(
                    "extension_cli_needs_setup:" + ",".join(required)
                )
            runtime = self._runtime(manifest, digest, owner_scope)
            runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
            for folder in ("home", "cache", "bin"):
                (runtime / folder).mkdir(exist_ok=True)
            receipt = runtime / "validated.json"
            if not receipt.exists():
                started = time.monotonic()
                for argv in contract["execution"]["install"]:
                    remaining = max(1, int(300 - (time.monotonic() - started)))
                    _run(
                        path,
                        runtime,
                        manifest,
                        argv,
                        timeout=remaining,
                        validation=True,
                    )
            for check in contract["execution"]["checks"]:
                actual = _invoke(
                    path,
                    runtime,
                    manifest,
                    contract,
                    check["name"],
                    check["arguments"],
                    validation=True,
                )
                if check["expected"] is not None and actual != check["expected"]:
                    raise ExtensionLifecycleError(
                        "extension_cli_operation_check_failed:" + check["name"]
                    )
            if package_tree_digest(path) != digest:
                raise ExtensionLifecycleError("extension_package_content_changed")
            atomic_write_json(receipt, {"digest": digest, "source_revision": revision})
            return None, True

    def activate(self, path: Path, manifest: dict) -> None:
        pass  # Durable installed state and validation receipt are the runtime index.

    def deactivate(self, path: Path, manifest: dict) -> None:
        with _LOCK:
            pass  # Each bounded invocation exits; preserve private runtime/user data.

    def execute(
        self,
        record: dict,
        name: str,
        arguments: dict,
        owner: str,
        cancel: threading.Event,
        revision: str | None = None,
    ) -> dict:
        with _LOCK:
            manifest = record["manifest"]
            state = json.loads((self.root / "lifecycle.json").read_text())
            installed = state["extensions"].get(manifest["extension_id"])
            if not installed or not installed["enabled"] or not record.get("enabled"):
                raise ExtensionLifecycleError("extension_disabled")
            if installed["owner_scope"] != owner:
                raise ExtensionLifecycleError("extension_owner_scope_mismatch")
            if revision is not None and revision != installed["active_revision"]:
                raise ExtensionLifecycleError(
                    "extension_cli_revision_changed_remount_required"
                )
            revision = installed["active_revision"]
            path = (
                self.root
                / "installed"
                / manifest["extension_id"]
                / "revisions"
                / revision
            )
            if json.loads((path / "jarvis-extension.json").read_text()) != manifest:
                from src.extension_registry import validate_extension_manifest

                if (
                    validate_extension_manifest(
                        json.loads((path / "jarvis-extension.json").read_text())
                    )
                    != manifest
                ):
                    raise ExtensionLifecycleError("extension_cli_manifest_changed")
            digest = package_tree_digest(path)
            runtime = self._runtime(manifest, digest, owner)
            receipt = json.loads((runtime / "validated.json").read_text())
            if receipt["digest"] != digest or name not in {
                c["name"] for c in record["effective_capabilities"]
            }:
                raise ExtensionLifecycleError("extension_cli_validation_required")
            return _invoke(
                path,
                runtime,
                manifest,
                _read_contract(path, manifest),
                name,
                arguments,
                cancel,
            )


async def execute_cli_tool(
    record: dict,
    name: str,
    arguments: dict,
    owner: str | None,
    revision: str | None = None,
) -> dict:
    from src.authority_protocol import operator_identity

    cancel = threading.Event()
    try:
        result = await asyncio.to_thread(
            GeneratedCliAdapter().execute,
            record,
            name,
            arguments,
            operator_identity(owner),
            cancel,
            revision,
        )
        return {
            "output": json.dumps(result, ensure_ascii=False),
            "result": result,
            "exit_code": 0,
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - tool boundary returns bounded runtime failures
        return {"error": str(exc)[:2000], "exit_code": 1}
    finally:
        cancel.set()
