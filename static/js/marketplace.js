import { humanSetupError } from './setupUi.js';

const API_BASE = window.location.origin;

let modal;
let launcher;
let search;
let category;
let results;
let summary;
let workspace;
let detail;
let detailContent;
let plugins = [];
let selectedId = null;
let previousFocus = null;
let loadGeneration = 0;
let actionGeneration = 0;
let scanUrl;
let scanButton;
let scanCancel;
let scanProgress;
let scanTitle;
let scanDetail;
let scanFill;
let scanPhases;
let scanResults;
let scanStatus;
let scanTimer = null;
let scanInFlight = false;
let scanGeneration = 0;
let scanStartedAt = null;
let scanId = sessionStorage.getItem('pandamonium-intake-scan');
let installedPlugins = [];
let installedSelectedId = null;
let installedList;
let installedSummary;
let installedView;
let installedDetail;
let installedDetailContent;
let installedBack;
let tabInstalled;
let tabMarketplace;
let tabAdd;
let panelInstalled;
let panelMarketplace;
let panelAdd;
let activeTab = 'installed';
const SCAN_PHASES = ['fetch', 'classify', 'extract', 'audit', 'understand', 'package', 'report'];
const REPO_CLASS_LABELS = {
  skill_bundle: 'a skill package',
  mcp_server: 'an MCP server',
  python_cli: 'a Python tool',
  node_cli: 'a Node tool',
  web_app: 'a web app',
  openapi: 'an OpenAPI service',
  go_cli: 'a Go command-line tool',
  go_module: 'a Go module',
  rust_cli: 'a Rust command-line tool',
  rust_lib: 'a Rust library',
  service: 'a container service',
  unknown: 'an unclassified repository',
};
const INSTALLABLE_REPO_CLASSES = new Set([
  'skill_bundle', 'mcp_server', 'python_cli', 'node_cli', 'web_app', 'openapi',
]);
const SCAN_POLL_INTERVAL_MS = 900;

const labels = {
  available: 'Available',
  installed: 'Installed',
  update_available: 'Update available',
  disabled: 'Disabled',
  incompatible: 'Incompatible',
  revoked: 'Revoked',
  deprecated: 'Deprecated',
};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function badge(text, tone = '') {
  return element('span', `marketplace-badge${tone ? ` is-${tone}` : ''}`, text);
}

function close() {
  if (!modal || modal.classList.contains('hidden')) return;
  stopScanPolling();
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  workspace?.classList.remove('has-detail');
  (previousFocus?.isConnected ? previousFocus : launcher)?.focus();
}

const TAB_ORDER = ['installed', 'marketplace', 'add'];

function setTab(tab, focus = false) {
  activeTab = TAB_ORDER.includes(tab) ? tab : 'installed';
  [
    [tabInstalled, panelInstalled, 'installed'],
    [tabMarketplace, panelMarketplace, 'marketplace'],
    [tabAdd, panelAdd, 'add'],
  ].forEach(([button, panel, name]) => {
    button?.setAttribute('aria-selected', String(activeTab === name));
    if (button) button.tabIndex = activeTab === name ? 0 : -1;
    if (panel) panel.hidden = activeTab !== name;
  });
  if (focus) ({ installed: tabInstalled, marketplace: tabMarketplace, add: tabAdd })[activeTab]?.focus({ preventScroll: true });
}

function showInstalledList() {
  installedSelectedId = null;
  renderInstalled();
  if (installedDetail) installedDetail.hidden = true;
  if (installedView) installedView.hidden = false;
}

function renderState(title, message) {
  results.replaceChildren();
  const state = element('div', 'marketplace-state');
  state.append(element('strong', '', title), element('span', '', message));
  results.append(state);
  summary.textContent = message;
}

function prependResultsNotice(title, message) {
  const state = element('div', 'marketplace-state');
  state.append(element('strong', '', title), element('span', '', message));
  results.prepend(state);
}

function renderCategories() {
  const selected = category.value;
  const values = [...new Set(plugins.flatMap(plugin => plugin.categories || []))].sort();
  category.replaceChildren(new Option('All categories', ''));
  values.forEach(value => category.append(new Option(value.replaceAll('-', ' '), value)));
  if (values.includes(selected)) category.value = selected;
}

function statusBadges(plugin) {
  const nodes = [];
  const installation = plugin.installation?.state || 'available';
  if (installation !== 'available') {
    nodes.push(badge(labels[installation] || installation, installation === 'update_available' ? 'warning' : 'positive'));
  }
  if (plugin.availability !== 'available') {
    nodes.push(badge(labels[plugin.availability] || plugin.availability, plugin.availability === 'revoked' ? 'danger' : 'warning'));
  }
  if (plugin.compatibility?.state === 'incompatible' && plugin.availability !== 'incompatible') {
    nodes.push(badge('Incompatible', 'danger'));
  } else if (plugin.compatibility?.state === 'compatible') {
    nodes.push(badge('Compatible', 'positive'));
  }
  if (plugin.origin === 'intake') {
    nodes.push(badge('GitHub intake', 'warning'));
  } else if (plugin.origin === 'configured') {
    nodes.push(badge('Configured', 'warning'));
  } else {
    nodes.push(badge('Verified', 'positive'));
  }
  return nodes;
}

function pluginMatches(plugin) {
  const query = search.value.trim().toLowerCase();
  const selectedCategory = category.value;
  const haystack = [
    plugin.name, plugin.summary, plugin.publisher?.name, plugin.license,
    ...(plugin.categories || []),
  ].join(' ').toLowerCase();
  return (!query || haystack.includes(query))
    && (!selectedCategory || plugin.categories?.includes(selectedCategory));
}

function renderCards() {
  const visible = plugins.filter(pluginMatches);
  results.replaceChildren();
  summary.textContent = `${visible.length} of ${plugins.length} plugin${plugins.length === 1 ? '' : 's'}`;
  if (!visible.length) return renderState('No matches', 'Try another search or category.');

  visible.forEach(plugin => {
    const card = element('button', 'marketplace-card');
    card.type = 'button';
    card.dataset.pluginId = plugin.id;
    card.setAttribute('aria-pressed', String(plugin.id === selectedId));
    const head = element('div', 'marketplace-card-head');
    head.append(element('strong', '', plugin.name), element('span', '', `v${plugin.version}`));
    const badges = element('div', 'marketplace-card-badges');
    badges.append(...statusBadges(plugin));
    const facts = element('div', 'marketplace-card-facts');
    const icon = element('span', 'marketplace-plugin-icon', plugin.icon || '◈');
    icon.setAttribute('aria-hidden', 'true');
    head.prepend(icon);
    facts.append(element('span', '', (plugin.categories || []).join(' · ')));
    card.append(head, element('p', 'marketplace-card-summary', plugin.summary), facts, renderReadiness(plugin), badges);
    card.addEventListener('click', () => selectPlugin(plugin.id));
    results.append(card);
  });
}

