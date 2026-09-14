"""Managed SSH connections for direct node access (MAD-935).

This module owns validation, key generation/import, host-key pinning, and the
system OpenSSH execution path for operator-configured SSH nodes.

Security contract:

* Private keys are stored encrypted at rest by the ``SshConnection`` model and
  are materialized to the managed directory only inside a short-lived temporary
  directory (mode 0o600) for the duration of a keygen/derive/test command.
* Private key material is never returned in any payload and never logged; the
  public key is not a secret and is shown so it can be installed on the node.
* Host keys fail closed. A connection with no pinned host key refuses to run,
  and a changed host key maps to a clear blocked state instead of trusting the
  new key silently.
* Tests run through the system ``ssh`` client with ``BatchMode=yes`` (no
  password/passphrase prompt) and an explicit managed config, never a shell
  string.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from core.platform_compat import IS_WINDOWS, safe_chmod
from src.constants import SSH_AUDIT_FILE, SSH_CONNECTIONS_DIR

logger = logging.getLogger(__name__)

# OpenSSH on Windows uses NUL instead of /dev/null for the global known-hosts
# sink. Either way the system file is ignored so only the pinned managed host
# key can satisfy strict checking.
_DEV_NULL = "NUL" if IS_WINDOWS else "/dev/null"

LABEL_MAX_CHARS = 80
DEFAULT_SSH_PORT = 22
CONNECT_TIMEOUT_SECONDS = 8
COMMAND_TIMEOUT_SECONDS = 20
SCAN_TIMEOUT_SECONDS = 10

STATE_UNKNOWN = "unknown"
STATE_CONNECTED = "connected"
STATE_AUTH_FAILED = "auth_failed"
STATE_HOST_KEY_UNKNOWN = "host_key_unknown"
STATE_HOST_KEY_CHANGED = "host_key_changed"
STATE_UNREACHABLE = "unreachable"
STATE_UNAVAILABLE = "unavailable"
STATE_FAILED = "failed"

CONNECTED_MESSAGE = "Connected. The connection is authorized, and no prompt was needed."
AUTH_FAILED_MESSAGE = (
    "The node rejected the key. Install this connection's public key on the node, "
    "then test again."
)
HOST_KEY_UNKNOWN_MESSAGE = (
    "The node's host key is not pinned yet. Scan and pin it before testing this connection."
)
HOST_KEY_CHANGED_MESSAGE = (
    "The node's host key changed. This connection stays blocked to prevent "
    "impersonation. Verify the new key with the node's operator, then scan and "
    "pin it again."
)
UNREACHABLE_MESSAGE = (
    "Could not reach the node. Check the address, the port, and that SSH is running."
)
TIMEOUT_MESSAGE = "The node did not answer in time. Check the address and try again."
UNAVAILABLE_MESSAGE = (
    "OpenSSH is not available on this machine, so the connection cannot be tested."
)
FAILED_MESSAGE = (
    "The connection failed. Check the node's SSH service and this connection's "
    "settings, then try again."
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{3,31}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,253}$")
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Preferred host key types, most preferred first. Ed25519 is the modern
# default; anything unknown sorts last rather than being dropped.
_KEY_TYPE_PRIORITY = {
    "ssh-ed25519": 0,
    "sk-ssh-ed25519@openssh.com": 1,
    "ecdsa-sha2-nistp256": 2,
    "rsa-sha2-512": 3,
    "rsa-sha2-256": 4,
    "ssh-rsa": 5,
}

# ── governed agent operations (MAD-936) ──────────────────────────────────
#
# Agent-initiated SSH is bounded to saved connections, never a free-form
# target. list/read are built here from fixed binaries and a quoted path; run
# accepts only a single command whose first token is on the connection's
# allowlist (a conservative read-only default when the operator has not set
# one). Every call is audited with connection, bounds, and redacted target.

AGENT_ACTIONS = ("list", "read", "run")
DEFAULT_ALLOWED_COMMANDS = (
    "cat",
    "date",
    "df",
    "du",
    "free",
    "head",
    "hostname",
    "id",
    "ls",
    "pwd",
    "stat",
    "tail",
    "uname",
    "uptime",
    "wc",
    "whoami",
)
MAX_ALLOWED_COMMANDS = 64
MAX_ALLOWED_COMMAND_CHARS = 120
MAX_REMOTE_PATH_CHARS = 1024
AGENT_COMMAND_TIMEOUT_SECONDS = 15
AGENT_LIST_MAX_BYTES = 64 * 1024
AGENT_READ_MAX_BYTES = 64 * 1024
AGENT_RUN_MAX_BYTES = 32 * 1024
AGENT_MIN_READ_BYTES = 1
_SAFE_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._=/@:+,%-]*$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._=/@:+,%-]+$")


class SshAgentError(RuntimeError):
    """Raised for governed agent SSH failures; callers map this to honest copy."""

    def __init__(self, code: str, message: str, *, reason: str = "", status_code: int = 400):
        super().__init__(message)
        self.code = str(code or "failed")
        self.message = str(message or "")
        self.reason = str(reason or self.code)
        self.status_code = int(status_code)


def _shell_quote(value: str) -> str:
    """POSIX single-quote a path so the remote shell treats it as a literal."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def effective_allowed_commands(connection: Any) -> list[str]:
    """The connection's run allowlist (built-in read-only default when unset)."""
    raw = getattr(connection, "allowed_commands", None)
    if raw:
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            commands = []
            for item in parsed:
                token = " ".join(str(item or "").split())
                if token and token not in commands:
                    commands.append(token)
            if commands:
                return commands
    return list(DEFAULT_ALLOWED_COMMANDS)


