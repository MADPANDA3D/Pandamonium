"""Developer-authorized validation and immutable GitHub marketplace publication.

Only the publisher host has gh credentials and a signing key. Package execution
uses the existing bounded sandbox; signing happens after validation has stopped.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.atomic_io import atomic_write_json
from scripts.build_marketplace_catalog import (
    build_catalog,
    build_entry,
    load_private_key,
    public_key_b64,
)
from services.memory.skills import SkillsManager
from src.extension_cli_adapter import GeneratedCliAdapter
from src.extension_installer import ExtensionLifecycleError
from src.extension_metadata import package_metadata
from src.extension_package import (
    build_prepared_package,
    extract_package,
    package_tree_digest,
)
from src.extension_registry import (
    IMMUTABLE_REVISION_PATTERN,
    validate_extension_manifest,
)
from src.extension_scan import ExtensionStaticScanner, _source_secrets
from src.extension_skill_adapter import SkillBundleAdapter
from src.marketplace_catalog import MarketplaceCatalogError, validate_published_catalog
from src.marketplace_channel import (
    BUNDLED,
    CATALOG_PATH,
    CHANNEL_BRANCH,
    MAX_CATALOG_BYTES,
    REPOSITORY,
)

KEY_ID = "pandamonium-marketplace-2026"


class PublicationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _gh(*args: str, payload: dict | None = None) -> bytes:
    try:
        command = ["gh", *args]
        if payload is not None:
            command += ["--input", "-"]
        result = subprocess.run(
            command,
            input=json.dumps(payload).encode() if payload is not None else None,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.returncode:
            # Never expose gh credential helpers, server bodies or host paths.
            if args[0] == "api" and b"(HTTP 409)" in result.stderr:
                raise PublicationError("marketplace_github_conflict")
            raise PublicationError(
                "marketplace_github_failed:Check publisher access and retry."
            )
        return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicationError(
            "marketplace_github_unavailable:Install/authenticate gh on the publisher host."
        ) from exc


def _json(*args: str, payload: dict | None = None) -> dict:
    return json.loads(_gh(*args, payload=payload))


def read_remote_catalog() -> tuple[dict, str]:
    result = _json(
        "api", f"repos/{REPOSITORY}/contents/{CATALOG_PATH}?ref={CHANNEL_BRANCH}"
    )
    raw = base64.b64decode(result["content"])
    if len(raw) > MAX_CATALOG_BYTES:
        raise PublicationError("marketplace_catalog_too_large")
    return json.loads(raw), result["sha"]


def _keys() -> dict:
    return json.loads((BUNDLED / "trusted_keys.json").read_text())


def _key():
    name = os.getenv("PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE", "")
    path = Path(name)
    if not name or not path.is_file() or path.stat().st_mode & 0o077:
        raise PublicationError(
            "marketplace_publisher_setup:Set PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE to the publisher's mode-600 key."
        )
    try:
        key = load_private_key(path)
    except SystemExit as exc:
        raise PublicationError("marketplace_signing_key_invalid") from exc
    if _keys().get(KEY_ID) != public_key_b64(key):
        raise PublicationError("marketplace_signing_key_mismatch")
    return key


def validate_package(path: Path, manifest: dict, work: Path, owner: str) -> dict:
    """Validate in disposable state without registering in the user's account."""
    revision = manifest["source"]["revision"]
    skills = SkillsManager(str(work / "skills"))
    skill_adapter = SkillBundleAdapter(skills)
    if skill_adapter.supports(manifest):
        catalog, healthy = skill_adapter.validate_for_owner(
            path, manifest, revision, owner_scope=owner
        )
        skill_adapter.activate_for_owner(
            path, manifest, catalog, revision, owner_scope=owner
        )
        try:
            admitted = skills.index_for(owner)
            if not healthy or len(admitted) != len(catalog["skills"]):
                raise PublicationError("marketplace_skill_validation_failed")
            return {"kind": "native_skills", "count": len(admitted)}
        finally:
            skill_adapter.deactivate_for_owner(path, manifest, owner_scope=owner)
    adapter = GeneratedCliAdapter(work / "extensions")
    if not adapter.supports(manifest):
        raise PublicationError(
            "marketplace_validation_unavailable:Prepare a native skill or isolated executable adapter with operation checks."
        )
    runtime = adapter._runtime(manifest, package_tree_digest(path), owner)
    try:
        _catalog, healthy = adapter.validate_for_owner(path, manifest, revision, owner)
        if not healthy:
            raise PublicationError("marketplace_operation_validation_failed")
        return {
            "kind": "isolated_operations",
            "checks": len(adapter.preview(path, manifest)["checks"]),
        }
    finally:
        from src.extension_knowledge import index_root

        shutil.rmtree(index_root(runtime), ignore_errors=True)
        shutil.rmtree(runtime, ignore_errors=True)


