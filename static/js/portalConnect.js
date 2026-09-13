export const PORTAL_URL_PATTERN = /^https?:\/\/[^\s]+$/i;
export const PORTAL_KEY_MIN_LENGTH = 20;

const PORTAL_URL_ISSUE = 'Enter the full Portal MCP URL, for example https://portal.example.com/api/mcp.';
const PORTAL_KEY_ISSUE = 'Paste the complete master key from your Portal account.';
const PORTAL_CONNECT_ISSUE = "We couldn't connect to your Portal. Check the URL and master key, then try again.";
const PORTAL_REACH_ISSUE = "We couldn't reach your Portal. Check the URL and that this machine can reach it, then try again.";

export function portalUrlIssue(value) {
  return PORTAL_URL_PATTERN.test(String(value || '').trim()) ? '' : PORTAL_URL_ISSUE;
}

export function portalMasterKeyIssue(value) {
  return String(value || '').trim().length >= PORTAL_KEY_MIN_LENGTH ? '' : PORTAL_KEY_ISSUE;
}

export async function connectPortal(options = {}) {
  const apiBase = options.apiBase || '';
  const fetchImpl = options.fetchImpl || fetch;
  const portalUrl = String(options.portalUrl || '').trim();
  const masterKey = String(options.masterKey || '').trim();

  if (portalUrlIssue(portalUrl)) return { ok: false, failure: 'portal_url', payload: {} };
  if (portalMasterKeyIssue(masterKey)) return { ok: false, failure: 'master_key', payload: {} };

  try {
    const response = await fetchImpl(`${apiBase}/api/mcp/portal/connect`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ master_key: masterKey, portal_url: portalUrl }),
    });
    let payload = {};
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) return { ok: false, failure: 'http_error', status: response.status, payload };
    return { ok: true, status: response.status, payload };
  } catch (_) {
    return { ok: false, failure: 'unreachable', payload: {} };
  }
}

export function portalConnectFailureMessage(result) {
  if (result && result.failure === 'portal_url') return PORTAL_URL_ISSUE;
  if (result && result.failure === 'master_key') return PORTAL_KEY_ISSUE;
  if (result && result.failure === 'unreachable') return PORTAL_REACH_ISSUE;
  return PORTAL_CONNECT_ISSUE;
}

export function portalConnectSuccessMessage(payload) {
  const tools = Number(payload && payload.tool_count) || 0;
  if (tools > 0) {
    return `MAD MCP Portal is connected — ${tools} tool${tools === 1 ? '' : 's'} available.`;
  }
  return 'MAD MCP Portal is connected.';
}

export default {
  connectPortal,
  portalUrlIssue,
  portalMasterKeyIssue,
  portalConnectFailureMessage,
  portalConnectSuccessMessage,
};
