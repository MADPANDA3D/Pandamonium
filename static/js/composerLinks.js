import { linkHtml } from './markdown.js';

// Keep the existing textarea as the submission/draft contract. Rich paste is
// an editing surface over it, so every chat/voice/command caller keeps its API.
export function initComposerLinks() {
  const input = document.getElementById('message');
  if (!input || input.dataset.richPaste) return;
  input.dataset.richPaste = 'true';
  const editor = document.createElement('div');
  editor.id = 'message-editor';
  editor.contentEditable = 'true';
  editor.setAttribute('role', 'textbox');
  editor.setAttribute('aria-label', 'Message input');
  editor.setAttribute('aria-multiline', 'true');
  editor.hidden = true;
  input.after(editor);
  const value = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  const focus = input.focus.bind(input);
  const selection = input.setSelectionRange.bind(input);
  let pasting = false;
  const active = () => !editor.hidden;
  function raw(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.data;
    if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return '';
    if (node.dataset?.url) return node.dataset.url;
    if (node.nodeName === 'BR') return '\n';
    return [...node.childNodes].map(child => raw(child)).join('');
  }
  function rich(text) {
    const fragment = document.createDocumentFragment();
    let offset = 0;
    for (const match of text.matchAll(/https?:\/\/[^\s<>"'`]+/g)) {
      const url = match[0].replace(/[.,;:!?]+$/, '');
      fragment.append(document.createTextNode(text.slice(offset, match.index)));
      const chip = document.createElement('span');
      chip.dataset.url = url;
      chip.contentEditable = 'false';
      chip.className = 'composer-link';
      chip.innerHTML = linkHtml(url, url);
      fragment.append(chip);
      offset = match.index + url.length;
    }
    fragment.append(document.createTextNode(text.slice(offset)));
    return fragment;
  }
  function select(start, end = start) {
    const points = [];
    let offset = 0;
    function walk(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        points.push({ node, offset, size: node.length }); offset += node.length;
      } else if (node.dataset?.url || node.nodeName === 'BR') {
        const index = [...node.parentNode.childNodes].indexOf(node);
        const size = raw(node).length;
        points.push({ node: node.parentNode, offset, size, before: index, after: index + 1 }); offset += size;
      } else node.childNodes.forEach(walk);
    }
    walk(editor);
    function point(at) {
      const p = points.find(p => at <= p.offset + p.size);
      if (!p) return [editor, editor.childNodes.length];
      return [p.node, p.before === undefined ? Math.max(0, at - p.offset) : at <= p.offset ? p.before : p.after];
    }
    const range = document.createRange();
    range.setStart(...point(start)); range.setEnd(...point(end));
    const selected = window.getSelection(); selected.removeAllRanges(); selected.addRange(range);
  }
  function sync(event) {
    value.set.call(input, raw(editor));
    const selected = window.getSelection();
    if (selected.rangeCount && editor.contains(selected.anchorNode) && editor.contains(selected.focusNode)) {
      const range = selected.getRangeAt(0);
      const before = range.cloneRange(); before.selectNodeContents(editor); before.setEnd(range.startContainer, range.startOffset);
      const start = raw(before.cloneContents());
      selection(start.length, start.length + raw(range.cloneContents()).length);
    }
    input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: pasting ? 'insertFromPaste' : event?.inputType || 'insertText', isComposing: event?.isComposing || false }));
  }
  Object.defineProperty(input, 'value', {
    get() { return value.get.call(input); },
    set(text) {
      value.set.call(input, text);
      if (active()) {
        editor.replaceChildren(rich(String(text)));
        if (!text) { editor.hidden = true; input.hidden = false; input.required = true; }
      }
    },
  });
  input.focus = options => active() ? editor.focus(options) : focus(options);
  input.setSelectionRange = (start, end, direction) => { selection(start, end, direction); if (active()) select(start, end); };
  function paste(event) {
    const text = event.clipboardData?.getData('text/plain');
    if (!text || (!active() && !/https?:\/\//.test(text))) return;
    event.preventDefault();
    if (!active()) {
      const start = input.selectionStart, end = input.selectionEnd;
      editor.replaceChildren(rich(input.value));
      input.hidden = true; input.required = false; editor.hidden = false; editor.focus(); select(start, end);
    }
    sync();
    const end = input.selectionStart + text.length;
    const container = document.createElement('div'); container.append(rich(text));
    // Browser editing commands preserve native undo/redo, including atomic chips.
    pasting = true;
    try { document.execCommand('insertHTML', false, container.innerHTML.replace(/\n/g, '<br>')); }
    finally { pasting = false; }
    select(end);
    editor.focus();
  }
  input.addEventListener('paste', paste);
  editor.addEventListener('paste', paste);
  editor.addEventListener('input', sync);
  editor.addEventListener('keydown', event => {
    sync();
    const forwarded = new KeyboardEvent('keydown', { key: event.key, code: event.code, ctrlKey: event.ctrlKey, metaKey: event.metaKey, shiftKey: event.shiftKey, altKey: event.altKey, isComposing: event.isComposing, bubbles: true, cancelable: true });
    if (!input.dispatchEvent(forwarded)) event.preventDefault();
    else if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); document.execCommand('insertLineBreak'); }
    event.stopPropagation();
  });
  editor.addEventListener('beforeinput', event => {
    if (pasting) return;
    const forwarded = new InputEvent('beforeinput', { inputType: event.inputType, data: event.data, isComposing: event.isComposing, bubbles: true, cancelable: true });
    if (!input.dispatchEvent(forwarded)) event.preventDefault();
  });
  for (const name of ['copy', 'cut']) editor.addEventListener(name, event => {
    const selected = window.getSelection();
    if (!selected.rangeCount || selected.isCollapsed) return;
    event.preventDefault(); event.clipboardData.setData('text/plain', raw(selected.getRangeAt(0).cloneContents()));
    if (name === 'cut') document.execCommand('delete');
  });
  editor.addEventListener('click', event => { if (event.target.closest('a')) event.preventDefault(); });
  editor.addEventListener('dblclick', event => {
    const chip = event.target.closest('[data-url]');
    if (!chip) return;
    const range = document.createRange(); range.selectNode(chip);
    const selected = window.getSelection(); selected.removeAllRanges(); selected.addRange(range);
    document.execCommand('insertText', false, chip.dataset.url);
  });
  new MutationObserver(() => {
    editor.contentEditable = String(!input.disabled);
    editor.setAttribute('aria-disabled', String(input.disabled));
    editor.setAttribute('enterkeyhint', input.getAttribute('enterkeyhint') || 'send');
  }).observe(input, { attributes: true, attributeFilter: ['disabled', 'enterkeyhint'] });
}