function renderInstalled() {
  if (!installedList) return;
  installedList.replaceChildren();
  if (!installedPlugins.length) {
    installedList.append(element('span', 'marketplace-installed-empty', 'No plugins installed.'));
    if (installedSummary) installedSummary.textContent = 'Nothing installed yet';
    return;
  }
  if (installedSummary) {
    installedSummary.textContent = `${installedPlugins.length} plugin${installedPlugins.length === 1 ? '' : 's'}`;
  }
  installedPlugins.forEach(plugin => {
    const row = element('button', `marketplace-installed-row${plugin.origin === 'configured' ? ' is-configured' : ''}`);
    row.type = 'button';
    row.dataset.installedId = plugin.id;
    row.setAttribute('aria-pressed', String(plugin.id === installedSelectedId));
    const head = element('div', 'marketplace-card-head');
    const icon = element('span', 'marketplace-plugin-icon', plugin.icon || '◈');
    icon.setAttribute('aria-hidden', 'true');
    head.append(icon, element('strong', '', plugin.name));
    row.append(head, element('p', 'marketplace-card-summary', plugin.summary || 'Open to inspect capabilities and setup.'), element('span', 'marketplace-categories', (plugin.categories || []).join(' · ')), renderReadiness(plugin));
    row.addEventListener('click', () => selectInstalled(plugin.id));
    installedList.append(row);
  });
}

async function loadInstalled(generation) {
  try {
    const payload = await api('/api/extensions/installed');
    if (generation !== loadGeneration) return;
    installedPlugins = Array.isArray(payload.plugins) ? payload.plugins : [];
    renderInstalled();
  } catch (error) {
    if (generation !== loadGeneration) return;
    installedPlugins = [];
    installedList?.replaceChildren(element('span', 'marketplace-installed-empty', humanSetupError(error, 'Installed plugins unavailable.')));
    if (installedSummary) installedSummary.textContent = 'Unavailable';
  }
}

function renderReadiness(plugin) {
  const value = plugin.readiness || { state: plugin.state === 'disabled' ? 'disabled' : 'needs_setup', message: 'Install or enable to verify setup and capabilities.' };
  const names = { ready: 'Ready', needs_setup: 'Needs setup', preparing: 'Preparing', failed: 'Failed', disabled: 'Disabled' };
  const section = element('div', 'marketplace-readiness');
  section.dataset.readiness = value.state;
  section.setAttribute('role', 'status');
  section.append(badge(names[value.state] || 'Needs setup', value.state === 'ready' ? 'positive' : value.state === 'failed' ? 'danger' : 'warning'), element('p', '', value.message));
  return section;
}

function productSections(container, plugin) {
  const capabilities = detailSection('What you can do');
  capabilities.append(listOrNone(plugin.capability_summaries || plugin.capabilities,
    item => `${item.description || item.name}${item.description ? ` — ${item.name}` : ''}${item.kind === 'skill' ? ' · skill' : ''}`));
  const examples = detailSection('Try asking');
  if (plugin.examples?.length) examples.append(listOrNone(plugin.examples, value => value));
  else examples.append(element('p', '', 'No usage examples were provided with this package.'));
  const setup = detailSection('Setup');
  const requirements = [...(plugin.requirements || []), ...(plugin.configuration || []).map(item => `${item.description}${item.required ? ' (required)' : ' (optional)'}`)];
  setup.append(requirements.length ? listOrNone(requirements, value => value) : element('p', '', 'No configuration declared. Installation still validates available capabilities.'));
  if ((plugin.capability_summaries || []).some(item => item.kind === 'skill')) {
    setup.append(element('p', '', 'This bundle also appears in Memory → Skills after installation. Skills provide guidance and supporting files.'));
  }
  container.append(capabilities, examples, setup);
  return setup;
}

function technicalDetails() {
  const details = element('details', 'marketplace-technical');
  details.append(element('summary', '', 'Technical details'));
  return details;
}

function pluginHeading(plugin) {
  const heading = element('div', 'marketplace-product-heading');
  const icon = element('span', 'marketplace-plugin-icon', plugin.icon || '◈');
  icon.setAttribute('aria-hidden', 'true');
  heading.append(icon, element('h3', '', plugin.name), element('p', 'marketplace-card-summary', plugin.summary || 'Open this integration to inspect its capabilities and setup.'), element('p', 'marketplace-categories', (plugin.categories || []).join(' · ')));
  return heading;
}

function renderInstalledDetail(payload) {
  installedDetailContent.replaceChildren(pluginHeading(payload), renderReadiness(payload));
  const setup = productSections(installedDetailContent, payload);
  if (payload.origin !== 'configured' && payload.configuration?.length) {
    renderRuntimeSetup(setup, payload.id, null, () => selectInstalled(payload.id));
  }
  if (payload.origin !== 'configured') {
    renderInstalledLifecycle(payload);
    installedDetailContent.append(renderPublication(`/api/extensions/installed/${encodeURIComponent(payload.id)}/publish`, payload.version));
  }
  const diagnostics = technicalDetails();
  const identity = detailSection('Identity');
  appendFacts(identity, [['Origin', payload.origin], ['State', payload.state], ['Version', payload.version || 'unversioned'], ['Runtime', payload.runtime || 'unknown'], ['Descriptor', payload.descriptor || 'unknown'], ['Revision', payload.source_revision || 'not recorded']]);
  const capabilities = detailSection('Capabilities and tools');
  capabilities.append(listOrNone(payload.capabilities, item => `${item.name} · ${item.kind} · ${item.permission_mode}${item.description ? ` — ${item.description}` : ''}`));
  const permissions = detailSection('Permissions and data boundaries');
  permissions.append(listOrNone([`Default: ${payload.permissions?.default || 'unknown'}`, ...Object.entries(payload.permissions?.capabilities || {}).map(([name, mode]) => `${name}: ${mode}`), ...['read', 'write', 'network'].map(kind => `${kind}: ${(payload.data_boundaries?.[kind] || []).join(', ') || 'none'}`)], value => value));
  const configuration = detailSection('Configuration keys');
  configuration.append(listOrNone(payload.configuration, item => `${item.key}${item.required ? ' · required' : ' · optional'}${item.secret ? ' · secret' : ''} — ${item.description}`));
  diagnostics.append(identity, capabilities, permissions, configuration);
  if (payload.notes?.length) diagnostics.append(listOrNone(payload.notes, value => value));
  installedDetailContent.append(diagnostics);
}

