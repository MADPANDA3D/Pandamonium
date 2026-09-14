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
let scanId = null;
let installedPlugins = [];
let installedSelectedId = null;
let installedList;
let installedSummary;
let installedView;
let installedDetail;
let installedDetailContent;
let installedBack;
let tabInstalled;
let tabAdd;
let panelInstalled;
let panelAdd;
let activeTab = 'installed';
const SCAN_PHASES = ['fetch', 'classify', 'extract', 'audit', 'report'];
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

function setTab(tab, focus = false) {
  activeTab = tab === 'add' ? 'add' : 'installed';
  [
    [tabInstalled, panelInstalled, 'installed'],
    [tabAdd, panelAdd, 'add'],
  ].forEach(([button, panel, name]) => {
    button?.setAttribute('aria-selected', String(activeTab === name));
    if (panel) panel.hidden = activeTab !== name;
  });
  if (focus) (activeTab === 'add' ? tabAdd : tabInstalled)?.focus({ preventScroll: true });
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
  nodes.push(badge('Verified', 'positive'));
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
    const dependencyCount = plugin.dependencies?.length || 0;
    const restart = plugin.restart_required === 'none' ? 'No restart' : `${plugin.restart_required} restart`;
    facts.append(
      element('span', '', plugin.publisher?.name || 'Unknown publisher'),
      element('span', '', plugin.license),
      element('span', '', `${plugin.permissions?.default || 'unknown'} permission`),
      element('span', '', `${dependencyCount} dependenc${dependencyCount === 1 ? 'y' : 'ies'}`),
      element('span', '', restart),
      element('span', '', (plugin.categories || []).join(' · ')),
      element('span', '', `sha256:${(plugin.provenance?.sha256 || '').slice(0, 10)}…`),
    );
    card.append(head, badges, element('p', 'marketplace-card-summary', plugin.summary), facts);
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
    row.setAttribute('role', 'listitem');
    row.setAttribute('aria-pressed', String(plugin.id === installedSelectedId));
    row.append(
      element('strong', '', plugin.name),
      element(
        'span',
        'marketplace-installed-state',
        plugin.origin === 'configured'
          ? 'configured'
          : `${plugin.state}${plugin.capability_count ? ` · ${plugin.capability_count} cap` : ''}`,
      ),
    );
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

function renderInstalledDetail(payload) {
  installedDetailContent.replaceChildren();
  const heading = element('div');
  heading.append(element('h3', '', `${payload.name}${payload.version ? ` ${payload.version}` : ''}`));
  const badges = element('div', 'marketplace-detail-badges');
  badges.append(
    badge(
      payload.origin === 'configured' ? 'Configured' : payload.state === 'enabled' ? 'Enabled' : 'Disabled',
      payload.state === 'disabled' ? 'warning' : 'positive',
    ),
    badge(payload.runtime || 'unknown', ''),
  );
  heading.append(badges, element('p', '', payload.origin === 'configured' ? 'Configured surface' : 'Installed plugin'));
  installedDetailContent.append(heading);

  const identity = detailSection('Identity');
  appendFacts(identity, [
    ['Origin', payload.origin],
    ['Runtime', payload.runtime || 'unknown'],
    ['Descriptor', payload.descriptor || 'unknown'],
    ['Revision', payload.source_revision || 'not recorded'],
  ]);
  installedDetailContent.append(identity);

  const capabilities = detailSection('Capabilities and tools');
  capabilities.append(listOrNone(
    payload.capabilities,
    item => `${item.name} · ${item.kind} · ${item.permission_mode}${item.description ? ` — ${item.description}` : ''}`,
  ));
  installedDetailContent.append(capabilities);

  const permissions = detailSection('Permissions');
  const permissionItems = [`Default: ${payload.permissions?.default || 'unknown'}`];
  Object.entries(payload.permissions?.capabilities || {}).forEach(([name, mode]) => permissionItems.push(`${name}: ${mode}`));
  permissions.append(listOrNone(permissionItems, value => value));
  installedDetailContent.append(permissions);

  const boundaries = detailSection('Data boundaries');
  const boundary = payload.data_boundaries || {};
  boundaries.append(listOrNone(
    ['read', 'write', 'network'].map(kind => `${kind}: ${(boundary[kind] || []).length ? (boundary[kind] || []).join(', ') : 'none'}`),
    value => value,
  ));
  installedDetailContent.append(boundaries);

  const configuration = detailSection('Configuration');
  configuration.append(listOrNone(
    payload.configuration,
    item => `${item.key}${item.required ? ' · required' : ' · optional'}${item.secret ? ' · secret' : ''} — ${item.description}`,
  ));
  configuration.append(element('p', 'marketplace-action-status', 'Values live in Settings/Connections; no secret values are shown here.'));
  installedDetailContent.append(configuration);

  if (payload.notes?.length) {
    const notes = detailSection('Notes');
    notes.append(listOrNone(payload.notes, value => value));
    installedDetailContent.append(notes);
  }
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
    renderInstalledDetail(payload);
  } catch (error) {
    installedDetailContent.replaceChildren();
    const failure = element('div', 'marketplace-state');
    failure.append(element('strong', '', 'Plugin detail unavailable'), element('span', '', humanSetupError(error, 'Try again in a moment.')));
    installedDetailContent.append(failure);
  }
  installedDetail.focus();
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

  const heading = detailSection('Scan result');
  heading.append(element('p', '', `Repository classified as ${artifact.repo_class} at revision ${(artifact.source_revision || '').slice(0, 12)}…`));
  appendFacts(heading, [
    ['Artifact digest', artifact.artifact_digest || 'unavailable'],
    ['Files scanned', String(artifact.bounds?.files_scanned ?? 0)],
    ['Bytes scanned', String(artifact.bounds?.bytes_scanned ?? 0)],
    ['No execution', 'Static scan only — no repository build or install command ran'],
  ]);
  scanResults.append(heading);

  const capabilities = detailSection('Extracted capabilities');
  capabilities.append(listOrNone(artifact.capabilities, item => `${item.name} · ${item.kind} · ${item.descriptor} — ${item.evidence_path}`));
  scanResults.append(capabilities);

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
  scanResults.append(findings);

  const inventory = detailSection('Dependencies and licenses');
  inventory.append(
    listOrNone(artifact.dependencies, item => `${item.ecosystem}: ${item.name}${item.version ? ` ${item.version}` : ''}`),
    listOrNone(artifact.licenses, value => value),
  );
  scanResults.append(inventory);

  const draft = detailSection('Draft manifest');
  if (artifact.draft_manifest) {
    appendFacts(draft, [
      ['Extension id', artifact.draft_manifest.extension_id],
      ['Name', artifact.draft_manifest.name],
      ['Runtime', `${artifact.draft_manifest.runtime?.type} · ${artifact.draft_manifest.runtime?.entrypoint}`],
      ['Default permission', artifact.draft_manifest.permissions?.default || 'read_only'],
    ]);
    const actions = element('div', 'marketplace-action-buttons');
    const install = element('button', 'marketplace-action-primary', 'Install plugin…');
    install.type = 'button';
    install.addEventListener('click', () => prepareSourceAction(artifact, draft, actions));
    actions.append(install);
    draft.append(
      actions,
      element('p', 'marketplace-action-status', 'Nothing is installed yet — the next step shows the approval preview before anything changes.'),
    );
  } else {
    draft.append(element('p', '', 'This repository class did not produce a draft manifest; install stays unavailable.'));
  }
  scanResults.append(draft);
}

