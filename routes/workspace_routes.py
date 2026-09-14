"""Workspace API - browse server directories to pick a tool workspace folder."""
import os
from fastapi import APIRouter, Request, HTTPException, Query

from src.auth_helpers import effective_user
from src.tool_security import owner_is_admin_or_single_user

# Cap entries returned per directory (mirrors filesystem_tools._CODENAV_MAX_HITS).
# A huge directory shouldn't dump thousands of rows into the picker; the user can
# type/paste a path to jump straight in instead.
_MAX_BROWSE_DIRS = 500

# Honest, actionable gate copy. The UI maps 401/403 to these details so a
# signed-out or non-admin user is told what to do instead of seeing a generic
# "Could not browse folders" (MAD-883).
_BROWSE_FORBIDDEN_DETAIL = (
    "Workspace browsing requires the installation admin. "
    "Sign in as an admin account to choose a workspace."
)
_VET_FORBIDDEN_DETAIL = (
    "Workspace selection requires the installation admin. "
    "Sign in as an admin account to set a workspace."
)


def _workspace_owner(request: Request):
    """The real owner behind the request: cookie session or the owner stamped
    on a bearer API token (via effective_user). Using get_current_user alone
    made a paired/token client authenticated as the owner fail the admin gate
    because it only saw the sandboxed "api" pseudo-user (MAD-883)."""
    return effective_user(request)


def setup_workspace_routes():
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    @router.get("/browse")
    def browse(request: Request, path: str = Query(default="")):
        """List subdirectories of `path` (default: home) so the UI can navigate
        the server filesystem and pick a workspace folder. Directories only.

        ADMIN-ONLY: this enumerates the server filesystem, so it is gated the
        same way the file/shell tools are (read_file/write_file/bash are in
        NON_ADMIN_BLOCKED_TOOLS). A non-admin who can't use those tools must not
        be able to map the host's directory tree either.
        """
        owner = _workspace_owner(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail=_BROWSE_FORBIDDEN_DETAIL)

        # Resolve symlinks so the reported path is canonical and the UI navigates
        # real directories (defends against symlink games in displayed paths).
        target = os.path.realpath(os.path.expanduser(path.strip() or "~"))
        if not os.path.isdir(target):
            target = os.path.realpath(os.path.expanduser("~"))

        dirs = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        # Don't follow symlinks when classifying - a symlinked
                        # dir is skipped rather than letting the browser wander
                        # off via a link. Hidden entries are omitted.
                        if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                            # Build the child path server-side with os.path.join
                            # so it's correct on Windows (backslashes) and Linux.
                            dirs.append({"name": entry.name, "path": os.path.join(target, entry.name)})
                    except OSError:
                        continue
        except (PermissionError, OSError):
            dirs = []

        dirs_sorted = sorted(dirs, key=lambda d: d["name"].lower())
        truncated = len(dirs_sorted) > _MAX_BROWSE_DIRS
        parent = os.path.dirname(target)
        from src.tool_execution import vet_workspace
        return {
            "path": target,
            "parent": parent if parent and parent != target else None,
            "dirs": dirs_sorted[:_MAX_BROWSE_DIRS],
            "truncated": truncated,
            # Whether this directory may be bound as a workspace (filesystem
            # roots and sensitive dirs may be browsed through but not chosen).
            "selectable": vet_workspace(target) is not None,
        }

    @router.get("/vet")
    def vet(request: Request, path: str = Query(default="")):
        """Validate a workspace path without binding it.

        The UI calls this before persisting a manually typed path (/workspace
        set) so a typo, file path, deleted folder, sensitive dir, or filesystem
        root is rejected up front with the canonical path returned on success,
        instead of being stored client-side and silently dropped at chat time.
        Admin-gated like /browse: it confirms path existence on the host.
        """
        owner = _workspace_owner(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail=_VET_FORBIDDEN_DETAIL)
        from src.tool_execution import vet_workspace
        resolved = vet_workspace(path)
        return {"ok": resolved is not None, "path": resolved}

    @router.post("/session")
    async def set_for_session(request: Request):
        """Persist (or clear) the active workspace on one chat session.

        This is what makes the setting shared instead of per-browser: the
        picker and ``/workspace set|clear`` call it so other clients that open
        the same chat see the same workspace, and the agent's
        ``manage_workspace`` tool writes through the same store. The path is
        vetted with the same ``vet_workspace`` used at chat-bind time.
        """
        owner = _workspace_owner(request)
        if not owner_is_admin_or_single_user(owner):
            raise HTTPException(status_code=403, detail=_VET_FORBIDDEN_DETAIL)

        payload = await _request_payload(request)
        session_id = str(payload.get("session_id") or payload.get("session") or "").strip()
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")

        # Owner-scoped: never let one user rewrite another user's chat.
        from routes.session_routes import _verify_session_owner
        _verify_session_owner(request, session_id)

        from src.workspace_store import set_session_workspace
        clear = str(payload.get("clear") or "").strip().lower() in ("1", "true", "yes", "on")
        raw_path = str(payload.get("path") or "").strip()
        if clear or not raw_path:
            set_session_workspace(session_id, "")
            return {"ok": True, "path": None, "workspace": "", "cleared": True}

        from src.tool_execution import vet_workspace
        resolved = vet_workspace(raw_path)
        if not resolved:
            return {
                "ok": False,
                "path": None,
                "workspace": "",
                "error": (
                    f"'{raw_path}' is not a usable workspace folder. It must be an "
                    "existing directory, not a filesystem root or a sensitive path."
                ),
            }
        if not set_session_workspace(session_id, resolved):
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        return {"ok": True, "path": resolved, "workspace": resolved}

    return router


async def _request_payload(request: Request) -> dict:
    """Read a JSON object or form fields from a POST body, tolerating both."""
    data = {}
    try:
        body = await request.json()
        if isinstance(body, dict):
            data.update(body)
    except Exception:
        pass
    if not data:
        try:
            form = await request.form()
            data.update({key: value for key, value in form.items()})
        except Exception:
            pass
    return data