async function renderRuntimeSetup(container, id, planId, onSaved) {
  const status = element('p', 'marketplace-action-status', 'Reading setup…');
  status.setAttribute('role', 'status');
  container.append(status);
  try {
    const url = `/api/extensions/runtime/${encodeURIComponent(id)}/configuration`;
    const setup = await api(url + (planId ? `?plan_id=${encodeURIComponent(planId)}` : ''));
    const form = element('form', 'marketplace-runtime-setup');
    const inputs = [];
    for (const field of setup.fields || []) {
      const label = element('label', '', `${field.key} · ${field.description}${field.secret ? ' · secret' : ''}${field.required ? ' (required)' : ''}`);
      const input = element(field.key === 'ENDPOINT_ID' ? 'select' : 'input');
      if (field.key === 'ENDPOINT_ID') {
        label.prepend(element('strong', '', 'Deployment node / connected service — '));
        const empty = element('option', '', 'Choose a configured endpoint');
        empty.value = '';
        input.append(empty);
        for (const target of setup.targets || []) {
          const option = element('option', '', `${target.name} · ${target.kind} · ${target.base_url}`);
          option.value = target.id;
          input.append(option);
        }
      } else {
        input.type = field.secret ? 'password' : 'text';
        input.autocomplete = 'off';
        input.maxLength = 8192;
      }
      input.name = field.key;
      input.value = field.value || '';
      input.required = field.required && !field.configured;
      if (field.secret && field.configured) input.placeholder = 'Saved — leave blank to keep';
      label.append(input);
      form.append(label);
      inputs.push([field, input]);
    }
    if (setup.targets) form.append(element('p', '', 'Choose the node running this service. Add or edit its address and credential in Settings → Endpoints. Installation verifies a real operation before connecting voice.'));
    const save = element('button', 'marketplace-action-primary', 'Save setup');
    save.type = 'submit';
    form.append(save);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      save.disabled = true;
      try {
        const values = Object.fromEntries(inputs.filter(([field, input]) => !field.secret || input.value).map(([field, input]) => [field.key, input.value || null]));
        await api(url, { method: 'PUT', body: JSON.stringify({ values, ...(planId ? { plan_id: planId } : {}) }) });
        inputs.filter(([field]) => field.secret).forEach(([, input]) => { input.value = ''; });
        status.textContent = 'Saved. Revalidating setup requires a fresh install or enable preview.';
        await onSaved();
      } catch (error) {
        status.textContent = humanSetupError(error);
      } finally { save.disabled = false; }
    });
    container.append(form);
    status.textContent = 'Configuration is editable. Saving disables an installed package until it passes validation again.';
    return (setup.fields || []).some(field => field.required && !field.configured);
  } catch (error) {
    status.textContent = humanSetupError(error);
    return true;
  }
}

function renderInstalledLifecycle(payload) {
  const section = detailSection('Manage runtime');
  const status = element('p', 'marketplace-action-status');
  status.setAttribute('role', 'status');
  const actions = element('div', 'marketplace-action-buttons');
  for (const operation of [payload.state === 'enabled' ? 'disable' : 'enable', 'uninstall']) {
    const button = element('button', '', actionLabel(operation));
    button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const plan = await api('/api/extensions/plans/lifecycle', { method: 'POST', body: JSON.stringify({ operation, extension_id: payload.id }) });
        const approve = element('button', 'marketplace-action-primary', `Approve ${actionLabel(operation).toLowerCase()} once`);
        approve.type = 'button';
        approve.addEventListener('click', () => executeAction(plan, payload, operation, status, actions));
        actions.replaceChildren(approve);
        status.textContent = operation === 'uninstall' ? 'Remove this package and its tools. Retained user data stays available.' : 'Review and approve this runtime change.';
      } catch (error) { status.textContent = humanSetupError(error); button.disabled = false; }
    });
    actions.append(button);
  }
  section.append(actions, status);
  installedDetailContent.append(section);
}

async function selectInstalled(id) {
  installedSelectedId = id;
  selectedId = null;
  renderInstalled();
  renderCards();
  installedView.hidden = true;
  installedDetail.hidden = false;
  installedDetailContent.replaceChildren();
  const state = element('div', 'marketplace-state');
  state.append(element('strong', '', 'Loading plugin…'), element('span', '', 'Reading the installed record.'));
  installedDetailContent.append(state);
  try {
    const payload = await api(`/api/extensions/installed/${encodeURIComponent(id)}`);
    if (installedSelectedId !== id || activeTab !== 'installed') return;
    renderInstalledDetail(payload);
  } catch (error) {
    installedDetailContent.replaceChildren();
    const failure = element('div', 'marketplace-state');
    failure.append(element('strong', '', 'Plugin detail unavailable'), element('span', '', humanSetupError(error, 'Try again in a moment.')));
    installedDetailContent.append(failure);
  }
  if (installedSelectedId === id && activeTab === 'installed') installedDetail.focus();
}

function appendFacts(container, facts) {
  const list = element('dl', 'marketplace-facts');
  facts.forEach(([term, value]) => {
    list.append(element('dt', '', term));
    const description = element('dd');
    if (value instanceof Node) description.append(value);
    else description.textContent = value;
    list.append(description);
  });
  container.append(list);
}

function detailSection(title) {
  const section = element('section', 'marketplace-detail-section');
  section.append(element('h4', '', title));
  return section;
}

function externalLink(text, href) {
  const link = element('a', '', text);
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  return link;
}

function listOrNone(values, formatter) {
  const list = element('ul');
  if (!values?.length) {
    list.append(element('li', '', 'None'));
    return list;
  }
  values.forEach(value => list.append(element('li', '', formatter(value))));
  return list;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(humanSetupError(payload.detail || `marketplace_http_${response.status}`));
  return payload;
}

function stopScanPolling() {
  if (scanTimer !== null) {
    clearTimeout(scanTimer);
    scanTimer = null;
  }
}

function renderScanPhases(stage, state) {
  const activeIndex = Math.max(0, SCAN_PHASES.indexOf(stage));
  scanPhases?.querySelectorAll('li').forEach(item => {
    const index = SCAN_PHASES.indexOf(item.dataset.phase);
    let itemState = 'pending';
    if (state === 'complete') itemState = 'complete';
    else if (index < activeIndex) itemState = 'complete';
    else if (index === activeIndex) itemState = state === 'error' ? 'failed' : 'working';
    item.dataset.state = itemState;
  });
}

function setScanProgress({ state, title, detail, progress = 0, stage = 'fetch' }) {
  if (!scanProgress) return;
  const bounded = Math.max(0, Math.min(100, Number(progress) || 0));
  scanProgress.hidden = false;
  scanProgress.dataset.state = state;
  if (scanTitle) scanTitle.textContent = title;
  if (scanDetail) scanDetail.textContent = detail;
  if (scanFill) scanFill.style.width = `${bounded}%`;
  scanProgress.querySelector('.updater-progress-meter')?.setAttribute('aria-valuenow', String(bounded));
  renderScanPhases(stage, state);
}

