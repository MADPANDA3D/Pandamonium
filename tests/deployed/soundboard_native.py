"""Real pinned-package proof; run through scripts/check_package_runtimes.sh.

Usage: .venv/bin/python tests/deployed/soundboard_native.py PACKAGE_DIR OUTPUT_DIR
No scan/model API is called. All owner/install state is disposable.
"""

import asyncio
import atexit
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.getcwd())
package_dir, output_dir = map(Path, sys.argv[1:])
output_dir.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="mad968-native-") as directory:
    os.environ.update(
        ODYSSEUS_DATA_DIR=directory,
        ODYSSEUS_EXTENSIONS_DIR=directory + "/extensions",
        DATABASE_URL="sqlite:///" + directory + "/app.db",
        PYTHON_DOTENV_DISABLED="1",
    )
    from src import soundboard
    from src.authority_protocol import AuthorityStore
    from src.extension_agent_mount import (
        execute_mounted_extension_tool,
        mount_extension_capabilities,
    )
    from src.extension_cli_adapter import _SERVICES, GeneratedCliAdapter, _stop_service
    from src.extension_installer import ExtensionLifecycleManager
    from src.extension_registry import ExtensionRegistry

    atexit.register(lambda: [_stop_service(u) for u in list(_SERVICES)])
    receipt = json.loads((package_dir / "receipt.json").read_text())
    content = (package_dir / "package.tar.gz").read_bytes()
    registry = ExtensionRegistry()
    authority = AuthorityStore()
    adapter = GeneratedCliAdapter()
    manager = ExtensionLifecycleManager(
        registry=registry, authority=authority, adapters=[adapter]
    )
    owner = "soundboard-proof"

    def run(plan):
        authority.resolve(
            plan["authority_decision"]["decision_id"],
            operator_id=owner,
            choice="approve",
            scope="once",
        )
        return manager.execute_plan(plan["plan_id"], operator_id=owner)

    plan = manager.preview_source(
        "install",
        receipt["source"]["url"],
        receipt["source"]["revision"],
        operator_id=owner,
        scan_id="reviewed-soundboard",
        scan_revision=receipt["source"]["revision"],
        draft_manifest=receipt["manifest"],
        prepared_content=content,
        prepared_metadata={**receipt, "id": "reviewed-soundboard"},
    )
    try:
        installed = run(plan)
        print("INSTALLED", flush=True)
        names = [
            "myinstants_api__search",
            "myinstants_api__detail",
            "myinstants_api__favorites",
        ]
        specs = mount_extension_capabilities(registry, names)["mounted"]
        assert len(specs) == 3
        search = asyncio.run(
            execute_mounted_extension_tool(
                specs[0], {"query": "vine boom"}, owner=owner
            )
        )
        assert search["exit_code"] == 0, search
        rows = search["result"]["sounds"]
        assert rows
        print("SEARCH", rows[0], flush=True)
        record = registry.snapshot()["extensions"]["myinstants-api"]
        _, runtime, _, _, config = adapter._validated_context(record, owner)
        soundboard.favorite(runtime, rows[0]["id"], True)
        favorites = asyncio.run(
            execute_mounted_extension_tool(specs[2], {}, owner=owner)
        )
        assert favorites["result"]["sounds"] == [rows[0]]
        from core.database import init_db
        from core.models import ChatMessage
        from core.session_manager import SessionManager

        init_db()
        sessions = SessionManager()
        session = sessions.create_session("soundboard-proof", "Cue persistence", "", "none", owner=owner)
        message = ChatMessage("assistant", f"First boom, second boom {rows[0]['cue']} and onward.")
        sessions.add_message(session.id, message)
        cues = message.metadata["sound_cues"]
        assert cues[0]["message_id"] == message.metadata["_db_id"]
        restored = SessionManager().get_session(session.id).history[-1]
        assert restored.metadata["sound_cues"] == cues
        denied = asyncio.run(
            execute_mounted_extension_tool(specs[2], {}, owner="another-owner")
        )
        assert denied["exit_code"] == 1
        audio, kind = soundboard.audio(runtime, rows[0]["id"])
        (output_dir / "preview.mp3").write_bytes(audio)
        print("AUDIO", len(audio), kind, flush=True)
        for op in ["disable", "enable"]:
            run(manager.preview_lifecycle(op, "myinstants-api", operator_id=owner))
        favorites2 = asyncio.run(
            execute_mounted_extension_tool(specs[2], {}, owner=owner)
        )
        assert favorites2["result"] == favorites["result"]
        (output_dir / "native.json").write_text(
            json.dumps(
                {
                    "install": installed,
                    "sha256": receipt["sha256"],
                    "search": search["result"],
                    "favorites": favorites["result"],
                    "audio_bytes": len(audio),
                    "audio_type": kind,
                    "foreign_owner_denied": True,
                    "disable_enable_preserved": True,
                    "cue_metadata_reload_preserved": True,
                    "model_calls": 0,
                },
                indent=2,
            )
        )
        run(manager.preview_lifecycle("uninstall", "myinstants-api", operator_id=owner))
        assert not registry.snapshot()["extensions"]
        assert soundboard.read_state(runtime)["favorites"] == [rows[0]["id"]]
        print("UNINSTALLED; OWNER FAVORITES RETAINED", flush=True)
    finally:
        for u in list(_SERVICES):
            _stop_service(u)