def _release(tag: str) -> dict:
    # The REST tag endpoint excludes drafts; resolve their ID through gh first.
    record = _json("release", "view", tag, "--repo", REPOSITORY, "--json", "databaseId")
    return _json("api", f"repos/{REPOSITORY}/releases/{record['databaseId']}")


def _upload(archive: Path, manifest: dict, digest: str, proof: dict) -> str:
    tag = f"plugin-{manifest['extension_id']}-{manifest['version']}-{digest}"
    try:
        release = _release(tag)
    except PublicationError:
        notes = archive.parent / "release-notes.md"
        notes.write_text(
            f"# {manifest['name']} {manifest['version']}\n\n"
            "Tested integration package published by the developer action (MAD-962). "
            "Installs the prepared integration, including generated files, without regenerating it.\n\n"
            f"Source: {manifest['source']['url']} at `{manifest['source']['revision']}`.\n\n"
            f"SHA-256: `{digest}`. Validation: `{json.dumps(proof, sort_keys=True)}`.\n\n"
            "Configuration values and runtime data are excluded. Native skill validation checks admission; "
            "executable validation runs the declared operation checks in the bounded sandbox. "
            "Owner setup and target prerequisites still apply on installation. "
            "Earlier artifacts remain available for rollback.\n"
        )
        try:
            _gh(
                "release",
                "create",
                tag,
                "--repo",
                REPOSITORY,
                "--draft",
                "--latest=false",
                "--title",
                f"Plugin: {manifest['name']} {manifest['version']}",
                "--notes-file",
                str(notes),
            )
        except PublicationError:
            pass  # A concurrent identical publication may have created the draft.
        release = _release(tag)
    matches = [a for a in release["assets"] if a["name"] == archive.name]
    if not matches:
        if not release["draft"]:
            raise PublicationError("marketplace_immutable_release_incomplete")
        try:
            _gh("release", "upload", tag, str(archive), "--repo", REPOSITORY)
        except PublicationError:
            pass  # Reconcile uncertain upload/race by the exact asset hash below.
        release = _release(tag)
        matches = [a for a in release["assets"] if a["name"] == archive.name]
    if len(matches) != 1 or matches[0]["size"] != archive.stat().st_size:
        raise PublicationError("marketplace_uploaded_artifact_mismatch")
    content = _gh(
        "api",
        f"repos/{REPOSITORY}/releases/assets/{matches[0]['id']}",
        "-H",
        "Accept: application/octet-stream",
    )
    if hashlib.sha256(content).hexdigest() != digest:
        raise PublicationError("marketplace_uploaded_artifact_mismatch")
    if release["draft"]:
        _gh(
            "release",
            "edit",
            tag,
            "--repo",
            REPOSITORY,
            "--draft=false",
            "--latest=false",
        )
    if _release(tag)["draft"]:
        raise PublicationError("marketplace_release_not_public")
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}"


def update_catalog(entry: dict | None, key, *, rollback: dict | None = None) -> dict:
    """GitHub contents SHA is the compare-and-swap guard across publisher hosts."""
    keys = _keys()
    if rollback is not None:
        validate_published_catalog(rollback, trusted_keys=keys)
    for _attempt in range(4):
        current, sha = read_remote_catalog()
        validate_published_catalog(current, trusted_keys=keys)
        entries = list(
            rollback["entries"] if rollback is not None else current["entries"]
        )
        if entry is not None:
            identity = (entry["manifest"]["extension_id"], entry["manifest"]["version"])
            previous = next(
                (
                    e
                    for e in entries
                    if (e["manifest"]["extension_id"], e["manifest"]["version"])
                    == identity
                ),
                None,
            )
            if previous:
                if (
                    previous["artifact"]["sha256"] != entry["artifact"]["sha256"]
                    or previous["manifest"] != entry["manifest"]
                ):
                    raise PublicationError(
                        "marketplace_version_conflict:Increment the package version for changed contents."
                    )
                if previous["review"]["status"] != "active":
                    raise PublicationError("marketplace_package_not_active")
                return current
            entries.append(entry)
        now = datetime.now(timezone.utc)
        catalog = build_catalog(
            entries,
            private_key=key,
            key_id=KEY_ID,
            catalog_id="pandamonium-community",
            generated_at=now.isoformat(),
            expires_at=(now + timedelta(days=90)).isoformat(),
        )
        validate_published_catalog(catalog, trusted_keys=keys)
        content = json.dumps(catalog, indent=2).encode()
        if len(content) > MAX_CATALOG_BYTES:
            raise PublicationError("marketplace_catalog_too_large")
        try:
            _json(
                "api",
                "--method",
                "PUT",
                f"repos/{REPOSITORY}/contents/{CATALOG_PATH}",
                payload={
                    "branch": CHANNEL_BRANCH,
                    "sha": sha,
                    "message": "chore(marketplace): "
                    + ("restore catalog" if rollback else "publish tested package")
                    + "\n\nRefs: MAD-962",
                    "content": base64.b64encode(content).decode(),
                },
            )
        except PublicationError as exc:
            if exc.code != "marketplace_github_conflict":
                raise
            if rollback is not None:
                raise PublicationError(
                    "marketplace_catalog_changed_during_rollback"
                ) from None
            continue  # Retry from current truth; never overwrite a concurrent entry.
        observed, _sha = read_remote_catalog()
        validate_published_catalog(observed, trusted_keys=keys)
        if rollback is not None:
            if observed["entries"] == entries:
                return observed
            raise PublicationError("marketplace_catalog_changed_during_rollback")
        if entry is not None and any(
            e["artifact"] == entry["artifact"] for e in observed["entries"]
        ):
            return observed
    raise PublicationError(
        "marketplace_catalog_conflict:Publication was not confirmed; retry safely."
    )