function renderScanArtifact(artifact) {
  scanResults.replaceChildren();
  scanResults.scrollTop = 0;
  scanResults.hidden = false;

  const plugin = { requirements: artifact.integration?.requirements || [], configuration: artifact.integration?.setup || [], ...artifact.plugin, name: artifact.draft_manifest?.name || 'Repository scan' };
  if (!plugin.summary && artifact.integration) plugin.summary = artifact.integration.purpose;
  scanResults.append(pluginHeading(plugin), renderReadiness({ readiness: { state: artifact.draft_manifest ? 'needs_setup' : 'failed', message: artifact.draft_manifest ? 'Package prepared. Operations have not been executed or verified. Install to complete setup and validation.' : 'No installable integration was produced. Review the findings below.' } }));
  productSections(scanResults, plugin);
  const diagnostics = technicalDetails();
  const heading = detailSection('Scan result');
  heading.append(listOrNone(plugin.configuration, item => `${item.key}: ${item.description}`));
  if (artifact.integration?.validation) heading.append(listOrNone(artifact.integration.validation, item => item));
  const classLabel = REPO_CLASS_LABELS[artifact.repo_class] || `a ${artifact.repo_class} repository`;
  heading.append(element('p', '', `Repository classified as ${classLabel} at revision ${(artifact.source_revision || '').slice(0, 12)}…`));
  appendFacts(heading, [
    ['Artifact digest', artifact.artifact_digest || 'unavailable'],
    ['Files scanned', String(artifact.bounds?.files_scanned ?? 0)],
    ['Bytes scanned', String(artifact.bounds?.bytes_scanned ?? 0)],
    ['No execution', 'Static scan only — no repository build or install command ran'],
  ]);
  diagnostics.append(heading);

  const capabilities = detailSection('Extracted capabilities');
  capabilities.append(listOrNone(artifact.capabilities, item => `${item.name} · ${item.kind} · ${item.descriptor} — ${item.evidence_path}`));
  diagnostics.append(capabilities);

  const findings = detailSection('Findings');
  if (artifact.findings?.length) {
    const list = element('ul');
    artifact.findings.forEach(item => {
      const tone = item.severity === 'critical' || item.severity === 'high'
        ? 'danger' : item.severity === 'medium' ? 'warning' : 'positive';
      const row = element('li');
      row.append(badge(item.severity, tone), element('span', '', ` ${item.category} · ${item.title}${item.evidence ? ` — ${item.evidence}` : ''}`));
      list.append(row);
    });
    findings.append(list);
  } else {
    findings.append(listOrNone([], value => value));
  }
  diagnostics.append(findings);

  const inventory = detailSection('Dependencies and licenses');
  inventory.append(
    listOrNone(artifact.dependencies, item => `${item.ecosystem}: ${item.name}${item.version ? ` ${item.version}` : ''}`),
    listOrNone(artifact.licenses, value => value),
  );
  diagnostics.append(inventory);

  const draft = detailSection('Install');
  if (artifact.draft_manifest) {
    const actions = element('div', 'marketplace-action-buttons');
    const install = element('button', 'marketplace-action-primary', 'Install plugin…');
    install.type = 'button';
    install.addEventListener('click', () => prepareSourceAction(artifact, draft, actions));
    actions.append(install);
    draft.append(renderPublication(`/api/extensions/scans/${encodeURIComponent(scanId)}/publish`));
    draft.append(
      actions,
      element('p', 'marketplace-action-status', 'Nothing is installed yet — the next step shows the approval preview before anything changes.'),
    );
  } else if (INSTALLABLE_REPO_CLASSES.has(artifact.repo_class)) {
    draft.append(element('p', '', 'This repository did not produce an installable draft manifest; install stays unavailable.'));
  } else {
    const classLabel = REPO_CLASS_LABELS[artifact.repo_class] || `a ${artifact.repo_class} repository`;
    draft.append(element('p', '', `This looks like ${classLabel} — Pandamonium scanned its dependencies, licenses, and findings but does not install this repository type as a plugin yet. Nothing was ingested.`));
  }
  scanResults.append(draft, diagnostics);
}

async function pollScan(scanId, generation) {
  if (scanInFlight || generation !== scanGeneration) return;
  scanInFlight = true;
  try {
    const job = await api(`/api/extensions/scans/${encodeURIComponent(scanId)}`);
    if (generation !== scanGeneration) return;
    const stopped = ['failed', 'cancelled'].includes(job.status);
    const state = job.status === 'succeeded' ? 'complete' : stopped ? 'error' : 'working';
    if (scanCancel) scanCancel.hidden = state !== 'working';
    scanButton.disabled = state === 'working';
    const elapsed = scanStartedAt && state === 'working'
      ? ` · ${Math.max(1, Math.round((Date.now() - scanStartedAt) / 1000))}s`
      : '';
    setScanProgress({
      state,
      title: job.status === 'succeeded' ? 'Scan complete' : stopped ? 'Scan stopped' : `${job.message || 'Scanning…'}${elapsed}`,
      detail: job.status === 'succeeded'
        ? 'Review this package’s capabilities and setup below.'
        : humanSetupError(job.error || job.message || ''),
      progress: job.progress,
      stage: job.stage,
    });
    if (job.status === 'succeeded' && job.artifact) {
      renderScanArtifact(job.artifact);
      scanStatus.textContent = 'Review the extracted capabilities and findings before installing.';
      return;
    }
    if (stopped) {
      scanStatus.textContent = `Scan stopped: ${humanSetupError(job.error || job.message || 'unknown error')}`;
      return;
    }
  } catch (error) {
    setScanProgress({ state: 'error', title: 'Scan unavailable', detail: humanSetupError(error), progress: 0, stage: 'fetch' });
    scanStatus.textContent = `Scan request failed: ${humanSetupError(error)}`;
    scanButton.disabled = false;
    scanCancel.hidden = true;
    return;
  } finally {
    scanInFlight = false;
  }
  scanTimer = setTimeout(() => pollScan(scanId, generation), SCAN_POLL_INTERVAL_MS);
}

async function startSourceScan() {
  const url = scanUrl.value.trim();
  scanStatus.textContent = '';
  scanResults.hidden = true;
  scanResults.replaceChildren();
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:') throw new Error('extension_scan_source_invalid');
  } catch {
    setScanProgress({ state: 'error', title: 'Invalid repository URL', detail: 'Use a public https:// repository URL.', progress: 0, stage: 'fetch' });
    return;
  }
  stopScanPolling();
  const generation = ++scanGeneration;
  scanId = null;
  scanStartedAt = Date.now();
  scanButton.disabled = true;
  setScanProgress({ state: 'working', title: 'Starting scan…', detail: url, progress: 0, stage: 'fetch' });
  try {
    const job = await api('/api/extensions/scans', {
      method: 'POST',
      body: JSON.stringify({ source_url: url }),
    });
    if (generation !== scanGeneration) return;
    scanId = job.scan_id || null;
    if (scanId) sessionStorage.setItem('pandamonium-intake-scan', scanId);
    scanCancel.hidden = false;
    pollScan(job.scan_id, generation);
  } catch (error) {
    setScanProgress({ state: 'error', title: 'Scan unavailable', detail: humanSetupError(error), progress: 0, stage: 'fetch' });
  } finally {
    scanButton.disabled = Boolean(scanId);
  }
}