def validate_allowed_commands(value: Any) -> str | None:
    """Normalize a policy value (JSON array or comma/newline list) for storage."""
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.startswith("["):
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "Allowed commands must be a JSON array or a comma-separated list.")
        if not isinstance(items, list):
            raise HTTPException(400, "Allowed commands must be a list of commands.")
    else:
        items = re.split(r"[\n,]", raw)
    commands: list[str] = []
    for item in items:
        token = " ".join(str(item or "").split())
        if not token:
            continue
        if len(token) > MAX_ALLOWED_COMMAND_CHARS or not _SAFE_COMMAND_RE.match(token):
            raise HTTPException(
                400,
                "Allowed commands must be simple commands (letters, numbers, and . _ = / @ : + , -% only).",
            )
        if token not in commands:
            commands.append(token)
    if len(commands) > MAX_ALLOWED_COMMANDS:
        raise HTTPException(400, f"Keep the allowlist to {MAX_ALLOWED_COMMANDS} commands or fewer.")
    return json.dumps(commands) if commands else None


def validate_remote_path(value: Any, default: str = "") -> str:
    path = str(value if value is not None else "").strip()
    if not path:
        if default:
            return default
        raise SshAgentError("invalid_path", "Enter the file or folder path on the node.")
    if len(path) > MAX_REMOTE_PATH_CHARS or _CONTROL_RE.search(path):
        raise SshAgentError("invalid_path", "Enter a plain file or folder path on the node.")
    return path


def validate_run_command(command: Any, allowed: list[str]) -> str:
    value = " ".join(str(command or "").split())
    if not value or len(value) > MAX_ALLOWED_COMMAND_CHARS:
        raise SshAgentError("invalid_command", "Enter one simple command from the connection's allowlist.")
    if not _SAFE_COMMAND_RE.match(value):
        raise SshAgentError(
            "invalid_command",
            "Commands with shell operators are not allowed. Use one command from the allowlist.",
        )
    tokens = value.split(" ")
    if any(not _SAFE_TOKEN_RE.match(token) for token in tokens):
        raise SshAgentError("invalid_command", "That command contains unsupported characters.")
    if tokens[0] not in allowed:
        raise SshAgentError(
            "command_not_allowed",
            f"'{tokens[0]}' is not allowed for this connection. Allowed commands: {', '.join(allowed)}.",
        )
    return value


def build_list_command(path: str) -> str:
    return f"ls -la -- {_shell_quote(path)}"


def build_read_command(path: str, max_bytes: int) -> str:
    return f"head -c {int(max_bytes)} -- {_shell_quote(path)}"


