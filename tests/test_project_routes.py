"""Project API tests (MAD-902): create/import/list through the route layer."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes import project_routes


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    import core.constants as constants
    monkeypatch.setattr(constants, "DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(project_routes, "get_current_user", lambda request: SimpleNamespace(id="u1"))
    monkeypatch.setattr(project_routes, "owner_is_admin_or_single_user", lambda owner: True)
    return tmp_path


def _route(path, method):
    router = project_routes.setup_project_routes()
    for route in router.routes:
        if getattr(route, "path", "") == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} not found")


class _Request:
    pass


def test_create_then_list_projects(data_dir):
    create = _route("/api/projects", "POST")
    listed_route = _route("/api/projects", "GET")

    result = create(_Request(), body={"name": "Workstation"})

    assert result["project"]["name"] == "Workstation"
    assert result["projects"][0]["id"] == result["project"]["id"]
    listing = listed_route(_Request())
    assert listing["projects"][0]["id"] == result["project"]["id"]
    assert str(data_dir / "data" / "projects") in listing["root"]


def test_import_folder_via_route(data_dir):
    folder = data_dir / "import-me"
    folder.mkdir()
    create = _route("/api/projects", "POST")

    result = create(_Request(), body={"path": str(folder)})

    assert result["project"]["path"] == str(folder.resolve())


def test_invalid_project_input_returns_400(data_dir):
    create = _route("/api/projects", "POST")

    with pytest.raises(HTTPException) as exc:
        create(_Request(), body={"name": "../escape"})
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        create(_Request(), body={"path": str(data_dir / "missing")})
    assert exc.value.status_code == 400


def test_delete_project_route(data_dir):
    create = _route("/api/projects", "POST")
    delete = _route("/api/projects/{project_id}", "DELETE")

    project = create(_Request(), body={"name": "Disposable"})["project"]
    assert delete(project["id"], _Request())["projects"] == []
    with pytest.raises(HTTPException) as exc:
        delete(project["id"], _Request())
    assert exc.value.status_code == 404


def test_projects_are_admin_gated(data_dir, monkeypatch):
    monkeypatch.setattr(project_routes, "owner_is_admin_or_single_user", lambda owner: False)
    listed = _route("/api/projects", "GET")

    with pytest.raises(HTTPException) as exc:
        listed(_Request())
    assert exc.value.status_code == 403
