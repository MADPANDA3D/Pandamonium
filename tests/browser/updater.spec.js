import { expect, test } from '@playwright/test';


const OLD_COMMIT = '1111111111111111111111111111111111111111';
const NEW_COMMIT = '2222222222222222222222222222222222222222';

function shellRoutes(page, handler) {
  return page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const handled = handler(route, path);
    if (handled) return handled;
    if (path === '/api/auth/status') {
      return route.fulfill({ json: { username: 'leo', is_admin: true, privileges: {} } });
    }
    if (path === '/api/sessions' || path === '/api/model-endpoints' || path === '/api/models') {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ json: {} });
  });
}

test('updater dialog survives the restart gap and reconciles the installed version', async ({ page }) => {
  let checks = 0;
  let applies = 0;
  let applyAttempts = 0;
  let rollbacks = 0;
  let statusPolls = 0;
  let finishCheck;
  await page.addInitScript(() => {
    window.__nativeConfirmCalls = 0;
    window.confirm = () => {
      window.__nativeConfirmCalls += 1;
      return false;
    };
  });
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') {
      return route.fulfill({ json: applies ? {
        version: '1.0.11', commit: NEW_COMMIT, release: '1.0.11-22222222',
        latest_version: '1.0.11', update_available: false, update_status: 'current',
        compatible: true, can_update: false,
        installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
        release_check: { status: 'current', message: null },
      } : {
        version: '1.0.10', commit: OLD_COMMIT, release: '1.0.10-11111111',
        latest_version: '1.0.10', update_available: false, update_status: 'current',
        compatible: true, can_update: false,
        installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
        release_check: { status: 'current', message: null },
      } });
    }
    if (path === '/api/update/check') {
      checks += 1;
      return new Promise(resolve => {
        finishCheck = () => resolve(route.fulfill({ json: {
          version: '1.0.10', commit: OLD_COMMIT, release: '1.0.10-11111111',
          latest_version: '1.0.11', latest_commit: NEW_COMMIT,
          update_available: true, update_status: 'available', compatible: true, can_update: true,
          update_url: 'https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.11',
          installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
          release_check: { status: 'available', message: null },
        } }));
      });
    }
    if (path === '/api/update/apply') {
      applyAttempts += 1;
      expect(route.request().postDataJSON()).toEqual({ version: '1.0.11', commit: NEW_COMMIT });
      if (applyAttempts === 1) {
        return route.fulfill({
          status: 409,
          json: { detail: 'available release changed; check again before approving' },
        });
      }
      applies += 1;
      return route.fulfill({ json: {
        status: 'queued', phase: 'queued', progress: 0, message: 'Update queued',
        rollback_available: false,
      } });
    }
    if (path === '/api/update/rollback') {
      rollbacks += 1;
      return route.fulfill({ json: {
        status: 'queued', phase: 'rollback', progress: 0, message: 'Rollback queued',
        rollback_available: false,
      } });
    }
    if (path === '/api/update/status') {
      if (!applies) return route.fulfill({ json: { status: 'idle' } });
      statusPolls += 1;
      if (statusPolls === 1) {
        return route.fulfill({ json: {
          status: 'running', phase: 'backup', progress: 40,
          message: 'Creating and verifying full data backup',
          backup_location: '/var/backups/odysseus/update-1.0.11-proof',
          rollback_available: false,
        } });
      }
      if (statusPolls === 2) return route.abort('connectionrefused');
      return route.fulfill({ json: {
        status: 'succeeded', phase: 'complete', progress: 100,
        message: 'Updated to v1.0.11',
        backup_location: '/var/backups/odysseus/update-1.0.11-proof',
        rollback_available: true,
      } });
    }
    return null;
  });

  await page.goto('/static/index.html');
  await expect(page.locator('#sidebar-update-state')).toHaveText('Up to date');
  await expect(page.locator('#sidebar-update-check')).toBeVisible();
  await expect(page.locator('#sidebar-update-action')).toBeHidden();
  const checkMetrics = await page.locator('#sidebar-update-check').evaluate(button => ({
    width: button.getBoundingClientRect().width,
    rowWidth: button.parentElement.getBoundingClientRect().width,
    fontSize: parseFloat(getComputedStyle(button).fontSize),
  }));
  expect(Math.abs(checkMetrics.width - checkMetrics.rowWidth)).toBeLessThanOrEqual(1);
  expect(checkMetrics.fontSize).toBeGreaterThanOrEqual(12);

  await page.locator('#sidebar-update-check').click();
  await expect(page.locator('#updater-modal')).toBeVisible();
  await expect(page.locator('#updater-progress-card')).toHaveAttribute('data-state', 'working');
  await expect(page.locator('#updater-progress-title')).toHaveText('Scanning stable releases');
  await expect.poll(() => typeof finishCheck).toBe('function');
  finishCheck();
  await expect(page.locator('#updater-apply')).toBeVisible();
  await expect(page.locator('#updater-check')).toBeHidden();
  await expect(page.locator('#updater-release-summary')).toContainText('v1.0.11 is available');
  await expect(page.locator('#sidebar-update-check')).toBeHidden();
  await expect(page.locator('#sidebar-update-action')).toHaveText('Update to v1.0.11');
  const updateMetrics = await page.locator('#sidebar-update-action').evaluate(button => {
    const style = getComputedStyle(button);
    const color = style.backgroundColor.match(/\d+/g).map(Number);
    return {
      width: button.getBoundingClientRect().width,
      rowWidth: button.parentElement.getBoundingClientRect().width,
      fontSize: parseFloat(style.fontSize),
      isGreen: color[1] > color[0] && color[1] > color[2],
    };
  });
  expect(Math.abs(updateMetrics.width - updateMetrics.rowWidth)).toBeLessThanOrEqual(1);
  expect(updateMetrics.fontSize).toBeGreaterThanOrEqual(12);
  expect(updateMetrics.isGreen).toBe(true);
  const modalActionMetrics = await page.locator('#updater-apply').evaluate(button => ({
    width: button.getBoundingClientRect().width,
    rowWidth: button.parentElement.getBoundingClientRect().width,
    fontSize: parseFloat(getComputedStyle(button).fontSize),
    color: getComputedStyle(button).backgroundColor.match(/\d+/g).map(Number),
  }));
  expect(Math.abs(modalActionMetrics.width - modalActionMetrics.rowWidth)).toBeLessThanOrEqual(1);
  expect(modalActionMetrics.fontSize).toBeGreaterThanOrEqual(12);
  expect(modalActionMetrics.color[1]).toBeGreaterThan(modalActionMetrics.color[0]);
  expect(await page.locator('#updater-release-summary').evaluate(
    element => parseFloat(getComputedStyle(element).fontSize),
  )).toBeGreaterThanOrEqual(12);
  expect(checks).toBe(1);

  await page.locator('#close-updater-modal').click();
  await expect(page.locator('#sidebar-update-action')).toBeFocused();
  await page.locator('#sidebar-update-action').click();
  await expect(page.locator('#updater-modal')).toBeVisible();

  await page.locator('#updater-apply').click();
  await expect(page.locator('#styled-confirm-overlay')).toBeVisible();
  await expect(page.locator('#styled-confirm-ok')).toHaveClass(/confirm-btn-primary/);
  await expect(page.locator('#styled-confirm-ok')).not.toHaveClass(/confirm-btn-danger/);
  await expect(page.locator('#styled-confirm-ok')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.locator('#styled-confirm-cancel')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('#styled-confirm-overlay')).toBeHidden();
  await expect(page.locator('#updater-modal')).toBeVisible();
  await expect(page.locator('#updater-apply')).toBeFocused();
  expect(applies).toBe(0);

  await page.locator('#updater-apply').click();
  await page.locator('#styled-confirm-ok').click();
  await expect(page.locator('#updater-progress-title')).toHaveText('Scanning stable releases');
  await expect.poll(() => checks).toBe(2);
  finishCheck();
  await expect(page.locator('#updater-apply')).toBeVisible();
  expect(applies).toBe(0);

  await page.locator('#updater-apply').click();
  await page.locator('#styled-confirm-ok').click();
  await expect(page.locator('#updater-progress-title')).toContainText('full data backup');
  await expect(page.locator('#updater-progress-percent')).toHaveText('40%');
  await expect(page.locator('#updater-progress-fill')).toHaveCSS('width', /.+/);
  expect(await page.evaluate(() => window.__nativeConfirmCalls)).toBe(0);
  await page.keyboard.press('Escape');
  await expect(page.locator('#updater-modal')).toBeHidden();
  await expect(page.locator('#sidebar-update-check')).toBeEnabled();
  await expect(page.locator('#sidebar-update-check')).toHaveText('View update progress');
  await page.locator('#sidebar-update-check').click();
  await expect(page.locator('#updater-modal')).toBeVisible();
  expect(checks).toBe(2);
  await expect(page.locator('#updater-progress-card')).toHaveAttribute('data-state', 'reconnecting');
  await expect(page.locator('#updater-progress-card')).toHaveAttribute('data-state', 'complete', { timeout: 7000 });
  await expect(page.locator('#updater-installed-version')).toHaveText('v1.0.11');
  await expect(page.locator('#sidebar-update-version')).toHaveText('Version v1.0.11');
  await expect(page.locator('#updater-backup')).toContainText('/var/backups/odysseus/update-1.0.11-proof');
  await expect(page.locator('#updater-rollback')).toBeVisible();
  expect(await page.evaluate(() => performance.getEntriesByType('navigation').length)).toBe(1);

  await page.locator('#updater-rollback').click();
  await expect(page.locator('#styled-confirm-ok')).toHaveClass(/confirm-btn-danger/);
  await page.locator('#styled-confirm-cancel').click();
  await expect(page.locator('#updater-rollback')).toBeFocused();
  expect(rollbacks).toBe(0);
  await page.locator('#updater-rollback').click();
  await page.locator('#styled-confirm-ok').click();
  expect(rollbacks).toBe(1);
  expect(await page.evaluate(() => window.__nativeConfirmCalls)).toBe(0);

  await page.keyboard.press('Escape');
  await expect(page.locator('#updater-modal')).toBeHidden();
  await expect(page.locator('#sidebar-update-check')).toBeFocused();
});

