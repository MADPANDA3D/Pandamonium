from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_shell_exposes_pandamonium_brand_and_accessible_portal_entrypoint():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    theme = (ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")
    portal = (ROOT / "static" / "js" / "madMcp.js").read_text(encoding="utf-8")
    login = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
    manifest = (ROOT / "static" / "manifest.json").read_text(encoding="utf-8")

    assert '<title>Pandamonium</title>' in index
    assert 'class="sidebar-brand-logo"' in index
    assert '>Pandamonium</span>' in index
    assert 'id="tool-mad-mcp-btn"' in index
    assert 'id="mad-mcp-modal"' in index
    assert 'type="password"' in index
    assert 'autocomplete="off"' in index
    assert 'aria-live="polite"' in index
    assert "font-size: 14px" in styles
    assert "pandamonium:" in theme
    assert "const DEFAULT_THEME = 'pandamonium'" in theme
    assert "https://portal.madpanda3d.com/api/mcp" not in portal
    assert "Pandamonium — Sign In" in login
    assert "Welcome to your private AI control plane" in login
    assert '"name": "Pandamonium"' in manifest


def test_public_shell_packages_required_visual_assets():
    assert (ROOT / "static" / "icons" / "pandamonium.png").is_file()
    assert (
        ROOT
        / "static"
        / "vendor"
        / "organic-sphere"
        / "social"
        / "share-1200x630.png"
    ).is_file()

    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!static/vendor/organic-sphere/social/share-1200x630.png" in ignore