def resolve_saved_connection(reference: Any) -> Any:
    """Resolve one saved SshConnection by id or case-insensitive label."""
    value = str(reference or "").strip()
    if not value:
        raise SshAgentError(
            "not_found",
            "Name a saved SSH connection from Settings \u2192 SSH Connections.",
        )
    from core.database import SessionLocal, SshConnection

    with SessionLocal() as session:
        rows = session.query(SshConnection).order_by(SshConnection.created_at.asc()).all()
        exact = [row for row in rows if row.id == value]
        if exact:
            return exact[0]
        matches = [row for row in rows if str(row.label or "").strip().casefold() == value.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SshAgentError(
            "ambiguous",
            f"More than one saved connection is named '{value}'. Use the connection id from Settings \u2192 SSH Connections.",
        )
    raise SshAgentError(
        "not_found",
        f"No saved SSH connection is named '{value}'. Add it in Settings \u2192 SSH Connections.",
    )


def _run_command_bounded(
    argv: list[str],
    timeout: int = AGENT_COMMAND_TIMEOUT_SECONDS,
    max_bytes: int = AGENT_RUN_MAX_BYTES,
):
    """Run one OpenSSH command with a hard timeout and a hard output ceiling.

    stdout is read up to ``max_bytes`` and the child is killed when it exceeds
    that; stderr goes to a temporary file so a chatty child can never deadlock
    the pipes. Return shape matches ``_run_command`` plus ``truncated``.
    """
    timer = None
    proc = None
    timed_out = False
    truncated = False
    raw = b""
    stderr_raw = b""
    try:
        with tempfile.TemporaryFile() as errfile:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=errfile,
            )

            def _kill():
                nonlocal timed_out
                timed_out = True
                try:
                    proc.kill()
                except OSError:
                    pass

            timer = threading.Timer(timeout, _kill)
            timer.daemon = True
            timer.start()
            try:
                raw = proc.stdout.read(max_bytes + 1) if proc.stdout else b""
                if len(raw) > max_bytes:
                    truncated = True
                    raw = raw[:max_bytes]
                    try:
                        proc.kill()
                    except OSError:
                        pass
                if proc.stdout:
                    proc.stdout.close()
                proc.wait()
                errfile.seek(0)
                stderr_raw = errfile.read(8192)
            finally:
                timer.cancel()
    except OSError as exc:
        return SimpleNamespace(returncode=127, stdout="", stderr=str(exc), truncated=False)
    returncode = 124 if timed_out else (proc.returncode if proc is not None else 127)
    return SimpleNamespace(
        returncode=returncode,
        stdout=raw.decode("utf-8", errors="replace"),
        stderr=stderr_raw.decode("utf-8", errors="replace"),
        truncated=truncated,
    )


@contextlib.contextmanager
def managed_agent_material(connection: Any):
    """Materialize the pinned host key and managed key for one agent call."""
    binary = resolve_ssh_binary()
    if not binary:
        raise SshAgentError("unavailable", UNAVAILABLE_MESSAGE, reason="openssh_unavailable")
    if not getattr(connection, "host_key", None):
        raise SshAgentError("host_key_unknown", HOST_KEY_UNKNOWN_MESSAGE, reason="host_key_unknown")
    try:
        workdir = ensure_connection_dir(connection.id)
    except HTTPException as exc:
        raise SshAgentError("storage_unavailable", str(exc.detail)) from exc
    known_hosts_path = workdir / "known_hosts"
    _write_owner_only(known_hosts_path, str(connection.host_key))
    with tempfile.TemporaryDirectory(prefix="agent-", dir=workdir) as tmp:
        key_path = None
        if bool(getattr(connection, "keyless", False)) and getattr(connection, "private_key", None):
            key_path = os.path.join(tmp, "id_ed25519")
            _write_owner_only(key_path, str(connection.private_key))
        config_path = os.path.join(tmp, "config")
        _write_owner_only(
            config_path,
            build_managed_config(
                connection,
                identity_path=key_path,
                known_hosts_path=str(known_hosts_path),
            ),
        )
        yield binary, config_path, str(known_hosts_path)


def _run_remote(
    connection: Any,
    remote_command: str,
    *,
    timeout: int = AGENT_COMMAND_TIMEOUT_SECONDS,
    max_bytes: int = AGENT_RUN_MAX_BYTES,
):
    """Run one bounded remote command through the managed OpenSSH config."""
    with managed_agent_material(connection) as (binary, config_path, known_hosts_path):
        alias = managed_alias(connection.id)
        argv = [
            binary,
            "-F",
            config_path,
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
            "-o",
            f"GlobalKnownHostsFile={_DEV_NULL}",
            alias,
            remote_command,
        ]
        proc = _run_command_bounded(argv, timeout=timeout, max_bytes=max_bytes)
    if proc.truncated and proc.stdout:
        return proc
    if proc.returncode == 0:
        return proc
    if proc.returncode == 124:
        raise SshAgentError(
            "timeout",
            "The node did not finish the operation in time. Try a narrower path or command.",
            reason="timeout",
        )
    if proc.returncode == 127:
        raise SshAgentError("unavailable", FAILED_MESSAGE, reason="openssh_unavailable")
    if proc.returncode == 255:
        classified = classify_ssh_failure(proc.stderr or "")
        raise SshAgentError(classified["state"], classified["message"], reason=classified["reason"])
    safe_stderr = ""
    try:
        from src.authority_protocol import redact_secret_text

        safe_stderr = redact_secret_text(str(proc.stderr or "")[:300]).strip()
    except Exception:
        safe_stderr = ""
    message = f"The command exited with code {proc.returncode} on the node."
    if safe_stderr:
        message = f"{message} {safe_stderr}"
    raise SshAgentError("command_failed", message, reason="command_failed")


