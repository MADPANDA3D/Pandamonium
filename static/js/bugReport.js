// static/js/bugReport.js
//
// Guided in-app bug reports (MAD-856).
//
// A compact right-dock workflow on desktop (reuses the canonical modal +
// edge-dock framework; it opens docked right and can be peeled off or resized
// like every other tool window) and a full-width sheet on mobile.
//
// Privacy contract enforced here:
//   * only the fields the user types, the screenshots the user adds, and the
//     server's allowlisted diagnostic bundle are ever sent;
//   * no keystrokes, message contents, console logs, or background screens are
//     captured — paste only reads an explicit clipboard image paste;
//   * a capture session survives navigation via a local draft (screenshot
//     bytes live on the server keyed by the draft id; only small thumbnails
//     are stored locally);
//   * submission goes through a server route; the browser never receives a
//     GitHub credential.

import Storage, { KEYS } from './storage.js';
import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';

const MODAL_ID = 'bug-report-modal';
const DRAFT_KEY = KEYS.FEEDBACK_DRAFT || 'odysseus-feedback-draft';
const API = '/api/feedback';
const MAX_ATTACHMENTS = 8;
const MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024;
const MAX_THUMB_PX = 320;
const PREVIEW_MAX_PX = 1200;

const TYPES = [
  { value: 'bug', label: 'Bug' },
  { value: 'requested_fix', label: 'Requested fix' },
  { value: 'product_change', label: 'Product change' },
  { value: 'security', label: 'Security vulnerability (private)' },
];

let _modal = null;
let _config = null;
let _configError = '';
let _pasteHandler = null;

const _state = {
  step: 0,
  draft: _defaultDraft(),
  attachments: [],
  diagnostics: null,
  prepared: null,
  preparing: false,
  submitting: false,
  status: '',
  statusKind: '',
  issue: null,
  duplicateChoice: 'new',
  confirmed: false,
};

function _defaultDraft() {
  return {
    draft_id: `draft-${_randomToken(16)}`,
    idempotency_key: `idem-${_randomToken(20)}`,
    type: 'bug',
    summary: '',
    goal: '',
    expected: '',
    actual: '',
    steps: [''],
    workaround: '',
    reviewed_text: '',
    include_diagnostics: true,
  };
}

function _randomToken(length) {
  const bytes = new Uint8Array(Math.ceil(length / 2));
  (window.crypto || window.msCrypto).getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('').slice(0, length);
}

function _byId(id) { return document.getElementById(id); }

function _currentRoute() {
  return `${window.location.pathname || '/'}${window.location.hash || ''}`;
}

function _viewportClass() {
  if (window.innerWidth <= 768) return 'mobile';
  if (window.innerWidth <= 1024) return 'tablet';
  return 'desktop';
}

function _userAgentClass() {
  const ua = navigator.userAgent || '';
  const browser = /Firefox\/(\d+)/.exec(ua) ? `Firefox ${RegExp.$1}`
    : /Edg\/(\d+)/.exec(ua) ? `Edge ${RegExp.$1}`
    : /Chrome\/(\d+)/.exec(ua) ? `Chrome ${RegExp.$1}`
    : /Version\/(\d+).*Safari/.exec(ua) ? `Safari ${RegExp.$1}`
    : 'browser';
  const platform = /Windows/i.test(ua) ? 'windows'
    : /Macintosh|Mac OS X/i.test(ua) ? 'macos'
    : /Android/i.test(ua) ? 'android'
    : /iPhone|iPad|iPod/i.test(ua) ? 'ios'
    : /Linux/i.test(ua) ? 'linux'
    : 'unknown';
  return `${browser} on ${platform}`;
}

// ── persistence ──────────────────────────────────────────────────────────

function _persistDraft() {
  try {
    Storage.setJSON(DRAFT_KEY, {
      draft: _state.draft,
      attachments: _state.attachments.map((item) => ({
        localId: item.localId,
        attachmentId: item.attachmentId,
        name: item.name,
        bytes: item.bytes,
        sha256: item.sha256,
        contentType: item.contentType,
        label: item.label,
        route: item.route,
        thumb: item.thumb,
        status: item.status,
        error: item.error,
      })),
    });
  } catch (_) { /* quota/private mode: the in-memory session still works */ }
}

function _restoreDraft() {
  const saved = Storage.getJSON(DRAFT_KEY, null);
  if (!saved || typeof saved !== 'object') return;
  if (saved.draft && typeof saved.draft === 'object') {
    _state.draft = { ..._defaultDraft(), ...saved.draft };
    if (!_state.draft.draft_id) _state.draft.draft_id = `draft-${_randomToken(16)}`;
    if (!_state.draft.idempotency_key) _state.draft.idempotency_key = `idem-${_randomToken(20)}`;
  }
  if (Array.isArray(saved.attachments)) {
    _state.attachments = saved.attachments
      .filter((item) => item && item.localId)
      .slice(0, MAX_ATTACHMENTS)
      .map((item) => ({
        localId: String(item.localId),
        attachmentId: item.attachmentId || '',
        name: String(item.name || 'screenshot'),
        bytes: Number(item.bytes) || 0,
        sha256: String(item.sha256 || ''),
        contentType: String(item.contentType || ''),
        label: String(item.label || ''),
        route: String(item.route || ''),
        thumb: String(item.thumb || ''),
        status: item.status === 'failed' ? 'failed' : item.attachmentId ? 'uploaded' : 'failed',
        error: String(item.error || ''),
      }));
  }
}

