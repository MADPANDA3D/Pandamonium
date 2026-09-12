"""Every mounted JOS protocol rule has a live enforcement owner (MAD-893)."""

import importlib

import src.protocol_registry as registry

_REQUIRED_PROTOCOLS = {
    "JOS-P0",
    "JOS-P1",
    "JOS-P2",
    "JOS-P3",
    "JOS-P4",
    "JOS-P5",
    "JOS-P6",
    "JOS-P7",
    "JOS-IPAV",
}


def test_every_shipped_pack_declares_enforcement_and_budget():
    for pack in registry.load_protocol_packs():
        assert pack.ok, pack.error
        assert pack.enforcement, f"{pack.id} declares no enforcement owner"
        assert pack.token_budget > 0, pack.id
        assert pack.body.strip(), pack.id


def _module_name(target: str) -> str:
    module_path = target.split("#")[0].strip()
    if module_path.endswith(".py"):
        module_path = module_path[:-3]
    return module_path.replace("/", ".")


def test_every_enforcement_target_module_exists():
    for pack in registry.load_protocol_packs():
        for target in pack.enforcement:
            importlib.import_module(_module_name(target))


def test_every_jos_protocol_has_a_versioned_pack():
    protocols = {pack.protocol for pack in registry.load_protocol_packs() if pack.protocol}
    assert _REQUIRED_PROTOCOLS <= protocols


def test_core_and_duty_scopes_cover_the_stack():
    packs = registry.load_protocol_packs()
    assert {pack.scope for pack in packs} == {"core", "duty"}
    core_ids = {pack.id for pack in packs if pack.scope == "core"}
    assert core_ids == {"jos-p0-engine", "jos-p1-identity"}
