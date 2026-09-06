import { expect, test } from '@playwright/test';


test('footer keeps check and install distinct and exposes progress, backup, and rollback', async ({ page }) => {
  let checks = 0;
  let applies = 0;
  let statusPolls = 0;
  let versionReads = 0;
  await page.route('**/api/**', route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/version') {
      versionReads += 1;
      if (versionReads > 1) {
        return route.fulfill({ json: {
          version: '1.0.11',
          commit: '2222222222222222222222222222222222222222',
          release: '1.0.11-22222222',
          update_available: false,
          update_status: 'current',
          installation: { supported: true, trigger: 'systemd-path' },
        } });
      }
      return route.fulfill({ json: {
        version: '1.0.10',
        commit: '1111111111111111111111111111111111111111',
        release: 'mad790-11111111',
        update_available: false,
        update_status: 'current',
        installation: { supported: true, trigger: 'systemd-path' },
        operation: { status: 'idle' },
      } });
    }
    if (path === '/api/update/check') {
      checks += 1;
      expect(request.method()).toBe('POST');
      return route.fulfill({ json: {
        version: '1.0.10',
        commit: '1111111111111111111111111111111111111111',
        release: 'mad790-11111111',
        latest_version: '1.0.11',
        latest_commit: '2222222222222222222222222222222222222222',
        update_available: true,
        update_status: 'available',
        compatible: true,
        can_update: true,
        installation: { supported: true, trigger: 'systemd-path' },
        operation: { status: 'idle' },
      } });
    }
    if (path === '/api/update/apply') {
      applies += 1;
      expect(request.method()).toBe('POST');
      expect(request.postDataJSON()).toEqual({
        version: '1.0.11',
        commit: '2222222222222222222222222222222222222222',
      });
      return route.fulfill({ json: {
        status: 'queued', phase: 'queued', progress: 0,
        message: 'Update queued', rollback_available: false,
      } });
    }
    if (path === '/api/update/status') {
      if (!applies) return route.fulfill({ json: { status: 'idle' } });
      statusPolls += 1;
      return route.fulfill({ json: statusPolls < 2 ? {
        status: 'running', phase: 'backup', progress: 40,
        message: 'Creating and verifying full data backup',
        backup_location: '/var/backups/odysseus/update-1.0.11-proof',
        rollback_available: false,
      } : {
        status: 'succeeded', phase: 'complete', progress: 100,
        message: 'Updated to v1.0.11',
        backup_location: '/var/backups/odysseus/update-1.0.11-proof',
        rollback_available: true,
      } });
    }
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (path === '/api/sessions' || path === '/api/model-endpoints' || path === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });

  await page.goto('/static/index.html');
  await expect(page.locator('#sidebar-update-state')).toHaveText('Up to date');
  expect(checks).toBe(0);
  expect(applies).toBe(0);

  await page.locator('#sidebar-update-check').click();
  await expect(page.locator('#sidebar-update-action')).toHaveText('Update to v1.0.11');
  expect(checks).toBe(1);
  expect(applies).toBe(0);

  page.once('dialog', dialog => dialog.accept());
  await page.locator('#sidebar-update-action').click();
  await expect(page.locator('#sidebar-update-detail')).toContainText('Update queued');
  expect(applies).toBe(1);

  await expect(page.locator('#sidebar-update-detail')).toHaveText('Updated to v1.0.11', { timeout: 5000 });
  await expect(page.locator('#sidebar-update-backup')).toHaveText(
    'Backup: /var/backups/odysseus/update-1.0.11-proof',
  );
  await expect(page.locator('#sidebar-update-rollback')).toBeVisible();
});