function _clearDraft() {
  _state.draft = _defaultDraft();
  _state.attachments = [];
  _state.diagnostics = null;
  _state.prepared = null;
  _state.issue = null;
  _state.confirmed = false;
  _state.duplicateChoice = 'new';
  _state.status = '';
  Storage.remove(DRAFT_KEY);
}

// ── config / server calls ────────────────────────────────────────────────

async function _fetchJson(url, options) {
  const response = await fetch(url, { credentials: 'same-origin', ...(options || {}) });
  let body = null;
  try { body = await response.json(); } catch (_) { body = null; }
  if (!response.ok) {
    const detail = body && (body.detail || body.error);
    const error = new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return body;
}

async function _loadConfig(force = false) {
  if (_config && !force) return _config;
  try {
    _config = await _fetchJson(`${API}/config`);
    _configError = '';
  } catch (error) {
    _config = null;
    _configError = error.message || 'The report service is unavailable.';
  }
  return _config;
}

function _draftPayload() {
  return {
    ..._state.draft,
    steps: _state.draft.steps.map((step) => String(step || '').trim()).filter(Boolean),
    route: _currentRoute(),
    session_id: window.sessionModule?.getCurrentSessionId?.() || '',
    client: {
      user_agent_class: _userAgentClass(),
      viewport_class: _viewportClass(),
      locale: navigator.language || '',
    },
    attachment_ids: _state.attachments
      .filter((item) => item.status === 'uploaded' && item.attachmentId)
      .map((item) => item.attachmentId),
  };
}

async function _loadDiagnostics() {
  const params = new URLSearchParams({
    route: _currentRoute(),
    user_agent_class: _userAgentClass(),
    viewport_class: _viewportClass(),
    locale: navigator.language || '',
    session_id: window.sessionModule?.getCurrentSessionId?.() || '',
  });
  try {
    const result = await _fetchJson(`${API}/diagnostics?${params.toString()}`);
    _state.diagnostics = result.diagnostics || null;
  } catch (error) {
    _state.diagnostics = null;
    _setStatus(`Diagnostics unavailable: ${error.message}`, 'warn');
  }
  _renderDiagnosticsPreview();
}

async function _prepareReview() {
  _state.preparing = true;
  _setStatus('Preparing the exact report for review…', '');
  _renderFooter();
  try {
    _state.prepared = await _fetchJson(`${API}/prepare`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_draftPayload()),
    });
    _state.diagnostics = _state.prepared.diagnostics || _state.diagnostics;
    _state.duplicateChoice = 'new';
    _setStatus('', '');
  } catch (error) {
    _state.prepared = null;
    _setStatus(error.message, 'error');
  } finally {
    _state.preparing = false;
    _renderReview();
    _renderFooter();
  }
}

// ── screenshots ──────────────────────────────────────────────────────────

async function _makeThumb(file, maxPx = MAX_THUMB_PX, quality = 0.7) {
  const bitmap = await _decodeImage(file);
  const scale = Math.min(1, maxPx / Math.max(bitmap.width || 1, bitmap.height || 1));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round((bitmap.width || 1) * scale));
  canvas.height = Math.max(1, Math.round((bitmap.height || 1) * scale));
  canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  if (bitmap.close) bitmap.close();
  return canvas.toDataURL('image/jpeg', quality);
}

async function _decodeImage(blob) {
  if (window.createImageBitmap) {
    try { return await createImageBitmap(blob); } catch (_) { /* fall through */ }
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => { URL.revokeObjectURL(url); resolve(image); };
    image.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read that image.')); };
    image.src = url;
  });
}

function _findAttachment(localId) {
  return _state.attachments.find((item) => item.localId === localId) || null;
}

async function _uploadAttachment(item, blob) {
  item.status = 'uploading';
  item.error = '';
  _renderAttachments();
  try {
    const form = new FormData();
    form.append('draft_id', _state.draft.draft_id);
    form.append('label', item.label || '');
    form.append('route', item.route || '');
    form.append('file', blob, item.name || 'screenshot.png');
    const result = await _fetchJson(`${API}/upload`, { method: 'POST', body: form });
    const attachment = (result && result.attachment) || {};
    item.attachmentId = attachment.id || '';
    item.bytes = Number(attachment.bytes) || item.bytes;
    item.sha256 = attachment.sha256 || '';
    item.contentType = attachment.content_type || item.contentType;
    item.status = item.attachmentId ? 'uploaded' : 'failed';
    item.error = item.attachmentId ? '' : 'Upload did not return an id.';
  } catch (error) {
    item.status = 'failed';
    item.error = error.message || 'Upload failed.';
  }
  _persistDraft();
  _renderAttachments();
  _renderFooter();
}