test('host-managed container keeps provenance and separates a GitHub outage from update mode', async ({ page }) => {
  let checks = 0;
  const base = {
    version: '1.0.17', commit: '91cc845d26bb7e605bb07ff6107a54fbd0910394', release: null,
    latest_version: null, latest_commit: null, update_available: false, can_update: false,
    installation: {
      supported: false, kind: 'container', trigger: 'disabled',
      reason: 'Container updates must be run from the host.',
    },
  };
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') {
      return route.fulfill({ json: {
        ...base, update_status: 'unavailable',
        compatibility_reason: 'GitHub release metadata is unavailable',
        release_check: { status: 'unavailable', message: 'GitHub release metadata is unavailable' },
      } });
    }
    if (path === '/api/update/check') {
      checks += 1;
      return route.fulfill({ json: checks === 1 ? {
        ...base, update_status: 'unavailable',
        compatibility_reason: 'GitHub release metadata is unavailable',
        release_check: { status: 'unavailable', message: 'GitHub release metadata is unavailable' },
      } : {
        ...base, latest_version: '1.0.18', latest_commit: '55d4223c60c719fd00216eeea85dc169af4632cc',
        update_available: true, update_status: 'available', compatible: true,
        update_url: 'https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.18',
        release_check: { status: 'available', message: null },
      } });
    }
    if (path === '/api/update/status') return route.fulfill({ json: { status: 'idle' } });
    return null;
  });

  await page.goto('/static/index.html');
  await expect(page.locator('#sidebar-update-check')).toBeVisible();
  await expect(page.locator('#sidebar-update-action')).toBeHidden();
  await page.locator('#sidebar-update-check').click();
  await expect(page.locator('#updater-installed-commit')).toHaveText('91cc845d');
  await expect(page.locator('#updater-installation-kind')).toHaveText('Docker container');
  await expect(page.locator('#updater-update-mode')).toHaveText('Host-managed');
  await expect(page.locator('#updater-release-summary')).toContainText('could not reach GitHub');
  await expect(page.locator('#updater-apply')).toBeHidden();

  await page.locator('#updater-check').click();
  await expect(page.locator('#updater-release-summary')).toContainText('v1.0.18 is available');
  await expect(page.locator('#updater-manual-guidance')).toBeVisible();
  await expect(page.locator('#updater-manual-command')).toContainText('docker compose');
  await expect(page.locator('#updater-release-link')).toHaveAttribute(
    'href', 'https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.18',
  );
  await expect(page.locator('#updater-apply')).toBeHidden();
  expect(checks).toBe(2);
});

