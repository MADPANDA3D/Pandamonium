// static/js/setupUi.js
// Shared copy, routing, and error mapping for setup surfaces (MAD-925).
// Copy and routing only — no backend contract. Import-free so the node copy
// tests can load it directly; the wizard is resolved from window at call time.

export const MANAGED_BY_ADMIN_COPY = 'Managed by your administrator';
export const MODEL_SETUP_ENTRY_LABEL = 'Connect a model engine';

// Raw backend codes never belong in a user-facing setup error. Known codes get
// a message plus next step; any other code-shaped detail falls back to generic
// recovery copy instead of leaking the code.
const SETUP_ERROR_MESSAGES = {
  extension_scan_source_invalid:
    'That repository link is not supported. Use a public https:// link to a GitHub repository, then start the scan again.',
  extension_scan_not_found:
    'That scan session expired. Start a new scan to continue.',
  extension_scan_unavailable:
    'That scan is not finished yet. Wait for it to complete, then install from its result.',
  extension_scan_source_mismatch:
    'This install does not match the scanned repository. Start a new scan and install from its result.',
  extension_scan_revision_mismatch:
    'This scan is pinned to a different revision. Start a new scan and install from its result.',
  extension_scan_binding_invalid:
    'The install preview could not be matched to its scan. Start a new scan and install from its result.',
  extension_scan_draft_not_allowed:
    'That install request mixed a signed package with a scan draft. Refresh the plugin list and try again.',
  extension_scan_manifest_mismatch:
    'The reviewed draft manifest could not be verified. Start a new scan and install from its result.',
  extension_scan_manifest_source_mismatch:
    'The reviewed draft manifest does not belong to this repository. Start a new scan and install from its result.',
  extension_manifest_missing:
    'This repository has no jarvis-extension.json, and no reviewed scan draft was available. Scan the repository, then install from the scan result.',
  extension_scan_bounds_exceeded:
    'That repository is too large to scan. Follow its own install instructions instead.',
  extension_scan_capability_duplicate:
    'That repository declares the same capability more than once, so no manifest could be reviewed. Report it to the publisher, or try a different repository.',
  extension_plugin_not_found:
    'That plugin is no longer in the catalog. Refresh the plugin list and try again.',
  extension_not_installed:
    'That plugin is not installed yet. Install it first, then run the action again.',
  extension_action_denied:
    'This action was not approved. Approve the request once, or ask your administrator to allow it.',
  extension_action_failed:
    'The plugin action did not finish. Try again; if it keeps failing, check the server logs.',
  extension_manifest_invalid:
    'That plugin package did not pass its manifest check. Use a reviewed package, or ask the publisher to fix it.',
  extension_already_installed_use_upgrade:
    'This plugin is already installed. Use Update from the Installed plugins tab.',
  extension_not_installed_use_install:
    'This plugin is not installed yet. Install it first, then run the action again.',
  extension_catalog_unavailable:
    'The plugin catalog is unavailable right now. Check the connection and try again.',
  marketplace_catalog_unsigned:
    'The plugin catalog could not be verified as trusted, so it was not loaded. Check for an app update, or ask your administrator to republish it.',
  extension_health_unavailable:
    'That plugin did not report a healthy state. Restart it, or reinstall from the plugin page.',
  updater_lock_held:
    'Another update is already running. Wait for it to finish, then check for updates again.',
  update_failed:
    'The update did not finish. The previous release is still active — try again, or roll back from Settings.',
};

