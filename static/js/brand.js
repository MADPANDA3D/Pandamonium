const DEFAULT_BRAND = Object.freeze({
  name: 'WhoAmI',
  logo: '',
  accent: '#e06c75',
});

export const MAX_LOGO_BYTES = 512 * 1024;
const LOGO_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const LOGO_DATA_URL = /^data:image\/(?:png|jpeg|webp);base64,([a-z0-9+/]+={0,2})$/i;
const ACCENT = /^#[0-9a-f]{6}$/i;
const CATEGORY_C = /\p{C}/u;
const ROUTE_LABELS = {
  '/calendar': 'Calendar',
  '/notes': 'Notes',
  '/cookbook': 'Cookbook',
  '/email': 'Email',
  '/memory': 'Memory',
  '/gallery': 'Gallery',
  '/tasks': 'Tasks',
  '/library': 'Library',
};

let currentBrand = { ...DEFAULT_BRAND };
let brandPromise = null;

function logoBytes(value) {
  const match = LOGO_DATA_URL.exec(value);
  if (!match) return -1;
  const padding = match[1].endsWith('==') ? 2 : match[1].endsWith('=') ? 1 : 0;
  return Math.floor((match[1].length * 3) / 4) - padding;
}

function validName(value) {
  const name = typeof value === 'string' ? value.trim() : '';
  return name && [...name].length <= 48 && !CATEGORY_C.test(name) ? name : '';
}

function validLogo(value) {
  if (value === '') return '';
  if (typeof value !== 'string') return null;
  const size = logoBytes(value);
  return size >= 0 && size <= MAX_LOGO_BYTES ? value : null;
}

export function normalizeBrand(raw = {}) {
  const name = validName(raw.name);
  const logo = validLogo(raw.logo);
  const accent = typeof raw.accent === 'string' && ACCENT.test(raw.accent) ? raw.accent.toLowerCase() : '';
  return {
    name: name || DEFAULT_BRAND.name,
    logo: logo === null ? DEFAULT_BRAND.logo : logo,
    accent: accent || DEFAULT_BRAND.accent,
  };
}

export function validateBrand(raw = {}) {
  const name = validName(raw.name);
  if (!name) throw new Error('Harness name must be 1–48 visible characters.');
  const logo = validLogo(raw.logo);
  if (logo === null) throw new Error('Logo must be a PNG, JPEG, or WebP image no larger than 512 KiB.');
  if (typeof raw.accent !== 'string' || !ACCENT.test(raw.accent)) {
    throw new Error('Accent must be a six-digit hex color.');
  }
  return { name, logo, accent: raw.accent.toLowerCase() };
}

export function getBrand() {
  return { ...currentBrand };
}

export function getBrandName() {
  return currentBrand.name;
}

export function getBrandChatName() {
  return `${currentBrand.name} Chat`;
}

function titleFor(brand, pathname) {
  const path = String(pathname || '/').toLowerCase();
  if (path === '/login') return `${brand.name} — Login`;
  const route = ROUTE_LABELS[path];
  return route ? `${route} — ${brand.name}` : `${brand.name} Chat`;
}

