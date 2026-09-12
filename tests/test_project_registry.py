"""Project registry tests (MAD-902): real directories, create/import, safety."""
import json
from pathlib import Path

import pytest

from src import project_registry


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    import core.constants as constants
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path


def test_create_project_makes_and_registers_directory(data_dir):
    project = project_registry.add_project(name="Demo Project")

    assert Path(project["path"]).is_dir()
    assert project["available"] is True
    assert project["reason"] == ""
    assert Path(project["path"]).parent == Path(data_dir / "data" / "projects")

    stored = json.loads((data_dir / "data" / "projects.json").read_text())
    assert stored["projects"][0]["id"] == project["id"]

    listed = project_registry.list_projects()
    assert [p["id"] for p in listed] == [project["id"]]


def test_create_project_rejects_bad_names_and_duplicates(data_dir):
    with pytest.raises(ValueError):
        project_registry.add_project(name="")
    with pytest.raises(ValueError):
        project_registry.add_project(name="../escape")
    with pytest.raises(ValueError):
        project_registry.add_project(name="nested/path")

    project_registry.add_project(name="Demo")
    with pytest.raises(ValueError, match="already exists"):
        project_registry.add_project(name="Demo")


def test_import_existing_folder(data_dir):
    existing = data_dir / "existing-folder"
    existing.mkdir()

    project = project_registry.add_project(path=str(existing))

    assert project["path"] == str(existing.resolve())
    assert project["name"] == "existing-folder"
    with pytest.raises(ValueError, match="already a project"):
        project_registry.add_project(path=str(existing))


def test_import_rejects_missing_and_root_paths(data_dir):
    with pytest.raises(ValueError):
        project_registry.add_project(path=str(data_dir / "missing"))
    with pytest.raises(ValueError):
        project_registry.add_project(path="/")


def test_missing_folder_reports_unavailable_without_deleting(data_dir):
    project = project_registry.add_project(name="Ephemeral")
    Path(project["path"]).rmdir()

    listed = project_registry.list_projects()
    assert listed[0]["available"] is False
    assert "no longer exists" in listed[0]["reason"]


def test_remove_project_forgets_only_the_registry(data_dir):
    project = project_registry.add_project(name="Keep Folder")
    folder = Path(project["path"])
    assert folder.is_dir()

    assert project_registry.remove_project(project["id"]) is True
    assert project_registry.list_projects() == []
    assert folder.is_dir()
    assert project_registry.remove_project(project["id"]) is False