def publish_package(
    content: bytes,
    *,
    owner: str,
    progress: Callable[[str], None],
    source_revision: str | None = None,
    version: str | None = None,
) -> dict:
    # Check setup without reading the private key until package execution is over.
    if not os.getenv("PANDAMONIUM_MARKETPLACE_SIGNING_KEY_FILE"):
        raise PublicationError(
            "marketplace_publisher_setup:Configure the publisher signing key and gh access."
        )
    with tempfile.TemporaryDirectory(prefix="pandamonium-publish-") as temporary:
        work = Path(temporary)
        tree = work / "package"
        progress("Checking package contents and license")
        extract_package(content, tree)
        manifest = validate_extension_manifest(
            json.loads((tree / "jarvis-extension.json").read_text())
        )
        revision = source_revision or manifest["source"]["revision"]
        target_version = version or manifest["version"]
        if not re.fullmatch(
            r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", target_version
        ):
            raise PublicationError(
                "marketplace_version_required:Choose a package version such as 1.0.0."
            )
        if manifest["source"]["revision"] not in {"self", revision}:
            raise PublicationError("marketplace_source_revision_mismatch")
        if not IMMUTABLE_REVISION_PATTERN.fullmatch(revision):
            raise PublicationError("marketplace_immutable_revision_required")
        input_digest = hashlib.sha256(content).hexdigest()
        if (
            manifest["source"]["revision"] != revision
            or manifest["version"] != target_version
        ):
            # Finalize draft provenance/version BEFORE testing and signing bytes.
            manifest["source"]["revision"] = revision
            manifest["version"] = target_version
            atomic_write_json(str(tree / "jarvis-extension.json"), manifest, indent=2)
            build_prepared_package(tree, work / "finalized.tar.gz")
            content = (work / "finalized.tar.gz").read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        before = package_tree_digest(tree)
        # Use the package writer's private-file checks; only original bytes ship.
        build_prepared_package(tree, work / "audit.tar.gz")
        files = [p for p in tree.rglob("*") if p.is_file()]
        for path in files:
            if _source_secrets(
                path, path.read_bytes().decode("utf-8", errors="replace")
            ):
                raise PublicationError(
                    "marketplace_package_secret:Remove credentials from the prepared package."
                )
        licenses = ExtensionStaticScanner()._licenses(tree, files)
        if not licenses:
            raise PublicationError(
                "marketplace_license_required:Include the upstream license in the package."
            )
        progress("Validating in disposable runtime")
        proof = validate_package(tree, manifest, work, owner)
        if package_tree_digest(tree) != before:
            raise PublicationError("marketplace_package_changed")
        progress("Signing and uploading tested artifact")
        key = _key()
        archive = (
            work / f"pandamonium-plugin-{manifest['extension_id']}-{digest}.tar.gz"
        )
        archive.write_bytes(content)
        base = f"https://github.com/{REPOSITORY}/releases/download/plugin-{manifest['extension_id']}-{manifest['version']}-{digest}"
        metadata = package_metadata(manifest)
        system = {"darwin": "macos"}.get(
            platform.system().lower(), platform.system().lower()
        )
        architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(
            platform.machine().lower(), platform.machine().lower()
        )
        spec = {
            "summary": metadata["summary"],
            "categories": metadata["categories"] or ["developer-tools"],
            "license": " AND ".join(licenses),
            "publisher": {
                "id": "madpanda3d",
                "name": "MADPANDA3D",
                "url": "https://github.com/MADPANDA3D",
            },
            "compatibility": {
                "pandamonium_min": "1.0.68",
                "pandamonium_max": "1.99.99",
                "platforms": [system],
                "architectures": [architecture],
            },
            "reviewer": "developer-action/isolated-validation",
        }
        artifact = {
            "manifest": manifest,
            "revision": manifest["source"]["revision"],
            "sha256": digest,
            "size_bytes": len(content),
            "filename": archive.name,
        }
        entry = build_entry(
            spec, artifact, private_key=key, key_id=KEY_ID, artifact_base_url=base
        )
        now = datetime.now(timezone.utc)
        validate_published_catalog(
            build_catalog(
                [entry],
                private_key=key,
                key_id=KEY_ID,
                catalog_id="pandamonium-community",
                generated_at=now.isoformat(),
                expires_at=(now + timedelta(days=90)).isoformat(),
            ),
            trusted_keys=_keys(),
        )
        uploaded_base = _upload(archive, manifest, digest, proof)
        entry["artifact"]["url"] = f"{uploaded_base}/{archive.name}"
        progress("Publishing catalog and verifying readback")
        catalog = update_catalog(entry, key)
        return {
            "state": "published",
            "input_digest": input_digest,
            "digest": digest,
            "extension_id": manifest["extension_id"],
            "version": manifest["version"],
            "artifact_url": entry["artifact"]["url"],
            "catalog_generated_at": catalog["generated_at"],
            "validation": proof,
        }


