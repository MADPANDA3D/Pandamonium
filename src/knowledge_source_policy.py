"""JOS P3 source inventory and exclusion policy for canonical and derived docs."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Dict, Iterable, Optional


EXCLUDED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".obsidian",
    ".trash",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
EXCLUDED_FILENAMES = {
    ".env",
    ".env.local",
    "credentials.json",
    "secrets.json",
}
EXCLUDED_SUFFIXES = {".key", ".p12", ".pem"}
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def exclusion_reason(path: Path, *, root: Optional[Path] = None) -> Optional[str]:
    """Return the deterministic exclusion reason for a source path."""
    candidate = Path(path)
    try:
        relative = candidate.resolve().relative_to(Path(root).resolve()) if root else candidate
    except ValueError:
        return "outside_root"
    parts = {part.casefold() for part in relative.parts}
    blocked = sorted(parts & EXCLUDED_PARTS)
    if blocked:
        return f"excluded_path:{blocked[0]}"
    if candidate.name.casefold() in EXCLUDED_FILENAMES:
        return "secret_filename"
    if candidate.suffix.casefold() in EXCLUDED_SUFFIXES:
        return "secret_filetype"
    return None


def wiki_source_links(text: str) -> list[str]:
    """Extract stable Obsidian links from a generated wiki page's sources field."""
    frontmatter = text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else ""
    if "sources:" not in frontmatter and "source_file:" not in frontmatter:
        return []
    return sorted(set(_WIKI_LINK_RE.findall(frontmatter)))


def _wiki_lineage_links(links: list[str], wiki_root: Path) -> list[str]:
    lineage = set(links)
    for link in links:
        linked = wiki_root / link
        if not linked.suffix:
            linked = linked.with_suffix(".md")
        try:
            linked.resolve().relative_to(wiki_root.resolve())
        except ValueError:
            continue
        if linked.is_file():
            nested = wiki_source_links(linked.read_text(encoding="utf-8", errors="replace"))
            lineage.update(nested)
    return sorted(lineage)


def source_record(
    path: Path,
    *,
    root: Path,
    wiki_root: Optional[Path] = None,
    owner: Optional[str] = None,
    generation_version: Optional[str] = None,
) -> Dict:
    """Build a provenance record without writing or indexing the source."""
    path = Path(path).resolve()
    root = Path(root).resolve()
    reason = exclusion_reason(path, root=root)
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    document_class = "canonical_document"
    if wiki_root:
        try:
            path.relative_to(Path(wiki_root).resolve())
            document_class = "generated_wiki"
        except ValueError:
            pass

    content = path.read_bytes() if path.is_file() else b""
    links = wiki_source_links(content.decode("utf-8", errors="replace")) if document_class == "generated_wiki" else []
    lineage_links = _wiki_lineage_links(links, Path(wiki_root)) if document_class == "generated_wiki" else []
    if document_class == "generated_wiki":
        if not links:
            reason = reason or "wiki_missing_sources"
        elif not generation_version:
            reason = reason or "wiki_missing_generation_version"
        elif any(any(part.casefold() in EXCLUDED_PARTS for part in Path(link).parts) for link in lineage_links):
            reason = reason or "wiki_excluded_source"

    stat = path.stat()
    return {
        "path": str(path),
        "relative_path": str(relative),
        "content_hash": hashlib.sha256(content).hexdigest(),
        "modified_time": int(stat.st_mtime),
        "document_class": document_class,
        "owner": owner,
        "visibility": "owner" if owner else "shared",
        "source_links": links,
        "lineage_links": lineage_links,
        "generation_version": generation_version if document_class == "generated_wiki" else None,
        "indexable": reason is None,
        "exclusion_reason": reason,
    }


def inventory_markdown(
    paths: Iterable[Path],
    *,
    root: Path,
    wiki_root: Optional[Path] = None,
    owner: Optional[str] = None,
    generation_version: Optional[str] = None,
) -> list[Dict]:
    return [
        source_record(
            path,
            root=root,
            wiki_root=wiki_root,
            owner=owner,
            generation_version=generation_version,
        )
        for path in paths
        if Path(path).is_file() and Path(path).suffix.casefold() == ".md"
    ]


def validate_wiki_ingest(document: Dict) -> None:
    """Fail closed when derived wiki input lacks clean, versioned lineage."""
    if str(document.get("domain") or "") != "wiki":
        return
    if str(document.get("authority") or "") != "secondary":
        raise ValueError("wiki_must_be_secondary")
    links = document.get("source_links")
    if not isinstance(links, list) or not links:
        raise ValueError("wiki_missing_sources")
    if not str(document.get("generation_version") or "").strip():
        raise ValueError("wiki_missing_generation_version")
    if any(
        any(part.casefold() in EXCLUDED_PARTS for part in Path(str(link)).parts)
        for link in links
    ):
        raise ValueError("wiki_excluded_source")