async function addFiles(fileList, route = '') {
  const files = Array.from(fileList || []).filter((file) => /^image\//.test(file.type || ''));
  if (!files.length) {
    _setStatus('Only PNG, JPEG, or WebP screenshots can be attached.', 'warn');
    return;
  }
  for (const file of files) {
    if (_state.attachments.length >= MAX_ATTACHMENTS) {
      _setStatus(`Attach at most ${MAX_ATTACHMENTS} screenshots.`, 'warn');
      break;
    }
    if (!/^image\/(png|jpeg|webp)$/.test(file.type || '')) {
      _setStatus(`${file.name || 'That file'} is not a PNG, JPEG, or WebP image.`, 'warn');
      continue;
    }
    if (file.size > MAX_ATTACHMENT_BYTES) {
      _setStatus(`${file.name || 'That screenshot'} is larger than 8 MB. Crop it and try again.`, 'warn');
      continue;
    }
    let thumb = '';
    try { thumb = await _makeThumb(file); } catch (_) { thumb = ''; }
    const item = {
      localId: `shot-${_randomToken(12)}`,
      attachmentId: '',
      name: file.name || 'screenshot.png',
      bytes: file.size,
      sha256: '',
      contentType: file.type,
      label: '',
      route: route || _currentRoute(),
      thumb,
      status: 'uploading',
      error: '',
    };
    _state.attachments.push(item);
    _renderAttachments();
    await _uploadAttachment(item, file);
  }
  _setStatus('', '');
}

function _moveAttachment(localId, delta) {
  const index = _state.attachments.findIndex((item) => item.localId === localId);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= _state.attachments.length) return;
  const [item] = _state.attachments.splice(index, 1);
  _state.attachments.splice(target, 0, item);
  _persistDraft();
  _renderAttachments();
}

function _removeAttachment(localId) {
  _state.attachments = _state.attachments.filter((item) => item.localId !== localId);
  _persistDraft();
  _renderAttachments();
  _renderFooter();
}

// ── DOM ──────────────────────────────────────────────────────────────────

function _field(labelText, id, options = {}) {
  const wrap = document.createElement('label');
  wrap.className = 'bug-report-field';
  wrap.setAttribute('for', id);
  const label = document.createElement('span');
  label.className = 'bug-report-label';
  label.textContent = labelText;
  wrap.appendChild(label);
  let input;
  if (options.rows) {
    input = document.createElement('textarea');
    input.rows = options.rows;
    input.maxLength = options.maxLength || 2000;
  } else {
    input = document.createElement('input');
    input.type = 'text';
    input.maxLength = options.maxLength || 2000;
  }
  input.id = id;
  input.className = 'styled-prompt-input bug-report-input';
  input.placeholder = options.placeholder || '';
  input.required = !!options.required;
  wrap.appendChild(input);
  return wrap;
}

