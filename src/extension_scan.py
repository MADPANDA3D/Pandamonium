"""Bounded source assessment and package preparation (MAD-914/MAD-958).

Pinned source -> static audit -> optional model understanding -> prepared package.
Repository build, install, and lifecycle commands never run during intake.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # Python 3.10 test/dev venv
    import tomli as tomllib  # type: ignore[no-redef]

from core.atomic_io import atomic_write_json
from services.memory.skill_importer import MAX_FILE_BYTES as SKILL_FILE_BYTES
from services.memory.skill_importer import MAX_FILES as SKILL_BUNDLE_FILES
from services.memory.skill_importer import MAX_TOTAL_BYTES as SKILL_BUNDLE_BYTES
from services.memory.skill_importer import _is_text_file
from src.constants import DATA_DIR
from src.extension_capability_inventory import (
    MAX_SCAN_BYTES,
    MAX_SCAN_DURATION_MS,
    MAX_SCAN_FILES,
    SCAN_VERSION,
    redact_scan_evidence,
    scan_artifact_digest,
    scan_stage_progress,
    validate_scan_artifact,
)
from src.extension_installer import ExtensionLifecycleError, GitSourceClient
from src.extension_intake import IntakeError, generate_integration
from src.extension_package import (
    PackageError,
    build_prepared_package,
    package_tree_digest,
)
from src.extension_registry import ExtensionContractError, validate_extension_manifest
from src.extension_skill_adapter import SkillBundleAdapter

SCAN_DIR = Path(DATA_DIR) / "extension_scans"

MAX_FILE_READ_BYTES = 262_144
MAX_TOTAL_READ_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_CAPABILITIES = 64
MAX_ARTIFACT_DEPENDENCIES = 128
MAX_ARTIFACT_FINDINGS = 64
TEXT_SUFFIXES = frozenset({
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".toml",
    ".yaml", ".yml", ".md", ".txt", ".sh", ".bash", ".zsh", ".cfg", ".ini",
    ".html", ".css", ".sql", ".rs", ".go", ".java", ".rb", ".php", ".lua",
    ".env.example", ".dockerfile",
})
TEXT_NAMES = frozenset({"dockerfile", "makefile", "license", "copying", "readme", "skill.md"})

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "high"),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "high"),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "high"),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "high"),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "critical"),
    ("assigned-secret", re.compile(
        r"(?i)\b(?:token|password|passwd|secret|api[_-]?key)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{12,}"
    ), "medium"),
)
DANGEROUS_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("curl-pipe-shell", re.compile(r"(?i)\bcurl\b[^\n|]{0,200}\|\s*(?:ba)?sh\b"), "high"),
    ("wget-pipe-shell", re.compile(r"(?i)\bwget\b[^\n|]{0,200}\|\s*(?:ba)?sh\b"), "high"),
    ("base64-pipe-shell", re.compile(r"(?i)\bbase64\s+-d[^\n|]{0,100}\|\s*(?:ba)?sh\b"), "high"),
    ("chmod-777", re.compile(r"(?i)\bchmod\s+(?:-R\s+)?777\b"), "medium"),
    ("sudo-usage", re.compile(r"(?i)(?:^|\s)sudo\s+\S"), "low"),
)
POSTINSTALL_SCRIPTS = ("preinstall", "install", "postinstall")
SPDX_HINTS = (
    ("MIT", re.compile(r"\bMIT License\b")),
    ("Apache-2.0", re.compile(r"\bApache License,?\s+Version 2\.0\b|Apache-2\.0")),
    ("GPL-3.0", re.compile(r"\bGNU GENERAL PUBLIC LICENSE\b[\s\S]{0,200}Version 3")),
    ("AGPL-3.0", re.compile(r"\bGNU AFFERO GENERAL PUBLIC LICENSE\b[\s\S]{0,200}Version 3")),
    ("BSD-3-Clause", re.compile(r"\bBSD 3-Clause\b|Redistribution and use in source and binary forms")),
    ("ISC", re.compile(r"\bISC License\b")),
    ("MPL-2.0", re.compile(r"\bMozilla Public License\b[\s\S]{0,100}2\.0")),
    ("Unlicense", re.compile(r"\bThis is free and unencumbered software\b")),
)


class ExtensionScanError(RuntimeError):
    """A stable fail-closed scan error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _slug(value: str, *, maximum: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:maximum]
    if not slug or not slug[0].isalpha():
        slug = f"ext-{slug}".rstrip("-")[:maximum]
    return slug


def _tool_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")[:96]
    if not name or not name[0].isalpha():
        name = f"tool_{name}"[:96]
    return name


def _is_text_path(path: Path) -> bool:
    lowered = path.name.lower()
    if lowered in TEXT_NAMES or lowered.startswith(("license", "readme")):
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def _repo_name(source_url: str) -> str:
    path = urlparse(source_url).path.rstrip("/")
    name = Path(path).name
    name = name.removesuffix(".git")
    return name or "extension"


SKILL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,59}$")
SKILL_FIELD_PATTERN = re.compile(r"^(name|description):\s*(.*?)\s*$")


