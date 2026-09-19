import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "integrations" / "repository-packages"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive(name):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as bundle:
        data = b"ok"
        member = tarfile.TarInfo(name)
        member.size = len(data)
        bundle.addfile(member, io.BytesIO(data))
    stream.seek(0)
    return tarfile.open(fileobj=stream, mode="r:")


def test_pandaflix_recipe_extracts_on_python_311_and_rejects_traversal(tmp_path):
    setup = _load("pandaflix_setup", PACKAGES / "pandaflix" / "setup.py")
    with _archive("go/bin/go") as archive:
        setup._extract_archive(archive, tmp_path)
    assert (tmp_path / "go" / "bin" / "go").read_bytes() == b"ok"
    with _archive("../escape") as archive, pytest.raises(RuntimeError, match="unsafe"):
        setup._extract_archive(archive, tmp_path)
    recipe = json.loads((PACKAGES / "pandaflix" / "proposal.json").read_text())
    generated = next(item["content"] for item in recipe["files"] if item["path"] == ".pandamonium/setup.py")
    assert generated == (PACKAGES / "pandaflix" / "setup.py").read_text()


def test_ani_cli_countdown_does_not_require_an_interactive_menu():
    setup = _load("ani_cli_setup", PACKAGES / "ani-cli" / "setup.py")
    adapter = (PACKAGES / "ani-cli" / "adapter.py").read_text()
    recipe = json.loads((PACKAGES / "ani-cli" / "proposal.json").read_text())
    generated = {item["path"]: item["content"] for item in recipe["files"]}
    assert "fzf" not in setup.DEPENDENCIES
    assert "'ANI_CLI_MENU': '/bin/true'" in adapter
    assert generated[".pandamonium/setup.py"] == (
        PACKAGES / "ani-cli" / "setup.py"
    ).read_text()
    assert generated[".pandamonium/adapter.py"] == adapter