function _buildModal() {
  _modal = document.createElement('div');
  _modal.id = MODAL_ID;
  _modal.className = 'modal bug-report-modal';
  _modal.setAttribute('role', 'dialog');
  _modal.setAttribute('aria-modal', 'true');
  _modal.setAttribute('aria-labelledby', 'bug-report-heading');
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content bug-report-content" id="bug-report-content">
      <div class="modal-header bug-report-header" id="bug-report-header">
        <h4 id="bug-report-heading">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px;margin-right:6px"><path d="m8 2 1.88 1.88M14.12 3.88 16 2M9 7.13v-1a3.003 3.003 0 1 1 6 0v1M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6M12 20v-9M6.53 9C4.6 8.8 3 7.1 3 5M6 13H2M3 21c0-2.1 1.7-3.9 3.8-4M20.97 5c0 2.1-1.6 3.8-3.5 4M22 13h-4M17.2 17c2.1.1 3.8 1.9 3.8 4"/></svg>
          Report a bug
        </h4>
        <div class="bug-report-header-actions">
          <button type="button" class="close-btn" id="bug-report-minimize" title="Minimize" aria-label="Minimize bug report">_</button>
          <button type="button" class="close-btn" id="bug-report-close" title="Close" aria-label="Close bug report">✖</button>
        </div>
      </div>
      <div class="bug-report-tabs" id="bug-report-tabs" role="tablist" aria-label="Report steps">
        <button type="button" role="tab" class="bug-report-tab" data-step="0" aria-selected="true" aria-controls="bug-report-panel-capture" id="bug-report-tab-capture">1. Describe</button>
        <button type="button" role="tab" class="bug-report-tab" data-step="1" aria-selected="false" aria-controls="bug-report-panel-evidence" id="bug-report-tab-evidence">2. Evidence</button>
        <button type="button" role="tab" class="bug-report-tab" data-step="2" aria-selected="false" aria-controls="bug-report-panel-review" id="bug-report-tab-review">3. Review</button>
      </div>
      <div class="modal-body bug-report-body">
        <section id="bug-report-panel-capture" class="bug-report-panel" role="tabpanel" aria-labelledby="bug-report-tab-capture"></section>
        <section id="bug-report-panel-evidence" class="bug-report-panel" role="tabpanel" aria-labelledby="bug-report-tab-evidence" hidden></section>
        <section id="bug-report-panel-review" class="bug-report-panel" role="tabpanel" aria-labelledby="bug-report-tab-review" hidden></section>
      </div>
      <div class="modal-footer bug-report-footer">
        <span id="bug-report-status" class="bug-report-status" role="status" aria-live="polite"></span>
        <div class="bug-report-footer-actions">
          <button type="button" class="confirm-btn confirm-btn-secondary" id="bug-report-back">Back</button>
          <button type="button" class="confirm-btn confirm-btn-primary" id="bug-report-next">Next</button>
          <button type="button" class="confirm-btn confirm-btn-primary" id="bug-report-submit" hidden>Submit report</button>
        </div>
      </div>
    </div>`;
  document.body.appendChild(_modal);

  const content = _byId('bug-report-content');
  const header = _byId('bug-report-header');
  makeWindowDraggable(_modal, { content, header });

  _modal.querySelectorAll('.bug-report-tab').forEach((tab) => {
    tab.addEventListener('click', () => _goToStep(Number(tab.dataset.step)));
  });
  _byId('bug-report-close').addEventListener('click', closeBugReport);
  _byId('bug-report-minimize').addEventListener('click', () => {
    try { Modals.minimize(MODAL_ID); } catch (_) { closeBugReport(); }
  });
  _byId('bug-report-back').addEventListener('click', () => _goToStep(_state.step - 1));
  _byId('bug-report-next').addEventListener('click', () => _next());
  _byId('bug-report-submit').addEventListener('click', () => _submit());

  _renderCapture();
  _renderEvidence();
  _renderReview();
  _renderFooter();
}

// ── capture step ─────────────────────────────────────────────────────────

function _renderCapture() {
  const panel = _byId('bug-report-panel-capture');
  if (!panel) return;
  panel.replaceChildren();

  const typeWrap = _field('What kind of report is this?', 'bug-report-type');
  const select = document.createElement('select');
  select.id = 'bug-report-type';
  select.className = 'styled-prompt-input bug-report-input';
  for (const type of TYPES) {
    const option = document.createElement('option');
    option.value = type.value;
    option.textContent = type.label;
    option.selected = _state.draft.type === type.value;
    select.appendChild(option);
  }
  select.addEventListener('change', () => {
    _state.draft.type = select.value;
    _persistDraft();
    _renderReview();
  });
  typeWrap.replaceChild(select, typeWrap.querySelector('input'));
  panel.appendChild(typeWrap);

  panel.appendChild(_field('Summary', 'bug-report-summary', {
    placeholder: 'One line: what went wrong?', required: true, maxLength: 160,
  }));
  panel.appendChild(_field('What were you trying to do?', 'bug-report-goal', {
    rows: 2, placeholder: 'The task or flow you were in', maxLength: 2000,
  }));
  panel.appendChild(_field('What did you expect to happen?', 'bug-report-expected', {
    rows: 2, maxLength: 2000,
  }));
  panel.appendChild(_field('What actually happened?', 'bug-report-actual', {
    rows: 3, placeholder: 'Include the exact error text if you saw one', maxLength: 4000,
  }));

  const stepsField = document.createElement('div');
  stepsField.className = 'bug-report-field';
  const stepsLabel = document.createElement('span');
  stepsLabel.className = 'bug-report-label';
  stepsLabel.textContent = 'Steps to reproduce (ordered)';
  stepsField.appendChild(stepsLabel);
  const list = document.createElement('div');
  list.id = 'bug-report-steps-list';
  list.className = 'bug-report-steps';
  stepsField.appendChild(list);
  const add = document.createElement('button');
  add.type = 'button';
  add.id = 'bug-report-step-add';
  add.className = 'bug-report-add-step';
  add.textContent = '+ Add step';
  add.addEventListener('click', () => {
    if (_state.draft.steps.length >= 30) return;
    _state.draft.steps.push('');
    _renderSteps();
  });
  stepsField.appendChild(add);
  panel.appendChild(stepsField);

  panel.appendChild(_field('Workaround', 'bug-report-workaround', {
    rows: 2, placeholder: 'How did you get past it? (optional)', maxLength: 2000,
  }));
  panel.appendChild(_field('Anything else? (optional)', 'bug-report-reviewed-text', {
    rows: 2,
    placeholder: 'Extra context you reviewed. Secrets are redacted automatically.',
    maxLength: 4000,
  }));

  const bindField = (id, key) => {
    const input = _byId(id);
    if (!input) return;
    input.value = _state.draft[key] || '';
    input.addEventListener('input', () => {
      _state.draft[key] = input.value;
      _persistDraft();
    });
  };
  bindField('bug-report-summary', 'summary');
  bindField('bug-report-goal', 'goal');
  bindField('bug-report-expected', 'expected');
  bindField('bug-report-actual', 'actual');
  bindField('bug-report-workaround', 'workaround');
  bindField('bug-report-reviewed-text', 'reviewed_text');

  _renderSteps();
}

function _renderSteps() {
  const list = _byId('bug-report-steps-list');
  if (!list) return;
  if (!_state.draft.steps.length) _state.draft.steps = [''];
  list.replaceChildren();
  _state.draft.steps.forEach((value, index) => {
    const row = document.createElement('div');
    row.className = 'bug-report-step';
    const number = document.createElement('span');
    number.className = 'bug-report-step-number';
    number.textContent = String(index + 1);
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'styled-prompt-input bug-report-step-input';
    input.maxLength = 600;
    input.value = value || '';
    input.setAttribute('aria-label', `Reproduction step ${index + 1}`);
    input.addEventListener('input', () => {
      _state.draft.steps[index] = input.value;
      _persistDraft();
    });
    const up = _iconButton('↑', `Move step ${index + 1} up`, () => _moveStep(index, -1));
    const down = _iconButton('↓', `Move step ${index + 1} down`, () => _moveStep(index, 1));
    const remove = _iconButton('✖', `Remove step ${index + 1}`, () => {
      _state.draft.steps.splice(index, 1);
      if (!_state.draft.steps.length) _state.draft.steps = [''];
      _persistDraft();
      _renderSteps();
    });
    row.append(number, input, up, down, remove);
    list.appendChild(row);
  });
}

function _moveStep(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= _state.draft.steps.length) return;
  const [value] = _state.draft.steps.splice(index, 1);
  _state.draft.steps.splice(target, 0, value);
  _persistDraft();
  _renderSteps();
}

function _iconButton(glyph, label, handler) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'bug-report-icon-btn';
  button.textContent = glyph;
  button.setAttribute('aria-label', label);
  button.title = label;
  button.addEventListener('click', handler);
  return button;
}

// ── evidence step ────────────────────────────────────────────────────────

function _renderEvidence() {
  const panel = _byId('bug-report-panel-evidence');
  if (!panel) return;
  panel.replaceChildren();

  const intro = document.createElement('p');
  intro.className = 'bug-report-hint';
  intro.textContent = 'Add screenshots with the file picker, drag and drop, or paste (Ctrl/Cmd+V). Label each one with the step it shows.';
  panel.appendChild(intro);

  const drop = document.createElement('div');
  drop.id = 'bug-report-drop-zone';
  drop.className = 'bug-report-drop-zone';
  drop.tabIndex = 0;
  drop.setAttribute('role', 'button');
  drop.setAttribute('aria-label', 'Add screenshots');
  drop.innerHTML = '<span>Drop screenshots here or <strong>choose files</strong></span>';
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.id = 'bug-report-file-input';
  fileInput.accept = 'image/png,image/jpeg,image/webp';
  fileInput.multiple = true;
  fileInput.className = 'bug-report-file-input';
  fileInput.addEventListener('change', () => {
    addFiles(fileInput.files, _currentRoute());
    fileInput.value = '';
  });
  const choose = document.createElement('button');
  choose.type = 'button';
  choose.className = 'confirm-btn confirm-btn-secondary';
  choose.id = 'bug-report-choose-files';
  choose.textContent = 'Choose files';
  choose.addEventListener('click', () => fileInput.click());
  drop.addEventListener('click', (event) => {
    if (event.target === choose) return;
    fileInput.click();
  });
  drop.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      fileInput.click();
    }
  });
  drop.addEventListener('dragover', (event) => {
    event.preventDefault();
    drop.classList.add('is-dragover');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('is-dragover'));
  drop.addEventListener('drop', (event) => {
    event.preventDefault();
    drop.classList.remove('is-dragover');
    addFiles(event.dataTransfer && event.dataTransfer.files, _currentRoute());
  });
  drop.appendChild(choose);
  panel.appendChild(drop);
  panel.appendChild(fileInput);

  const list = document.createElement('div');
  list.id = 'bug-report-attachment-list';
  list.className = 'bug-report-attachments';
  panel.appendChild(list);

  const diagnosticsBox = document.createElement('details');
  diagnosticsBox.className = 'bug-report-diagnostics';
  diagnosticsBox.id = 'bug-report-diagnostics-box';
  diagnosticsBox.open = true;
  diagnosticsBox.innerHTML = `
    <summary>Diagnostics included automatically (allowlisted and redacted)</summary>
    <label class="bug-report-toggle">
      <input type="checkbox" id="bug-report-include-diagnostics">
      <span>Include these diagnostics in the public issue</span>
    </label>
    <pre class="bug-report-diagnostics-pre" id="bug-report-diagnostics-preview">Loading…</pre>`;
  panel.appendChild(diagnosticsBox);
  const include = _byId('bug-report-include-diagnostics');
  include.checked = _state.draft.include_diagnostics !== false;
  include.addEventListener('change', () => {
    _state.draft.include_diagnostics = include.checked;
    _persistDraft();
    _renderDiagnosticsPreview();
  });

  _renderAttachments();
  _renderDiagnosticsPreview();
  if (!_state.diagnostics) _loadDiagnostics();
}

function _renderAttachments() {
  const list = _byId('bug-report-attachment-list');
  if (!list) return;
  list.replaceChildren();
  if (!_state.attachments.length) {
    const empty = document.createElement('p');
    empty.className = 'bug-report-hint';
    empty.id = 'bug-report-attachments-empty';
    empty.textContent = 'No screenshots attached yet.';
    list.appendChild(empty);
    return;
  }
  _state.attachments.forEach((item, index) => {
    const row = document.createElement('div');
    row.className = 'bug-report-attachment';
    if (item.status === 'failed') row.classList.add('is-failed');

    const thumb = document.createElement('div');
    thumb.className = 'bug-report-thumb';
    if (item.thumb) {
      const image = document.createElement('img');
      image.src = item.thumb;
      image.alt = item.label || item.name || `Screenshot ${index + 1}`;
      thumb.appendChild(image);
    } else {
      thumb.textContent = '🖼';
    }

    const meta = document.createElement('div');
    meta.className = 'bug-report-attachment-meta';
    const name = document.createElement('span');
    name.className = 'bug-report-attachment-name';
    name.textContent = `${index + 1}. ${item.name || 'screenshot'}`;
    const label = document.createElement('input');
    label.type = 'text';
    label.className = 'styled-prompt-input bug-report-attachment-label';
    label.maxLength = 80;
    label.placeholder = 'Step label (e.g. "after tapping Send")';
    label.value = item.label || '';
    label.setAttribute('aria-label', `Step label for screenshot ${index + 1}`);
    label.addEventListener('input', () => {
      item.label = label.value;
      _persistDraft();
    });
    const route = document.createElement('span');
    route.className = 'bug-report-attachment-route';
    route.textContent = `${item.route || ''}${item.sha256 ? ` · sha256 ${item.sha256.slice(0, 12)}…` : ''}`;
    meta.append(name, label, route);
    if (item.status === 'failed') {
      const failed = document.createElement('span');
      failed.className = 'bug-report-attachment-error';
      failed.textContent = item.error || 'Upload failed.';
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'bug-report-retry';
      retry.textContent = 'Retry upload';
      retry.setAttribute('aria-label', `Retry upload for screenshot ${index + 1}`);
      retry.addEventListener('click', () => _retryAttachment(item));
      meta.append(failed, retry);
    } else if (item.status === 'uploading') {
      const uploading = document.createElement('span');
      uploading.className = 'bug-report-attachment-route';
      uploading.textContent = 'Uploading…';
      meta.appendChild(uploading);
    }

    const actions = document.createElement('div');
    actions.className = 'bug-report-attachment-actions';
    actions.append(
      _iconButton('↑', `Move screenshot ${index + 1} up`, () => _moveAttachment(item.localId, -1)),
      _iconButton('↓', `Move screenshot ${index + 1} down`, () => _moveAttachment(item.localId, 1)),
      _iconButton('✖', `Remove screenshot ${index + 1}`, () => _removeAttachment(item.localId)),
    );

    row.append(thumb, meta, actions);
    list.appendChild(row);
  });
}

async function _retryAttachment(item) {
  if (!item.thumb) {
    _setStatus('That screenshot must be re-added (its preview was lost).', 'warn');
    return;
  }
  try {
    const blob = await (await fetch(item.thumb)).blob();
    await _uploadAttachment(item, blob);
  } catch (error) {
    item.status = 'failed';
    item.error = error.message || 'Retry failed.';
    _renderAttachments();
  }
}

function _renderDiagnosticsPreview() {
  const pre = _byId('bug-report-diagnostics-preview');
  if (!pre) return;
  if (_state.draft.include_diagnostics === false) {
    pre.textContent = 'Diagnostics will not be included.';
    return;
  }
  if (!_state.diagnostics) {
    pre.textContent = 'Loading diagnostics…';
    return;
  }
  try {
    pre.textContent = JSON.stringify(_state.diagnostics, null, 2);
  } catch (_) {
    pre.textContent = 'Diagnostics could not be displayed.';
  }
}

// ── review step ──────────────────────────────────────────────────────────

function _renderReview() {
  const panel = _byId('bug-report-panel-review');
  if (!panel) return;
  panel.replaceChildren();

  if (_state.prepared) {
    const title = document.createElement('div');
    title.className = 'bug-report-field';
    title.innerHTML = '<span class="bug-report-label">Public issue title</span>';
    const titleValue = document.createElement('pre');
    titleValue.className = 'bug-report-review-title';
    titleValue.id = 'bug-report-review-title';
    titleValue.textContent = _state.prepared.title || '';
    title.appendChild(titleValue);
    panel.appendChild(title);

    const bodyField = document.createElement('div');
    bodyField.className = 'bug-report-field';
    bodyField.innerHTML = '<span class="bug-report-label">Public issue body (exact)</span>';
    const bodyValue = document.createElement('pre');
    bodyValue.className = 'bug-report-review-body';
    bodyValue.id = 'bug-report-review-body';
    bodyValue.textContent = _state.prepared.body || '';
    bodyField.appendChild(bodyValue);
    panel.appendChild(bodyField);
  }

  const attachments = document.createElement('div');
  attachments.className = 'bug-report-review-attachments';
  attachments.id = 'bug-report-review-attachments';
  attachments.innerHTML = '<span class="bug-report-label">Attachments</span>';
  if (_state.attachments.length) {
    const list = document.createElement('ul');
    list.className = 'bug-report-review-list';
    _state.attachments.forEach((item, index) => {
      const line = document.createElement('li');
      line.textContent = `${index + 1}. ${item.name || 'screenshot'}${item.label ? ` — ${item.label}` : ''}${item.status !== 'uploaded' ? ' (not uploaded)' : ''}`;
      list.appendChild(line);
    });
    attachments.appendChild(list);
  } else {
    const none = document.createElement('p');
    none.className = 'bug-report-hint';
    none.textContent = 'None.';
    attachments.appendChild(none);
  }
  panel.appendChild(attachments);

  const duplicates = document.createElement('div');
  duplicates.className = 'bug-report-duplicates';
  duplicates.id = 'bug-report-duplicates';
  duplicates.innerHTML = '<span class="bug-report-label">Existing open issues</span>';
  const candidates = (_state.prepared && _state.prepared.duplicates) || [];
  if (_state.prepared && _state.prepared.duplicates_error) {
    const warn = document.createElement('p');
    warn.className = 'bug-report-hint';
    warn.textContent = `Duplicate check unavailable: ${_state.prepared.duplicates_error}`;
    duplicates.appendChild(warn);
  }
  const newChoice = _duplicateRadio('bug-report-duplicate-new', 'new', 'Continue as a new report');
  duplicates.appendChild(newChoice);
  for (const candidate of candidates) {
    const line = _duplicateRadio(
      `bug-report-duplicate-${candidate.number}`,
      String(candidate.number),
      `Add evidence to #${candidate.number} — ${candidate.title}`,
    );
    duplicates.appendChild(line);
  }
  if (!candidates.length && !(_state.prepared && _state.prepared.duplicates_error)) {
    const none = document.createElement('p');
    none.className = 'bug-report-hint';
    none.textContent = 'No similar open issues found.';
    duplicates.appendChild(none);
  }
  panel.appendChild(duplicates);

  const security = document.createElement('div');
  security.className = 'bug-report-security-notice';
  security.id = 'bug-report-security-notice';
  security.hidden = !(_state.prepared && _state.prepared.security_private_only);
  if (!security.hidden) {
    const link = document.createElement('a');
    link.href = _state.prepared.security_url || '#';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.id = 'bug-report-security-link';
    link.textContent = 'Open the private security advisory page';
    security.append(
      'Security reports are never posted publicly. Submit this privately instead: ',
      link,
    );
  }
  panel.appendChild(security);

  const warning = document.createElement('label');
  warning.className = 'bug-report-public-warning';
  warning.id = 'bug-report-public-warning';
  warning.innerHTML = `<input type="checkbox" id="bug-report-confirm">
    <span>I understand this issue, its body, its diagnostics, and its screenshots will be
    <strong>public</strong> on GitHub.</span>`;
  panel.appendChild(warning);
  const confirm = _byId('bug-report-confirm');
  confirm.checked = _state.confirmed;
  confirm.addEventListener('change', () => {
    _state.confirmed = confirm.checked;
    _renderFooter();
  });

  const copyButton = document.createElement('button');
  copyButton.type = 'button';
  copyButton.className = 'confirm-btn confirm-btn-secondary';
  copyButton.id = 'bug-report-refresh-review';
  copyButton.textContent = 'Refresh review';
  copyButton.addEventListener('click', () => _prepareReview());
  panel.appendChild(copyButton);

  if (_state.issue) {
    const success = document.createElement('div');
    success.className = 'bug-report-success';
    success.id = 'bug-report-success';
    const link = document.createElement('a');
    link.href = _state.issue.url || '#';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.id = 'bug-report-issue-link';
    link.textContent = _state.issue.url || 'issue';
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'confirm-btn confirm-btn-secondary';
    copy.id = 'bug-report-copy-report';
    copy.textContent = 'Copy redacted report';
    copy.addEventListener('click', () => _copyReport());
    const another = document.createElement('button');
    another.type = 'button';
    another.className = 'confirm-btn confirm-btn-secondary';
    another.id = 'bug-report-start-another';
    another.textContent = 'Start another report';
    another.addEventListener('click', () => {
      _clearDraft();
      _renderCapture();
      _renderEvidence();
      _renderReview();
      _goToStep(0);
      _loadConfig(true);
    });
    success.append('Filed as ', link, '. ', copy, ' ', another);
    panel.appendChild(success);
  }
}