def _bounded_text(text: str, limit: int, truncated: bool) -> tuple[str, bool]:
    raw = str(text or "").encode("utf-8")
    hit = bool(truncated) or len(raw) > limit
    if len(raw) > limit:
        raw = raw[:limit]
    output = raw.decode("utf-8", errors="replace")
    if hit:
        output += f"\n... [truncated at {limit} bytes; ask for a narrower path or command]"
    return output, hit


def _clamped_int(value: Any, default: int, *, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _agent_source(connection: Any, action: str, **extra: Any) -> dict[str, Any]:
    source: dict[str, Any] = {
        "kind": "ssh_node",
        "connection_id": connection.id,
        "label": connection.label,
        "action": action,
        "read_only": action in ("list", "read"),
    }
    source.update({key: value for key, value in extra.items() if value is not None})
    return source


def _agent_citation(connection: Any, *, path: str | None = None, command: str | None = None) -> str:
    node = f'ssh node "{connection.label}"'
    if path:
        return f"{node}: {path}"
    if command:
        return f'{node}: "{command}"'
    return node


def _audit_agent_call(
    connection: Any,
    action: str,
    *,
    state: str,
    output_bytes: int,
    truncated: bool,
    actor: str | None,
    path: str | None = None,
    command: str | None = None,
) -> None:
    detail: dict[str, Any] = {
        "source": "agent",
        "action": action,
        "state": state,
        "timeout_seconds": AGENT_COMMAND_TIMEOUT_SECONDS,
        "max_output_bytes": (
            AGENT_LIST_MAX_BYTES
            if action == "list"
            else AGENT_READ_MAX_BYTES
            if action == "read"
            else AGENT_RUN_MAX_BYTES
        ),
        "output_bytes": max(0, int(output_bytes or 0)),
        "truncated": bool(truncated),
    }
    if path:
        detail["path"] = path
    if command:
        detail["command"] = command
    record_ssh_audit(
        getattr(connection, "id", "") or "",
        f"agent-{action}",
        state,
        state if state != "ok" else "",
        actor,
        detail=detail,
    )


def _execute_agent_operation(connection: Any, action: str, args: dict[str, Any]) -> dict[str, Any]:
    if action == "list":
        path = validate_remote_path(args.get("path"), default=".")
        proc = _run_remote(connection, build_list_command(path), max_bytes=AGENT_LIST_MAX_BYTES)
        output, truncated = _bounded_text(proc.stdout, AGENT_LIST_MAX_BYTES, proc.truncated)
        return {
            "ok": True,
            "action": action,
            "output": output,
            "truncated": truncated,
            "source": _agent_source(connection, action, path=path),
            "citation": _agent_citation(connection, path=path),
            "limits": {
                "timeout_seconds": AGENT_COMMAND_TIMEOUT_SECONDS,
                "max_output_bytes": AGENT_LIST_MAX_BYTES,
            },
        }
    if action == "read":
        path = validate_remote_path(args.get("path"))
        max_bytes = _clamped_int(
            args.get("max_bytes"),
            AGENT_READ_MAX_BYTES,
            low=AGENT_MIN_READ_BYTES,
            high=AGENT_READ_MAX_BYTES,
        )
        proc = _run_remote(
            connection,
            build_read_command(path, max_bytes + 1),
            max_bytes=max_bytes + 1,
        )
        output, truncated = _bounded_text(proc.stdout, max_bytes, proc.truncated)
        return {
            "ok": True,
            "action": action,
            "output": output,
            "truncated": truncated,
            "source": _agent_source(connection, action, path=path),
            "citation": _agent_citation(connection, path=path),
            "limits": {
                "timeout_seconds": AGENT_COMMAND_TIMEOUT_SECONDS,
                "max_output_bytes": max_bytes,
            },
        }
    allowed = effective_allowed_commands(connection)
    command = validate_run_command(args.get("command"), allowed)
    proc = _run_remote(connection, command, max_bytes=AGENT_RUN_MAX_BYTES)
    output, truncated = _bounded_text(proc.stdout, AGENT_RUN_MAX_BYTES, proc.truncated)
    return {
        "ok": True,
        "action": action,
        "output": output,
        "truncated": truncated,
        "source": _agent_source(connection, action, command=command),
        "citation": _agent_citation(connection, command=command),
        "limits": {
            "timeout_seconds": AGENT_COMMAND_TIMEOUT_SECONDS,
            "max_output_bytes": AGENT_RUN_MAX_BYTES,
            "allowed_commands": allowed,
        },
    }


def execute_agent_operation(
    connection: Any,
    action: Any,
    arguments: dict[str, Any] | None = None,
    *,
    actor: str | None = None,
) -> dict[str, Any]:
    """Run one governed agent SSH operation and audit the attempt."""
    action_value = str(action or "").strip().lower()
    args = dict(arguments or {})
    if action_value not in AGENT_ACTIONS:
        raise SshAgentError("invalid_action", "The SSH action must be list, read, or run.")
    started = time.monotonic()
    path = None
    command = None
    try:
        if action_value == "read":
            path = str(args.get("path") or "").strip() or None
        elif action_value == "list":
            path = str(args.get("path") or "").strip() or None
        else:
            command = " ".join(str(args.get("command") or "").split()) or None
        result = _execute_agent_operation(connection, action_value, args)
    except SshAgentError as exc:
        _audit_agent_call(
            connection,
            action_value,
            state=exc.reason,
            output_bytes=0,
            truncated=False,
            actor=actor,
            path=path,
            command=command,
        )
        raise
    logger.info(
        "ssh agent operation action=%s state=ok id=%s duration_ms=%s",
        action_value,
        getattr(connection, "id", ""),
        int((time.monotonic() - started) * 1000),
    )
    _audit_agent_call(
        connection,
        action_value,
        state="ok",
        output_bytes=len(result["output"].encode("utf-8")),
        truncated=result["truncated"],
        actor=actor,
        path=result["source"].get("path"),
        command=result["source"].get("command"),
    )
    return result


class SshScanError(RuntimeError):
    """Raised when a host key cannot be scanned; the route maps this to a 400."""


# ── validation ───────────────────────────────────────────────────────────


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def validate_label(value: Any) -> str:
    label = _normalized_text(value)
    if not label:
        raise HTTPException(400, "Connection label is required.")
    if len(label) > LABEL_MAX_CHARS or _CONTROL_RE.search(label):
        raise HTTPException(400, "Connection label must be 80 characters or fewer.")
    return label


def validate_connection_id(value: Any) -> str:
    connection_id = str(value or "").strip()
    if not _ID_RE.match(connection_id):
        raise HTTPException(400, "Invalid SSH connection id.")
    return connection_id


def validate_host(value: Any) -> str:
    host = str(value or "").strip()
    if (
        not host
        or len(host) > 253
        or not _HOST_RE.match(host)
        or _CONTROL_RE.search(host)
        or host.startswith("-")
    ):
        raise HTTPException(
            400,
            "Enter a hostname or IP address using letters, numbers, dots, or dashes only.",
        )
    return host


def validate_user(value: Any) -> str:
    user = str(value or "").strip()
    if not user:
        raise HTTPException(400, "Enter the SSH user for this node.")
    if not _USER_RE.match(user) or _CONTROL_RE.search(user):
        raise HTTPException(400, "The SSH user may use letters, numbers, dots, or dashes only.")
    return user


def validate_port(value: Any) -> int:
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{1,5}", raw):
        raise HTTPException(400, "Enter a port between 1 and 65535.")
    port = int(raw)
    if port < 1 or port > 65535:
        raise HTTPException(400, "Enter a port between 1 and 65535.")
    return port


def new_connection_id() -> str:
    return f"ssh-{uuid.uuid4().hex[:10]}"


# ── managed directory ────────────────────────────────────────────────────


def connection_dir(connection_id: Any) -> Path:
    """Resolve one connection's managed directory with a traversal guard."""
    return Path(SSH_CONNECTIONS_DIR) / validate_connection_id(connection_id)


def ensure_connection_dir(connection_id: Any) -> Path:
    path = connection_dir(connection_id)
    try:
        path.mkdir(parents=True, exist_ok=True)
        safe_chmod(path, 0o700)
    except OSError as exc:
        logger.warning("Could not prepare the managed SSH directory: %s", exc.__class__.__name__)
        raise HTTPException(500, "Could not prepare the connection's managed folder.") from exc
    return path


def remove_connection_dir(connection_id: Any) -> None:
    try:
        shutil.rmtree(connection_dir(connection_id), ignore_errors=True)
    except Exception:
        logger.warning("Could not remove the managed SSH directory for %s", connection_id)


def _write_owner_only(path: Path | str, content: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    safe_chmod(path, 0o600)


# ── system OpenSSH ───────────────────────────────────────────────────────


def _which(name: str) -> str | None:
    return shutil.which(name)


def resolve_ssh_binary() -> str | None:
    return _which("ssh")


def resolve_ssh_keygen_binary() -> str | None:
    return _which("ssh-keygen")


def resolve_ssh_keyscan_binary() -> str | None:
    return _which("ssh-keyscan")


def _run_command(argv: list[str], timeout: int = COMMAND_TIMEOUT_SECONDS):
    """Run one OpenSSH command without a shell, capturing text output."""
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SimpleNamespace(returncode=124, stdout=exc.stdout or "", stderr=exc.stderr or "")
    except OSError as exc:
        return SimpleNamespace(returncode=127, stdout="", stderr=str(exc))


# ── key material ─────────────────────────────────────────────────────────


def generate_keypair(connection_id: str) -> dict[str, str]:
    """Generate an unencrypted ed25519 keypair for one managed connection.

    The pair is generated into a temporary directory and returned as text; the
    caller stores the private key encrypted and nothing persists on disk.
    """
    binary = resolve_ssh_keygen_binary()
    if not binary:
        raise HTTPException(503, "OpenSSH is not available on this machine, so a keypair cannot be generated.")
    workdir = ensure_connection_dir(connection_id)
    try:
        with tempfile.TemporaryDirectory(prefix="keygen-", dir=workdir) as tmp:
            key_path = os.path.join(tmp, "id_ed25519")
            proc = _run_command(
                [
                    binary,
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    f"pandamonium-ssh:{connection_id}",
                    "-f",
                    key_path,
                ],
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
            if proc.returncode != 0:
                logger.warning("ssh-keygen failed with code %s", proc.returncode)
                raise HTTPException(500, "Could not generate the SSH keypair.")
            private_key = Path(key_path).read_text(encoding="utf-8")
            public_key = Path(f"{key_path}.pub").read_text(encoding="utf-8").strip()
    except HTTPException:
        raise
    except OSError as exc:
        logger.warning("SSH keypair generation IO failure: %s", exc.__class__.__name__)
        raise HTTPException(500, "Could not generate the SSH keypair.") from exc
    return {"private_key": private_key, "public_key": public_key}


def derive_public_key(private_key: str) -> str:
    """Derive the public key for an imported OpenSSH private key.

    The private key is written only to a short-lived 0o600 temporary file under
    the managed directory and removed when the call returns. Failures (wrong
    format, passphrase-protected key) fail closed with human copy.
    """
    key_text = str(private_key or "")
    if "PRIVATE KEY" not in key_text:
        raise HTTPException(400, "Paste an OpenSSH private key.")
    binary = resolve_ssh_keygen_binary()
    if not binary:
        raise HTTPException(503, "OpenSSH is not available on this machine, so a key cannot be imported.")
    workdir = Path(SSH_CONNECTIONS_DIR)
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise HTTPException(500, "Could not prepare the connection's managed folder.")
    with tempfile.TemporaryDirectory(prefix="derive-", dir=workdir) as tmp:
        key_path = Path(tmp) / "id_ed25519"
        _write_owner_only(key_path, key_text)
        proc = _run_command([binary, "-y", "-f", str(key_path)], timeout=COMMAND_TIMEOUT_SECONDS)
        public_key = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not public_key:
        raise HTTPException(
            400,
            "That private key could not be read. Paste an unencrypted OpenSSH private key.",
        )
    return public_key[-1]


# ── host keys ────────────────────────────────────────────────────────────


def _fingerprint_for_blob(blob_b64: str) -> str:
    blob = base64.b64decode(blob_b64, validate=True)
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return f"SHA256:{digest}"


def _sort_host_key_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        entries,
        key=lambda entry: _KEY_TYPE_PRIORITY.get(str(entry.get("key_type") or ""), 9),
    )


def scan_host_key(host: str, port: int, timeout: float = SCAN_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """Scan a node's public host key(s) without authenticating.

    Returns one entry per key type with its SHA256 fingerprint and the exact
    known_hosts line. Raises :class:`SshScanError` when nothing is reachable.
    """
    binary = resolve_ssh_keyscan_binary()
    if not binary:
        raise SshScanError("OpenSSH is not available on this machine, so the host key cannot be scanned.")
    try:
        proc = _run_command(
            [binary, "-T", str(int(timeout)), "-p", str(int(port)), str(host)],
            timeout=int(timeout) + 5,
        )
    except Exception:
        raise SshScanError("Could not scan the node's host key.")
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line in (proc.stdout or "").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        parts = value.split()
        if len(parts) < 3:
            continue
        key_type, blob_b64 = parts[1], parts[2]
        try:
            fingerprint = _fingerprint_for_blob(blob_b64)
        except Exception:
            continue
        marker = (key_type, fingerprint)
        if marker in seen:
            continue
        seen.add(marker)
        entries.append(
            {
                "key_type": key_type,
                "fingerprint": fingerprint,
                "known_hosts_line": " ".join(parts[:3]),
            }
        )
    if not entries:
        raise SshScanError("Could not scan the node's host key. Check the address, the port, and that SSH is running.")
    return _sort_host_key_entries(entries)


# ── execution ────────────────────────────────────────────────────────────


def managed_alias(connection_id: str) -> str:
    return f"pandamonium-{validate_connection_id(connection_id)}"


def build_managed_config(
    connection: Any,
    *,
    identity_path: str | None = None,
    known_hosts_path: str | None = None,
) -> str:
    """Build the explicit managed OpenSSH config for one connection."""
    lines = [
        f"Host {managed_alias(connection.id)}",
        f"  HostName {connection.host}",
        f"  User {connection.user}",
        f"  Port {int(connection.port or DEFAULT_SSH_PORT)}",
        "  IdentitiesOnly yes",
        "  BatchMode yes",
        "  PasswordAuthentication no",
        "  KbdInteractiveAuthentication no",
        "  StrictHostKeyChecking yes",
        "  ConnectTimeout " + str(CONNECT_TIMEOUT_SECONDS),
        "  ServerAliveInterval 5",
        "  ServerAliveCountMax 2",
        "  LogLevel ERROR",
    ]
    if known_hosts_path:
        lines.append(f"  UserKnownHostsFile {known_hosts_path}")
        lines.append(f"  GlobalKnownHostsFile {_DEV_NULL}")
    if identity_path:
        lines.append(f"  IdentityFile {identity_path}")
    return "\n".join(lines) + "\n"


def classify_ssh_failure(stderr: str) -> dict[str, str]:
    """Map raw ssh stderr to a redacted fail-closed state with human copy."""
    text = str(stderr or "")
    lower = text.lower()
    if "remote host identification has changed" in lower:
        return {
            "state": STATE_HOST_KEY_CHANGED,
            "reason": "host_key_changed",
            "message": HOST_KEY_CHANGED_MESSAGE,
        }
    if (
        "host key verification failed" in lower
        or "no ed25519 host key is known" in lower
        or "no rsa host key is known" in lower
        or "no ecdsa host key is known" in lower
        or "no matching host key" in lower
    ):
        return {
            "state": STATE_HOST_KEY_UNKNOWN,
            "reason": "host_key_unknown",
            "message": HOST_KEY_UNKNOWN_MESSAGE,
        }
    if "permission denied" in lower or "too many authentication failures" in lower:
        return {
            "state": STATE_AUTH_FAILED,
            "reason": "authentication_failed",
            "message": AUTH_FAILED_MESSAGE,
        }
    unreachable_markers = (
        "connection timed out",
        "operation timed out",
        "connection refused",
        "no route to host",
        "network is unreachable",
        "could not resolve hostname",
        "name or service not known",
        "connection closed by remote host",
        "connection reset by peer",
        "connection closed by",
        "broken pipe",
        "kex_exchange_identification",
    )
    if any(marker in lower for marker in unreachable_markers):
        return {
            "state": STATE_UNREACHABLE,
            "reason": "connection_failed",
            "message": UNREACHABLE_MESSAGE,
        }
    return {"state": STATE_FAILED, "reason": "command_failed", "message": FAILED_MESSAGE}


def run_connection_test(connection: Any) -> dict[str, Any]:
    """Test one saved connection through the system OpenSSH client.

    Fails closed before running when no host key is pinned. The command is
    argv-only (no shell) with BatchMode=yes so a saved connection never
    prompts. Raw stderr is classified and never echoed.
    """
    binary = resolve_ssh_binary()
    if not binary:
        return {
            "ok": False,
            "state": STATE_UNAVAILABLE,
            "reason": "openssh_unavailable",
            "message": UNAVAILABLE_MESSAGE,
        }
    if not getattr(connection, "host_key", None):
        return {
            "ok": False,
            "state": STATE_HOST_KEY_UNKNOWN,
            "reason": "host_key_unknown",
            "message": HOST_KEY_UNKNOWN_MESSAGE,
        }
    try:
        workdir = ensure_connection_dir(connection.id)
    except HTTPException as exc:
        return {
            "ok": False,
            "state": STATE_FAILED,
            "reason": "storage_unavailable",
            "message": str(exc.detail),
        }
    known_hosts_path = workdir / "known_hosts"
    _write_owner_only(known_hosts_path, str(connection.host_key))
    try:
        with tempfile.TemporaryDirectory(prefix="run-", dir=workdir) as tmp:
            key_path = None
            if bool(getattr(connection, "keyless", False)) and getattr(connection, "private_key", None):
                key_path = os.path.join(tmp, "id_ed25519")
                _write_owner_only(key_path, str(connection.private_key))
            config_path = os.path.join(tmp, "config")
            _write_owner_only(
                config_path,
                build_managed_config(
                    connection,
                    identity_path=key_path,
                    known_hosts_path=str(known_hosts_path),
                ),
            )
            alias = managed_alias(connection.id)
            argv = [
                binary,
                "-F",
                config_path,
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "PasswordAuthentication=no",
                "-o",
                f"UserKnownHostsFile={known_hosts_path}",
                "-o",
                f"GlobalKnownHostsFile={_DEV_NULL}",
                alias,
                "true",
            ]
            proc = _run_command(argv, timeout=COMMAND_TIMEOUT_SECONDS)
    except OSError as exc:
        logger.warning("SSH connection test IO failure: %s", exc.__class__.__name__)
        return {
            "ok": False,
            "state": STATE_FAILED,
            "reason": "storage_unavailable",
            "message": FAILED_MESSAGE,
        }
    if proc.returncode == 0:
        return {"ok": True, "state": STATE_CONNECTED, "reason": "", "message": CONNECTED_MESSAGE}
    if proc.returncode == 124:
        return {"ok": False, "state": STATE_UNREACHABLE, "reason": "timeout", "message": TIMEOUT_MESSAGE}
    result = classify_ssh_failure(getattr(proc, "stderr", "") or "")
    result["ok"] = False
    return result


# ── payloads and audit ───────────────────────────────────────────────────


def connection_payload(connection: Any) -> dict[str, Any]:
    """Redacted connection payload. Never includes private key material."""
    checked_at = getattr(connection, "last_checked_at", None)
    return {
        "id": connection.id,
        "label": connection.label,
        "host": connection.host,
        "user": connection.user,
        "port": int(connection.port or DEFAULT_SSH_PORT),
        "keyless": bool(getattr(connection, "keyless", False)),
        "has_private_key": bool(getattr(connection, "private_key", None)),
        "public_key": connection.public_key or "",
        "host_key_pinned": bool(getattr(connection, "host_key", None)),
        "host_key_fingerprint": connection.host_key_fingerprint or None,
        "host_key_type": connection.host_key_type or None,
        "allowed_commands": effective_allowed_commands(connection),
        "allowed_commands_custom": bool(getattr(connection, "allowed_commands", None)),
        "status": {
            "state": connection.last_status or STATE_UNKNOWN,
            "reason": connection.last_status_reason or "",
            "message": connection.last_status_message or "",
            "checked_at": checked_at.replace(tzinfo=timezone.utc).isoformat()
            if isinstance(checked_at, datetime) and checked_at.tzinfo is None
            else (checked_at.isoformat() if isinstance(checked_at, datetime) else None),
        },
    }


def record_ssh_audit(
    connection_id: str,
    action: str,
    state: str,
    reason: str = "",
    actor: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one redacted audit event for a connect/test or agent operation.

    The record intentionally omits host, user, and any key material so an audit
    file leak cannot expose node topology or secrets. Optional ``detail`` is
    flattened to redacted, bounded string/number values for the same reason.
    """
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "connection_id": str(connection_id or ""),
        "action": str(action or ""),
        "state": str(state or ""),
        "reason": str(reason or ""),
        "actor": str(actor or ""),
    }
    if isinstance(detail, dict) and detail:
        safe_detail: dict[str, Any] = {}
        for key, value in list(detail.items())[:24]:
            name = str(key)[:40]
            if isinstance(value, bool) or isinstance(value, (int, float)):
                safe_detail[name] = value
            else:
                text = str(value if value is not None else "")
                try:
                    from src.authority_protocol import redact_secret_text

                    text = redact_secret_text(text)
                except Exception:
                    pass
                safe_detail[name] = text[:500]
        entry["detail"] = safe_detail
    logger.info(
        "ssh connection action=%s state=%s reason=%s id=%s",
        entry["action"],
        entry["state"],
        entry["reason"],
        entry["connection_id"],
    )
    try:
        path = Path(SSH_AUDIT_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not path.exists()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        if new_file:
            safe_chmod(path, 0o600)
    except OSError:
        logger.warning("Could not write the SSH connection audit event")


def store_host_key(connection: Any, entries: list[dict[str, Any]]) -> None:
    """Pin the scanned host key(s) onto a connection row."""
    ordered = _sort_host_key_entries([entry for entry in entries if entry.get("known_hosts_line")])
    if not ordered:
        raise SshScanError("Could not scan the node's host key.")
    connection.host_key = "\n".join(str(entry["known_hosts_line"]) for entry in ordered)
    connection.host_key_fingerprint = str(ordered[0]["fingerprint"])
    connection.host_key_type = str(ordered[0]["key_type"])


def generate_and_store_keypair(SessionLocal, connection_id: str) -> dict[str, Any]:
    """Generate a managed keypair for one row, store it encrypted, return payload."""
    from core.database import SshConnection

    with SessionLocal() as session:
        row = session.query(SshConnection).filter_by(id=validate_connection_id(connection_id)).first()
        if row is None:
            raise HTTPException(404, "SSH connection not found.")
        pair = generate_keypair(row.id)
        row.private_key = pair["private_key"]
        row.public_key = pair["public_key"]
        session.commit()
        session.refresh(row)
        return connection_payload(row)


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Defensive copy used by routes before returning a payload."""
    safe = dict(payload)
    safe.pop("private_key", None)
    return safe
