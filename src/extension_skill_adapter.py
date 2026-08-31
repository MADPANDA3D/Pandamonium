"""Native SkillsManager adapter for reviewed JOS extension skill bundles."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

from services.memory.skill_format import Skill, parse_frontmatter, slugify
from services.memory.skill_importer import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    _is_text_file,
)
from services.memory.skills import SkillsManager
from src.extension_installer import ExtensionLifecycleError
from src.extension_registry import MANIFEST_VERSION, SKILL_ID_PATTERN


_FRONTMATTER_FIELDS = frozenset({
    "name", "description", "version", "tags", "platforms",
    "requires_toolsets", "fallback_for_toolsets",
})
_LIST_FIELDS = frozenset({
    "tags", "platforms", "requires_toolsets", "fallback_for_toolsets",
})
_FRONTMATTER_KEY = re.compile(r"^([a-z_][a-z0-9_]*):", re.IGNORECASE)
_FRONTMATTER_LIST_ITEM = re.compile(r"^\s+-\s+.+$")


class SkillBundleAdapter:
    """Admit explicit skill IDs from one immutable checkout into native storage."""

    def __init__(self, skills_manager: SkillsManager):
        self.skills = skills_manager
        self._lock = threading.RLock()
        self._transactions: dict[str, tuple[Path, Path, Path | None]] = {}

    def supports(self, manifest: Mapping[str, Any]) -> bool:
        lifecycle = manifest.get("lifecycle") or {}
        descriptor = ((manifest.get("capabilities") or {}).get("descriptor") or {})
        return (
            (manifest.get("runtime") or {}).get("type") == "skills"
            and descriptor.get("type") == "skill_bundle"
            and all(not lifecycle.get(name) for name in ("install", "start", "stop", "remove"))
        )

    @staticmethod
    def _checkout_path(root: Path, relative: str, *, directory: bool = False) -> Path:
        root = root.resolve()
        candidate = root / relative
        try:
            relative_parts = candidate.relative_to(root).parts
            current = root
            for part in relative_parts:
                current = current / part
                if current.is_symlink():
                    raise ExtensionLifecycleError("extension_skill_path_unsafe")
            resolved = candidate.resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise ExtensionLifecycleError("extension_skill_path_unavailable") from exc
        if not resolved.is_relative_to(root):
            raise ExtensionLifecycleError("extension_skill_path_unsafe")
        if directory and not resolved.is_dir():
            raise ExtensionLifecycleError("extension_skill_path_unavailable")
        if not directory and not resolved.is_file():
            raise ExtensionLifecycleError("extension_skill_path_unavailable")
        return resolved

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES or path.is_symlink() or not path.is_file():
                raise ExtensionLifecycleError("extension_skill_asset_unsafe")
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ExtensionLifecycleError("extension_skill_asset_not_text") from exc
        except OSError as exc:
            raise ExtensionLifecycleError("extension_skill_asset_unreadable") from exc

    @classmethod
    def _strict_skill(cls, skill_file: Path) -> tuple[Skill, str]:
        text = cls._read_text(skill_file)
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ExtensionLifecycleError("extension_skill_frontmatter_required")
        frontmatter_text = text[4:text.find("\n---\n", 4)]
        keys = []
        pending_list = None
        for line in frontmatter_text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = _FRONTMATTER_KEY.match(line)
            if match:
                keys.append(match.group(1))
                pending_list = match.group(1) if not line.partition(":")[2].strip() else None
            elif not pending_list or not _FRONTMATTER_LIST_ITEM.match(line):
                raise ExtensionLifecycleError("extension_skill_frontmatter_malformed")
        metadata, body = parse_frontmatter(text)
        if len(keys) != len(set(keys)) or set(metadata) != set(keys):
            raise ExtensionLifecycleError("extension_skill_frontmatter_malformed")
        if set(metadata) - _FRONTMATTER_FIELDS:
            raise ExtensionLifecycleError("extension_skill_frontmatter_unsupported")
        name = metadata.get("name")
        description = metadata.get("description")
        if (
            not isinstance(name, str)
            or not SKILL_ID_PATTERN.fullmatch(name)
            or slugify(name) != name
            or not isinstance(description, str)
            or not description.strip()
            or len(description) > 1_000
            or any(ord(char) < 32 and char not in "\t\n" for char in description)
            or not body.strip()
        ):
            raise ExtensionLifecycleError("extension_skill_frontmatter_invalid")
        version = metadata.get("version", "1.0.0")
        if not isinstance(version, str) or not version.strip() or len(version) > 80:
            raise ExtensionLifecycleError("extension_skill_frontmatter_invalid")
        for field in _LIST_FIELDS:
            values = metadata.get(field, [])
            if (
                not isinstance(values, list)
                or len(values) > 32
                or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in values)
                or len(values) != len(set(values))
            ):
                raise ExtensionLifecycleError("extension_skill_frontmatter_invalid")
        skill = Skill.from_markdown(text, path=str(skill_file))
        if skill.name != name:
            raise ExtensionLifecycleError("extension_skill_id_invalid")
        return skill, text

    @classmethod
    def _skill_files(cls, skill_root: Path, checkout: Path) -> dict[str, str]:
        files: dict[str, str] = {}
        total = 0
        for path in sorted(skill_root.rglob("*")):
            relative_checkout = path.relative_to(checkout)
            if ".git" in relative_checkout.parts:
                continue
            if path.is_symlink():
                raise ExtensionLifecycleError("extension_skill_asset_unsafe")
            if path.is_dir():
                continue
            if skill_root == checkout and relative_checkout.as_posix() == "jarvis-extension.json":
                continue
            relative = path.relative_to(skill_root).as_posix()
            if not _is_text_file(path.name):
                raise ExtensionLifecycleError("extension_skill_asset_not_text")
            text = cls._read_text(path)
            total += len(text.encode("utf-8"))
            files[relative] = text
            if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ExtensionLifecycleError("extension_skill_bundle_too_large")
        if "SKILL.md" not in files:
            raise ExtensionLifecycleError("extension_skill_entrypoint_unavailable")
        return files

    def _resolve_bundle(
        self,
        install_path: Path,
        manifest: Mapping[str, Any],
        source_revision: str,
        owner_scope: str,
    ) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
        if not owner_scope or len(owner_scope) > 200:
            raise ExtensionLifecycleError("extension_skill_owner_scope_invalid")
        descriptor = manifest["capabilities"]["descriptor"]
        include = list(descriptor["include"])
        entrypoint = str(manifest["runtime"]["entrypoint"])
        checkout = install_path.resolve()
        candidates: dict[str, Path] = {}
        if descriptor["format"] == "agent_skill":
            skill_file = self._checkout_path(checkout, entrypoint)
            if skill_file.name != "SKILL.md":
                raise ExtensionLifecycleError("extension_skill_entrypoint_invalid")
            skill, _text = self._strict_skill(skill_file)
            candidates[skill.name] = skill_file.parent
            if include != [skill.name]:
                raise ExtensionLifecycleError("extension_skill_catalog_mismatch")
        else:
            plugin_file = self._checkout_path(checkout, entrypoint)
            try:
                plugin = json.loads(self._read_text(plugin_file))
            except (TypeError, ValueError) as exc:
                raise ExtensionLifecycleError("extension_plugin_descriptor_invalid") from exc
            if (
                not isinstance(plugin, Mapping)
                or not isinstance(plugin.get("skills"), str)
                or not plugin["skills"].strip()
                or len(plugin["skills"]) > 500
            ):
                raise ExtensionLifecycleError("extension_plugin_descriptor_invalid")
            skills_root = self._checkout_path(checkout, plugin["skills"], directory=True)
            for skill_dir in sorted(skills_root.iterdir()):
                if skill_dir.is_symlink():
                    raise ExtensionLifecycleError("extension_skill_path_unsafe")
                if not skill_dir.is_dir() or skill_dir.name not in include:
                    continue
                skill_file = self._checkout_path(checkout, (skill_dir / "SKILL.md").relative_to(checkout).as_posix())
                skill, _text = self._strict_skill(skill_file)
                if skill.name != skill_dir.name or skill.name in candidates:
                    raise ExtensionLifecycleError("extension_skill_id_invalid")
                candidates[skill.name] = skill_dir
            if set(candidates) != set(include):
                raise ExtensionLifecycleError("extension_skill_catalog_mismatch")

        category = f"extension-{manifest['extension_id']}"
        source_prefix = f"extension:{manifest['extension_id']}@"
        for existing in self.skills.load(owner_scope):
            if existing.get("name") not in include:
                continue
            if existing.get("category") != category or not str(existing.get("source") or "").startswith(source_prefix):
                raise ExtensionLifecycleError("extension_skill_name_collision")
        category_path = Path(self.skills.skills_root) / category
        if category_path.exists():
            managed = [item for item in self.skills.load_all() if item.get("category") == category]
            entries = list(category_path.iterdir()) if category_path.is_dir() else []
            if (
                not managed
                or category_path.is_symlink()
                or {entry.name for entry in entries} != {item["name"] for item in managed}
                or any(not entry.is_dir() or entry.is_symlink() for entry in entries)
                or any(
                    item.get("owner") != owner_scope
                    or not str(item.get("source") or "").startswith(source_prefix)
                    for item in managed
                )
            ):
                raise ExtensionLifecycleError("extension_skill_owner_collision")

        bundles: dict[str, dict[str, str]] = {}
        catalog_skills = []
        total_files = 0
        total_bytes = 0
        for skill_id in include:
            root = candidates[skill_id]
            skill, _text = self._strict_skill(root / "SKILL.md")
            files = self._skill_files(root, checkout)
            total_files += len(files)
            total_bytes += sum(len(text.encode("utf-8")) for text in files.values())
            if total_files > MAX_FILES or total_bytes > MAX_TOTAL_BYTES:
                raise ExtensionLifecycleError("extension_skill_bundle_too_large")
            bundles[skill_id] = files
            catalog_skills.append({
                "id": skill_id,
                "source_path": root.relative_to(checkout).as_posix() or ".",
                "owner_scope": owner_scope,
                "platforms": list(skill.platforms),
                "requires_toolsets": list(skill.requires_toolsets),
            })
        return {
            "protocol_version": MANIFEST_VERSION,
            "extension_id": manifest["extension_id"],
            "version": manifest["version"],
            "source_revision": source_revision,
            "skills": catalog_skills,
        }, bundles

    def validate_for_owner(
        self,
        install_path: Path,
        manifest: Mapping[str, Any],
        source_revision: str,
        *,
        owner_scope: str,
    ) -> tuple[Mapping[str, Any], bool]:
        catalog, _bundles = self._resolve_bundle(
            install_path, manifest, source_revision, owner_scope
        )
        return catalog, True

    def activate_for_owner(
        self,
        install_path: Path,
        manifest: Mapping[str, Any],
        resolved_catalog: Mapping[str, Any] | None,
        source_revision: str,
        *,
        owner_scope: str,
    ) -> None:
        catalog, bundles = self._resolve_bundle(
            install_path, manifest, source_revision, owner_scope
        )
        if catalog != resolved_catalog:
            raise ExtensionLifecycleError("extension_skill_catalog_changed")
        extension_id = str(manifest["extension_id"])
        category = f"extension-{extension_id}"
        source = f"extension:{extension_id}@{source_revision}"
        skills_root = Path(self.skills.skills_root)
        transaction = Path(tempfile.mkdtemp(prefix=f".jos-{extension_id}-", dir=skills_root.parent))
        staging_manager = SkillsManager(str(transaction / "staged"))
        try:
            for skill_id, files in bundles.items():
                imported = staging_manager.import_bundle_from_files(
                    files, owner=owner_scope, category=category
                )
                if imported.get("name") != skill_id or not staging_manager.update_skill(
                    skill_id,
                    {"status": "published", "source": source, "category": category},
                    owner=owner_scope,
                ):
                    raise ExtensionLifecycleError("extension_skill_stage_failed")
            staged_category = Path(staging_manager.skills_root) / category
            active_category = skills_root / category
            backup = transaction / "previous"
            with self._lock:
                if extension_id in self._transactions:
                    raise ExtensionLifecycleError("extension_skill_activation_in_progress")
                previous: Path | None = None
                if active_category.exists():
                    os.replace(active_category, backup)
                    previous = backup
                try:
                    os.replace(staged_category, active_category)
                except Exception:
                    if previous and previous.exists():
                        os.replace(previous, active_category)
                    raise
                self._transactions[extension_id] = (transaction, active_category, previous)
        except Exception:
            shutil.rmtree(transaction, ignore_errors=True)
            raise

    def commit_activation(self, manifest: Mapping[str, Any]) -> None:
        extension_id = str(manifest["extension_id"])
        with self._lock:
            transaction = self._transactions.pop(extension_id, None)
        if transaction:
            shutil.rmtree(transaction[0], ignore_errors=True)

    def rollback_activation(self, manifest: Mapping[str, Any]) -> None:
        extension_id = str(manifest["extension_id"])
        with self._lock:
            transaction = self._transactions.pop(extension_id, None)
            if not transaction:
                return
            root, active, previous = transaction
            if active.exists():
                shutil.rmtree(active)
            if previous and previous.exists():
                os.replace(previous, active)
        shutil.rmtree(root, ignore_errors=True)

    def deactivate_for_owner(
        self,
        install_path: Path,
        manifest: Mapping[str, Any],
        *,
        owner_scope: str,
    ) -> None:
        extension_id = str(manifest["extension_id"])
        category = f"extension-{extension_id}"
        source_prefix = f"extension:{extension_id}@"
        active = Path(self.skills.skills_root) / category
        if not active.exists():
            return
        managed = [item for item in self.skills.load_all() if item.get("category") == category]
        entries = list(active.iterdir()) if active.is_dir() else []
        if (
            active.is_symlink()
            or not managed
            or {entry.name for entry in entries} != {item["name"] for item in managed}
            or any(not entry.is_dir() or entry.is_symlink() for entry in entries)
            or any(
                item.get("owner") != owner_scope
                or not str(item.get("source") or "").startswith(source_prefix)
                for item in managed
            )
        ):
            raise ExtensionLifecycleError("extension_skill_removal_ambiguous")
        removed = Path(tempfile.mkdtemp(prefix=f".jos-remove-{extension_id}-", dir=active.parent.parent))
        target = removed / category
        os.replace(active, target)
        shutil.rmtree(removed, ignore_errors=True)