function boatIcon(accent) {
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><path d='M16 4L16 22L6 22Z' fill='${accent}'/><path d='M16 8L16 22L24 22Z' fill='${accent}' opacity='0.6'/><path d='M4 24Q10 20 16 24Q22 28 28 24' stroke='${accent}' stroke-width='2.5' fill='none' stroke-linecap='round'/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function setIcon(doc, selector, rel, href) {
  let link = doc.querySelector(selector);
  if (!link && doc.createElement && doc.head) {
    link = doc.createElement('link');
    link.rel = rel;
    doc.head.appendChild(link);
  }
  if (link) link.href = href;
}

function setManifest(doc, brand, pathname) {
  if (pathname !== '/' && pathname !== '/login') return;
  if (!globalThis.Blob || !globalThis.URL?.createObjectURL) return;
  let link = doc.querySelector("link[rel='manifest']");
  if (!link && doc.createElement && doc.head) {
    link = doc.createElement('link');
    link.rel = 'manifest';
    doc.head.appendChild(link);
  }
  if (!link) return;

  const logoType = brand.logo.match(/^data:(image\/(?:png|jpeg|webp));/i)?.[1]?.toLowerCase();
  const icons = brand.logo
    ? [{ src: brand.logo, sizes: 'any', type: logoType, purpose: 'any' }]
    : [
        { src: '/static/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any maskable' },
        { src: '/static/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
        { src: '/static/icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
      ];
  const manifest = {
    name: brand.name,
    short_name: [...brand.name].slice(0, 24).join(''),
    description: 'Self-hosted AI chat with memory, documents, and tools',
    start_url: '/',
    scope: '/',
    display: 'standalone',
    orientation: 'any',
    background_color: '#282c34',
    theme_color: brand.accent,
    icons,
  };
  const manifestUrl = globalThis.URL.createObjectURL(new Blob(
    [JSON.stringify(manifest)],
    { type: 'application/manifest+json' },
  ));
  const previous = globalThis.window?._instanceBrandManifestUrl;
  link.href = manifestUrl;
  if (globalThis.window) globalThis.window._instanceBrandManifestUrl = manifestUrl;
  if (previous && previous !== manifestUrl) globalThis.URL.revokeObjectURL?.(previous);
}

function formatResponseDetail(body, fallback) {
  const detail = body?.detail ?? body?.error;
  if (Array.isArray(detail)) {
    const messages = detail.map(item => {
      const message = typeof item === 'string' ? item : item?.msg;
      return String(message || '').replace(/^Value error,\s*/i, '').trim();
    }).filter(Boolean);
    return messages.length ? messages.join('; ') : fallback;
  }
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail.message === 'string') return detail.message;
  return fallback;
}

export function applyBrand(raw, doc = globalThis.document, pathname = globalThis.location?.pathname || '/') {
  const brand = normalizeBrand(raw);
  const previousChatName = `${currentBrand.name} Chat`;
  currentBrand = brand;
  if (!doc) return getBrand();

  if (globalThis.window) globalThis.window._instanceBrand = getBrand();
  doc.documentElement?.style?.setProperty('--brand-color', brand.accent);
  doc.documentElement?.classList?.toggle('has-instance-logo', Boolean(brand.logo));
  doc.querySelectorAll?.('[data-brand-name]').forEach(node => { node.textContent = brand.name; });
  doc.querySelectorAll?.('[data-brand-chat-name]').forEach(node => {
    // `#current-meta` becomes live session state after session hydration. A
    // delayed brand read may update the empty-chat label, but never a real
    // session name.
    if (node.id === 'current-meta' && node.textContent !== previousChatName) return;
    node.textContent = `${brand.name} Chat`;
  });
  doc.querySelectorAll?.('[data-brand-message-placeholder]').forEach(node => {
    node.setAttribute('placeholder', `Message ${brand.name}...`);
  });
  doc.querySelectorAll?.('[data-brand-logo]').forEach(node => {
    node.hidden = !brand.logo;
    if (brand.logo) node.src = brand.logo;
    else node.removeAttribute?.('src');
  });
  doc.querySelectorAll?.('[data-brand-logo-fallback]').forEach(node => { node.hidden = Boolean(brand.logo); });
  doc.title = titleFor(brand, pathname);

  // Custom identity wins everywhere. Without one, keep Mark 7's route-specific
  // favicons and only recolor the default root/login boat.
  if (brand.logo || pathname === '/' || pathname === '/login') {
    const icon = brand.logo || boatIcon(brand.accent);
    setIcon(doc, "link[rel='icon']", 'icon', icon);
    setIcon(doc, "link[rel='apple-touch-icon']", 'apple-touch-icon', icon);
  }
  setManifest(doc, brand, pathname);
  if (globalThis.window?.CustomEvent) {
    globalThis.window.dispatchEvent(new globalThis.window.CustomEvent('instance-brand-changed', {
      detail: getBrand(),
    }));
  }
  return getBrand();
}

export async function loadBrand(fetchImpl = globalThis.fetch, doc = globalThis.document) {
  if (brandPromise) return brandPromise;
  brandPromise = (async () => {
    try {
      const response = await fetchImpl('/api/brand', { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Brand request failed (${response.status})`);
      return applyBrand(await response.json(), doc);
    } catch (_) {
      return applyBrand(DEFAULT_BRAND, doc);
    }
  })();
  return brandPromise;
}

export async function saveBrand(raw, fetchImpl = globalThis.fetch, doc = globalThis.document) {
  const brand = validateBrand(raw);
  const response = await fetchImpl('/api/admin/brand', {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(brand),
  });
  if (!response.ok) {
    let message = `Brand save failed (${response.status})`;
    try {
      const body = await response.json();
      message = formatResponseDetail(body, message);
    } catch (_) {}
    throw new Error(message);
  }
  const stored = await response.json();
  const applied = applyBrand(stored.brand || stored, doc);
  brandPromise = Promise.resolve(applied);
  return applied;
}

export function readLogoFile(file, FileReaderImpl = globalThis.FileReader) {
  if (!file || !LOGO_TYPES.has(file.type)) {
    return Promise.reject(new Error('Choose a PNG, JPEG, or WebP image.'));
  }
  if (file.size > MAX_LOGO_BYTES) {
    return Promise.reject(new Error('Logo must be 512 KiB or smaller.'));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReaderImpl();
    reader.onerror = () => reject(new Error('Could not read the logo file.'));
    reader.onload = () => {
      const logo = validLogo(reader.result);
      if (logo === null) reject(new Error('Logo must be a PNG, JPEG, or WebP image no larger than 512 KiB.'));
      else resolve(logo);
    };
    reader.readAsDataURL(file);
  });
}

export { DEFAULT_BRAND };