// Every backend family gets actionable copy, so no reachable failure lands on
// the generic fallback (MAD-953). Specific codes above always win.
const SETUP_ERROR_FAMILIES = [
  [
    /^extension_skill_/,
    'One of the skills in this repository did not pass the install checks, so nothing was installed. Start a new scan; if it keeps failing, ask the publisher to fix the skill files.',
  ],
  [
    /^extension_(?:git|checkout|source|staging)_/,
    'Pandamonium could not fetch that exact repository revision. Check that the link is public, then start a new scan and install from its result.',
  ],
  [
    /^extension_mcp_/,
    'That plugin\u2019s MCP runtime did not pass validation, so nothing was installed. Ask the publisher to fix the plugin, or try a different repository.',
  ],
  [
    /^extension_manifest_/,
    'That plugin manifest did not pass validation, so nothing was installed. Scan the repository again, or ask the publisher to fix the manifest.',
  ],
  [
    /^extension_plugin_/,
    'This repository\u2019s plugin descriptor is not supported by this version of Pandamonium. Ask the publisher to fix it, or try a different repository.',
  ],
  [
    /^extension_scan_/,
    'The repository scan did not finish cleanly. Start a new scan and install from its result.',
  ],
  [
    /^extension_(?:inventory|surface)_/,
    'That repository scan result did not pass validation. Start a new scan and install from its result.',
  ],
  [
    /^marketplace_/,
    'The plugin catalog is unavailable right now. Check the connection and try again.',
  ],
  [
    /^extension_(?:registry|capability|catalog|runtime|configuration|descriptor|permission|health|inline)_/,
    'That plugin package did not pass its validation checks, so nothing was installed. Try a reviewed package, or ask the publisher to fix it.',
  ],
  [
    /^extension_adapter_/,
    'That plugin type is not supported by this version of Pandamonium. Check for an app update, or try a different repository.',
  ],
  [
    /^extension_entrypoint_/,
    'That plugin\u2019s entrypoint could not be opened. Ask the publisher to fix the package, or try a different repository.',
  ],
  [
    /^extension_root_/,
    'That plugin asked for a storage path outside the safe area, so it cannot be installed as written.',
  ],
  [
    /^extension_rollback_/,
    'That plugin revision cannot be rolled back. Check the installed revisions and try again.',
  ],
  [
    /^extension_upgrade_/,
    'The previous plugin revision is missing, so this update cannot proceed. Install the plugin again and retry the update.',
  ],
  [
    /^extension_plan_/,
    'That install preview is no longer available. Start a new scan and try again.',
  ],
  [
    /^extension_lifecycle_/,
    'That plugin action is not supported here. Refresh the plugin list and try again.',
  ],
  [
    /^extension_owner_scope_/,
    'That plugin belongs to a different operator account on this installation. Sign in as that operator, or install your own copy.',
  ],
  [
    /^extension_(?:id|signed_manifest|resolved_catalog)_/,
    'The plugin changed or did not match its reviewed package, so nothing was installed. Start a new scan and try again.',
  ],
];

const RAW_SETUP_CODE = /^(?:extension|manifest|marketplace|updater|update)_[a-z0-9_]+(?:[:\s].*)?$/;

export function isAdminSurface() {
  return typeof window === 'undefined' ? true : window._isAdmin !== false;
}

export function openModelSetupWizard() {
  const wizard = typeof window !== 'undefined' ? window.setupWizardModule : null;
  if (wizard && typeof wizard.open === 'function') {
    wizard.open({ step: 'model' });
    return true;
  }
  return false;
}

export function createModelSetupEntry(extraClass = '') {
  const link = document.createElement('a');
  link.href = '#';
  link.className = ['accent-link', 'setup-wizard-entry', extraClass].filter(Boolean).join(' ');
  link.setAttribute('role', 'button');
  link.textContent = MODEL_SETUP_ENTRY_LABEL;
  link.title = 'Open the setup wizard at the model step';
  link.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    openModelSetupWizard();
  });
  return link;
}

export function humanSetupError(value, fallback = "Something went wrong during setup. Check the connection and try again.") {
  const raw = typeof value === 'string'
    ? value
    : (value && (value.detail || value.message)) || '';
  const text = String(raw).trim();
  if (!text) return fallback;
  if (SETUP_ERROR_MESSAGES[text]) return SETUP_ERROR_MESSAGES[text];
  for (const [pattern, message] of SETUP_ERROR_FAMILIES) {
    if (pattern.test(text)) return message;
  }
  if (RAW_SETUP_CODE.test(text)) return fallback;
  return text;
}

export default {
  MANAGED_BY_ADMIN_COPY,
  MODEL_SETUP_ENTRY_LABEL,
  isAdminSurface,
  openModelSetupWizard,
  createModelSetupEntry,
  humanSetupError,
};