function _duplicateRadio(id, value, labelText) {
  const label = document.createElement('label');
  label.className = 'bug-report-radio';
  const input = document.createElement('input');
  input.type = 'radio';
  input.name = 'bug-report-duplicate';
  input.id = id;
  input.value = value;
  input.checked = _state.duplicateChoice === value;
  input.addEventListener('change', () => { _state.duplicateChoice = value; });
  const span = document.createElement('span');
  span.textContent = labelText;
  label.append(input, span);
  return label;
}

async function _copyReport() {
  const report = _state.issue?.report;
  const text = report
    ? `${report.title}\n\n${report.body}\n\n${_state.issue.url || ''}`.trim()
    : '';
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    _setStatus('Redacted report copied.', 'ok');
  } catch (_) {
    _setStatus('Copy failed; select the review text and copy it manually.', 'warn');
  }
}

// ── flow ─────────────────────────────────────────────────────────────────

function _goToStep(step) {
  const next = Math.max(0, Math.min(2, Number(step) || 0));
  _state.step = next;
  ['capture', 'evidence', 'review'].forEach((name, index) => {
    const panel = _byId(`bug-report-panel-${name}`);
    if (panel) panel.hidden = index !== next;
  });
  _modal.querySelectorAll('.bug-report-tab').forEach((tab) => {
    tab.setAttribute('aria-selected', String(Number(tab.dataset.step) === next));
  });
  if (next === 1) _loadDiagnostics();
  if (next === 2) _prepareReview();
  _renderFooter();
}

