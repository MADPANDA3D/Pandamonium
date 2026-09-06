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
  modal.classList.add('hidden');
  modal.setAttribute('aria-hidden', 'true');
  workspace?.classList.remove('has-detail');
  (previousFocus?.isConnected ? previousFocus : launcher)?.focus();
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

  if (plugin.review?.security_advisories?.length) {
    const advisories = detailSection('Security advisories');
    advisories.append(listOrNone(plugin.review.security_advisories, item => `${item.id} · ${item.severity} — ${item.summary}`));
    detailContent.append(advisories);
  }
}

function selectPlugin(id, focus = true) {
  const plugin = plugins.find(item => item.id === id);
  if (!plugin) return;
  selectedId = id;
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
  try {
    const response = await fetch(`${API_BASE}/api/extensions/marketplace`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`marketplace_http_${response.status}`);
    const payload = await response.json();
    if (generation !== loadGeneration) return;
    plugins = Array.isArray(payload.plugins) ? payload.plugins : [];
    renderCategories();
    if (payload.status === 'offline') return renderState('Marketplace offline', 'No verified catalog is available. Refresh after connectivity or catalog configuration is restored.');
    if (payload.status === 'error') return renderState('Catalog verification failed', payload.failure || 'The marketplace catalog could not be verified.');
    if (payload.status === 'empty') return renderState('No plugins published', 'The verified catalog is empty. Installed plugins remain unchanged.');
    renderCards();
    if (plugins[0] && window.innerWidth > 720) selectPlugin(plugins[0].id, false);
  } catch (error) {
    if (generation !== loadGeneration) return;
    plugins = [];
    renderCategories();
    renderState('Marketplace unavailable', error?.message || 'The marketplace request failed.');
  }
}

function open() {
  previousFocus = document.activeElement;
  modal.classList.remove('hidden');
  modal.setAttribute('aria-hidden', 'false');
  search.value = '';
  category.value = '';
  load();
  requestAnimationFrame(() => search.focus());
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
  if (!modal || !launcher || !search || !category || !results || !summary || !workspace || !detail || !detailContent) return;
  launcher.addEventListener('click', open);
  document.getElementById('close-marketplace-modal')?.addEventListener('click', close);
  document.getElementById('marketplace-retry')?.addEventListener('click', load);
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