class PublicationJobs:
    """One bounded local worker; content identity makes retries safe after restart."""

    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="marketplace-publisher"
        )
        self.active: dict[str, str] = {}

    def get(self, job_id: str, owner: str) -> dict:
        if len(job_id) != 64 or any(c not in "0123456789abcdef" for c in job_id):
            raise PublicationError("marketplace_publication_not_found")
        try:
            job = json.loads((self.root / (job_id + ".json")).read_text())
        except (OSError, ValueError) as exc:
            raise PublicationError("marketplace_publication_not_found") from exc
        if job.pop("owner") != owner:
            raise PublicationError("marketplace_publication_not_found")
        if job["state"] == "publishing" and job_id not in self.active:
            job.update(
                state="failed",
                message="Publication interrupted; retry Add to marketplace.",
            )
        return job

    def start(
        self,
        content: bytes,
        owner: str,
        source_revision: str | None = None,
        version: str | None = None,
    ) -> dict:
        job_id = hashlib.sha256(
            owner.encode()
            + b"\0"
            + (source_revision or "").encode()
            + b"\0"
            + (version or "").encode()
            + b"\0"
            + content
        ).hexdigest()
        with self.lock:
            if job_id in self.active:
                return self.get(job_id, owner)
            if self.active:
                raise PublicationError(
                    "marketplace_publisher_busy:Wait for the current publication, then retry."
                )
            self.root.mkdir(parents=True, exist_ok=True)
            for old in sorted(
                self.root.glob("*.json"), key=lambda p: p.stat().st_mtime
            )[:-31]:
                old.unlink()
            job = {
                "id": job_id,
                "owner": owner,
                "state": "publishing",
                "message": "Preparing publication",
                "stage": "Preparing publication",
            }
            atomic_write_json(str(self.root / (job_id + ".json")), job)
            self.active[job_id] = owner

            def run() -> None:
                def progress(message: str) -> None:
                    job["message"] = message
                    job["stage"] = message
                    atomic_write_json(str(self.root / (job_id + ".json")), job)

                try:
                    result = publish_package(
                        content,
                        owner=owner,
                        progress=progress,
                        source_revision=source_revision,
                        version=version,
                    )
                    job.update(result, message="Published")
                except (
                    PublicationError,
                    ExtensionLifecycleError,
                    MarketplaceCatalogError,
                ) as exc:
                    job.update(state="failed", message=exc.code)
                except Exception:  # noqa: BLE001 - persist a sanitized job failure
                    job.update(
                        state="failed",
                        message="marketplace_publication_failed:Validation or publication failed; retry safely.",
                    )
                finally:
                    atomic_write_json(str(self.root / (job_id + ".json")), job)
                    with self.lock:
                        self.active.pop(job_id, None)

            self.executor.submit(run)
            return {key: value for key, value in job.items() if key != "owner"}
