"""Disposable real-source/package/native-tool smoke; reviewed recipes are not model proof.

Usage: .venv/bin/python scripts/validate_cli_package.py --recipe integrations/repository-packages/yt-dlp/proposal.json
Native skills: --url https://github.com/dietrichgebert/ponytail --ref <commit>
"""

import argparse
import asyncio
import atexit
import json
import os
import sys
import tempfile
from pathlib import Path


def reviewed_reply(proposal, messages):
    context = json.loads(messages[-1]["content"])
    excerpts = context["untrusted_source_excerpts"]
    evidence = [*proposal["evidence"], *(s["evidence"] for s in proposal["setup"])]
    for interface in proposal["interfaces"]:
        evidence.extend(
            [*interface["evidence"], *interface.get("arguments", {}).values()]
        )
    missing = {
        e["path"]: e["quote"][:200]
        for e in evidence
        if e["quote"] not in excerpts.get(e["path"], "")
    }
    if missing:
        return json.dumps({"find_text": missing})
    return json.dumps({"proposal": proposal})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", type=Path)
    parser.add_argument("--url")
    parser.add_argument("--ref")
    parser.add_argument(
        "--output", type=Path, help="Retain package and receipt after cleanup"
    )
    args = parser.parse_args()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
    proposal = json.loads(args.recipe.read_text()) if args.recipe else None
    source = (
        proposal["manifest"]["source"]
        if proposal
        else {"url": args.url, "revision": args.ref}
    )
    if not all(source.values()):
        parser.error("Provide --recipe or both --url and --ref")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="pandamonium-cli-smoke-") as directory:
        os.environ["ODYSSEUS_DATA_DIR"] = directory
        os.environ["ODYSSEUS_EXTENSIONS_DIR"] = directory + "/runtime-extensions"
        os.environ["DATABASE_URL"] = "sqlite:///" + directory + "/app.db"
        from core.database import Base, engine
        from services.memory.skills import SkillsManager
        from src.agent_tools.admin_tools import do_manage_extensions
        from src.authority_protocol import AuthorityStore
        from src.extension_agent_mount import execute_mounted_extension_tool
        from src.extension_cli_adapter import (
            _SERVICES,
            GeneratedCliAdapter,
            _stop_service,
        )
        from src.extension_installer import ExtensionLifecycleManager, GitSourceClient
        from src.extension_registry import ExtensionRegistry
        from src.extension_scan import ExtensionStaticScanner
        from src.extension_skill_adapter import SkillBundleAdapter
        from src.tools.system import do_manage_skills

        Base.metadata.create_all(engine)
        # This process uses only the disposable installation above. Reap even on failure.
        atexit.register(lambda: [_stop_service(unit) for unit in list(_SERVICES)])

        owner = "disposable-cli-validation"
        scanner = ExtensionStaticScanner(
            git_client=GitSourceClient(timeout_seconds=120),
            model=(lambda messages, check: reviewed_reply(proposal, messages))
            if proposal
            else None
        )
        artifact = scanner.run(source["url"], source["revision"], operator_id=owner)
        if not artifact.get("draft_manifest"):
            raise RuntimeError(json.dumps(artifact.get("integration")))
        registry, authority = ExtensionRegistry(), AuthorityStore()
        manager = ExtensionLifecycleManager(
            registry=registry,
            authority=authority,
            adapters=[
                GeneratedCliAdapter(),
                SkillBundleAdapter(SkillsManager(directory)),
            ],
        )

        def execute(plan):
            decision = plan["authority_decision"]
            authority.resolve(
                decision["decision_id"],
                operator_id=owner,
                choice="approve",
                scope="once",
            )
            return manager.execute_plan(plan["plan_id"], operator_id=owner)

        package = artifact["package"]
        plan = manager.preview_source(
            "install",
            source["url"],
            source["revision"],
            operator_id=owner,
            scan_id=package["id"],
            scan_revision=artifact["source_revision"],
            draft_manifest=artifact["draft_manifest"],
            prepared_content=(
                scanner.data_dir / package["id"] / "package.tar.gz"
            ).read_bytes(),
            prepared_metadata=package,
        )
        installed = execute(plan)
        extension_id = plan["extension_id"]
        results = []
        discovery = asyncio.run(
            do_manage_extensions(
                json.dumps({"action": "inspect", "extension_id": extension_id}), owner
            )
        )
        if proposal and proposal.get("execution"):
            checks = proposal["execution"]["checks"]
            mounted = asyncio.run(
                do_manage_extensions(
                    json.dumps(
                        {"action": "mount", "names": [c["name"] for c in checks]}
                    ),
                    owner,
                )
            )
            for spec, check in zip(mounted["mounted_extension_tools"], checks):
                result = asyncio.run(
                    execute_mounted_extension_tool(
                        spec, check["arguments"], owner=owner
                    )
                )
                assert result.get("exit_code") == 0, result
                if "expected" in check:
                    assert result["result"] == check["expected"], result
                results.append(
                    {
                        "tool": spec["name"],
                        "arguments": check["arguments"],
                        "result": result["result"],
                    }
                )
            assert len(results) == len(checks)
        else:
            for skill in artifact["draft_manifest"]["capabilities"]["descriptor"][
                "include"
            ]:
                result = asyncio.run(
                    do_manage_skills(
                        json.dumps({"action": "view", "name": skill}), owner
                    )
                )
                assert result.get("results", "").startswith("---\n"), result
                results.append(
                    {"skill": skill, "native_body_chars": len(result["results"])}
                )
        for operation in ("disable", "enable", "uninstall"):
            execute(
                manager.preview_lifecycle(operation, extension_id, operator_id=owner)
            )
            if operation == "disable":
                assert not registry.snapshot()["extensions"][extension_id]["enabled"]
        assert not registry.snapshot()["extensions"]
        receipt = {
            "source": source,
            "package": package,
            "installed": installed,
            "discovery": discovery,
            "operations": results,
            "lifecycle": ["install", "disable", "enable", "uninstall"],
            "model": "reviewed proposal fixture" if proposal else "native descriptor",
            "temporary_data_directory": directory,
        }
        if args.output:
            (args.output / "package.tar.gz").write_bytes(
                (scanner.data_dir / package["id"] / "package.tar.gz").read_bytes()
            )
    assert not Path(directory).exists()
    receipt["temporary_data_removed"] = True
    if args.output:
        (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