def _read_skill_identity(path: Path) -> dict[str, str] | None:
    """Read a bounded frontmatter identity that the skill adapter will admit."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_READ_BYTES:
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.startswith("---\n"):
        return None
    marker = text.find("\n---", 4)
    if marker == -1:
        return None
    fields: dict[str, str] = {}
    for line in text[4:marker].splitlines():
        match = SKILL_FIELD_PATTERN.match(line)
        if match:
            fields[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    name = fields.get("name") or ""
    description = fields.get("description") or ""
    if not SKILL_NAME_PATTERN.fullmatch(name) or not description.strip():
        return None
    if not text[marker + 4 :].strip():
        return None
    return {"name": name, "description": description}


class ExtensionStaticScanner:
    """One bounded static pass over a pinned checkout."""

    def __init__(
        self,
        *,
        git_client: Any | None = None,
        staging_root: Path | str | None = None,
        data_dir: Path | str | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_files: int = MAX_SCAN_FILES,
        max_bytes: int = MAX_SCAN_BYTES,
        max_duration_ms: int = MAX_SCAN_DURATION_MS,
        model: Callable[[list[dict], Callable[[], None]], str] | None = None,
    ):
        self.git = git_client or GitSourceClient()
        self.staging_root = Path(
            staging_root or (Path(DATA_DIR) / "extensions")
        ).resolve()
        self.data_dir = Path(data_dir or SCAN_DIR)
        self.clock = clock
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.max_duration_ms = max_duration_ms
        self.model = model

    # -- helpers ------------------------------------------------------------

    def _walk(self, root: Path, *, deadline: float, check: Callable[[], None] = lambda: None) -> list[Path]:
        files: list[Path] = []
        total_bytes = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if name != ".git")
            if any((Path(dirpath) / name).is_symlink() for name in dirnames):
                raise ExtensionScanError("extension_package_member_invalid")
            for filename in sorted(filenames):
                check()
                path = Path(dirpath) / filename
                try:
                    stat = path.lstat()
                except OSError:
                    continue
                if path.is_symlink():
                    raise ExtensionScanError("extension_package_member_invalid")
                if not path.is_file():
                    continue
                files.append(path)
                total_bytes += stat.st_size
                if len(files) > self.max_files or total_bytes > self.max_bytes:
                    raise ExtensionScanError("extension_scan_bounds_exceeded")
                if self.clock() > deadline:
                    raise ExtensionScanError("extension_scan_bounds_exceeded")
        return files

    def _read_text(self, path: Path, remaining: list[int]) -> str:
        if remaining[0] <= 0:
            return ""
        try:
            size = path.stat().st_size
        except OSError:
            return ""
        if size <= 0 or size > MAX_FILE_READ_BYTES:
            return ""
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        if len(data) > remaining[0]:
            data = data[: remaining[0]]
        remaining[0] -= len(data)
        return data

    # -- stages -------------------------------------------------------------

    @staticmethod
    def _has_go_binary(relative: Mapping[str, Path]) -> bool:
        if "main.go" in relative:
            return True
        return any(
            key.startswith("cmd/") and key.endswith(".go") for key in relative
        )

    @staticmethod
    def _has_rust_binary(relative: Mapping[str, Path]) -> bool:
        if "src/main.rs" in relative:
            return True
        if any(
            key.startswith("src/bin/") and key.endswith(".rs") for key in relative
        ):
            return True
        cargo = next(
            (path for key, path in relative.items() if key.lower() == "cargo.toml"),
            None,
        )
        if cargo is None:
            return False
        try:
            return "[[bin]]" in cargo.read_text(encoding="utf-8")
        except OSError:
            return False

    @staticmethod
    def _declared_skill_plugin(relative: Mapping[str, Path]) -> bool:
        for descriptor_key in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
            descriptor_path = relative.get(descriptor_key)
            if descriptor_path is None:
                continue
            try:
                descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (
                isinstance(descriptor, Mapping)
                and isinstance(descriptor.get("skills"), str)
                and descriptor["skills"].strip()
            ):
                return True
        return False

    @staticmethod
    def _skill_intent(relative: Mapping[str, Path]) -> bool:
        """Skills classify a repo only when they are its primary surface."""
        if "SKILL.md" in relative:
            return True
        if ExtensionStaticScanner._declared_skill_plugin(relative):
            return True
        return any(
            key.startswith("skills/") and key.lower().endswith("/skill.md")
            for key in relative
        )

    def _classify(self, root: Path, files: list[Path]) -> tuple[str, dict[str, Any] | None]:
        relative = {path.relative_to(root).as_posix(): path for path in files}
        manifest = None
        manifest_text = ""
        manifest_path = relative.get("jarvis-extension.json")
        if manifest_path is not None:
            try:
                manifest_text = manifest_path.read_text(encoding="utf-8")
                candidate = json.loads(manifest_text)
                manifest = validate_extension_manifest(candidate)
            except (OSError, ValueError, ExtensionContractError):
                manifest = None
        if manifest is not None:
            runtime_type = manifest["runtime"]["type"]
            descriptor = manifest["capabilities"]["descriptor"]["type"]
            if runtime_type == "skills" or descriptor == "skill_bundle":
                return "skill_bundle", manifest
            if runtime_type == "mcp":
                return "mcp_server", manifest
            if runtime_type == "openapi":
                return "openapi", manifest
            if runtime_type == "web":
                return "web_app", manifest
            if (root / "package.json").is_file():
                return "node_cli", manifest
            return "python_cli", manifest

        lowered = {key.lower() for key in relative}
        names = {Path(key).name.lower() for key in relative}

        # An explicit plugin descriptor that declares skills is distribution
        # intent and outranks generic language tooling.
        if self._declared_skill_plugin(relative):
            return "skill_bundle", None

        # A repository is primarily what it builds. Toolchain signals outrank
        # incidental skill folders that repos keep for their own dev tooling.
        if "go.mod" in names:
            return ("go_cli" if self._has_go_binary(relative) else "go_module"), None
        if "cargo.toml" in names:
            return ("rust_cli" if self._has_rust_binary(relative) else "rust_lib"), None
        if names & {"mcp.json", ".mcp.json", "mcp-config.json"}:
            return "mcp_server", None
        if names & {"pyproject.toml", "setup.py", "requirements.txt"}:
            return "python_cli", None
        if "package.json" in names:
            return "node_cli", None
        if any(
            key.endswith(("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json"))
            for key in lowered
        ):
            return "openapi", None
        if "index.html" in names:
            return "web_app", None
        if names & {
            "dockerfile",
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        }:
            return "service", None
        if self._skill_intent(relative):
            return "skill_bundle", None
        return "unknown", None

    def _extract(
        self, root: Path, files: list[Path], repo_class: str
    ) -> list[dict[str, Any]]:
        capabilities: list[dict[str, Any]] = []
        seen: set[str] = set()
        relative = {path.relative_to(root).as_posix(): path for path in files}

        def add(name: str, kind: str, descriptor: str, evidence: str) -> None:
            if name in seen:
                return
            if len(capabilities) >= MAX_ARTIFACT_CAPABILITIES:
                return
            seen.add(name)
            capabilities.append({
                "name": name,
                "kind": kind,
                "descriptor": descriptor,
                "evidence_path": evidence,
            })

        if repo_class == "skill_bundle":
            for key in sorted(relative):
                if not key.lower().endswith("skill.md"):
                    continue
                identity = _read_skill_identity(relative[key])
                if identity is not None:
                    add(identity["name"], "skill", "skill_bundle", key)
                    continue
                fallback = Path(key).parent.name or _repo_name(str(root))
                add(_slug(fallback), "skill", "skill_bundle", key)
        elif repo_class == "python_cli":
            pyproject = relative.get("pyproject.toml")
            if pyproject is not None:
                try:
                    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                    scripts = ((data.get("project") or {}).get("scripts") or {})
                    for script in sorted(scripts):
                        add(_tool_name(script), "tool", "inline", "pyproject.toml")
                except (OSError, ValueError, tomllib.TOMLDecodeError):
                    pass
            if not capabilities and "setup.py" in relative:
                add(_tool_name(Path(_repo_name(str(root))).name), "tool", "inline", "setup.py")
        elif repo_class == "node_cli":
            package = relative.get("package.json")
            if package is not None:
                try:
                    data = json.loads(package.read_text(encoding="utf-8"))
                    bins = data.get("bin") or {}
                    if isinstance(bins, str):
                        bins = {data.get("name") or "cli": bins}
                    for name in sorted(bins):
                        add(_tool_name(name), "tool", "inline", "package.json")
                except (OSError, ValueError):
                    pass
        elif repo_class == "mcp_server":
            for candidate in ("mcp.json", ".mcp.json", "mcp-config.json"):
                match = relative.get(candidate) or next(
                    (path for key, path in relative.items() if Path(key).name == candidate), None
                )
                if match is not None:
                    add(_tool_name(f"{_repo_name(str(root))}_mcp"), "endpoint", "mcp", match.relative_to(root).as_posix())
                    break
        elif repo_class == "web_app":
            for key in sorted(relative):
                if Path(key).name.lower() == "index.html":
                    add(_tool_name(f"{_repo_name(str(root))}_surface"), "endpoint", "live_catalog", key)
                    break
        elif repo_class == "openapi":
            for key in sorted(relative):
                if not Path(key).name.lower().endswith(("openapi.json", "swagger.json")):
                    continue
                try:
                    data = json.loads(relative[key].read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for path, methods in sorted((data.get("paths") or {}).items()):
                    if not isinstance(methods, Mapping):
                        continue
                    for method, operation in sorted(methods.items()):
                        if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                            continue
                        operation_id = ""
                        if isinstance(operation, Mapping):
                            operation_id = str(operation.get("operationId") or "")
                        name = _tool_name(operation_id or f"{method}_{path}")
                        add(name, "endpoint", "openapi", key)
                        if len(capabilities) >= MAX_ARTIFACT_CAPABILITIES:
                            break
                break
        return capabilities

    @staticmethod
    def _skill_bundle_assets(skill_dir: Path) -> tuple[int, int] | None:
        """Count importable files for one skill, or None when it cannot install."""
        files = 0
        total_bytes = 0
        for path in sorted(skill_dir.rglob("*")):
            if path.is_symlink():
                return None
            if path.is_dir():
                continue
            if not _is_text_file(path.name):
                return None
            try:
                size = path.stat().st_size
            except OSError:
                return None
            if size > SKILL_FILE_BYTES:
                return None
            files += 1
            total_bytes += size
        return files, total_bytes

    @staticmethod
    def _admitted_skill_name(skill_file: Path) -> str | None:
        """Return the skill name only when the installer would admit the file."""
        try:
            skill, _text = SkillBundleAdapter.validate_skill_document(skill_file)
        except ExtensionLifecycleError:
            return None
        return skill.name

    @staticmethod
    def _agent_skill_layout(
        entrypoint: str, skill_file: Path
    ) -> dict[str, Any] | None:
        name = ExtensionStaticScanner._admitted_skill_name(skill_file)
        if name is None:
            return None
        assets = ExtensionStaticScanner._skill_bundle_assets(skill_file.parent)
        if assets is None:
            return None
        if assets[0] > SKILL_BUNDLE_FILES or assets[1] > SKILL_BUNDLE_BYTES:
            return None
        return {
            "format": "agent_skill",
            "entrypoint": entrypoint,
            "include": [name],
            "excluded": [],
        }

    def _descriptor_skill_layout(
        self, root: Path, descriptor_key: str, descriptor_path: Path
    ) -> dict[str, Any] | None:
        try:
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(descriptor, Mapping):
            return None
        skills_value = descriptor.get("skills")
        if not isinstance(skills_value, str) or not skills_value.strip():
            return None
        try:
            skills_root = (root / skills_value).resolve(strict=True)
        except (OSError, ValueError):
            return None
        if (
            not skills_root.is_relative_to(root)
            or skills_root.is_symlink()
            or not skills_root.is_dir()
        ):
            return None
        include: list[str] = []
        excluded: list[str] = []
        total_files = 0
        total_bytes = 0
        for skill_dir in sorted(skills_root.iterdir()):
            if skill_dir.is_symlink() or not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file() or skill_file.is_symlink():
                continue
            name = self._admitted_skill_name(skill_file)
            assets = self._skill_bundle_assets(skill_dir)
            if name is None or assets is None or name != skill_dir.name:
                excluded.append(skill_dir.name)
                continue
            files_count, bytes_count = assets
            if (
                total_files + files_count > SKILL_BUNDLE_FILES
                or total_bytes + bytes_count > SKILL_BUNDLE_BYTES
            ):
                excluded.append(skill_dir.name)
                continue
            include.append(name)
            total_files += files_count
            total_bytes += bytes_count
        if not include:
            return None
        return {
            "format": "codex_plugin",
            "entrypoint": descriptor_key,
            "include": include,
            "excluded": excluded,
        }

    def _skill_bundle_layout(
        self, root: Path, files: list[Path]
    ) -> dict[str, Any] | None:
        """Pick the installable skill layout the adapter can admit, if any."""
        relative = {path.relative_to(root).as_posix(): path for path in files}
        for descriptor_key in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
            descriptor_path = relative.get(descriptor_key)
            if descriptor_path is None:
                continue
            layout = self._descriptor_skill_layout(root, descriptor_key, descriptor_path)
            if layout is not None:
                return layout
        root_skill = relative.get("SKILL.md")
        if root_skill is not None:
            layout = self._agent_skill_layout("SKILL.md", root_skill)
            if layout is not None:
                return layout
        candidates: list[tuple[str, str]] = []
        for key in sorted(relative):
            if not key.lower().endswith("/skill.md") or not key.startswith("skills/"):
                continue
            parent = Path(key).parent.name
            if not parent:
                continue
            name = self._admitted_skill_name(relative[key])
            assets = self._skill_bundle_assets(relative[key].parent)
            if name is None or assets is None or name != parent:
                continue
            candidates.append((key, name))
        if len(candidates) != 1:
            return None
        entrypoint, name = candidates[0]
        return {
            "format": "agent_skill",
            "entrypoint": entrypoint,
            "include": [name],
            "excluded": [],
        }

    def _dependencies(self, root: Path, files: list[Path]) -> list[dict[str, Any]]:
        dependencies: list[dict[str, Any]] = []
        relative = {path.relative_to(root).as_posix(): path for path in files}

        def add(ecosystem: str, name: str, version: str | None = None) -> None:
            if len(dependencies) >= MAX_ARTIFACT_DEPENDENCIES or not name:
                return
            item: dict[str, Any] = {"ecosystem": ecosystem, "name": str(name)[:200]}
            if version:
                item["version"] = str(version)[:80]
            dependencies.append(item)

        pyproject = relative.get("pyproject.toml")
        if pyproject is not None:
            try:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                project = data.get("project") or {}
                for spec in project.get("dependencies") or []:
                    match = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(.*)", str(spec))
                    if match:
                        add("pypi", match.group(1), match.group(2) or None)
                for group in (project.get("optional-dependencies") or {}).values():
                    for spec in group or []:
                        match = re.match(r"\s*([A-Za-z0-9_.\-]+)\s*(.*)", str(spec))
                        if match:
                            add("pypi", match.group(1), match.group(2) or None)
            except (OSError, ValueError, tomllib.TOMLDecodeError):
                pass
        requirements = relative.get("requirements.txt")
        if requirements is not None:
            try:
                for line in requirements.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith(("#", "-")):
                        continue
                    match = re.match(r"([A-Za-z0-9_.\-]+)\s*(.*)", line)
                    if match:
                        add("pypi", match.group(1), match.group(2) or None)
            except OSError:
                pass
        package = relative.get("package.json")
        if package is not None:
            try:
                data = json.loads(package.read_text(encoding="utf-8"))
                for key, version in sorted((data.get("dependencies") or {}).items()):
                    add("npm", key, str(version))
                for key, version in sorted((data.get("devDependencies") or {}).items()):
                    add("npm", key, str(version))
            except (OSError, ValueError):
                pass
        go_mod = relative.get("go.mod")
        if go_mod is not None:
            try:
                for raw_line in go_mod.read_text(encoding="utf-8").splitlines():
                    line = raw_line.split("//", 1)[0].strip()
                    if not line or line.startswith(("module ", "go ", "toolchain ", "replace ", "exclude ", ")", "require (")):
                        continue
                    if line.startswith("require "):
                        line = line[len("require "):].strip()
                    parts = line.split()
                    if (
                        len(parts) >= 2
                        and parts[1].startswith("v")
                        and re.fullmatch(r"[A-Za-z0-9_.\-/]+", parts[0])
                    ):
                        add("go", parts[0], parts[1])
            except OSError:
                pass
        cargo = relative.get("Cargo.toml")
        if cargo is not None:
            try:
                data = tomllib.loads(cargo.read_text(encoding="utf-8"))
                for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                    for name, spec in sorted((data.get(section) or {}).items()):
                        if isinstance(spec, str):
                            version = spec
                        elif isinstance(spec, Mapping) and spec.get("version"):
                            version = str(spec["version"])
                        else:
                            version = None
                        add("cargo", name, version)
            except (OSError, ValueError, tomllib.TOMLDecodeError):
                pass
        return dependencies

    def _licenses(self, root: Path, files: list[Path]) -> list[str]:
        licenses: list[str] = []
        for path in files:
            name = path.name.lower()
            if not (name.startswith(("license", "copying"))):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:8_000]
            except OSError:
                continue
            for spdx, pattern in SPDX_HINTS:
                if pattern.search(text) and spdx not in licenses:
                    licenses.append(spdx)
                    break
            if len(licenses) >= 32:
                break
        return licenses

    def _audit(
        self,
        root: Path,
        files: list[Path],
        repo_class: str,
        licenses: list[str],
        remaining: list[int],
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        def add(finding_id: str, severity: str, category: str, title: str, evidence: str | None = None) -> None:
            base = re.sub(r"[^a-z0-9_-]+", "-", finding_id.lower()).strip("-")[:50] or "finding"
            unique = base
            counter = 2
            while unique in seen_ids:
                unique = f"{base}-{counter}"
                counter += 1
            seen_ids.add(unique)
            item: dict[str, Any] = {
                "id": unique,
                "severity": severity,
                "category": category,
                "title": title[:200],
            }
            if evidence:
                item["evidence"] = redact_scan_evidence(evidence)
            if len(findings) < MAX_ARTIFACT_FINDINGS:
                findings.append(item)

        for path in files:
            if not _is_text_path(path):
                continue
            relative = path.relative_to(root).as_posix()
            if ".git" in path.parts:
                continue
            try:
                text = self._read_text(path, remaining)
            except OSError:
                continue
            if not text:
                continue
            for pattern_id, pattern, severity in SECRET_PATTERNS:
                match = pattern.search(text)
                if match:
                    add(
                        f"secret-{pattern_id}",
                        severity,
                        "secret",
                        f"Possible {pattern_id} in {relative}",
                        match.group(0)[:200],
                    )
            for pattern_id, pattern, severity in DANGEROUS_PATTERNS:
                match = pattern.search(text)
                if match:
                    add(
                        f"dangerous-{pattern_id}",
                        severity,
                        "dangerous_pattern",
                        f"Possible {pattern_id} in {relative}",
                        match.group(0)[:200],
                    )
        package = root / "package.json"
        if package.is_file():
            try:
                data = json.loads(package.read_text(encoding="utf-8"))
                scripts = data.get("scripts") or {}
                for hook in POSTINSTALL_SCRIPTS:
                    if hook in scripts:
                        add(
                            f"postinstall-{hook}",
                            "medium",
                            "postinstall",
                            f"npm {hook} script runs on install",
                            f"scripts.{hook} = {scripts[hook]}",
                        )
            except (OSError, ValueError):
                pass
        binary_bytes = 0
        for path in files:
            if _is_text_path(path):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size >= 1_048_576:
                binary_bytes += size
        if binary_bytes:
            add(
                "oversized-blob",
                "low",
                "oversized_blob",
                "Large binary assets present",
                f"{binary_bytes} bytes in files >= 1 MiB",
            )
        if not licenses:
            add("license-missing", "low", "license", "No license file detected")
        return findings

    def _draft_manifest(
        self,
        source_url: str,
        repo_class: str,
        capabilities: list[dict[str, Any]],
        licenses: list[str],
        *,
        layout: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        repo = _repo_name(source_url)
        extension_id = _slug(repo, maximum=63)
        runtime: dict[str, Any] | None = None
        descriptors: dict[str, Any] | None = None
        if repo_class == "skill_bundle" and layout:
            runtime = {"type": "skills", "entrypoint": layout["entrypoint"]}
            descriptors = {
                "type": "skill_bundle",
                "format": layout["format"],
                "include": list(layout["include"]),
            }
        if runtime is None or descriptors is None:
            return None
        capabilities_block: dict[str, Any] = {"descriptor": descriptors}
        draft = {
            "protocol_version": "jos-extension.v1",
            "extension_id": extension_id,
            "name": repo.replace("-", " ").replace("_", " ").title() or "Extension",
            "version": "0.0.0-draft",
            "source": {"url": source_url, "revision": "self"},
            "runtime": runtime,
            "capabilities": capabilities_block,
            "permissions": {"default": "read_only", "capabilities": {}},
            "health": {"type": "catalog", "timeout_seconds": 5},
            "lifecycle": {"install": [], "start": [], "stop": [], "remove": []},
            "data_boundaries": {"read": [], "write": [], "network": []},
            "removal": {"remove_paths": [], "preserve_paths": []},
            "rollback": {"strategy": "pinned_revision", "retain_revisions": 1},
        }
        try:
            return validate_extension_manifest(draft)
        except ExtensionContractError:
            return None

    # -- job ----------------------------------------------------------------

    def run(
        self,
        source_url: str,
        requested_ref: str,
        *,
        operator_id: str,
        progress: Callable[[int, str, str], None] | None = None,
        scan_id: str | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict[str, Any]:
        """Run one bounded scan and return the terminal, validated artifact."""
        started = self.clock()
        deadline = started + (self.max_duration_ms / 1000.0)
        scan_id = str(uuid.UUID(scan_id)) if scan_id else str(uuid.uuid4())
        staging = self.staging_root / "staging" / scan_id
        package_dir = self.data_dir / scan_id
        completed = False

        def check() -> None:
            if cancelled():
                raise ExtensionScanError("extension_scan_cancelled")
            if self.clock() > deadline:
                raise ExtensionScanError("extension_scan_bounds_exceeded")

        def report(stage: str, message: str) -> None:
            check()
            if progress is not None:
                progress(scan_stage_progress(stage), stage, message)

        try:
            report("fetch", "Resolving pinned revision")
            source, ref, revision = self.git.resolve_revision(source_url, requested_ref)
            report("fetch", "Checking out pinned revision")
            self.git.checkout(source, ref, revision, staging)

            report("classify", "Classifying repository")
            files = self._walk(staging, deadline=deadline, check=check)
            files_bytes = sum(path.stat().st_size for path in files)
            repo_class, _manifest = self._classify(staging, files)
            if (staging / "jarvis-extension.json").exists() and _manifest is None:
                raise ExtensionScanError("extension_manifest_invalid")

            report("extract", "Extracting entrypoints and capabilities")
            capabilities = self._extract(staging, files, repo_class)
            if _manifest and _manifest["capabilities"]["descriptor"]["type"] == "inline":
                capabilities = [{"name": item["function"]["name"], "kind": "tool", "descriptor": "inline",
                                 "evidence_path": "jarvis-extension.json"}
                                for item in _manifest["capabilities"]["schemas"]]
            layout = (
                self._skill_bundle_layout(staging, files)
                if repo_class == "skill_bundle"
                else None
            )
            remaining = [MAX_TOTAL_READ_BYTES]

            report("audit", "Auditing dependencies, licenses, and findings")
            dependencies = self._dependencies(staging, files)
            licenses = self._licenses(staging, files)
            findings = self._audit(staging, files, repo_class, licenses, remaining)
            if (
                layout
                and layout.get("excluded")
                and len(findings) < MAX_ARTIFACT_FINDINGS
            ):
                excluded = [str(name)[:80] for name in layout["excluded"]][:32]
                findings.append(
                    {
                        "id": "skill-assets-excluded",
                        "severity": "low",
                        "category": "skill_asset",
                        "title": (
                            f"{len(excluded)} skill(s) stay out of the draft: "
                            "frontmatter or assets did not pass import checks"
                        )[:200],
                        "evidence": redact_scan_evidence(", ".join(excluded)[:200]),
                    }
                )

            draft = _manifest or self._draft_manifest(source, repo_class, capabilities, licenses, layout=layout)
            integration = None
            if self.model is not None and draft is None:
                source_remaining = [MAX_TOTAL_READ_BYTES]

                def read_source(path: Path) -> str:
                    if path.is_symlink() or not path.resolve().is_relative_to(staging.resolve()):
                        return ""
                    if (path.name == ".env" or (path.name.startswith(".env.") and path.name not in {".env.example", ".env.sample", ".env.template"})
                            or path.name.lower() in {"credentials.json", "id_rsa", "id_ed25519"}
                            or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}):
                        return ""
                    text = self._read_text(path, source_remaining)
                    if "\x00" in text or "\ufffd" in text:
                        return ""
                    for _name, pattern, _severity in SECRET_PATTERNS:
                        text = pattern.sub("[REDACTED]", text)
                    return text

                report("understand", "Reading purpose and interface definitions")
                try:
                    generated = generate_integration(
                        staging, files, source, revision, lambda messages: self.model(messages, check), read_source, check,
                        lambda message: report("understand", message),
                    )
                except IntakeError as exc:
                    raise ExtensionScanError(str(exc)) from exc
                integration = {key: value for key, value in generated.items() if key not in {"manifest", "files"}}
                repo_class = generated["repo_class"]
                draft = generated["manifest"]
                capabilities = [{"name": item["name"], "kind": item["kind"],
                    "descriptor": (draft or {}).get("capabilities", {}).get("descriptor", {}).get("type", "inline"),
                    "evidence_path": item["evidence"][0]["path"]} for item in integration["interfaces"]]
                for relative, content in generated["files"].items():
                    check()
                    if any(pattern.search(content) for _name, pattern, _severity in SECRET_PATTERNS):
                        raise ExtensionScanError("extension_scan_generated_secret")
                    target = staging / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
                if draft:
                    atomic_write_json(str(staging / ".pandamonium" / "integration.json"), integration, indent=2)

            package = None
            if draft:
                report("package", "Preserving source and integration package; runtime validation still required")
                if any(item["category"] == "secret" for item in findings):
                    raise ExtensionScanError("extension_scan_source_secret")
                # Only this scan's disposable checkout is moved/modified.
                if not _manifest:
                    atomic_write_json(str(staging / "jarvis-extension.json"), draft, indent=2)
                git_dir = staging / ".git"
                if git_dir.is_dir() and not git_dir.is_symlink():
                    shutil.rmtree(git_dir)
                else:
                    git_dir.unlink(missing_ok=True)
                package_dir.mkdir(parents=True, exist_ok=True)
                prepared = package_dir / "prepared"
                shutil.move(str(staging), str(prepared))
                archive = package_dir / "package.tar.gz"
                build_prepared_package(prepared, archive)
                package = {"id": scan_id, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                           "size_bytes": archive.stat().st_size, "tree_digest": package_tree_digest(prepared)}
                check()

            report("report", "Building scan artifact")
            elapsed_ms = max(int((self.clock() - started) * 1000), 0)
            artifact: dict[str, Any] = {
                "scan_version": SCAN_VERSION,
                "source_url": source_url,
                "source_revision": revision,
                "stage": "report",
                "repo_class": repo_class,
                "capabilities": capabilities,
                "dependencies": dependencies,
                "licenses": licenses,
                "findings": findings,
                "draft_manifest": draft,
                "bounds": {
                    "files_scanned": len(files),
                    "bytes_scanned": min(files_bytes, self.max_bytes),
                    "duration_ms": min(elapsed_ms, self.max_duration_ms),
                },
                "executed_repo_commands": [],
            }
            if integration is not None:
                artifact["integration"] = integration
            if package is not None:
                artifact["package"] = package
            artifact["artifact_digest"] = scan_artifact_digest(artifact)
            result = validate_scan_artifact(artifact, require_complete=True)
            completed = True
            return result
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if not completed:
                shutil.rmtree(package_dir / "prepared", ignore_errors=True)
                (package_dir / "package.tar.gz").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# In-process scan jobs (single-worker uvicorn)
# ---------------------------------------------------------------------------

def configured_scan_model(owner: str, loop: asyncio.AbstractEventLoop) -> Callable:
    """Use the owner's existing model routing on the application's event loop."""
    async def request(messages: list[dict]) -> str:
        from src.llm_core import llm_call_async_with_fallback
        from src.task_endpoint import resolve_task_candidates

        candidates = await asyncio.to_thread(resolve_task_candidates, owner=owner)
        if not candidates:
            raise ExtensionScanError("extension_scan_model_unavailable")
        # This is explicit user work. The background quiet gate would wait forever
        # while the user polls this scan's progress or keeps the browser visible.
        return await asyncio.wait_for(llm_call_async_with_fallback(
            candidates, messages=messages, max_tokens=12000, temperature=0.1,
            timeout=90, max_retries=1, workload="foreground",
        ), timeout=90)

    def invoke(messages: list[dict], check: Callable[[], None]) -> str:
        future = asyncio.run_coroutine_threadsafe(request(messages), loop)
        try:
            while True:
                check()
                try:
                    return future.result(timeout=0.2)
                except FutureTimeout:
                    if future.done():
                        raise ExtensionScanError("extension_scan_model_timeout") from None
        except ExtensionScanError:
            raise
        except Exception:  # noqa: BLE001 - provider errors must not expose credentials
            raise ExtensionScanError("extension_scan_model_unavailable") from None
        finally:
            if not future.done():
                future.cancel()
    return invoke


_SCAN_JOBS: dict[str, dict[str, Any]] = {}
_SCAN_LOCK = threading.RLock()
_SCAN_CANCEL: dict[str, threading.Event] = {}


def _job_dir(scan_id: str, data_dir: Path | None = None) -> Path:
    try:
        scan_id = str(uuid.UUID(scan_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ExtensionScanError("extension_scan_id_invalid") from exc
    return Path(data_dir or SCAN_DIR) / scan_id


def _persist_job(job: Mapping[str, Any]) -> None:
    target = _job_dir(str(job["scan_id"]))
    target.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(target / "status.json"), dict(job), indent=2)


def _public_job(job: Mapping[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in job.items()
        if key not in {"artifact", "error_detail"}
    }
    if job.get("artifact") is not None:
        public["artifact"] = job["artifact"]
    if job.get("error_detail"):
        public["error_detail"] = job["error_detail"]
    return public


def start_scan(
    source_url: str,
    requested_ref: str = "HEAD",
    *,
    operator_id: str,
    scanner: ExtensionStaticScanner | None = None,
) -> dict[str, Any]:
    """Start one bounded static scan and return its public job record."""
    if not str(source_url or "").strip():
        raise ExtensionScanError("extension_scan_source_invalid")
    scanner = scanner or ExtensionStaticScanner()
    scan_id = str(uuid.uuid4())
    now = time.time()
    job: dict[str, Any] = {
        "scan_id": scan_id,
        "status": "queued",
        "stage": "fetch",
        "progress": 0,
        "message": "Queued",
        "source_url": source_url,
        "requested_ref": requested_ref,
        "source_revision": None,
        "artifact_digest": None,
        "created_at": now,
        "updated_at": now,
        "operator_id": operator_id,
        "artifact": None,
        "error": None,
    }
    cancel = threading.Event()
    with _SCAN_LOCK:
        if len(_SCAN_CANCEL) >= 2:
            raise ExtensionScanError("extension_scan_busy")
        for old_id in list(_SCAN_JOBS):
            if len(_SCAN_JOBS) < 128:
                break
            if old_id not in _SCAN_CANCEL:
                _SCAN_JOBS.pop(old_id)  # Terminal readback remains available on disk.
        _SCAN_JOBS[scan_id] = job
        _SCAN_CANCEL[scan_id] = cancel
        try:
            _persist_job(job)
        except OSError:
            _SCAN_JOBS.pop(scan_id, None)
            _SCAN_CANCEL.pop(scan_id, None)
            raise ExtensionScanError("extension_scan_storage_unavailable") from None

    def _progress(percent: int, stage: str, message: str) -> None:
        with _SCAN_LOCK:
            if cancel.is_set():
                raise ExtensionScanError("extension_scan_cancelled")
            job.update({
                "status": "running",
                "stage": stage,
                "progress": max(0, min(100, int(percent))),
                "message": message,
                "updated_at": time.time(),
            })
            _persist_job(job)

    def _worker() -> None:
        try:
            artifact = scanner.run(
                source_url, requested_ref, operator_id=operator_id, progress=_progress,
                scan_id=scan_id, cancelled=cancel.is_set,
            )
            with _SCAN_LOCK:
                if cancel.is_set():
                    raise ExtensionScanError("extension_scan_cancelled")
                job.update({
                    "status": "succeeded",
                    "stage": "report",
                    "progress": 100,
                    "message": "Source assessment complete; runtime operations remain unverified",
                    "source_revision": artifact["source_revision"],
                    "artifact_digest": artifact["artifact_digest"],
                    "artifact": artifact,
                    "updated_at": time.time(),
                })
                _persist_job(job)
        except (ExtensionScanError, ExtensionLifecycleError, ExtensionContractError, PackageError) as exc:
            with _SCAN_LOCK:
                job.update({
                    "status": "cancelled" if cancel.is_set() else "failed",
                    "message": str(exc.code),
                    "error": str(exc.code),
                    "updated_at": time.time(),
                })
                _persist_job(job)
        except Exception:  # noqa: BLE001 - job boundary must report, not raise
            with _SCAN_LOCK:
                job.update({
                    "status": "failed",
                    "message": "extension_scan_failed",
                    "error": "extension_scan_failed",
                    "error_detail": "Check source access, package bounds and configured model availability, then retry.",
                    "updated_at": time.time(),
                })
                _persist_job(job)
        finally:
            with _SCAN_LOCK:
                _SCAN_CANCEL.pop(scan_id, None)
            if cancel.is_set():
                target = scanner.data_dir / scan_id
                shutil.rmtree(target / "prepared", ignore_errors=True)
                (target / "package.tar.gz").unlink(missing_ok=True)

    threading.Thread(target=_worker, name=f"extension-scan-{scan_id}", daemon=True).start()
    return _public_job(job)


def get_scan(scan_id: str) -> dict[str, Any] | None:
    """Return a scan job by id, falling back to the persisted record."""
    with _SCAN_LOCK:
        job = _SCAN_JOBS.get(scan_id)
        if job is not None:
            return _public_job(job)
    try:
        status_path = _job_dir(scan_id) / "status.json"
    except ExtensionScanError:
        return None
    if not status_path.is_file():
        return None
    try:
        record = json.loads(status_path.read_text(encoding="utf-8"))
        if record.get("status") in {"queued", "running"}:
            record.update(status="failed", error="extension_scan_interrupted",
                          message="Scan interrupted by restart. Start a new scan.", updated_at=time.time())
            _persist_job(record)
            shutil.rmtree(_job_dir(scan_id) / "prepared", ignore_errors=True)
            (_job_dir(scan_id) / "package.tar.gz").unlink(missing_ok=True)
        return record
    except (OSError, ValueError):
        return None


def cancel_scan(scan_id: str, *, operator_id: str) -> dict[str, Any] | None:
    with _SCAN_LOCK:
        job = get_scan(scan_id)
        if job is None or job.get("operator_id") != operator_id:
            return None
        if job["status"] not in {"queued", "running"}:
            return job
        _SCAN_CANCEL[scan_id].set()
        job = _SCAN_JOBS[scan_id]
        job.update(status="cancelled", message="Scan cancelled; cleaning up temporary package files.",
                   error="extension_scan_cancelled", updated_at=time.time())
        _persist_job(job)
        return _public_job(job)


def scan_package_content(artifact: Mapping[str, Any]) -> bytes | None:
    package = artifact.get("package")
    if not package:
        return None
    path = _job_dir(package["id"]) / "package.tar.gz"
    if (not path.is_file() or path.is_symlink() or path.stat().st_size != package["size_bytes"]):
        raise ExtensionScanError("extension_scan_package_unavailable")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != package["sha256"]:
        raise ExtensionScanError("extension_scan_package_changed")
    return content


def reset_scan_jobs() -> None:
    """Test helper: drop in-memory scan jobs."""
    with _SCAN_LOCK:
        for event in _SCAN_CANCEL.values():
            event.set()
        _SCAN_CANCEL.clear()
        _SCAN_JOBS.clear()
