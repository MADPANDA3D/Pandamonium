// MAD-976: Settings → SSH Connections tailnet scan + keyless Tailscale SSH.
// Verifies the scan lists online tailnet nodes by name and OS (never exposing a
// tailnet address), that adding a node resolves the address server-side, that a
// node advertising Tailscale SSH is added keyless, and that finishing setup with
// the node password installs the managed key.
import { expect, test } from '@playwright/test';

const PEERS = [
  { id: 'a'.repeat(32), name: 'madpanda-workstation', os: 'linux', keyless: false },
  { id: 'b'.repeat(32), name: 'oracle', os: 'linux', keyless: true },
  { id: 'c'.repeat(32), name: 'iphone172', os: 'ios', keyless: false },
];

const ADDED = {
  id: 'ssh-newtailnet1',
  label: 'madpanda-workstation',
  host: '100.64.242.88',
  user: 'leo',
  port: 22,
  keyless: true,
  auth_mode: 'managed_key',
  has_private_key: true,
  public_key: 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKeyForTests pandamonium-ssh:ssh-newtailnet1',
  host_key_pinned: true,
  host_key_fingerprint: 'SHA256:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC',
  host_key_type: 'ssh-ed25519',
  status: { state: 'unknown', reason: '', message: '', checked_at: null },
};