function _next() {
  if (_state.step === 0) {
    if (String(_state.draft.summary || '').trim().length < 4) {
      _setStatus('Add a short summary before continuing.', 'error');
      const summary = _byId('bug-report-summary');
      if (summary) summary.focus();
      return;
    }
    _setStatus('', '');
    _goToStep(1);
    return;
  }
  if (_state.step === 1) {
    const pending = _state.attachments.filter((item) => item.status !== 'uploaded');
    if (pending.length) {
      _setStatus('Finish uploading (or remove) every screenshot before review.', 'error');
      return;
    }
    _setStatus('', '');
    _goToStep(2);
  }
}

function _renderFooter() {
  const back = _byId('bug-report-back');
  const next = _byId('bug-report-next');
  const submit = _byId('bug-report-submit');
  if (!back || !next || !submit) return;
  back.hidden = _state.step === 0;
  next.hidden = _state.step >= 2;
  submit.hidden = _state.step !== 2 || !!_state.issue;
  const config = _config;
  const blockedReason = !config
    ? (_configError || 'The report service is unavailable.')
    : !config.enabled
      ? (config.reason || 'In-app bug reports are disabled.')
      : !config.public_submission
        ? (config.github_reason || 'GitHub submission is not configured on this installation.')
        : '';
  const securityOnly = !!(_state.prepared && _state.prepared.security_private_only);
  submit.disabled = _state.submitting || !_state.confirmed || !!blockedReason || securityOnly;
  submit.title = blockedReason || (securityOnly ? 'Security reports must use the private advisory path.' : '');
  const hasPendingUploads = _state.attachments.some((item) => item.status !== 'uploaded');
  if (hasPendingUploads && _state.step < 2) next.disabled = false;
  _renderStatus();
}

