import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = bool(shutil.which("node"))


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_brand_applies_logo_favicon_manifest_and_preserves_live_session_name():
    values = _node_eval(
        r"""
        let manifestBlob = null;
        globalThis.Blob = class { constructor(parts, options) { this.parts = parts; this.options = options; } };
        globalThis.URL = {
          createObjectURL(blob) { manifestBlob = blob; return 'blob:brand'; },
          revokeObjectURL() {},
        };
        globalThis.window = {
          CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
          dispatchEvent() {},
        };
        const styleValues = {};
        const classes = {};
        const currentMeta = { id: 'current-meta', textContent: 'Quarterly planning' };
        const name = { textContent: '' };
        const logo = { hidden: true, src: '', removeAttribute() {} };
        const fallback = { hidden: false };
        const placeholder = { setAttribute(key, value) { this[key] = value; } };
        const links = {
          icon: { href: '' }, apple: { href: '' }, manifest: { href: '/static/manifest.json' },
        };
        const doc = {
          title: '',
          documentElement: {
            style: { setProperty(key, value) { styleValues[key] = value; } },
            classList: { toggle(key, value) { classes[key] = value; } },
          },
          querySelector(selector) {
            if (selector === "link[rel='icon']") return links.icon;
            if (selector === "link[rel='apple-touch-icon']") return links.apple;
            if (selector === "link[rel='manifest']") return links.manifest;
            return null;
          },
          querySelectorAll(selector) {
            return {
              '[data-brand-name]': [name],
              '[data-brand-chat-name]': [currentMeta],
              '[data-brand-message-placeholder]': [placeholder],
              '[data-brand-logo]': [logo],
              '[data-brand-logo-fallback]': [fallback],
            }[selector] || [];
          },
        };
        const { applyBrand } = await import('./static/js/brand.js');
        const image = 'data:image/png;base64,iVBORw0KGgo=';
        applyBrand({ name: 'Panda Node', logo: image, accent: '#ABCDEF' }, doc, '/');
        const manifest = JSON.parse(manifestBlob.parts[0]);
        console.log(JSON.stringify({
          name: name.textContent,
          session: currentMeta.textContent,
          logo: logo.src,
          fallbackHidden: fallback.hidden,
          placeholder: placeholder.placeholder,
          title: doc.title,
          accent: styleValues['--brand-color'],
          hasLogo: classes['has-instance-logo'],
          favicon: links.icon.href,
          manifestHref: links.manifest.href,
          manifestName: manifest.name,
          manifestIcon: manifest.icons[0].src,
          manifestAccent: manifest.theme_color,
        }));
        """
    )

    assert values == {
        "name": "Panda Node",
        "session": "Quarterly planning",
        "logo": "data:image/png;base64,iVBORw0KGgo=",
        "fallbackHidden": True,
        "placeholder": "Message Panda Node...",
        "title": "Panda Node Chat",
        "accent": "#abcdef",
        "hasLogo": True,
        "favicon": "data:image/png;base64,iVBORw0KGgo=",
        "manifestHref": "blob:brand",
        "manifestName": "Panda Node",
        "manifestIcon": "data:image/png;base64,iVBORw0KGgo=",
        "manifestAccent": "#abcdef",
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_brand_validation_admin_save_remove_and_fastapi_error_formatting():
    values = _node_eval(
        r"""
        const { saveBrand, validateBrand } = await import('./static/js/brand.js');
        const calls = [];
        const doc = {
          title: '',
          documentElement: { style: { setProperty() {} }, classList: { toggle() {} } },
          querySelector() { return null; },
          querySelectorAll() { return []; },
        };
        const fetchOk = async (url, options) => {
          calls.push({ url, options });
          return { ok: true, json: async () => JSON.parse(options.body) };
        };
        const removed = await saveBrand(
          { name: '  My Harness  ', logo: '', accent: '#A1B2C3' }, fetchOk, doc,
        );
        let categoryError = '';
        try { validateBrand({ name: 'bad\u200dname', logo: '', accent: '#e06c75' }); }
        catch (error) { categoryError = error.message; }
        let detailError = '';
        try {
          await saveBrand(
            { name: 'Valid', logo: '', accent: '#e06c75' },
            async () => ({
              ok: false,
              status: 422,
              json: async () => ({ detail: [{ msg: 'Value error, name must be visible' }] }),
            }),
            doc,
          );
        } catch (error) { detailError = error.message; }
        console.log(JSON.stringify({
          removed,
          url: calls[0].url,
          method: calls[0].options.method,
          credentials: calls[0].options.credentials,
          body: JSON.parse(calls[0].options.body),
          categoryError,
          detailError,
        }));
        """
    )

    assert values["removed"] == {"name": "My Harness", "logo": "", "accent": "#a1b2c3"}
    assert values["url"] == "/api/admin/brand"
    assert values["method"] == "PUT"
    assert values["credentials"] == "same-origin"
    assert values["body"] == values["removed"]
    assert "visible characters" in values["categoryError"]
    assert values["detailError"] == "name must be visible"


def test_login_branding_is_first_run_only_and_fail_open_after_successful_login():
    login = (ROOT / "static" / "login.html").read_text(encoding="utf-8")
    assert "setupBrandGroup.hidden = m !== 'setup'" in login
    assert "brandModule = await import('/static/js/brand.js')" in login
    assert "[login-brand] using defaults" in login
    assert "await fetch('/api/version')" not in login
    assert "fetch('/api/version')\n    .then" in login
    save_start = login.index("if (setupBrandDraft) {")
    finish = login.index("finishLogin();", save_start)
    save_block = login[save_start:finish]
    assert "await brandModule.saveBrand(setupBrandDraft)" in save_block
    assert "catch (error)" in save_block
    assert "setup identity was not saved" in save_block


def test_static_brand_defaults_admin_gate_and_theme_logo_precedence():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "static" / "manifest.json").read_text(encoding="utf-8"))
    theme = (ROOT / "static" / "js" / "theme.js").read_text(encoding="utf-8")
    service_worker = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    middleware = (ROOT / "core" / "middleware.py").read_text(encoding="utf-8")

    assert 'class="admin-card admin-only" id="settings-brand-card"' in index
    assert manifest["name"] == manifest["short_name"] == "Pandamonium"
    assert "const instanceBrand = window._instanceBrand" in theme
    assert "if (instanceBrand?.logo)" in theme
    assert "window.addEventListener('instance-brand-changed'" in theme
    assert "'/static/js/brand.js'" in service_worker
    assert '"manifest-src \'self\' blob:; "' in middleware


def test_high_visibility_app_copy_uses_runtime_brand_name():
    files = {
        name: (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
        for name in (
            "slashCommands.js",
            "settings.js",
            "chat.js",
            "document.js",
            "emailLibrary.js",
            "cookbook.js",
        )
    }

    for source in files.values():
        assert "getBrandName" in source

    assert "role.textContent = 'Pandamonium'" not in files["slashCommands.js"]
    assert "Welcome to Pandamonium" not in files["slashCommands.js"]
    assert '<div class="role">Pandamonium</div>' not in files["chat.js"]
    assert "Failed to attach from Pandamonium" not in files["document.js"]
    assert "Show Pandamonium reminder emails" not in files["emailLibrary.js"]
    assert "Pandamonium app itself" not in files["cookbook.js"]
    assert "install Pandamonium keys" not in files["cookbook.js"]
