// Shared setup-surface helper node checks (MAD-925).
//
// Loads static/js/setupUi.js the same way tests/test_model_connect.js does, so
// the copy/error mapping is exercised without a browser.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'static/js/setupUi.js'),
    'utf8',
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
}

async function main() {
  globalThis.window = {};
  const mod = await loadModule();

  assert.equal(mod.MANAGED_BY_ADMIN_COPY, 'Managed by your administrator');
  assert.equal(mod.MODEL_SETUP_ENTRY_LABEL, 'Connect a model engine');

  // Known raw codes become a message plus next step.
  assert.match(mod.humanSetupError('extension_scan_source_invalid'), /https:\/\//);
  assert.match(mod.humanSetupError('extension_action_denied'), /not approved/i);
  assert.match(mod.humanSetupError('extension_action_denied'), /approve|administrator/i);
  assert.match(mod.humanSetupError('extension_action_failed'), /try again/i);
  assert.match(mod.humanSetupError('updater_lock_held'), /already running/i);
  assert.match(mod.humanSetupError('update_failed'), /previous release is still active/i);
  assert.match(mod.humanSetupError('extension_manifest_invalid'), /manifest/i);
  assert.match(mod.humanSetupError('extension_skill_frontmatter_malformed'), /skill/i);
  assert.match(mod.humanSetupError('extension_git_url_not_public'), /public/i);
  assert.match(mod.humanSetupError('extension_mcp_validation_required'), /runtime|validation/i);
  assert.match(mod.humanSetupError('extension_already_installed_use_upgrade'), /Installed plugins/);
  assert.match(mod.humanSetupError('extension_adapter_required:skills:skill_bundle'), /support/i);
  assert.match(mod.humanSetupError('extension_lifecycle_install_invalid'), /not supported/i);

  // Unknown code-shaped details never leak the code.
  const fallback = "Something went wrong during setup. Check the connection and try again.";
  assert.equal(mod.humanSetupError('extension_mystery_code'), fallback);
  assert.equal(mod.humanSetupError('manifest_digest_mismatch'), fallback);
  assert.match(mod.humanSetupError('marketplace_http_404'), /catalog/i);
  assert.equal(mod.humanSetupError(''), fallback);

  // Every code the backend can raise resolves to real copy, not the fallback.
  const backendFiles = fs
    .readdirSync(path.join(__dirname, '..', 'src'))
    .filter((name) => /^extension_.*\.py$/.test(name))
    .map((name) => path.join(__dirname, '..', 'src', name));
  backendFiles.push(path.join(__dirname, '..', 'routes', 'extension_routes.py'));
  const codePattern =
    /(?:ExtensionLifecycleError|ExtensionScanError|ExtensionContractError|MarketplaceCatalogError|HTTPException)\([^)\n]*?["']([a-z][a-z0-9_]+)["']/g;
  const backendCodes = new Set();
  for (const file of backendFiles) {
    for (const match of fs.readFileSync(file, 'utf8').matchAll(codePattern)) {
      backendCodes.add(match[1]);
    }
  }
  assert.ok(backendCodes.size > 80, `expected many backend codes, found ${backendCodes.size}`);
  for (const code of backendCodes) {
    assert.notEqual(mod.humanSetupError(code), fallback, `no human copy for ${code}`);
  }

  // Human text passes through unchanged.
  assert.equal(mod.humanSetupError('GitHub sign-in failed.'), 'GitHub sign-in failed.');
  assert.equal(mod.humanSetupError({ detail: 'Try again shortly.' }), 'Try again shortly.');

  // The entry opens the wizard at the model step; admin detection defaults on.
  const calls = [];
  globalThis.window.setupWizardModule = { open: (options) => calls.push(options) };
  assert.equal(mod.isAdminSurface(), true);
  assert.equal(mod.openModelSetupWizard(), true);
  assert.deepEqual(calls, [{ step: 'model' }]);
  globalThis.window._isAdmin = false;
  assert.equal(mod.isAdminSurface(), false);

  // Missing wizard degrades to false instead of throwing.
  globalThis.window.setupWizardModule = {};
  assert.equal(mod.openModelSetupWizard(), false);

  console.log('setupUi node checks passed');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