function _setStatus(message, kind) {
  _state.status = message || '';
  _state.statusKind = kind || '';
  _renderStatus();
}

function _renderStatus() {
  const status = _byId('bug-report-status');
  if (!status) return;
  status.textContent = _state.status;
  status.className = `bug-report-status${_state.statusKind ? ` is-${_state.statusKind}` : ''}`;
}

async function _submit() {
  if (_state.submitting) return;
  if (!_state.confirmed) {
    _setStatus('Confirm the public visibility warning before submitting.', 'error');
    return;
  }
  _state.submitting = true;
  _setStatus('Submitting…', '');
  _renderFooter();
  try {
    const payload = {
      ..._draftPayload(),
      confirmation: true,
      duplicate_decision: _state.duplicateChoice === 'new' ? 'new' : 'existing',
      existing_issue_number: _state.duplicateChoice === 'new' ? undefined : Number(_state.duplicateChoice),
    };
    const result = await _fetchJson(`${API}/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    _state.issue = {
      url: result.issue_url,
      number: result.issue_number,
      report: result.redacted_report || null,
      alreadySubmitted: !!result.already_submitted,
    };
    _state.draft.idempotency_key = `idem-${_randomToken(20)}`;
    _persistDraft();
    _setStatus(
      result.already_submitted
        ? 'This report was already submitted; the existing issue is shown below.'
        : `Report filed as issue #${result.issue_number}.`,
      'ok',
    );
    _renderReview();
  } catch (error) {
    // The draft and its uploaded screenshots are preserved for a retry; the
    // idempotency key is stable so a retry cannot create a duplicate issue.
    _setStatus(`${error.message} Your draft is saved — fix the cause and retry.`, 'error');
  } finally {
    _state.submitting = false;
    _renderFooter();
  }
}

