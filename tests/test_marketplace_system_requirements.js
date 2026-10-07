const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const marketplace = fs.readFileSync(
  path.join(__dirname, '../static/js/marketplace.js'), 'utf8');
const setupUi = fs.readFileSync(
  path.join(__dirname, '../static/js/setupUi.js'), 'utf8');
const style = fs.readFileSync(
  path.join(__dirname, '../static/style.css'), 'utf8');

// Prerequisite report and one-shot sudo provisioning are wired.
assert.match(marketplace, /function renderSystemRequirements\(/);
assert.match(marketplace, /function provisionSystemRequirements\(/);
assert.match(marketplace, /function sudoPrompt\(/);
assert.match(marketplace, /\/api\/extensions\/system-requirements/);
assert.match(marketplace, /capabilities: requirements\.missing, password/);
assert.match(marketplace, /input\.type = 'password'/);
assert.match(marketplace, /input\.autocomplete = 'off'/);

// A consumed approval must not be re-clickable into authority_decision_not_pending.
assert.match(marketplace, /let approvalConsumed = false;/);
assert.match(marketplace, /approvalConsumed = true;/);
assert.match(marketplace, /if \(approvalConsumed\)/);
assert.match(marketplace, /actions\.replaceChildren\(\)/);

// Raw authority/system codes get user-facing copy instead of leaking.
assert.match(setupUi, /authority_decision_not_pending:/);
assert.match(setupUi, /authority_execution_context_unavailable:/);
assert.match(setupUi, /system_requirements_container_unsupported:/);
assert.match(setupUi, /system_requirements_provision_failed:/);

// The password must never be written to a console.
assert.doesNotMatch(marketplace, /console\.(log|error|warn|info)\([^)]*password/i);

// The sudo modal and requirement panel are styled.
assert.match(style, /\.marketplace-system-requirements\b/);
assert.match(style, /\.marketplace-sudo-overlay\b/);

console.log('marketplace system requirements wiring: ok');
