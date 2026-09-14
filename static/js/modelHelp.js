// modelHelp.js — MAD-931: per-section guided help for the AI Defaults model
// cards. Reuses the existing tour tooltip + halo (same classes, same injected
// style block) so Settings help and the product tours share one visual
// language; no new modal framework.
//
// The copy is operator-facing only: it explains what each lane does, what to
// pick, how fallbacks behave, and the cost/latency tradeoff, anchored to the
// live selects (endpoint names and model ids — never credentials).

import { bindMenuDismiss } from './escMenuStack.js';
import setupWizardModule from './setupWizard.js';

const STYLES_ID = 'tour-styles';
const TOUR_STYLES = `
  #tour-tooltip{position:fixed;z-index:10001;background:var(--bg);color:var(--fg);
    border:1px solid var(--border);border-radius:8px;padding:12px 14px;max-width:280px;
    font-family:inherit;font-size:0.8rem;line-height:1.5;
    box-shadow:0 2px 12px rgba(0,0,0,0.3);pointer-events:auto;
    opacity:0;transform:translateY(4px);transition:opacity 0.3s ease-out,transform 0.3s ease-out}
  #tour-tooltip.tour-fade-in{opacity:1;transform:translateY(0)}
  #tour-tooltip .tour-text{margin-bottom:8px;opacity:0.8}
  .tour-arrow{position:absolute;width:10px;height:10px;background:var(--bg);
    border:1px solid var(--border);transform:rotate(45deg);pointer-events:none}
  .tour-nav{display:flex;align-items:center;justify-content:space-between}
  .tour-nav button{background:none;border:1px solid var(--border);color:var(--fg);
    cursor:pointer;font-family:inherit;border-radius:4px;transition:all .1s}
  .tour-nav button:hover{background:color-mix(in srgb,var(--fg) 8%,transparent)}
  .tour-btn-arrow{font-size:1rem;padding:4px 12px;opacity:0.6}
  .tour-btn-arrow:hover{opacity:1}
  .tour-btn-skip{font-size:0.72rem;padding:3px 10px;opacity:0.35;border-color:transparent!important}
  .tour-btn-skip:hover{opacity:0.6}
`;

function _ensureTourStyles() {
  if (document.getElementById(STYLES_ID)) return;
  const style = document.createElement('style');
  style.id = STYLES_ID;
  style.textContent = TOUR_STYLES;
  document.head.appendChild(style);
}

/* ── Live anchor readouts (no secrets) ── */

function _selectedText(id) {
  const select = document.getElementById(id);
  if (!select || !select.value) return '';
  return String(select.options[select.selectedIndex]?.textContent || '').trim();
}

function _fallbackCount(id) {
  const host = document.getElementById(id);
  return host ? host.querySelectorAll('.settings-fallback-row').length : 0;
}

function _join(parts) {
  return parts.filter(Boolean).join(' · ');
}

function _fallbackSuffix(count) {
  if (!count) return '';
  return `${count} fallback${count === 1 ? '' : 's'}`;
}

/* ── Copy + live anchor per lane ── */

