"""JOS protocol pack registry: manifests, validation, and rendering."""

from pathlib import Path

import src.protocol_registry as registry

_GOOD_PACK = """---
id: good
version: "1.0"
scope: core
title: Good pack
domains: []
token_budget: 100
enforcement:
  - src/example.py
---

- Follow the good rule.
"""


def test_shipped_core_packs_load_with_valid_manifests():
    packs = registry.load_protocol_packs()
    ids = {pack.id for pack in packs}
    assert {"jos-p0-engine", "jos-p1-identity"} <= ids
    assert all(pack.ok for pack in packs), [pack.error for pack in packs if not pack.ok]
    core = [pack for pack in packs if pack.scope == registry.CORE_SCOPE]
    assert core
    assert all(pack.body and pack.token_budget > 0 for pack in core)


def test_core_block_renders_protocol_ids_and_rule_text():
    block = registry.core_protocol_block()
    assert "Operating protocols" in block
    assert "JOS-P0" in block
    assert "JOS-P1" in block
    assert "replaceable reasoning engine" in block
    assert "Evidence before outcome" in block
    assert "Precedence" in block
    assert registry.PROTOCOL_BEGIN in block and registry.PROTOCOL_END in block


def test_strip_protocol_block_preserves_surrounding_sections():
    text = "fact\n\nidentity\n\n" + registry.core_protocol_block() + "\n\npreset"
    stripped = registry.strip_protocol_block(text)
    assert "Operating protocols" not in stripped
    assert registry.PROTOCOL_BEGIN not in stripped
    assert stripped == "fact\n\nidentity\n\npreset"
    assert registry.strip_protocol_block("plain text") == "plain text"


def test_malformed_pack_surfaces_error_and_is_not_rendered(tmp_path: Path):
    (tmp_path / "good.pack.md").write_text(_GOOD_PACK, encoding="utf-8")
    (tmp_path / "broken.pack.md").write_text(
        "---\nid: broken\nscope: core\n---\nbody\n", encoding="utf-8"
    )

    packs = registry.load_protocol_packs(tmp_path)
    errors = [pack for pack in packs if not pack.ok]
    assert errors, "malformed pack must not be silently dropped"
    assert "version" in (errors[0].error or "")

    block = registry.render_protocol_block(packs)
    assert "good" in block
    assert "broken" not in block

    status = registry.protocol_status(packs_dir=tmp_path)
    assert status["status"] == "degraded"
    assert status["errors"]
    assert status["core_ids"] == ["good"]


def test_filename_must_match_manifest_id(tmp_path: Path):
    (tmp_path / "wrong-name.pack.md").write_text(_GOOD_PACK, encoding="utf-8")
    packs = registry.load_protocol_packs(tmp_path)
    assert packs and not packs[0].ok
    assert "filename" in (packs[0].error or "")


def test_protocol_layer_can_be_disabled(monkeypatch):
    monkeypatch.setattr(registry, "load_settings", lambda: {"protocol_layer_enabled": False})
    assert registry.core_protocol_block() == ""
    assert registry.core_protocol_references() == []


def test_missing_packs_directory_is_visible():
    packs = registry.load_protocol_packs("/nonexistent/protocols")
    assert packs and not packs[0].ok
    assert packs[0].error
