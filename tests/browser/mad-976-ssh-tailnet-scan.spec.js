// MAD-976: Settings → SSH Connections tailnet scan.
// Verifies the scan lists online tailnet nodes by name and OS, never exposes a
// tailnet address to the browser, and adds the selected node through the
// opaque peer id so the server resolves the address.
import { expect, test } from '@playwright/test';

const PEERS = [
  { id: 'a'.repeat(32), name: 'madpanda-workstation', os: 'linux' },
  { id: 'b'.repeat(32), name: 'oracle', os: 'linux' },
  { id: 'c'.repeat(32), name: 'iphone172', os: 'ios' },
];

const ADDED = {
  id: 'ssh-newtailnet1',
  label: 'oracle',
  host: '100.117.131.123',
  user: 'root',
  port: 22,
  keyless: true,
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
  await expect(panel).toContainText('ios');
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
  const oracleRow = page.locator('#ssh-tailnet-results .ssh-tailnet-row', { hasText: 'oracle' });
  await oracleRow.locator('[data-ssh-tailnet]').click();

  await expect(page.locator('#ssh-edit-label')).toHaveValue('oracle');
  await expect(page.locator('#ssh-edit-host')).toBeDisabled();
  await expect(page.locator('#ssh-edit-host')).toHaveValue('');
  await page.locator('#ssh-edit-user').fill('root');
  await page.locator('#ssh-edit-keyless').check();
  await page.locator('[data-ssh-editor="save"]').click();

  await expect(page.locator('#ssh-msg')).toContainText('Install the public key');
  expect(addBody).toContain('tailnet_peer_id');
  expect(addBody).toContain('b'.repeat(32));

  const row = page.locator('.ssh-row[data-ssh-id="ssh-newtailnet1"]');
  await expect(row).toContainText('oracle');
  await expect(row).toContainText('root@100.117.131.123:22');
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