test('incompatible managed release shows the compatibility reason instead of host guidance', async ({ page }) => {
  const response = {
    version: '0.9.0', commit: OLD_COMMIT, release: '0.9.0-11111111',
    latest_version: '1.0.19', latest_commit: NEW_COMMIT,
    update_available: true, update_status: 'incompatible', compatible: false, can_update: false,
    compatibility_reason: 'Manual upgrade required from versions older than v1.0.11.',
    update_url: 'https://github.com/MADPANDA3D/Pandamonium/releases/tag/v1.0.19',
    installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
    release_check: {
      status: 'incompatible',
      message: 'Manual upgrade required from versions older than v1.0.11.',
    },
  };
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version' || path === '/api/update/check') {
      return route.fulfill({ json: response });
    }
    if (path === '/api/update/status') return route.fulfill({ json: { status: 'idle' } });
    return null;
  });

  await page.goto('/static/index.html');
  await expect(page.locator('#sidebar-update-check')).toBeHidden();
  await expect(page.locator('#sidebar-update-action')).toHaveText('View v1.0.19');
  await page.locator('#sidebar-update-action').click();
  await expect(page.locator('#updater-release-summary')).toContainText('cannot be installed here');
  await expect(page.locator('#updater-progress-detail')).toHaveText(
    'Manual upgrade required from versions older than v1.0.11.',
  );
  await expect(page.locator('#updater-manual-guidance')).toBeHidden();
  await expect(page.locator('#updater-apply')).toBeHidden();
});

