"""Projects API — Pandamonium agent workstation (MAD-902).

Projects are real directories the installation's agent can work in. Creating a
project makes a directory under the installation projects root; importing one
registers an existing folder. Both are admin-gated like the workspace picker
because they reveal/confirm host paths and create directories.
"""
from fastapi import APIRouter, Body, HTTPException, Request

from src import project_registry
from src.auth_helpers import get_current_user
from src.tool_security import owner_is_admin_or_single_user


def setup_project_routes():
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    def _require_owner(request: Request):
        owner = get_current_user(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail="Projects are admin-only")
        return owner

    @router.get("")
    def list_projects(request: Request):
        _require_owner(request)
        return {
            "projects": project_registry.list_projects(),
            "root": str(project_registry.projects_root()),
        }

    @router.post("")
    def create_project(request: Request, body: dict = Body(default={})):
        _require_owner(request)
        payload = body if isinstance(body, dict) else {}
        try:
            project = project_registry.add_project(
                name=str(payload.get("name") or ""),
                path=str(payload.get("path") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"project": project, "projects": project_registry.list_projects()}

    @router.delete("/{project_id}")
    def delete_project(project_id: str, request: Request):
        _require_owner(request)
        if not project_registry.remove_project(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        return {"projects": project_registry.list_projects()}

    return router