async function prepareSourceAction(artifact, section, actions) {
  actions.querySelectorAll('button').forEach(button => { button.disabled = true; });
  scanStatus.textContent = 'Preparing install preview…';
  try {
    const request = { operation: 'install', source_url: artifact.source_url, ref: artifact.source_revision };
    if (scanId) request.scan_id = scanId;
    const plan = await api('/api/extensions/plans/source', {
      method: 'POST',
      body: JSON.stringify(request),
    });
    section.querySelector('.marketplace-action-preview')?.remove();
    const manifest = plan.manifest || {};
    const preview = element('div', 'marketplace-action-preview');
    preview.append(
      element('strong', '', `Approval required: Install ${manifest.name || plan.extension_id}`),
      element('p', '', [
        `revision ${(plan.source_revision || '').slice(0, 12)}…`,
        `${Object.keys(plan.requested_permissions?.capabilities || {}).length} declared permission overrides`,
        `${Object.values(plan.lifecycle_commands || {}).flat().length} lifecycle command entries`,
        plan.manifest_origin === 'scan_draft'
          ? 'generated draft manifest (repository has no jarvis-extension.json)'
          : plan.manifest_origin === 'scan_package' ? 'prepared source package' : 'repository manifest',
        'static scan completed before install',
      ].join(' · ')),
    );
    if (plan.execution_recipe) {
      if (plan.execution_recipe.voice_model) preview.append(element('p', '', `After its operation check, connect the selected node to voice using ${plan.execution_recipe.voice_model}. You can change it in Settings.`));
      const recipe = element('details', '');
      recipe.append(
        element('summary', '', 'Private runtime setup and operation checks'),
        element('p', '', 'Runs after approval in an isolated Linux runtime. Source stays read-only; runtime files are preserved on removal.'),
        element('p', '', plan.manifest?.data_boundaries?.network?.length
          ? 'Network access is enabled for setup and tool calls. Declared destinations are descriptive; this runtime does not enforce a destination allowlist.'
          : 'Network access is disabled for setup and tool calls.'),
        element('pre', '', JSON.stringify(plan.execution_recipe, null, 2)),
      );
      preview.append(recipe);
    }
    const approvalActions = element('div', 'marketplace-action-buttons');
    const approve = element('button', 'marketplace-action-primary', 'Approve once');
    approve.type = 'button';
    approve.addEventListener('click', () => executeAction(
      plan,
      { id: plan.extension_id, name: manifest.name || plan.extension_id },
      'install',
      scanStatus,
      approvalActions,
    ));
    const cancel = element('button', '', '← Back to scan');
    cancel.type = 'button';
    cancel.addEventListener('click', () => { preview.remove(); scanStatus.textContent = 'Install cancelled. Back at the scan result.'; });
    approvalActions.append(approve, cancel);
    preview.append(
      approvalActions,
      element('p', 'marketplace-action-status', 'Nothing has been installed yet. Approve once to install this exact revision, or go back to the scan result.'),
    );
    section.append(preview);
    if (manifest.configuration?.length) {
      const setup = detailSection('Required setup');
      preview.prepend(setup);
      approve.disabled = await renderRuntimeSetup(setup, plan.extension_id, plan.plan_id,
        () => prepareSourceAction(artifact, section, actions));
    }
    scanStatus.textContent = 'Review the exact pinned revision, then approve once or go back.';
    preview.scrollIntoView({ block: 'center' });
    approve.focus({ preventScroll: true });
  } catch (error) {
    scanStatus.textContent = `Install preview unavailable: ${humanSetupError(error)}`;
    actions.querySelectorAll('button').forEach(button => { button.disabled = false; });
  }
}

function actionOptions(plugin) {
  const installation = plugin.installation || {};
  const actions = [];
  if (plugin.origin === 'configured') return actions;
  if (!installation.current_version && plugin.availability === 'available') {
    actions.push(['install', 'Install']);
  } else if (installation.current_version) {
    if (installation.update_available && plugin.availability === 'available') actions.push(['upgrade', 'Update']);
    if (installation.enabled) actions.push(['disable', 'Disable']);
    else if (plugin.availability === 'available') actions.push(['enable', 'Enable']);
    if (plugin.rollback?.available_revisions?.length) actions.push(['rollback', 'Rollback']);
    actions.push(['uninstall', 'Remove']);
  }
  return actions;
}

function actionLabel(operation) {
  return { install: 'Install', upgrade: 'Update', enable: 'Enable', disable: 'Disable', rollback: 'Rollback', uninstall: 'Remove' }[operation] || operation;
}

async function executeAction(plan, plugin, operation, status, actions) {
  const installedAction = installedSelectedId === plugin.id;
  actions.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try {
    const decision = plan.authority_decision || {};
    if (decision.decision === 'approval_required') {
      await api(`/api/authority/decisions/${encodeURIComponent(decision.decision_id)}`, {
        method: 'POST', body: JSON.stringify({ choice: 'approve', scope: 'once' }),
      });
    } else if (decision.decision !== 'allow') {
      throw new Error('extension_action_denied');
    }
    status.textContent = `Preparing · ${actionLabel(operation)} in progress…`;
    status.dataset.readiness = 'preparing';
    const result = await api(`/api/extensions/plans/${encodeURIComponent(plan.plan_id)}/execute`, { method: 'POST' });
    if (result.result?.status !== 'succeeded') throw new Error('extension_action_failed');
    window.dispatchEvent(new Event('pandamonium:extensions-changed'));
    await load();
    if (installedAction) {
      if (operation === 'uninstall') showInstalledList();
      else await selectInstalled(plugin.id);
    }
    status.textContent = `${actionLabel(operation)} completed.`;
    summary.textContent = `${plugin.name}: ${actionLabel(operation)} completed.`;
  } catch (error) {
    status.textContent = `Failed · ${actionLabel(operation)}: ${humanSetupError(error)}`;
    status.dataset.readiness = 'failed';
    actions.querySelectorAll('button').forEach(button => { button.disabled = false; });
  }
}