export const MODEL_HELP = {
  utility: {
    label: 'Utility model',
    what: 'Runs the small background jobs — naming chats, tidying text, pulling memories, compacting long conversations — so your main chat model stays free for the conversation.',
    pick: 'A small, fast model that is always reachable. A local endpoint is ideal: no per-use cost, no network hop, and it keeps working when a cloud provider is down.',
    fallback: 'If this lane is left blank it follows your default chat model, and the fallbacks you list here are tried in order when the chosen model fails.',
    cost: 'Local models cost nothing per run and are quick on modern hardware. Hosted models bill by tokens; a small cheap model is normally plenty for these chores.',
    anchor: () => _join([
      'Endpoint ' + (_selectedText('set-utilityEpSelect') || 'not pinned'),
      'Model ' + (_selectedText('set-utilityModelSelect') || 'follows your chat model'),
      _fallbackSuffix(_fallbackCount('set-utilityFallbacks')),
    ]),
  },
  vision: {
    label: 'Vision',
    what: 'Reads images — describing a photo, answering questions about a screenshot, and pulling text out of scans — whenever an image is part of the conversation.',
    pick: 'A model that accepts images. Larger multimodal models handle small print and busy scenes better; local vision models keep images private and free but run slower.',
    fallback: 'With nothing pinned, Pandamonium auto-detects an image-capable model from your connected endpoints, then tries the fallbacks you list in order.',
    cost: 'Images consume many tokens, so hosted vision calls cost more than a text turn. Local vision keeps the cost at zero and the image on your machine.',
    anchor: () => _join([
      'Model ' + (_selectedText('set-vlModelSelect') || 'Auto-detect picks a vision model'),
      _fallbackSuffix(_fallbackCount('set-visionFallbacks')),
    ]),
  },
  research: {
    label: 'Research model',
    what: 'Drives Deep Research: planning the investigation, reading pages, cross-checking sources, and writing the long answer.',
    pick: 'A strong model with a large context window. Leave it blank to reuse your chat model, or dedicate a capable model so long research runs do not compete with everyday chat.',
    fallback: 'With no research model set, Deep Research uses the same model as chat. If a dedicated model is set and fails, Pandamonium retries with the fallbacks you configured for it.',
    cost: 'Research makes many calls, so it is the most expensive lane. A mid-priced model with a large window is usually the best balance; a local model keeps it free but slower.',
    anchor: () => _join([
      'Endpoint ' + (_selectedText('set-researchEndpoint') || 'Same as chat'),
      'Model ' + (_selectedText('set-researchModel') || 'Same as chat'),
    ]),
  },
  image: {
    label: 'Image generation',
    what: 'Creates and edits images from your prompts, including inpainting and outpainting in the editor.',
    pick: 'An image-capable model; editing works best with one that supports inpainting. Quality sets how much time and money each image spends — low is quickest, high is most detailed.',
    fallback: 'If no model is pinned, Pandamonium auto-detects a suitable image model from your endpoints. Fallbacks cover the case where the selected model is unavailable.',
    cost: 'Hosted image models bill per image and per quality level. Local models are free but need a capable GPU and take longer for each render.',
    anchor: () => _join([
      'Model ' + (_selectedText('set-imgModelSelect') || 'Auto-detect'),
      'Quality ' + (_selectedText('set-imgQualitySelect') || 'Medium'),
    ]),
  },
  voice: {
    label: 'Voice',
    what: 'Speaks replies out loud during voice calls. The chat agent still does the thinking; this only chooses how it sounds.',
    pick: 'Built-in browser speech is instant and free but sounds synthetic. A local voice is free and more natural. A hosted voice sounds best and bills per character.',
    fallback: 'If a voice or model is left unpinned, the default voice is used, and each identity can keep its own voice mapping.',
    cost: 'Browser speech costs nothing. Local speech costs nothing but uses your hardware. Hosted speech adds a little network latency and a per-character charge.',
    anchor: () => _join([
      'Voice ' + (_selectedText('set-ttsProviderSelect') || 'Off'),
      'Model ' + (_selectedText('set-ttsModelSelect') || 'provider default'),
    ]),
  },
};

/* ── Popup ── */

let _state = null;

function _runChatCommand(command) {
  const messageInput = document.getElementById('message');
  const chatForm = document.getElementById('chat-form');
  if (!messageInput || !chatForm) return;
  messageInput.value = command;
  messageInput.dispatchEvent(new Event('input', { bubbles: true }));
  chatForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
}

function _positionPopup(popup, target) {
  popup.style.visibility = 'hidden';
  popup.style.display = '';
  const width = popup.offsetWidth || 280;
  const height = popup.offsetHeight || 160;
  const rect = target.getBoundingClientRect();
  const gap = 12;
  let top;
  let left;
  if (rect.bottom + gap + height < window.innerHeight - 10) {
    top = rect.bottom + gap;
    left = rect.left + rect.width / 2 - width / 2;
  } else if (rect.top - gap - height > 10) {
    top = rect.top - gap - height;
    left = rect.left + rect.width / 2 - width / 2;
  } else {
    top = rect.top;
    left = rect.right + gap;
    if (left + width > window.innerWidth - 10) left = rect.left - width - gap;
  }
  if (left + width > window.innerWidth - 10) left = window.innerWidth - width - 10;
  if (left < 10) left = 10;
  if (top < 10) top = 10;
  popup.style.top = top + 'px';
  popup.style.left = left + 'px';
  popup.style.visibility = '';
}

function _makeHalo(target) {
  const halo = document.createElement('div');
  halo.className = 'tour-halo';
  document.body.appendChild(halo);
  const update = () => {
    const rect = target.getBoundingClientRect();
    halo.style.top = (rect.top - 4) + 'px';
    halo.style.left = (rect.left - 4) + 'px';
    halo.style.width = (rect.width + 8) + 'px';
    halo.style.height = (rect.height + 8) + 'px';
  };
  update();
  window.addEventListener('resize', update);
  window.addEventListener('scroll', update, true);
  requestAnimationFrame(() => halo.classList.add('tour-fade-in'));
  return {
    destroy() {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      halo.remove();
    },
  };
}

