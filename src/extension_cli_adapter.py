"""Generated CLI packages in a private Linux runtime, using native extension tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import selectors
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from core.atomic_io import atomic_write_json
from src import extension_configuration as configuration
from src import extension_resources as resources
from src.extension_installer import ExtensionLifecycleError, default_extensions_root
from src.extension_package import package_tree_digest

MAX_IO = 64 * 1024
# ponytail: serialize lifecycle/calls; per-package locks if throughput requires it.
_LOCK = threading.RLock()
_SERVICES: dict[str, subprocess.Popen] = {}


def _terminate(process: subprocess.Popen, unit: str) -> None:
    try:
        resources.stop(unit)
    finally:
        # The namespace still dies if the user manager becomes unavailable.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def _stop_service(unit: str) -> None:
    process = _SERVICES.pop(unit, None)
    if process is not None:
        _terminate(process, unit)
    else:
        resources.stop(unit)


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
    if execution.service and not manifest["data_boundaries"]["network"]:
        raise ExtensionLifecycleError("extension_service_network_declaration_required")
    if execution.service and (execution.knowledge or execution.voice_model):
        raise ExtensionLifecycleError("extension_service_recipe_combination_invalid")
    for argv in [
        *execution.install,
        *([execution.service] if execution.service else []),
    ]:
        if (
            not argv
            or len(argv) > 32
            or any(not a or len(a) > 500 or "\x00" in a for a in argv)
        ):
            raise ExtensionLifecycleError("extension_cli_install_argv_invalid")
    for name, tool in tools.items():
        if interfaces[name]["binding"].startswith("knowledge."):
            if execution.knowledge is None or interfaces[name]["binding"] not in {
                "knowledge.search",
                "knowledge.status",
                "knowledge.refresh",
            }:
                raise ExtensionLifecycleError("extension_knowledge_binding_invalid")
            from src.extension_knowledge import schemas

            parameters, output = schemas(interfaces[name]["binding"])
            if (
                tool["parameters"] != parameters
                or interfaces[name]["output_schema"] != output
            ):
                raise ExtensionLifecycleError("extension_knowledge_schema_invalid")
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
    path: Path,
    runtime: Path,
    network: bool,
    validation: bool = False,
    gate: int | None = None,
    config_fd: int | None = None,
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
    if gate is not None:
        command += ["--block-fd", str(gate)]
    if config_fd is not None:
        command += ["--ro-bind-data", str(config_fd), "/run/pandamonium/config.json"]
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
        "--size",
        "268435456",
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
            command += ["--size", "268435456", "--tmpfs", "/runtime/" + folder]
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


def _launch(
    path: Path,
    runtime: Path,
    manifest: dict,
    argv: list[str],
    *,
    validation: bool = False,
    config: dict[str, str] | None = None,
    service: bool = False,
) -> tuple[subprocess.Popen, str]:
    if not runtime.resolve().is_relative_to(resources.storage_root()):
        raise ExtensionLifecycleError(
            "extension_needs_setup:Runtime is outside bounded storage."
        )
    resources.admit()
    python = str(Path(sys.executable).resolve())
    argv = [python if argv[0] == "python" else argv[0], *argv[1:]]
    gate_read, gate_write = os.pipe()
    process = None
    unit = ""
    try:
        with tempfile.TemporaryFile() as config_file:
            config_file.write(json.dumps(config or {}).encode())
            config_file.seek(0)
            command = (
                _sandbox(
                    path,
                    runtime,
                    bool(manifest["data_boundaries"]["network"]),
                    validation,
                    gate_read,
                    config_file.fileno(),
                )
                + argv
            )
            command, unit = resources.scope(command)
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL if service else subprocess.PIPE,
                stdout=subprocess.DEVNULL if service else subprocess.PIPE,
                stderr=subprocess.DEVNULL if service else subprocess.PIPE,
                start_new_session=True,
                pass_fds=(gate_read, config_file.fileno()),
                env={
                    key: value
                    for key, value in os.environ.items()
                    if key in {"PATH", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
                },
            )
            for _ in range(100):
                try:
                    resources.verify_scope(unit)
                    break
                except ExtensionLifecycleError:
                    if process.poll() is not None:
                        raise
                    time.sleep(0.02)
            else:
                raise ExtensionLifecycleError(
                    "extension_needs_setup:Resource controller startup timed out."
                )
            os.write(gate_write, b"1")
        return process, unit
    except BaseException:
        if process:
            _terminate(process, unit)
        raise
    finally:
        os.close(gate_read)
        os.close(gate_write)


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
    config: dict[str, str] | None = None,
) -> bytes:
    payload = (
        json.dumps(arguments, allow_nan=False).encode()
        if arguments is not None
        else b""
    )
    if len(payload) > MAX_IO:
        raise ExtensionLifecycleError("extension_cli_arguments_too_large")
    process, unit = _launch(
        path, runtime, manifest, argv, validation=validation, config=config
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
            detail = bytes(output["stderr"]).decode(errors="replace")[-2000:]
            for value in (config or {}).values():
                if value:
                    detail = detail.replace(value, "[redacted]").replace(
                        json.dumps(value)[1:-1], "[redacted]"
                    )
            raise ExtensionLifecycleError("extension_cli_failed:" + detail)
        secrets = {
            field["key"]
            for field in manifest.get("configuration", [])
            if field.get("secret")
        } | {"PANDAMONIUM_ENDPOINT_TOKEN"}
        for secret_key in secrets:
            secret_value = (config or {}).get(secret_key)
            if secret_value and any(
                encoded in output["stdout"]
                for encoded in (
                    secret_value.encode(),
                    json.dumps(secret_value)[1:-1].encode(),
                )
            ):
                raise ExtensionLifecycleError("extension_cli_output_contains_secret")
        return bytes(output["stdout"])
    finally:
        try:
            _terminate(process, unit)
        finally:
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
    config: dict[str, str] | None = None,
) -> dict:
    if name not in contract["tools"]:
        raise ExtensionLifecycleError("extension_cli_capability_unavailable")
    Draft202012Validator(contract["tools"][name]["parameters"]).validate(arguments)
    binding = contract["interfaces"][name]["binding"]
    if binding.startswith("knowledge."):
        from src.extension_knowledge import execute

        result = execute(
            binding,
            arguments,
            path,
            runtime,
            manifest,
            contract["execution"]["knowledge"],
            cancel,
        )
        Draft202012Validator(contract["interfaces"][name]["output_schema"]).validate(
            result
        )
        if len(json.dumps(result).encode()) > MAX_IO:
            raise ExtensionLifecycleError("extension_cli_output_limit")
        return result
    raw = _run(
        path,
        runtime,
        manifest,
        ["python", "/package/" + manifest["runtime"]["entrypoint"], name],
        arguments=arguments,
        timeout=manifest["health"]["timeout_seconds"],
        cancel=cancel,
        validation=validation,
        config=config,
    )
    result = json.loads(raw)
    Draft202012Validator(contract["interfaces"][name]["output_schema"]).validate(result)
    return result


class GeneratedCliAdapter:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or default_extensions_root()).resolve()
        self._activations: dict[str, tuple[Path, dict, str]] = {}
        self._voice_previous: dict[str, dict] = {}

    supports = staticmethod(is_cli)

    def _service_state(self, runtime: Path) -> Path:
        return (
            self.root
            / "runtime-state"
            / (
                hashlib.sha256("/".join(runtime.parts[-3:]).encode()).hexdigest()
                + ".json"
            )
        )

    def _start_service(
        self,
        path: Path,
        runtime: Path,
        manifest: dict,
        contract: dict,
        config: dict[str, str],
        *,
        validation: bool = False,
    ) -> dict:
        if not contract["execution"]["service"]:
            return {}
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        service_config = {**config, "PANDAMONIUM_PORT": str(port)}
        process, unit = _launch(
            path,
            runtime,
            manifest,
            contract["execution"]["service"],
            config=service_config,
            validation=validation,
            service=True,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise ExtensionLifecycleError(
                        "extension_service_start_failed:Check dependencies and configuration."
                    )
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise ExtensionLifecycleError(
                    "extension_service_offline:Bind to 127.0.0.1 at PANDAMONIUM_PORT."
                )
            _SERVICES[unit] = process
            return {
                "unit": unit,
                "port": port,
                "configuration": configuration.fingerprint(config),
            }
        except BaseException:
            _terminate(process, unit)
            raise

    def _checks(
        self,
        path: Path,
        runtime: Path,
        manifest: dict,
        contract: dict,
        config: dict[str, str],
        *,
        validation: bool,
    ) -> None:
        for check in contract["execution"]["checks"]:
            actual = _invoke(
                path,
                runtime,
                manifest,
                contract,
                check["name"],
                check["arguments"],
                validation=validation,
                config=config,
            )
            if check["expected"] is not None and actual != check["expected"]:
                raise ExtensionLifecycleError(
                    "extension_cli_operation_check_failed:" + check["name"]
                )

    def preview(self, path: Path, manifest: dict) -> dict:
        contract = _read_contract(path, manifest)
        return {
            **contract["execution"],
            "source_exclusions": contract["source_exclusions"],
        }

    def _runtime(self, manifest: dict, digest: str, owner: str) -> Path:
        scope = hashlib.sha256(owner.encode()).hexdigest()[:24]
        installation = hashlib.sha256(str(self.root).encode()).hexdigest()[:24]
        return (
            resources.storage_root()
            / installation
            / scope
            / manifest["extension_id"]
            / digest
        )

    def validate(self, path: Path, manifest: dict, revision: str) -> tuple[None, bool]:
        raise ExtensionLifecycleError("extension_owner_scope_required")

    def validate_for_owner(
        self, path: Path, manifest: dict, revision: str, owner_scope: str
    ) -> tuple[None, bool]:
        with _LOCK:
            digest = package_tree_digest(path)
            contract = _read_contract(path, manifest)
            if contract["execution"]["prerequisites"]:
                raise ExtensionLifecycleError(
                    "extension_needs_setup:Connect an external runtime providing "
                    + ", ".join(contract["execution"]["prerequisites"])
                    + "; the local sandbox does not expose host devices or display."
                )
            config = configuration.values(manifest, owner_scope)
            runtime = self._runtime(manifest, digest, owner_scope)
            runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
            for folder in ("home", "cache", "bin"):
                (runtime / folder).mkdir(exist_ok=True)
            receipt = runtime / "validated.json"
            had_receipt = receipt.exists()
            try:
                previous = json.loads(receipt.read_text()) if had_receipt else {}
                if previous.get("configuration") != configuration.fingerprint(config):
                    # A failed setup must never retain approval for partially changed artifacts.
                    receipt.unlink(missing_ok=True)
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
                            config=config,
                        )
                service = self._start_service(
                    path, runtime, manifest, contract, config, validation=True
                )
                try:
                    if contract["execution"]["knowledge"]:
                        from src.extension_knowledge import index

                        index(
                            path, runtime, manifest, contract["execution"]["knowledge"]
                        )
                    check_config = {
                        **config,
                        **(
                            {"PANDAMONIUM_PORT": str(service["port"])}
                            if service
                            else {}
                        ),
                    }
                    self._checks(
                        path, runtime, manifest, contract, check_config, validation=True
                    )
                finally:
                    if service:
                        _stop_service(service["unit"])
                if package_tree_digest(path) != digest:
                    raise ExtensionLifecycleError("extension_package_content_changed")
                atomic_write_json(
                    str(receipt),
                    {
                        "digest": digest,
                        "source_revision": revision,
                        "configuration": configuration.fingerprint(config),
                    },
                )
                return None, True
            except BaseException:
                if not had_receipt:
                    shutil.rmtree(runtime, ignore_errors=True)
                    if contract["execution"]["knowledge"]:
                        from src.extension_knowledge import index_root

                        shutil.rmtree(index_root(runtime), ignore_errors=True)
                raise

    def activate(self, path: Path, manifest: dict) -> None:
        pass  # Durable installed state and validation receipt are the runtime index.

    def activate_for_owner(
        self,
        path: Path,
        manifest: dict,
        catalog: dict | None,
        revision: str,
        *,
        owner_scope: str,
    ) -> None:
        with _LOCK:
            contract = _read_contract(path, manifest)
            runtime = self._runtime(manifest, package_tree_digest(path), owner_scope)
            config = configuration.values(manifest, owner_scope)
            receipt = json.loads((runtime / "validated.json").read_text())
            if receipt.get("configuration") != configuration.fingerprint(config):
                raise ExtensionLifecycleError(
                    "extension_configuration_changed_preview_again"
                )
            if not contract["execution"]["service"]:
                if contract["execution"]["voice_model"]:
                    self._voice_previous[manifest["extension_id"]] = (
                        configuration.connect_voice(
                            config, contract["execution"]["voice_model"]
                        )
                    )
                return
            self.deactivate_for_owner(path, manifest, owner_scope=owner_scope)
            service = self._start_service(path, runtime, manifest, contract, config)
            try:
                self._checks(
                    path,
                    runtime,
                    manifest,
                    contract,
                    {**config, "PANDAMONIUM_PORT": str(service["port"])},
                    validation=False,
                )
                atomic_write_json(str(self._service_state(runtime)), service)
                self._activations[manifest["extension_id"]] = (
                    path,
                    manifest,
                    owner_scope,
                )
            except BaseException:
                _stop_service(service["unit"])
                raise

    def rollback_activation(self, manifest: dict) -> None:
        previous = self._voice_previous.pop(manifest["extension_id"], None)
        if previous is not None:
            from src.settings import load_settings, save_settings

            save_settings({**load_settings(), **previous})
        activation = self._activations.pop(manifest["extension_id"], None)
        if activation:
            path, active, owner = activation
            self.deactivate_for_owner(path, active, owner_scope=owner)

    def commit_activation(self, manifest: dict) -> None:
        self._voice_previous.pop(manifest["extension_id"], None)
        activation = self._activations.pop(manifest["extension_id"], None)
        if not activation or not (self.root / "lifecycle.json").exists():
            return
        new_path, _, owner = activation
        state = json.loads((self.root / "lifecycle.json").read_text())
        old = state["extensions"].get(manifest["extension_id"])
        if old:
            path = (
                self.root
                / "installed"
                / manifest["extension_id"]
                / "revisions"
                / old["active_revision"]
            )
            if path != new_path:
                self.deactivate_for_owner(
                    path,
                    json.loads((path / "jarvis-extension.json").read_text()),
                    owner_scope=owner,
                )

    def deactivate_for_owner(
        self, path: Path, manifest: dict, *, owner_scope: str
    ) -> None:
        with _LOCK:
            runtime = (
                Path(hashlib.sha256(owner_scope.encode()).hexdigest()[:24])
                / manifest["extension_id"]
                / package_tree_digest(path)
            )
            state = self._service_state(runtime)
            if state.exists():
                _stop_service(json.loads(state.read_text())["unit"])
                state.unlink()

    def deactivate(self, path: Path, manifest: dict) -> None:
        with _LOCK:
            pass  # Each bounded invocation exits; preserve private runtime/user data.

    def _validated_context(self, record: dict, owner: str, revision: str | None = None) -> tuple:
        """Read the same current package/configuration/health evidence used by calls."""
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
        config = configuration.values(manifest, owner)
        if receipt.get("configuration") != configuration.fingerprint(config):
            raise ExtensionLifecycleError(
                "extension_needs_setup:Configuration changed; enable to revalidate."
            )
        contract = _read_contract(path, manifest)
        if contract["execution"]["service"]:
            state_path = self._service_state(runtime)
            if not state_path.exists():
                raise ExtensionLifecycleError(
                    "extension_service_offline:Disable and enable to restart."
                )
            service = json.loads(state_path.read_text())
            resources.verify_scope(service["unit"])
            config = {**config, "PANDAMONIUM_PORT": str(service["port"])}
        if receipt["digest"] != digest:
            raise ExtensionLifecycleError("extension_cli_validation_required")
        return path, runtime, manifest, contract, config

    def readiness(self, record: dict, owner: str) -> dict[str, str]:
        # Do not block the UI behind an install or a running tool.
        if not _LOCK.acquire(blocking=False):
            return {"state": "preparing", "message": "A package operation is running. Refresh after it finishes."}
        try:
            self._validated_context(record, owner)
            return {"state": "ready", "message": "Operation checks passed for this package and configuration. Each call rechecks runtime state."}
        except (ExtensionLifecycleError, OSError, ValueError, KeyError, TypeError):
            return {"state": "needs_setup", "message": "Runtime validation is unavailable or stale. Check setup, then disable and enable to revalidate."}
        finally:
            _LOCK.release()

    def execute(
        self, record: dict, name: str, arguments: dict, owner: str,
        cancel: threading.Event, revision: str | None = None,
    ) -> dict:
        with _LOCK:
            path, runtime, manifest, contract, config = self._validated_context(record, owner, revision)
            if name not in {c["name"] for c in record["effective_capabilities"]}:
                raise ExtensionLifecycleError("extension_cli_validation_required")
            return _invoke(path, runtime, manifest, contract, name, arguments, cancel, config=config)


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
        identity = operator_identity(owner)
        if identity is None:
            raise ExtensionLifecycleError("extension_owner_scope_mismatch")
        result = await asyncio.to_thread(
            GeneratedCliAdapter().execute,
            record,
            name,
            arguments,
            identity,
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
