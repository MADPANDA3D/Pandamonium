"""Regression coverage for the registry-backed Plugins sidebar."""

from pathlib import Path
from types import SimpleNamespace

from routes.extension_routes import public_extension_catalog

ROOT = Path(__file__).resolve().parent.parent


def test_plugins_sidebar_uses_sanitized_extension_registry_projection():
    registry = SimpleNamespace(snapshot=lambda: {
        "extensions": {
            "oracle": {
                "enabled": True,
                "manifest": {
                    "name": "ORACLE",
                    "runtime": {"type": "web", "entrypoint": "/private/path"},
                    "source": {"url": "https://example.invalid/private.git"},
                },
            },
            "robin": {
                "enabled": False,
                "manifest": {"name": "Robin", "runtime": {"type": "mcp"}},
            },
        }
    })

    assert public_extension_catalog(registry) == {"plugins": [
        {"id": "oracle", "name": "ORACLE", "state": "enabled", "runtime": "web"},
        {"id": "robin", "name": "Robin", "state": "disabled", "runtime": "mcp"},
    ]}

    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="plugins-section"' in index
    assert 'id="plugins-list"' in index
    assert 'id="add-plugins-btn"' in index
    assert 'id="marketplace-modal"' in index
    assert 'aria-haspopup="dialog"' in index
    assert "/api/extensions/catalog" in app
    assert "marketplaceModule.init" in app
    assert "applyExtensionSurfaceControl" in app