test('updater dialog fits a phone viewport and disables scan motion when requested', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version' || path === '/api/update/check') {
      const response = { json: {
        version: '1.0.18', commit: '55d4223c60c719fd00216eeea85dc169af4632cc',
        release: '1.0.18-55d4223c', latest_version: '1.0.18', update_available: false,
        update_status: 'current', compatible: true, can_update: false,
        installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
        release_check: { status: 'current', message: null },
      } };
      if (path === '/api/update/check') {
        return new Promise(resolve => setTimeout(() => resolve(route.fulfill(response)), 250));
      }
      return route.fulfill(response);
    }
    if (path === '/api/update/status') return route.fulfill({ json: { status: 'idle' } });
    return null;
  });

  await page.goto('/static/index.html');
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#sidebar-update-check')).toBeVisible();
  await page.locator('#sidebar-update-check').click();
  await expect(page.locator('#updater-modal')).toBeVisible();
  const bounds = await page.locator('.updater-modal-content').evaluate(element => {
    const rect = element.getBoundingClientRect();
    return {
      left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
      scrollWidth: element.scrollWidth, clientWidth: element.clientWidth,
    };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(0);
  expect(bounds.right).toBeLessThanOrEqual(390);
  expect(bounds.top).toBeGreaterThanOrEqual(0);
  expect(bounds.bottom).toBeLessThanOrEqual(844);
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.clientWidth);
  await expect(page.locator('#updater-progress-card')).toHaveAttribute('data-state', 'working');
  await expect(page.locator('#updater-progress-title')).toHaveText('Scanning stable releases');
  expect(await page.locator('#updater-progress-card .mad-mcp-scan-line').evaluate(
    element => getComputedStyle(element).animationName,
  )).toBe('none');
});