async function prepareAction(plugin, operation, section, status, actions) {
  const generation = ++actionGeneration;
  actions.querySelectorAll('button').forEach(button => { button.disabled = true; });
  status.textContent = `Preparing ${actionLabel(operation).toLowerCase()} preview…`;
  try {
    const plan = await api('/api/extensions/marketplace/plans', {
      method: 'POST',
      body: JSON.stringify({
        operation,
        extension_id: plugin.id,
        ...(operation === 'install' || operation === 'upgrade' ? { version: plugin.version } : {}),
      }),
    });
    if (generation !== actionGeneration || selectedId !== plugin.id) return;
    section.querySelector('.marketplace-action-preview')?.remove();
    const preview = element('div', 'marketplace-action-preview');
    const artifact = plan.marketplace?.artifact;
    const removal = plan.removal || plugin.removal || {};
    preview.append(
      element('strong', '', `Approval required: ${actionLabel(operation)} ${plugin.name}`),
      element('p', '', [
        artifact ? `Verified sha256:${artifact.sha256}` : null,
        plan.marketplace?.target_version
          ? `${plan.marketplace.current_version || 'not installed'} → ${plan.marketplace.target_version}`
          : null,
        `${(plan.marketplace?.dependencies || plugin.dependencies || []).length} declared dependencies`,
        `${(plan.marketplace?.configuration || plugin.configuration || []).length} declared configuration keys`,
        `${plan.marketplace?.restart_required || plugin.restart_required || 'none'} restart`,
        operation === 'uninstall' ? `Delete now: ${(removal.deleted_paths || []).join(', ') || 'no user data'}; retain: ${(removal.retained_paths || []).join(', ') || 'all user data'}; package archived for recovery` : null,
      ].filter(Boolean).join(' · ')),
    );
    const approvalActions = element('div', 'marketplace-action-buttons');
    const approve = element('button', 'marketplace-action-primary', 'Approve once');
    approve.type = 'button';
    approve.addEventListener('click', () => executeAction(plan, plugin, operation, status, approvalActions));
    const cancel = element('button', '', 'Cancel');
    cancel.type = 'button';
    cancel.addEventListener('click', () => renderDetail(plugin));
    approvalActions.append(approve, cancel);
    preview.append(approvalActions);
    actions.replaceChildren();
    section.append(preview);
    status.textContent = 'Review the exact signed package, data, and restart scope before approval.';
    if (plan.manifest?.configuration?.length && ['install', 'upgrade', 'enable'].includes(operation)) {
      const setup = detailSection('Runtime setup');
      preview.prepend(setup);
      approve.disabled = await renderRuntimeSetup(setup, plan.extension_id, plan.plan_id,
        () => prepareAction(plugin, operation, section, status, actions));
    }
    approve.focus();
  } catch (error) {
    status.textContent = `${actionLabel(operation)} unavailable: ${humanSetupError(error)}`;
    actions.querySelectorAll('button').forEach(button => { button.disabled = false; });
  }
}

function renderActions(plugin) {
  const section = detailSection('Manage plugin');
  const status = element('p', 'marketplace-action-status', 'Choose an action to preview its exact approval scope.');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const actions = element('div', 'marketplace-action-buttons');
  actionOptions(plugin).forEach(([operation, label]) => {
    const button = element('button', operation === 'install' || operation === 'upgrade' ? 'marketplace-action-primary' : '', label);
    button.type = 'button';
    button.addEventListener('click', () => prepareAction(plugin, operation, section, status, actions));
    actions.append(button);
  });
  if (!actions.children.length) status.textContent = 'No lifecycle action is available for this package state.';
  section.append(status, actions);
  return section;
}

function renderPublication(endpoint, currentVersion = '1.0.0') {
  const section = detailSection('Publish to marketplace');
  const status = element('p', 'marketplace-action-status', 'Validate in disposable state, then sign and publish this package. Account configuration and runtime data stay private.');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const versionLabel = element('label', '', 'Package version ');
  const version = element('input');
  version.type = 'text';
  version.value = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/.test(currentVersion) ? currentVersion : '1.0.0';
  version.setAttribute('aria-label', 'Marketplace package version');
  versionLabel.append(version);
  const offer = element('button', '', 'Add to marketplace');
  offer.type = 'button';
  offer.addEventListener('click', async () => {
    offer.disabled = true;
    status.textContent = 'Preparing publication…';
    try {
      let result = await api(endpoint, { method: 'POST', body: JSON.stringify({ version: version.value.trim() }) });
      while (result.state === 'publishing') {
        status.textContent = result.message;
        await new Promise(resolve => setTimeout(resolve, 1000));
        if (!section.isConnected) return;
        result = await api(`/api/extensions/publications/${encodeURIComponent(result.id)}`);
      }
      if (result.state !== 'published') throw new Error(`${result.stage ? result.stage + ': ' : ''}${result.message || 'Publication was not confirmed. Retry safely.'}`);
      status.textContent = 'Published — the tested package is available in the shared marketplace.';
      offer.textContent = 'Published';
      section.querySelectorAll('.marketplace-facts').forEach(node => node.remove());
      appendFacts(section, [['Package SHA-256', result.digest], ['Version', result.version]]);
      const link = element('a', '', 'Published artifact');
      link.href = result.artifact_url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      section.append(link);
      await api('/api/extensions/marketplace/refresh', { method: 'POST' }).catch(() => {});
    } catch (error) {
      status.textContent = `Publication failed: ${humanSetupError(error)}`;
      offer.disabled = false;
      offer.textContent = 'Retry Add to marketplace';
    }
  });
  section.append(status, versionLabel, offer);
  return section;
}

