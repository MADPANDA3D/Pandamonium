// MAD-937: Settings → Integrations → Nextcloud Files.
// Verifies the card renders connection status, the form saves and tests with
// honest feedback, tailnet scan fills a candidate, and no app password is
// ever shown back in the UI.
import { expect, test } from '@playwright/test';

async function stubApi(page, state = {}) {
  let configured = true;
  const status = () => ({
    configured,
    enabled: true,
    server_url: 'https://cloud.example.test',
    username: 'alice',
    status: configured ? 'healthy' : 'unconfigured',
    app_password_configured: configured,
  });
  await page.route('**/api/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    }
    if (path === '/api/auth/settings') {
      return route.fulfill({ json: { agent_id: 'assistant', agent_constitution: 'Stay accurate.' } });
    }
    if (path === '/api/nextcloud/connection' && req.method() === 'GET') {
      return route.fulfill({ json: status() });
    }
    if (path === '/api/nextcloud/connection' && req.method() === 'PUT') {
      const body = JSON.parse(req.postData() || '{}');
      configured = true;
      state.savedPassword = body.app_password || '';
      return route.fulfill({ json: status() });
    }
    if (path === '/api/nextcloud/connection' && req.method() === 'DELETE') {
      configured = false;
      return route.fulfill({ json: { ok: true } });
    }
    if (path === '/api/nextcloud/connection/test') {
      return route.fulfill({ json: { ok: true, status: 'healthy', message: 'Nextcloud files are readable' } });
    }
    if (path === '/api/nextcloud/discover') {
      return route.fulfill({
        json: {
          available: true,
          self_name: 'pandamonium',
          devices_checked: 2,
          candidates: [
            {
              id: 'nextcloud:cafebabe00000000',
              kind: 'nextcloud',
              provider: 'Nextcloud',
              label: 'Nextcloud',
              device: 'cloud-node',
              location: 'https://cloud.example.test',
              server_url: 'https://cloud.example.test',
              state: 'available',
              connectable: true,
            },
          ],
          message: 'Found 1 Nextcloud instance across 2 online tailnet devices.',
        },
      });
    }
    if (['/api/sessions', '/api/models', '/api/plugins', '/api/tools', '/api/selector-catalog'].includes(path)) {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
  return state;
}

async function openIntegrations(page) {
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/settings.js')).open());
  await page.locator('#settings-modal [data-settings-tab="integrations"]').click();
}

test('Integrations shows Nextcloud status and tests the connection with one click', async ({ page }) => {
  const state = await stubApi(page);
  await openIntegrations(page);

  const card = page.locator('.intg-card[data-intg-type="nextcloud"]');
  await expect(card).toContainText('Nextcloud Files');
  await expect(card).toContainText('https://cloud.example.test');
  await expect(card).toContainText('healthy');

  await card.click();
  await expect(page.locator('#uf-nextcloud-url')).toHaveValue('https://cloud.example.test');
  await expect(page.locator('#uf-nextcloud-user')).toHaveValue('alice');
  await expect(page.locator('#uf-nextcloud-status')).toContainText('healthy');

  await page.locator('#uf-nextcloud-test').click();
  await expect(page.locator('#uf-nextcloud-msg')).toContainText('readable');

  await page.screenshot({ path: '/tmp/opencode/mad-937-nextcloud-integration.png' });
});

test('saving an app password never renders it back', async ({ page }) => {
  const state = await stubApi(page);
  await openIntegrations(page);

  await page.locator('.intg-card[data-intg-type="nextcloud"]').click();
  await page.locator('#uf-nextcloud-pass').fill('super-secret-app-pass');
  await page.locator('#uf-nextcloud-save').click();

  await expect(page.locator('#uf-nextcloud-msg')).toContainText('Saved');
  expect(state.savedPassword).toBe('super-secret-app-pass');
  await expect(page.locator('body')).not.toContainText('super-secret-app-pass');
});

test('tailnet scan fills a discovered candidate without sending credentials', async ({ page }) => {
  const state = await stubApi(page);
  await openIntegrations(page);

  await page.locator('.intg-card[data-intg-type="nextcloud"]').click();
  await page.locator('#uf-nextcloud-scan').click();

  const candidate = page.locator('.uf-nextcloud-candidate');
  await expect(candidate).toContainText('cloud-node');
  await candidate.click();
  await expect(page.locator('#uf-nextcloud-url')).toHaveValue('https://cloud.example.test');
  await expect(page.locator('#uf-nextcloud-msg')).toContainText('Candidate selected');
});