// ── open / close / init ──────────────────────────────────────────────────

function _wirePaste() {
  if (_pasteHandler) return;
  _pasteHandler = (event) => {
    if (!_modal || _modal.style.display === 'none') return;
    if (_state.step !== 1) return;
    const items = event.clipboardData && event.clipboardData.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
      if (item.kind === 'file' && /^image\//.test(item.type || '')) {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length) {
      event.preventDefault();
      addFiles(files, _currentRoute());
    }
  };
  document.addEventListener('paste', _pasteHandler);
}

export async function openBugReport() {
  const modal = _ensureModal();
  modal.classList.remove('hidden', 'modal-minimized');
  modal.style.display = 'flex';
  Modals.register(MODAL_ID, {
    defaultDock: 'right',
    restoreFn: () => { openBugReport(); },
    closeFn: () => { closeBugReport(); },
    minimizeFn: () => { closeBugReport(); },
    sidebarBtnId: 'overflow-bug-report-btn',
    label: 'Bug report',
    icon: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  });
  _wirePaste();
  _loadConfig(true).then(() => {
    if (_config && !_config.enabled) {
      _setStatus(_config.reason || 'In-app bug reports are disabled.', 'warn');
    } else if (_config && !_config.public_submission) {
      _setStatus(_config.github_reason || '', 'warn');
    }
    _renderFooter();
  });
  _loadDiagnostics();
  const summary = _byId('bug-report-summary');
  if (summary && !_state.draft.summary) summary.focus();
  return modal;
}

function _ensureModal() {
  if (_modal && _modal.isConnected) return _modal;
  _buildModal();
  return _modal;
}

export function closeBugReport() {
  if (_modal) {
    _modal.classList.add('hidden');
    _modal.style.display = 'none';
  }
  try { Modals.unregister(MODAL_ID); } catch (_) {}
}

export function initBugReport() {
  _restoreDraft();
  _ensureModal();
  const button = _byId('overflow-bug-report-btn');
  if (button) button.addEventListener('click', () => { openBugReport(); });
}

export default {
  initBugReport,
  openBugReport,
  closeBugReport,
  addFiles,
  _state,
};