function renderDetail(plugin) {
  detailContent.replaceChildren();
  const isCatalogEntry = !plugin.origin;
  detailContent.append(pluginHeading(plugin), renderReadiness(plugin));
  const setup = productSections(detailContent, plugin);
  if (plugin.installation?.current_version && plugin.configuration?.length && plugin.origin !== 'configured') {
    renderRuntimeSetup(setup, plugin.id, null, () => load());
  }
  detailContent.append(renderActions(plugin));
  if (plugin.origin === 'intake') detailContent.append(renderPublication(`/api/extensions/installed/${encodeURIComponent(plugin.id)}/publish`, plugin.version));
  const diagnostics = technicalDetails();
  detailContent.append(diagnostics);
  const provenance = detailSection('Package and provenance');
  const publisherLink = plugin.publisher?.url
    ? externalLink(plugin.publisher.name, plugin.publisher.url)
    : plugin.publisher?.name || 'Unknown';
  const sourceLink = plugin.provenance?.source_url
    ? externalLink(plugin.provenance.source_url, plugin.provenance.source_url)
    : plugin.origin === 'intake'
      ? 'Recorded at install time (source URL stays off this list)'
      : 'Not applicable';
  const provenanceFacts = [
    ['Publisher', publisherLink],
    ['License', plugin.license],
    ['Source', sourceLink],
    ['Revision', plugin.provenance?.source_revision || 'Unavailable'],
  ];
  if (isCatalogEntry) {
    provenanceFacts.push(
      ['Digest', `sha256:${plugin.provenance?.sha256 || 'unavailable'}`],
      ['Signature', 'Catalog + artifact verified'],
      ['Review', `${labels[plugin.review?.status] || plugin.review?.status} · ${plugin.review?.reviewer || 'unknown reviewer'}`],
    );
  } else {
    provenanceFacts.push(
      ['Digest', plugin.provenance?.sha256 ? `sha256:${plugin.provenance.sha256}` : 'Not recorded'],
      ['Signature', plugin.origin === 'intake' ? 'Not catalog-signed — installed from GitHub intake' : 'Not applicable'],
      ['Review', plugin.origin === 'intake' ? 'Locally reviewed intake scan' : 'Configured surface'],
    );
  }
  appendFacts(provenance, provenanceFacts);
  diagnostics.append(provenance);

  const compatibility = detailSection('Compatibility and installation');
  const installation = plugin.installation || {};
  if (isCatalogEntry) {
    appendFacts(compatibility, [
      ['Compatibility', `${labels[plugin.compatibility?.state] || plugin.compatibility?.state} with Pandamonium ${plugin.compatibility?.pandamonium_min}–${plugin.compatibility?.pandamonium_max}`],
      ['Platforms', (plugin.compatibility?.platforms || []).join(', ')],
      ['Architectures', (plugin.compatibility?.architectures || []).join(', ')],
      ['Installed state', labels[installation.state] || installation.state],
      ['Version', installation.current_version ? `${installation.current_version} installed · ${installation.target_version} published` : `${installation.target_version} published`],
      ['Restart', plugin.restart_required === 'none' ? 'No restart' : `${plugin.restart_required} restart required`],
    ]);
  } else {
    appendFacts(compatibility, [
      ['Origin', plugin.origin === 'intake' ? 'GitHub intake' : 'Configured surface'],
      ['Installed state', labels[installation.state] || installation.state],
      ['Version', installation.current_version || 'unversioned'],
      ['Restart', 'No restart'],
    ]);
  }
  diagnostics.append(compatibility);

  const permissions = detailSection('Permissions and data boundaries');
  const permissionItems = [`Default: ${plugin.permissions?.default || 'unknown'}`];
  Object.entries(plugin.permissions?.capabilities || {}).forEach(([name, mode]) => permissionItems.push(`${name}: ${mode}`));
  const boundaries = plugin.permissions?.data_boundaries || {};
  ['read', 'write', 'network'].forEach(kind => {
    const values = boundaries[kind] || [];
    permissionItems.push(`${kind}: ${values.length ? values.join(', ') : 'none'}`);
  });
  permissions.append(listOrNone(permissionItems, value => value));
  diagnostics.append(permissions);

  const dependencies = detailSection('Dependencies');
  dependencies.append(listOrNone(plugin.dependencies, item => `${item.id} ${item.minimum_version}–${item.maximum_version}${item.optional ? ' · optional' : ''} · ${item.dependency_type}`));
  diagnostics.append(dependencies);

  const configuration = detailSection('Configuration keys');
  configuration.append(listOrNone(plugin.configuration, item => `${item.key} · ${item.required ? 'required' : 'optional'}${item.secret ? ' · secret reference' : ''} — ${item.description}`));
  diagnostics.append(configuration);

  const removal = detailSection('Removal and rollback');
  removal.append(listOrNone([
    `Declared removable paths: ${(plugin.removal?.remove_paths || []).join(', ') || 'none'}`,
    `Declared preserve paths: ${(plugin.removal?.preserve_paths || []).join(', ') || 'all user data'}`,
    'Removal defaults to retaining user data and archives the package for recovery',
    `Retained revisions: ${plugin.rollback?.retain_revisions || 0}`,
  ], value => value));
  diagnostics.append(removal);

  if (plugin.review?.security_advisories?.length) {
    const advisories = detailSection('Security advisories');
    advisories.append(listOrNone(plugin.review.security_advisories, item => `${item.id} · ${item.severity} — ${item.summary}`));
    diagnostics.append(advisories);
  }
}

function selectPlugin(id, focus = true) {
  const plugin = plugins.find(item => item.id === id);
  if (!plugin) return;
  selectedId = id;
  installedSelectedId = null;
  renderInstalled();
  renderCards();
  renderDetail(plugin);
  workspace.classList.add('has-detail');
  if (focus) detail.focus();
}

function installedMarketplaceEntry(plugin) {
  const intake = plugin.origin !== 'configured';
  return {
    id: plugin.id,
    name: plugin.name,
    version: plugin.version || 'unversioned',
    summary: plugin.summary,
    categories: plugin.categories || [],
    icon: plugin.icon,
    examples: plugin.examples || [],
    requirements: plugin.requirements || [],
    capability_summaries: plugin.capability_summaries || [],
    readiness: plugin.readiness,
    availability: 'available',
    publisher: { name: intake ? 'GitHub intake' : 'Configured surface', url: null },
    license: 'Not catalog-signed',
    provenance: { source_url: null, source_revision: plugin.source_revision || '', sha256: '' },
    compatibility: { state: 'unknown' },
    installation: {
      state: plugin.state === 'enabled' ? 'installed' : plugin.state,
      current_version: plugin.version || '',
      target_version: plugin.version || '',
      enabled: plugin.state === 'enabled',
      update_available: false,
    },
    permissions: plugin.permissions || { default: 'read_only', capabilities: {}, data_boundaries: { read: [], write: [], network: [] } },
    dependencies: [],
    configuration: plugin.configuration || [],
    restart_required: 'none',
    removal: {},
    rollback: {},
    review: { status: intake ? 'intake' : 'configured', reviewer: 'local install' },
    origin: intake ? 'intake' : 'configured',
  };
}

function mergeInstalledIntoMarketplace(catalogPlugins) {
  const known = new Set(catalogPlugins.map(plugin => plugin.id));
  const extras = installedPlugins
    .filter(plugin => !known.has(plugin.id))
    .map(installedMarketplaceEntry);
  return [...catalogPlugins.map(plugin => ({ ...plugin, readiness: installedPlugins.find(item => item.id === plugin.id)?.readiness || { state: 'needs_setup', message: 'Not installed in this account. Install to complete setup and validation.' } })), ...extras];
}