async function pollScan(scanId, generation) {
  if (scanInFlight || generation !== scanGeneration) return;
  scanInFlight = true;
  try {
    const job = await api(`/api/extensions/scans/${encodeURIComponent(scanId)}`);
    if (generation !== scanGeneration) return;
    const state = job.status === 'succeeded' ? 'complete' : job.status === 'failed' ? 'error' : 'working';
    setScanProgress({
      state,
      title: job.status === 'succeeded' ? 'Scan complete' : job.status === 'failed' ? 'Scan failed' : (job.message || 'Scanning…'),
      detail: job.status === 'succeeded'
        ? `Classified as ${job.artifact?.repo_class || 'unknown'}`
        : humanSetupError(job.error || job.message || ''),
      progress: job.progress,
      stage: job.stage,
    });
    if (job.status === 'succeeded' && job.artifact) {
      renderScanArtifact(job.artifact);
      scanStatus.textContent = 'Review the extracted capabilities and findings before installing.';
      return;
    }
    if (job.status === 'failed') {
      scanStatus.textContent = `Scan stopped: ${humanSetupError(job.error || job.message || 'unknown error')}`;
      return;
    }
  } catch (error) {
    setScanProgress({ state: 'error', title: 'Scan unavailable', detail: humanSetupError(error), progress: 0, stage: 'fetch' });
    scanStatus.textContent = `Scan request failed: ${humanSetupError(error)}`;
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
  scanButton.disabled = true;
  setScanProgress({ state: 'working', title: 'Starting scan…', detail: url, progress: 0, stage: 'fetch' });
  try {
    const job = await api('/api/extensions/scans', {
      method: 'POST',
      body: JSON.stringify({ source_url: url }),
    });
    if (generation !== scanGeneration) return;
    scanId = job.scan_id || null;
    pollScan(job.scan_id, generation);
  } catch (error) {
    setScanProgress({ state: 'error', title: 'Scan unavailable', detail: humanSetupError(error), progress: 0, stage: 'fetch' });
  } finally {
    scanButton.disabled = false;
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
          : 'repository manifest',
        'static scan completed before install',
      ].join(' · ')),
    );
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
    status.textContent = `${actionLabel(operation)} in progress…`;
    const result = await api(`/api/extensions/plans/${encodeURIComponent(plan.plan_id)}/execute`, { method: 'POST' });
    if (result.result?.status !== 'succeeded') throw new Error('extension_action_failed');
    window.dispatchEvent(new Event('pandamonium:extensions-changed'));
    await load();
    status.textContent = `${actionLabel(operation)} completed.`;
    summary.textContent = `${plugin.name}: ${actionLabel(operation)} completed.`;
  } catch (error) {
    status.textContent = `${actionLabel(operation)} failed: ${humanSetupError(error)}`;
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

function renderDetail(plugin) {
  detailContent.replaceChildren();
  const heading = element('div');
  heading.append(element('h3', '', `${plugin.name} ${plugin.version}`));
  const badges = element('div', 'marketplace-detail-badges');
  badges.append(...statusBadges(plugin));
  heading.append(badges, element('p', '', plugin.summary));
  detailContent.append(heading);

  const provenance = detailSection('Package and provenance');
  const publisherLink = plugin.publisher?.url
    ? externalLink(plugin.publisher.name, plugin.publisher.url)
    : plugin.publisher?.name || 'Unknown';
  const sourceLink = plugin.provenance?.source_url
    ? externalLink(plugin.provenance.source_url, plugin.provenance.source_url)
    : 'Unavailable';
  appendFacts(provenance, [
    ['Publisher', publisherLink],
    ['License', plugin.license],
    ['Source', sourceLink],
    ['Revision', plugin.provenance?.source_revision || 'Unavailable'],
    ['Digest', `sha256:${plugin.provenance?.sha256 || 'unavailable'}`],
    ['Signature', 'Catalog + artifact verified'],
    ['Review', `${labels[plugin.review?.status] || plugin.review?.status} · ${plugin.review?.reviewer || 'unknown reviewer'}`],
  ]);
  detailContent.append(provenance);

  const compatibility = detailSection('Compatibility and installation');
  const installation = plugin.installation || {};
  appendFacts(compatibility, [
    ['Compatibility', `${labels[plugin.compatibility?.state] || plugin.compatibility?.state} with Pandamonium ${plugin.compatibility?.pandamonium_min}–${plugin.compatibility?.pandamonium_max}`],
    ['Platforms', (plugin.compatibility?.platforms || []).join(', ')],
    ['Architectures', (plugin.compatibility?.architectures || []).join(', ')],
    ['Installed state', labels[installation.state] || installation.state],
    ['Version', installation.current_version ? `${installation.current_version} installed · ${installation.target_version} published` : `${installation.target_version} published`],
    ['Restart', plugin.restart_required === 'none' ? 'No restart' : `${plugin.restart_required} restart required`],
  ]);
  detailContent.append(compatibility);

  const permissions = detailSection('Permissions and data boundaries');
  const permissionItems = [`Default: ${plugin.permissions?.default || 'unknown'}`];
  Object.entries(plugin.permissions?.capabilities || {}).forEach(([name, mode]) => permissionItems.push(`${name}: ${mode}`));
  const boundaries = plugin.permissions?.data_boundaries || {};
  ['read', 'write', 'network'].forEach(kind => {
    const values = boundaries[kind] || [];
    permissionItems.push(`${kind}: ${values.length ? values.join(', ') : 'none'}`);
  });
  permissions.append(listOrNone(permissionItems, value => value));
  detailContent.append(permissions);

  const dependencies = detailSection('Dependencies');
  dependencies.append(listOrNone(plugin.dependencies, item => `${item.id} ${item.minimum_version}–${item.maximum_version}${item.optional ? ' · optional' : ''} · ${item.dependency_type}`));
  detailContent.append(dependencies);

  const configuration = detailSection('Configuration keys');
  configuration.append(listOrNone(plugin.configuration, item => `${item.key} · ${item.required ? 'required' : 'optional'}${item.secret ? ' · secret reference' : ''} — ${item.description}`));
  detailContent.append(configuration);

  const removal = detailSection('Removal and rollback');
  removal.append(listOrNone([
    `Declared removable paths: ${(plugin.removal?.remove_paths || []).join(', ') || 'none'}`,
    `Declared preserve paths: ${(plugin.removal?.preserve_paths || []).join(', ') || 'all user data'}`,
    'Removal defaults to retaining user data and archives the package for recovery',
    `Retained revisions: ${plugin.rollback?.retain_revisions || 0}`,
  ], value => value));
  detailContent.append(removal);

  if (plugin.review?.security_advisories?.length) {
    const advisories = detailSection('Security advisories');
    advisories.append(listOrNone(plugin.review.security_advisories, item => `${item.id} · ${item.severity} — ${item.summary}`));
    detailContent.append(advisories);
  }
  detailContent.append(renderActions(plugin));
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

async function load() {
  const generation = ++loadGeneration;
  selectedId = null;
  workspace.classList.remove('has-detail');
  detailContent.replaceChildren();
  renderState('Loading plugins…', 'Verifying the signed catalog and local registry.');
  loadInstalled(generation);
  try {
    const response = await fetch(`${API_BASE}/api/extensions/marketplace`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`marketplace_http_${response.status}`);
    const payload = await response.json();
    if (generation !== loadGeneration) return;
    plugins = Array.isArray(payload.plugins) ? payload.plugins : [];
    renderCategories();
    if (payload.status === 'offline') return renderState('Marketplace offline', 'No verified catalog is available. Refresh after connectivity or catalog configuration is restored.');
    if (payload.status === 'error') return renderState('Catalog verification failed', humanSetupError(payload.failure || 'The marketplace catalog could not be verified.'));
    if (payload.status === 'empty') return renderState('No plugins published', 'The verified catalog is empty. Installed plugins remain unchanged.');
    renderCards();
    if (plugins[0] && window.innerWidth > 720) selectPlugin(plugins[0].id, false);
  } catch (error) {
    if (generation !== loadGeneration) return;
    plugins = [];
    renderCategories();
    renderState('Marketplace unavailable', humanSetupError(error, 'The marketplace request failed.'));
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
  const focusable = [...modal.querySelectorAll('button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href]')]
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
  results = document.getElementById('marketplace-results');
  summary = document.getElementById('marketplace-summary');
  workspace = document.getElementById('marketplace-workspace');
  detail = document.getElementById('marketplace-detail');
  detailContent = document.getElementById('marketplace-detail-content');
  scanUrl = document.getElementById('marketplace-source-url');
  scanButton = document.getElementById('marketplace-source-scan');
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
  tabAdd = document.getElementById('marketplace-tab-add');
  panelInstalled = document.getElementById('marketplace-panel-installed');
  panelAdd = document.getElementById('marketplace-panel-add');
  if (!modal || !launcher || !search || !category || !results || !summary || !workspace || !detail || !detailContent || !installedDetailContent) return;
  launcher.addEventListener('click', open);
  document.getElementById('close-marketplace-modal')?.addEventListener('click', close);
  document.getElementById('marketplace-retry')?.addEventListener('click', load);
  scanButton?.addEventListener('click', startSourceScan);
  scanUrl?.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      startSourceScan();
    }
  });
  tabInstalled?.addEventListener('click', () => setTab('installed'));
  tabAdd?.addEventListener('click', () => setTab('add'));
  modal.querySelector('.marketplace-tabs')?.addEventListener('keydown', event => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    setTab(activeTab === 'installed' ? 'add' : 'installed', true);
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
  search.addEventListener('input', renderCards);
  category.addEventListener('change', renderCards);
  modal.addEventListener('keydown', trapFocus);
  modal.addEventListener('click', event => {
    if (event.target === modal) close();
  });
}

export default { init, open, close };