/**
 * Close the open help popup. Restores focus to the originating "?" button
 * unless the caller is navigating somewhere else.
 */
export function closeModelHelp({ restoreFocus = true } = {}) {
  if (!_state) return;
  const { popup, halo, anchorButton, close } = _state;
  _state = null;
  try { close && close(); } catch (_) {}
  halo?.destroy();
  popup?.remove();
  if (anchorButton) anchorButton.setAttribute('aria-expanded', 'false');
  if (restoreFocus && anchorButton && anchorButton.isConnected) {
    try { anchorButton.focus({ preventScroll: true }); } catch (_) {}
  }
}

export function openModelHelp(key, anchorButton) {
  const help = MODEL_HELP[key];
  if (!help || !anchorButton) return;
  closeModelHelp({ restoreFocus: false });
  try { window.cancelActiveTour?.(); } catch (_) {}

  _ensureTourStyles();
  const card = anchorButton.closest('.admin-card') || anchorButton;
  const halo = _makeHalo(card);

  const anchorLine = (() => {
    try { return help.anchor(); } catch (_) { return ''; }
  })();

  const popup = document.createElement('div');
  popup.id = 'tour-tooltip';
  popup.dataset.modelHelpKey = key;
  popup.setAttribute('role', 'dialog');
  popup.setAttribute('aria-label', help.label + ' help');
  popup.innerHTML =
    '<div class="tour-help-title">' + help.label + '</div>' +
    '<div class="tour-text tour-help-body">' +
      '<p><b>What it does:</b> ' + help.what + '</p>' +
      '<p><b>What to pick:</b> ' + help.pick + '</p>' +
      '<p><b>If it fails:</b> ' + help.fallback + '</p>' +
      '<p><b>Cost &amp; speed:</b> ' + help.cost + '</p>' +
      (anchorLine ? '<p class="tour-help-now"><b>Right now:</b> ' + anchorLine + '.</p>' : '') +
    '</div>' +
    '<div class="tour-nav tour-help-actions">' +
      '<button type="button" class="tour-btn-arrow" data-act="tour">Walk me through all five</button>' +
      '<button type="button" class="tour-btn-skip" data-act="setup">Guided setup</button>' +
      '<button type="button" class="tour-btn-skip" data-act="close">Close</button>' +
    '</div>';
  document.body.appendChild(popup);

  const close = bindMenuDismiss(
    popup,
    () => closeModelHelp(),
    (event) => !popup.contains(event.target) && event.target !== anchorButton,
  );
  _state = { key, popup, halo, anchorButton, close };
  anchorButton.setAttribute('aria-expanded', 'true');

  popup.addEventListener('click', (event) => {
    const button = event.target.closest && event.target.closest('[data-act]');
    if (!button) return;
    const action = button.dataset.act;
    if (action === 'close') {
      closeModelHelp();
    } else if (action === 'tour') {
      closeModelHelp({ restoreFocus: false });
      _runChatCommand('/tour-models');
    } else if (action === 'setup') {
      closeModelHelp({ restoreFocus: false });
      try {
        setupWizardModule.open();
      } catch (_) {
        _runChatCommand('/setup');
      }
    }
  });

  requestAnimationFrame(() => {
    if (!popup.isConnected) return;
    _positionPopup(popup, card);
    popup.classList.add('tour-fade-in');
    popup.querySelector('[data-act]')?.focus({ preventScroll: true });
  });
}

let _initialized = false;

/** Bind the delegated "?" affordance once. */
export function initModelHelp() {
  if (_initialized) return;
  _initialized = true;
  document.addEventListener('click', (event) => {
    const button = event.target.closest && event.target.closest('[data-model-help]');
    if (!button) {
      // Switching settings tabs or closing the modal retires an open popup.
      if (_state && (
        event.target.closest?.('.settings-nav-item')
        || event.target.closest?.('.settings-modal-content .close-btn')
      )) {
        closeModelHelp({ restoreFocus: false });
      }
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const key = button.getAttribute('data-model-help');
    if (_state && _state.key === key) {
      closeModelHelp();
      return;
    }
    openModelHelp(key, button);
  });
}

export default { init: initModelHelp, open: openModelHelp, close: closeModelHelp, MODEL_HELP };
