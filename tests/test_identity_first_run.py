"""First-run identity is visible, resumable, and safe to project."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import routes.auth_routes as auth_routes


REPO_ROOT = Path(__file__).resolve().parent.parent


class _AuthManager:
    signup_enabled = False

    def status(self, _token):
        return {
            "configured": True,
            "authenticated": True,
            "username": "operator",
            "is_admin": True,
        }

    def get_privileges(self, _username):
        return {"can_use_agent": True}

    def is_admin(self, _username):
        return True


def _route(router, path):
    return next(route.endpoint for route in router.routes if route.path == path)


def test_authenticated_status_projects_resumable_identity_without_constitution(monkeypatch):
    monkeypatch.setattr(auth_routes, "migrate_from_settings", lambda: None)
    monkeypatch.setattr(auth_routes, "agent_identity_status", lambda: {
        "agent_id": "atlas",
        "display_name": "Atlas",
        "constitution_version": "2026.1",
        "status": "healthy",
        "source": "configured",
        "fallback_reasons": [],
    })
    endpoint = _route(auth_routes.setup_auth_routes(_AuthManager()), "/api/auth/status")

    result = asyncio.run(endpoint(SimpleNamespace(cookies={})))

    assert result["agent_identity"] == {
        "agent_id": "atlas",
        "display_name": "Atlas",
        "constitution_version": "2026.1",
        "status": "healthy",
        "source": "configured",
        "fallback_reasons": [],
    }
    assert "constitution" not in result["agent_identity"]


def test_public_ui_reuses_authenticated_identity_settings_and_guides_setup():
    html = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
    settings_js = (REPO_ROOT / "static" / "js" / "settings.js").read_text(encoding="utf-8")
    app_js = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    setup_js = (REPO_ROOT / "static" / "js" / "slashCommands.js").read_text(encoding="utf-8")

    for element_id in (
        "set-agentIdentityCard",
        "set-agentId",
        "set-agentDisplayName",
        "set-agentConstitution",
        "set-agentConstitutionVersion",
        "set-agentIdentitySave",
    ):
        assert f'id="{element_id}"' in html

    for setting_key in (
        "agent_id",
        "agent_display_name",
        "agent_constitution",
        "agent_constitution_version",
    ):
        assert setting_key in settings_js

    assert "Make Pandamonium yours" in app_js
    assert "Name the persistent agent" in app_js
    assert "Model engine" in app_js
    assert "Integrations" in app_js
    assert "pandamonium-first-run-dismissed" in app_js
    assert "settingsModule.open(definition.tab)" in app_js
    assert "Set up Pandamonium" in setup_js
    assert "Pandamonium is the harness" in setup_js
    assert "setup-guide-action" in setup_js
    assert "_showSetupOverview()" in setup_js