async function load() {
  const generation = ++loadGeneration;
  selectedId = null;
  workspace.classList.remove('has-detail');
  detailContent.replaceChildren();
  renderState('Loading plugins…', 'Verifying the signed catalog and local registry.');
  await loadInstalled(generation);
  if (generation !== loadGeneration) return;
  try {
    const response = await fetch(`${API_BASE}/api/extensions/marketplace`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`marketplace_http_${response.status}`);
    const payload = await response.json();
    if (generation !== loadGeneration) return;
    const catalogPlugins = Array.isArray(payload.plugins) ? payload.plugins : [];
    plugins = mergeInstalledIntoMarketplace(catalogPlugins);
    const hasInstalledExtras = plugins.length > catalogPlugins.length;
    renderCategories();
    if (payload.status === 'offline' && !hasInstalledExtras) return renderState('Marketplace offline', 'No verified catalog is available. Refresh after connectivity or catalog configuration is restored.');
    if (payload.status === 'error' && !hasInstalledExtras) return renderState('Catalog verification failed', humanSetupError(payload.failure || 'The marketplace catalog could not be verified.'));
    if (payload.status === 'empty' && !hasInstalledExtras) return renderState('No plugins published', 'The verified catalog is empty. Installed plugins remain unchanged.');
    renderCards();
    if (payload.channel?.state === 'last_known_good') prependResultsNotice('Using verified cache', payload.channel.message);
    if (payload.status === 'offline') {
      prependResultsNotice('Marketplace offline', 'The signed catalog is unavailable — showing installed plugins.');
    } else if (payload.status === 'error') {
      prependResultsNotice('Catalog verification failed', humanSetupError(payload.failure || 'The marketplace catalog could not be verified.'));
    } else if (payload.status === 'empty') {
      prependResultsNotice('No plugins published', 'The verified catalog is empty — showing installed plugins.');
    }
    if (hasInstalledExtras && payload.status !== 'ready') {
      summary.textContent = 'Signed catalog unavailable · showing installed plugins';
    }
  } catch (error) {
    if (generation !== loadGeneration) return;
    plugins = mergeInstalledIntoMarketplace([]);
    renderCategories();
    renderCards();
    if (plugins.length) {
      prependResultsNotice('Marketplace unavailable', humanSetupError(error, 'The marketplace request failed.'));
      summary.textContent = 'Signed catalog unavailable · showing installed plugins';
    } else {
      renderState('Marketplace unavailable', humanSetupError(error, 'The marketplace request failed.'));
    }
  }
}

function open() {
  previousFocus = document.activeElement;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  search.value = '';
  category.value = '';
  setTab('installed');
  showInstalledList();
  scanGeneration += 1;
  stopScanPolling();
  scanResults.hidden = true;
  scanResults.replaceChildren();
  scanStatus.textContent = '';
  scanProgress.hidden = true;
  if (scanId) pollScan(scanId, scanGeneration);
  load();
  requestAnimationFrame(() => tabInstalled?.focus({ preventScroll: true }));
}

function trapFocus(event) {
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopImmediatePropagation();
    close();
    return;
  }
  if (event.key !== 'Tab') return;
  const focusable = [...modal.querySelectorAll('button:not([disabled]):not([tabindex="-1"]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, a[href]')]
    .filter(node => node.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function init() {
  modal = document.getElementById('marketplace-modal');
  launcher = document.getElementById('add-plugins-btn');
  search = document.getElementById('marketplace-search');
  category = document.getElementById('marketplace-category');
  document.getElementById('marketplace-retry')?.addEventListener('click', async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await api('/api/extensions/marketplace/refresh', { method: 'POST' });
      await load();
    } catch (error) {
      summary.textContent = humanSetupError(error);
    } finally {
      button.disabled = false;
    }
  });
  results = document.getElementById('marketplace-results');
  summary = document.getElementById('marketplace-summary');
  workspace = document.getElementById('marketplace-workspace');
  detail = document.getElementById('marketplace-detail');
  detailContent = document.getElementById('marketplace-detail-content');
  scanUrl = document.getElementById('marketplace-source-url');
  scanButton = document.getElementById('marketplace-source-scan');
  if (scanButton) {
    scanCancel = element('button', 'marketplace-action-secondary', 'Cancel scan');
    scanCancel.type = 'button';
    scanCancel.hidden = true;
    scanButton.after(scanCancel);
    scanCancel.addEventListener('click', async () => {
      scanCancel.disabled = true;
      try {
        await api(`/api/extensions/scans/${encodeURIComponent(scanId)}/cancel`, { method: 'POST' });
        scanStatus.textContent = 'Scan cancelled. Nothing was installed.';
      } catch (error) {
        scanStatus.textContent = humanSetupError(error);
      } finally {
        scanCancel.disabled = false;
      }
    });
  }
  scanProgress = document.getElementById('marketplace-scan-progress');
  scanTitle = document.getElementById('marketplace-scan-title');
  scanDetail = document.getElementById('marketplace-scan-detail');
  scanFill = document.getElementById('marketplace-scan-fill');
  scanPhases = document.getElementById('marketplace-scan-phases');
  scanResults = document.getElementById('marketplace-scan-results');
  scanStatus = document.getElementById('marketplace-scan-status');
  installedList = document.getElementById('marketplace-installed-list');
  installedSummary = document.getElementById('marketplace-installed-summary');
  installedView = document.getElementById('marketplace-installed-view');
  installedDetail = document.getElementById('marketplace-installed-detail');
  installedDetailContent = document.getElementById('marketplace-installed-detail-content');
  installedBack = document.getElementById('marketplace-installed-back');
  tabInstalled = document.getElementById('marketplace-tab-installed');
  tabMarketplace = document.getElementById('marketplace-tab-marketplace');
  tabAdd = document.getElementById('marketplace-tab-add');
  panelInstalled = document.getElementById('marketplace-panel-installed');
  panelMarketplace = document.getElementById('marketplace-panel-marketplace');
  panelAdd = document.getElementById('marketplace-panel-add');
  if (!modal || !launcher || !search || !category || !results || !summary || !workspace || !detail || !detailContent || !installedDetailContent) return;
  launcher.addEventListener('click', open);
  document.getElementById('close-marketplace-modal')?.addEventListener('click', close);
  scanButton?.addEventListener('click', startSourceScan);
  scanUrl?.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      startSourceScan();
    }
  });
  tabInstalled?.addEventListener('click', () => setTab('installed'));
  tabMarketplace?.addEventListener('click', () => setTab('marketplace'));
  tabAdd?.addEventListener('click', () => setTab('add'));
  modal.querySelector('.marketplace-tabs')?.addEventListener('keydown', event => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const index = TAB_ORDER.indexOf(activeTab);
    const next = event.key === 'ArrowRight' ? (index + 1) % TAB_ORDER.length : (index - 1 + TAB_ORDER.length) % TAB_ORDER.length;
    setTab(TAB_ORDER[next], true);
  });
  installedBack?.addEventListener('click', () => {
    const previous = installedSelectedId;
    showInstalledList();
    installedList?.querySelector(`[data-installed-id="${CSS.escape(previous || '')}"]`)?.focus();
  });
  document.getElementById('marketplace-back')?.addEventListener('click', () => {
    workspace.classList.remove('has-detail');
    results.querySelector(`[data-plugin-id="${CSS.escape(selectedId || '')}"]`)?.focus();
  });
  const filterCards = () => { workspace.classList.remove('has-detail'); renderCards(); };
  search.addEventListener('input', filterCards);
  category.addEventListener('change', filterCards);
  modal.addEventListener('keydown', trapFocus);
  modal.addEventListener('click', event => {
    if (event.target === modal) close();
  });
}

export default { init, open, close };
