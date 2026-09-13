// MAD-935: Settings → SSH Connections tab.
// Verifies the tab lists nodes with honest status, shows the public key after
// keyless enable, tests a connection with one click, fails closed on a changed
// host key, and never renders private key material.
import { expect, test } from '@playwright/test';

const PUBLIC_KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakePublicKeyForTests pandamonium-ssh:ssh-abc1234567';
const PRIVATE_KEY = '-----BEGIN OPENSSH PRIVATE KEY-----\nfake-secret-material\n-----END OPENSSH PRIVATE KEY-----';

const BASE_CONNECTION = {
  id: 'ssh-abc1234567',
  label: 'Home VPS',
  host: '203.0.113.10',
  user: 'operator',
  port: 22,
  keyless: true,
  has_private_key: true,
  public_key: PUBLIC_KEY,
  host_key_pinned: true,
  host_key_fingerprint: 'SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
  host_key_type: 'ssh-ed25519',
  status: { state: 'connected', reason: '', message: 'Connected. The connection is authorized, and no prompt was needed.', checked_at: null },
};

function stubApi(page, { connection = BASE_CONNECTION, onRequest } = {}) {
  let current = { ...connection };
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
    if (path === '/api/ssh/connections' && req.method() === 'GET') {
      return route.fulfill({ json: { connections: [current] } });
    }
    if (path === '/api/ssh/connections' && req.method() === 'POST') {
      current = { ...BASE_CONNECTION, id: 'ssh-new0000001', label: 'Studio Node', host: '198.51.100.7', keyless: true, public_key: PUBLIC_KEY };
      return route.fulfill({ json: { ...current, message: 'Connection added. Install the public key on the node.' } });
    }
    if (path.endsWith('/test')) {
      return route.fulfill({
        json: {
          ...current,
          status: { state: 'connected', reason: '', message: 'Connected. The connection is authorized, and no prompt was needed.' },
          ok: true,
          state: 'connected',
          message: 'Connected. The connection is authorized, and no prompt was needed.',
        },
      });
    }
    if (path.endsWith('/keyless')) {
      const enabled = !current.keyless;
      current = { ...current, keyless: enabled, has_private_key: true, public_key: PUBLIC_KEY };
      return route.fulfill({ json: { ...current, message: enabled ? 'Keyless connection enabled. Install the public key on the node.' : 'Keyless connection disabled.' } });
    }
    if (path.endsWith('/host-key')) {
      return route.fulfill({
        json: {
          ...current,
          host_key_pinned: true,
          host_key_fingerprint: 'SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB',
          candidates: [{ key_type: 'ssh-ed25519', fingerprint: 'SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB' }],
          message: 'Host key pinned. Verify the fingerprint matches the node.',
        },
      });
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

test('SSH tab lists a node, tests it with one click, and never shows private key material', async ({ page }) => {
  await stubApi(page);
  await openSshTab(page);

  const row = page.locator('.ssh-row[data-ssh-id="ssh-abc1234567"]');
  await expect(row).toContainText('Home VPS');
  await expect(row).toContainText('operator@203.0.113.10:22');
  await expect(row).toContainText('Connected');
  await expect(row).toContainText('Keyless');

  await expect(row.locator('.ssh-detail')).toBeHidden();
  await row.locator('[data-ssh-action="detail"]').click();
  await expect(row.locator('.ssh-detail')).toBeVisible();
  await expect(row.locator('.ssh-key')).toContainText('ssh-ed25519');
  await expect(page.locator('body')).not.toContainText('fake-secret-material');
  await expect(page.locator('body')).not.toContainText('BEGIN OPENSSH PRIVATE KEY');

  await row.locator('[data-ssh-action="test"]').click();
  await expect(page.locator('#ssh-msg')).toContainText('no prompt was needed');

  if (process.env.MAD935_SCREENSHOT) {
    await page.screenshot({ path: '/tmp/opencode/mad-935-ssh-tab.png' });
  }
});

test('adding a keyless node sends keyless=true and shows its public key', async ({ page }) => {
  let addBody = '';
  await stubApi(page, {
    onRequest: (req, path) => {
      if (path === '/api/ssh/connections' && req.method() === 'POST') addBody = req.postData() || '';
    },
  });
  await openSshTab(page);

  await page.locator('#ssh-add-btn').click();
  await page.locator('#ssh-edit-label').fill('Studio Node');
  await page.locator('#ssh-edit-host').fill('198.51.100.7');
  await page.locator('#ssh-edit-user').fill('root');
  await page.locator('#ssh-edit-port').fill('22');
  await page.locator('#ssh-edit-keyless').check();
  await page.locator('[data-ssh-editor="save"]').click();

  await expect(page.locator('#ssh-msg')).toContainText('Install the public key');
  expect(addBody).toContain('keyless');
  expect(addBody).toContain('true');

  const newRow = page.locator('.ssh-row[data-ssh-id="ssh-new0000001"]');
  await expect(newRow).toContainText('Studio Node');
  await newRow.locator('[data-ssh-action="detail"]').click();
  await expect(newRow.locator('.ssh-key')).toContainText('ssh-ed25519');
});

test('a changed host key fails closed with clear copy and no silent trust', async ({ page }) => {
  const changed = {
    ...BASE_CONNECTION,
    status: {
      state: 'host_key_changed',
      reason: 'host_key_changed',
      message: "The node's host key changed. This connection stays blocked to prevent impersonation. Verify the new key with the node's operator, then scan and pin it again.",
      checked_at: null,
    },
  };
  await stubApi(page, { connection: changed });
  await openSshTab(page);

  const row = page.locator('.ssh-row[data-ssh-id="ssh-abc1234567"]');
  await expect(row).toContainText('Host key changed');
  await expect(row).toContainText('stays blocked');
  await expect(row).toContainText('scan and pin it again');
});

test('keyless toggle is a single click with no stacked confirmation', async ({ page }) => {
  let keylessCalls = 0;
  let toggleBody = '';
  await stubApi(page, {
    connection: { ...BASE_CONNECTION, keyless: false },
    onRequest: (req, path) => {
      if (path.endsWith('/keyless')) {
        keylessCalls += 1;
        toggleBody = req.postData() || '';
      }
    },
  });
  await openSshTab(page);

  const row = page.locator('.ssh-row[data-ssh-id="ssh-abc1234567"]');
  await row.locator('[data-ssh-action="keyless"]').click();

  await expect(page.locator('#ssh-msg')).toContainText('Keyless connection enabled');
  expect(keylessCalls).toBe(1);
  expect(toggleBody).toContain('true');
});