function stubApi(page, { discover, initialConnections = [], onRequest } = {}) {
  let connections = initialConnections;
  return page.route('**/api/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (onRequest) onRequest(req, path);
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (path === '/api/auth/settings') {
      return route.fulfill({ json: { agent_id: 'assistant', agent_constitution: 'Stay accurate.' } });
    }
    if (path === '/api/ssh/discover') {
      return route.fulfill({ json: discover || { available: true, peers: PEERS, message: 'Found 3 online tailnet node(s).' } });
    }
    if (path === '/api/ssh/connections' && req.method() === 'GET') {
      return route.fulfill({ json: { connections } });
    }
    if (path === '/api/ssh/connections' && req.method() === 'POST') {
      connections = [ADDED];
      return route.fulfill({ json: { ...ADDED, message: 'Connection added. Install the public key on the node.' } });
    }
    if (path.endsWith('/install-key')) {
      connections = [{ ...connections[0], status: { state: 'connected', reason: '', message: 'Connected. The connection is authorized, and no prompt was needed.', checked_at: null } }];
      return route.fulfill({ json: { ...connections[0], ok: true, state: 'connected', message: 'Public key installed on the node. The connection is ready.' } });
    }
    if (['/api/sessions', '/api/models', '/api/plugins', '/api/tools', '/api/selector-catalog'].includes(path)) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

async function openSshTab(page) {
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#settings-modal [data-settings-tab="ssh"]').click();
}

test('scanning the tailnet lists nodes by name and OS without leaking addresses', async ({ page }) => {
  await stubApi(page);
  await openSshTab(page);

  await page.locator('#ssh-scan-btn').click();

  const panel = page.locator('#ssh-tailnet-results .ssh-tailnet-panel');
  await expect(panel).toBeVisible();
  await expect(panel).toContainText('madpanda-workstation');
  await expect(panel).toContainText('oracle');
  await expect(panel).toContainText('iphone172');
  await expect(panel).toContainText('Keyless (Tailscale SSH)');
  await expect(panel).toContainText('SSH not enabled');
  await expect(panel).toContainText('sudo tailscale set --ssh');
  await expect(page.locator('body')).not.toContainText('100.117.131.123');
  await expect(page.locator('body')).not.toContainText('100.64.242.88');
});

test('adding a scanned node sends the opaque peer id and resolves the address server-side', async ({ page }) => {
  let addBody = '';
  await stubApi(page, {
    onRequest: (req, path) => {
      if (path === '/api/ssh/connections' && req.method() === 'POST') addBody = req.postData() || '';
    },
  });
  await openSshTab(page);

  await page.locator('#ssh-scan-btn').click();
  const row = page.locator('#ssh-tailnet-results .ssh-tailnet-row', { hasText: 'madpanda-workstation' });
  await row.locator('[data-ssh-tailnet]').click();

  await expect(page.locator('#ssh-edit-label')).toHaveValue('madpanda-workstation');
  await expect(page.locator('#ssh-edit-host')).toBeDisabled();
  await expect(page.locator('#ssh-edit-host')).toHaveValue('');
  await page.locator('#ssh-edit-user').fill('leo');
  await page.locator('#ssh-edit-keyless').check();
  await page.locator('[data-ssh-editor="save"]').click();

  await expect(page.locator('#ssh-msg')).toContainText('Install the public key');
  expect(addBody).toContain('tailnet_peer_id');
  expect(addBody).toContain('a'.repeat(32));

  const saved = page.locator('.ssh-row[data-ssh-id="ssh-newtailnet1"]');
  await expect(saved).toContainText('madpanda-workstation');
  await expect(saved).toContainText('leo@100.64.242.88:22');
});

test('a node with Tailscale SSH is added keyless with no key setup', async ({ page }) => {
  await stubApi(page);
  await openSshTab(page);

  await page.locator('#ssh-scan-btn').click();
  const oracleRow = page.locator('#ssh-tailnet-results .ssh-tailnet-row', { hasText: 'oracle' });
  await expect(oracleRow).toContainText('Keyless (Tailscale SSH)');
  await oracleRow.locator('[data-ssh-tailnet]').click();

  await expect(page.locator('#ssh-edit-host')).toBeDisabled();
  await expect(page.locator('#ssh-edit-keyless')).toHaveCount(0);
  await expect(page.locator('#ssh-editor')).toContainText('no key or password is needed');
});

test('a Tailscale SSH connection hides key controls and shows the badge', async ({ page }) => {
  const tailscaleConn = {
    ...ADDED,
    label: 'oracle',
    host: '100.117.131.123',
    auth_mode: 'tailscale_ssh',
    has_private_key: false,
    public_key: '',
    host_key_pinned: false,
  };
  await stubApi(page, { initialConnections: [tailscaleConn] });
  await openSshTab(page);

  const row = page.locator('.ssh-row[data-ssh-id="ssh-newtailnet1"]');
  await expect(row).toContainText('Tailscale SSH');
  await expect(row).toContainText('no key needed');
  await expect(row.locator('[data-ssh-action="host-key"]')).toHaveCount(0);
  await expect(row.locator('[data-ssh-action="detail"]')).toHaveCount(0);
  await expect(row.locator('[data-ssh-action="test"]')).toHaveCount(1);
});

test('an unavailable or empty tailnet shows honest copy', async ({ page }) => {
  await stubApi(page, {
    discover: { available: false, peers: [], message: 'Tailscale is not available on this machine, so the tailnet cannot be scanned.' },
  });
  await openSshTab(page);

  await page.locator('#ssh-scan-btn').click();
  await expect(page.locator('#ssh-tailnet-results')).toContainText('Tailscale is not available');
  await expect(page.locator('#ssh-tailnet-results .ssh-tailnet-panel')).toHaveCount(0);
});

test('a Tailscale check requirement renders a clickable approval link', async ({ page }) => {
  const checkConn = {
    ...ADDED,
    label: 'madpanda-workstation',
    host: '100.64.242.88',
    auth_mode: 'tailscale_ssh',
    has_private_key: false,
    public_key: '',
    host_key_pinned: false,
    status: {
      state: 'check_required',
      reason: 'check_required',
      message: 'Approve this device on your tailnet, then test again: https://login.tailscale.com/a/abc123',
      checked_at: null,
    },
  };
  await stubApi(page, { initialConnections: [checkConn] });
  await openSshTab(page);

  const row = page.locator('.ssh-row[data-ssh-id="ssh-newtailnet1"]');
  await expect(row).toContainText('Approval required');
  const link = row.locator('a.ssh-approve-link');
  await expect(link).toHaveAttribute('href', 'https://login.tailscale.com/a/abc123');
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(row.locator('a.ssh-link')).toHaveAttribute('href', 'https://login.tailscale.com/a/abc123');
});

test('finishing setup installs the key with the node password and goes green', async ({ page }) => {
  let installBody = '';
  const authFailed = {
    ...ADDED,
    status: { state: 'auth_failed', reason: 'authentication_failed', message: 'The node rejected the key. Install this connection\u2019s public key on the node, then test again.', checked_at: null },
  };
  await stubApi(page, {
    initialConnections: [authFailed],
    onRequest: (req, path) => {
      if (path.endsWith('/install-key')) installBody = req.postData() || '';
    },
  });
  await openSshTab(page);

  const row = page.locator('.ssh-row[data-ssh-id="ssh-newtailnet1"]');
  await expect(row).toContainText('Auth failed');
  await row.locator('[data-ssh-action="detail"]').click();
  await row.locator('[data-ssh-password]').fill('hunter2');
  await row.locator('[data-ssh-action="install-key"]').click();

  await expect(page.locator('#ssh-msg')).toContainText('installed on the node');
  expect(installBody).toContain('password');
  expect(installBody).toContain('hunter2');
  await expect(row).toContainText('Connected');
});
