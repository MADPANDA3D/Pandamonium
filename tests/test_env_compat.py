import os
from pathlib import Path
import subprocess
import sys

from src.env_compat import apply_legacy_env_aliases


ROOT = Path(__file__).resolve().parents[1]


def test_pandamonium_environment_wins_without_breaking_legacy_names():
    environ = {
        "PANDAMONIUM_DATA_DIR": "/new",
        "ODYSSEUS_DATA_DIR": "/old",
        "ODYSSEUS_ADMIN_USER": "legacy-admin",
    }

    apply_legacy_env_aliases(environ)

    assert environ["ODYSSEUS_DATA_DIR"] == "/new"
    assert environ["ODYSSEUS_ADMIN_USER"] == "legacy-admin"


def test_standalone_codex_bridge_accepts_canonical_environment():
    script = ROOT / "services" / "codex-bridge" / "pandamonium_codex_bridge.py"
    code = (
        "import importlib.util; "
        f"s=importlib.util.spec_from_file_location('bridge', {str(script)!r}); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.PORT)"
    )
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(ROOT),
        PANDAMONIUM_CODEX_BRIDGE_PORT="9123",
        ODYSSEUS_CODEX_BRIDGE_PORT="8123",
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "9123"
