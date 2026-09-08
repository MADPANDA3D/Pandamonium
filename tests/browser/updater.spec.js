import { expect, test } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';


const OLD_COMMIT = '1111111111111111111111111111111111111111';
const NEW_COMMIT = '2222222222222222222222222222222222222222';
const RELEASED_V1020_UPDATER = readFileSync('tests/fixtures/releases/v1.0.20/updater.js', 'utf8');
const RELEASED_V1021_UPDATER = readFileSync('tests/fixtures/releases/v1.0.21/updater.js', 'utf8');
const RELEASED_WORKER = readFileSync('tests/fixtures/releases/v1.0.20/sw.js', 'utf8');
const CURRENT_UPDATER = readFileSync('static/js/updater.js', 'utf8');
const CURRENT_WORKER = readFileSync('static/sw.js', 'utf8');
const FUTURE_WORKER = CURRENT_WORKER.replace('pandamonium-v390', 'pandamonium-v391');

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

for (const [scenario, bridgeReload] of [
  ['released v1.0.21 bridge', true],
  ['v1.0.24 future recovery', false],
]) for (const [formFactor, viewport] of [
  ['desktop', null],
  ['mobile', { width: 390, height: 844 }],
]) test(`${scenario} crosses a stale response and signed restart on ${formFactor}`, async ({ page }, testInfo) => {
  const trace = [];
  if (viewport) await page.setViewportSize(viewport);
  expect(createHash('sha256').update(RELEASED_V1020_UPDATER).digest('hex'))
    .toBe('2d34087cb28abc846791352a9fb0f39f65bd87d24cf466684366dd0e2c1838ca');
  expect(createHash('sha256').update(RELEASED_V1021_UPDATER).digest('hex'))
    .toBe('894dbf8c356b2274a5c9af249c74a535879610fabcf4a45401a3e16da4568782');
  expect(createHash('sha256').update(RELEASED_WORKER).digest('hex'))
    .toBe('d8eb76b8e6e038aa38d07416933f01a1a3b457b8dac1d1fd6e059e500d379f84');
  expect(RELEASED_WORKER).toContain("const CACHE_NAME = 'pandamonium-v387';");
  expect(RELEASED_V1021_UPDATER).not.toContain('registration.update()');
  expect(FUTURE_WORKER).toContain("const CACHE_NAME = 'pandamonium-v391';");
  const sourceVersion = bridgeReload ? '1.0.21' : '1.0.24';
  const sourceCommit = bridgeReload
    ? '1e5d2e3ab95b53d85b22bbe63a0aa8ee40f9d530'
    : '3'.repeat(40);
  const targetVersion = bridgeReload ? '1.0.24' : '1.0.25';
  const targetCommit = bridgeReload ? '3'.repeat(40) : '4'.repeat(40);
  const sourceUpdater = bridgeReload ? RELEASED_V1021_UPDATER : CURRENT_UPDATER;
  const sourceWorker = bridgeReload ? RELEASED_WORKER : CURRENT_WORKER;
  const targetWorker = bridgeReload ? CURRENT_WORKER : FUTURE_WORKER;
  const sourceCache = bridgeReload ? 'pandamonium-v387' : 'pandamonium-v390';
  const targetCache = bridgeReload ? 'pandamonium-v390' : 'pandamonium-v391';
  let finishInitialStatus;
  let applied = false;
  let applyCalls = 0;
  let failedStatusPolls = 0;
  let manualReloads = 0;
  let documentLoads = 0;
  let navigations = 0;
  let statusPolls = 0;
  let serviceWorkerStarts = 0;
  let workerScriptRequests = 0;
  const context = page.context();

  await context.route('**/static/js/updater.js', route => route.fulfill({
    body: applied ? CURRENT_UPDATER : sourceUpdater,
    contentType: 'text/javascript',
  }));
  await context.route('**/static/sw.js', route => route.fulfill({
    body: applied ? targetWorker : sourceWorker,
    contentType: 'text/javascript',
    headers: { 'Cache-Control': 'no-cache' },
  }));
  context.on('serviceworker', worker => {
    serviceWorkerStarts += 1;
    trace.push({ type: 'serviceworker', url: worker.url(), serviceWorkerStarts });
  });
  context.on('request', request => {
    if (new URL(request.url()).pathname === '/static/sw.js') {
      workerScriptRequests += 1;
      trace.push({ type: 'worker-script-request', workerScriptRequests });
    }
  });

  page.on('framenavigated', frame => {
    if (frame === page.mainFrame() && frame.url()) {
      navigations += 1;
      trace.push({ type: 'navigation', url: frame.url() });
    }
  });
  page.on('console', message => trace.push({ type: 'console', text: message.text() }));
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.resourceType() === 'document') documentLoads += 1;
    if (path.startsWith('/api/update/')) trace.push({ type: 'request', path });
  });
  page.on('requestfailed', request => trace.push({
    type: 'requestfailed',
    path: new URL(request.url()).pathname,
    error: request.failure()?.errorText,
  }));
  page.on('response', response => {
    const path = new URL(response.url()).pathname;
    if (path === '/api/version' || path.startsWith('/api/update/')) {
      trace.push({ type: 'response', path, status: response.status() });
    }
  });
  await shellRoutes(context, (route, path) => {
    if (path === '/api/version') {
      return route.fulfill({ json: applied ? {
        version: targetVersion, commit: targetCommit,
        release: `${targetVersion}-${targetCommit.slice(0, 8)}`,
        latest_version: targetVersion, update_available: false,
        update_status: 'current', compatible: true, can_update: false,
        installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
        release_check: { status: 'current', message: null },
      } : {
        version: sourceVersion, commit: sourceCommit,
        release: `${sourceVersion}-${sourceCommit.slice(0, 8)}`,
        latest_version: sourceVersion, update_available: false,
        update_status: 'current', compatible: true, can_update: false,
        installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
        release_check: { status: 'current', message: null },
      } });
    }
    if (path === '/api/update/check') {
      return route.fulfill({ json: {
        version: sourceVersion, commit: sourceCommit,
        release: `${sourceVersion}-${sourceCommit.slice(0, 8)}`,
        latest_version: targetVersion, latest_commit: targetCommit,
        update_available: true, update_status: 'available', compatible: true, can_update: true,
        installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
        release_check: { status: 'available', message: null },
      } });
    }
    if (path === '/api/update/apply') {
      applyCalls += 1;
      expect(route.request().postDataJSON()).toEqual({
        version: targetVersion,
        commit: targetCommit,
      });
      applied = true;
      return route.fulfill({ json: {
        status: 'queued', phase: 'queued', progress: 0, message: 'Update queued',
        rollback_available: false,
      } });
    }
    if (path === '/api/update/status') {
      statusPolls += 1;
      if (statusPolls === 1) {
        return new Promise(resolve => {
          finishInitialStatus = () => resolve(route.fulfill({ json: { status: 'idle' } }));
        });
      }
      if (statusPolls <= 4 || statusPolls === 7) {
        failedStatusPolls += 1;
        trace.push({ type: 'simulated-outage', path, statusPolls });
        return route.abort('connectionrefused');
      }
      return route.fulfill({ json: {
        status: 'succeeded', phase: 'complete', progress: 100,
        message: `Updated to v${targetVersion}`,
        backup_location: `/var/backups/odysseus/update-${targetVersion}-proof`,
        rollback_available: true,
      } });
    }
    return null;
  });

  await page.goto('/static/index.html');
  await expect.poll(() => typeof finishInitialStatus).toBe('function');
  await expect.poll(() => page.evaluate(() => caches.keys())).toContain(sourceCache);
  if (viewport) await page.locator('#hamburger-btn').click();
  await page.locator('#sidebar-update-check').click();
  await expect(page.locator('#updater-apply')).toBeVisible();
  await page.locator('#updater-apply').click();
  await page.locator('#styled-confirm-ok').click();
  await expect(page.locator('#updater-progress-title')).toHaveText('Update queued');
  if (!bridgeReload) {
    await context.addCookies([{
      name: 'pandamonium-test-sw',
      value: 'future',
      url: 'http://127.0.0.1:4173',
    }]);
  }
  finishInitialStatus();
  if (bridgeReload) {
    await expect.poll(() => statusPolls, { timeout: 1800 }).toBe(1);
    await expect(page.locator('#updater-progress-title')).toHaveText('Update queued');
    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'));
      window.dispatchEvent(new Event('online'));
      window.dispatchEvent(new Event('pageshow'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.waitForTimeout(500);
    expect(statusPolls).toBe(1);
    trace.push({ type: 'released-trigger-audit', statusPolls });
    manualReloads += 1;
    await page.reload();
  }

  try {
    await expect.poll(() => navigations, { timeout: 15000 }).toBeGreaterThanOrEqual(2);
    await expect.poll(() => statusPolls, { timeout: 15000 }).toBeGreaterThanOrEqual(8);
    await expect(page.locator('#updater-progress-card')).toHaveAttribute('data-state', 'complete', {
      timeout: 8000,
    });
    await expect(page.locator('#updater-progress-title')).toHaveText('Update installed');
    await expect(page.locator('#updater-modal')).toBeVisible();
    await expect(page.locator('#updater-installed-version')).toHaveText(`v${targetVersion}`);
    await expect.poll(() => page.evaluate(() => caches.keys())).toContain(targetCache);
    const cacheKeys = await page.evaluate(() => caches.keys());
    expect(cacheKeys).not.toContain(sourceCache);
    expect(applyCalls).toBe(1);
    expect(failedStatusPolls).toBe(4);
    expect(serviceWorkerStarts).toBeGreaterThanOrEqual(2);
    expect(manualReloads).toBe(bridgeReload ? 1 : 0);
    expect(await page.evaluate(() => document.visibilityState)).toBe('visible');
    expect(await page.evaluate(() => navigator.serviceWorker.controller?.scriptURL)).toContain('/static/sw.js');
    await page.waitForTimeout(350);
    expect(documentLoads).toBe(bridgeReload ? 3 : 2);
    expect(navigations).toBeLessThanOrEqual(bridgeReload ? 4 : 3);
    expect(new URL(page.url()).searchParams.has('pandamonium-update-reconcile')).toBe(false);
  } finally {
    trace.push({
      type: 'snapshot',
      statusPolls,
      failedStatusPolls,
      serviceWorkerStarts,
      workerScriptRequests,
      manualReloads,
      documentLoads,
      visibility: await page.evaluate(() => document.visibilityState),
      serviceWorker: await page.evaluate(() => navigator.serviceWorker?.controller?.scriptURL || null),
      title: await page.locator('#updater-progress-title').textContent(),
      percent: await page.locator('#updater-progress-percent').textContent(),
      version: await page.locator('#updater-installed-version').textContent(),
    });
    await testInfo.attach(`mad-839-${bridgeReload ? 'bridge' : 'future'}-restart-trace.json`, {
      body: Buffer.from(JSON.stringify(trace, null, 2)), contentType: 'application/json',
    });
  }
});

for (const [status, title] of [
  ['failed', 'Update failed safely'],
  ['rolled_back', 'Rollback verified'],
]) test(`updater renders the ${status} terminal state`, async ({ page }) => {
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') return route.fulfill({ json: {
      version: '1.0.21', commit: NEW_COMMIT, release: '1.0.21-22222222',
      latest_version: '1.0.21', update_available: false, update_status: 'current',
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'current', message: null },
    } });
    if (path === '/api/update/status') return route.fulfill({ json: {
      status, phase: 'complete', progress: 100,
      message: status === 'failed' ? 'Signature verification failed' : 'Rolled back to v1.0.20',
      rollback_available: false,
    } });
    return null;
  });

  await page.goto('/static/index.html');
  await expect(page.locator('#updater-progress-title')).toHaveText(title);
  await expect(page.locator('#updater-progress-card')).toHaveAttribute(
    'data-state', status === 'failed' ? 'error' : 'complete',
  );
});

test('updater dialog survives the restart gap and reconciles the installed version', async ({ page }) => {
  let checks = 0;
  let applies = 0;
  let applyAttempts = 0;
  let authPolls = 0;
  let navigations = 0;
  let rollbacks = 0;
  let statusPolls = 0;
  let finishCheck;
  page.on('framenavigated', frame => {
    if (frame === page.mainFrame() && frame.url()) navigations += 1;
  });
  await page.addInitScript(() => {
    window.__nativeConfirmCalls = 0;
    navigator.serviceWorker.getRegistration = async () => null;
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
      if (rollbacks) {
        authPolls += 1;
        return route.fulfill({ status: 401, json: { detail: 'Not authenticated' } });
      }
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
  await expect.poll(() => navigations, { timeout: 7000 }).toBeGreaterThanOrEqual(2);
  await expect(page.locator('#updater-modal')).toBeVisible();
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
  await expect(page).toHaveURL('/login');
  await page.waitForTimeout(1100);
  expect(authPolls).toBe(1);
});

test('startup status reconciliation survives an initial version outage', async ({ page }) => {
  let statusPolls = 0;
  let versionRequests = 0;
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') {
      versionRequests += 1;
      if (versionRequests === 1) return route.abort('connectionrefused');
      return route.fulfill({ json: {
        version: '1.0.22', commit: null, release: '1.0.22-proof',
        latest_version: '1.0.22', update_available: false, update_status: 'current',
        compatible: true, can_update: false,
        installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
        release_check: { status: 'current', message: null },
      } });
    }
    if (path === '/api/update/status') {
      statusPolls += 1;
      if (statusPolls < 3) return route.abort('connectionrefused');
      return route.fulfill({ json: {
        status: 'succeeded', phase: 'complete', progress: 100,
        message: 'Updated to v1.0.22', rollback_available: true,
      } });
    }
    return null;
  });

  await page.goto('/static/index.html');
  await expect(page.locator('#updater-progress-title')).toHaveText('Update installed');
  expect(statusPolls).toBe(3);
  expect(versionRequests).toBe(2);
});

test('historical terminal status does not refresh an unchanged release', async ({ page }) => {
  let documentLoads = 0;
  let statusPolls = 0;
  let registrationChecks = 0;
  await page.addInitScript(() => {
    navigator.serviceWorker.getRegistration = async () => {
      window.__registrationChecks = (window.__registrationChecks || 0) + 1;
      return null;
    };
  });
  page.on('request', request => {
    if (request.resourceType() === 'document') documentLoads += 1;
  });
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') return route.fulfill({ json: {
      version: '1.0.22', commit: OLD_COMMIT, release: '1.0.22-11111111',
      latest_version: '1.0.22', update_available: false, update_status: 'current',
      compatible: true, can_update: false,
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'current', message: null },
    } });
    if (path === '/api/update/status') {
      statusPolls += 1;
      if (statusPolls === 1) return route.abort('connectionrefused');
      return route.fulfill({ json: {
        status: 'succeeded', phase: 'complete', progress: 100,
        message: 'Updated to v1.0.22', rollback_available: true,
        target_commit: OLD_COMMIT,
      } });
    }
    return null;
  });

  await page.goto('/static/index.html');
  await expect.poll(() => statusPolls).toBe(2);
  await expect(page.locator('#updater-progress-title')).toHaveText('Update installed');
  registrationChecks = await page.evaluate(() => window.__registrationChecks || 0);
  expect(registrationChecks).toBe(0);
  expect(documentLoads).toBe(1);
});

test('a later update consumes the startup worker marker and retries transient status responses', async ({ page }) => {
  let applied = false;
  let applyCalls = 0;
  let documentLoads = 0;
  let statusPolls = 0;
  await page.addInitScript(() => {
    navigator.serviceWorker.getRegistration = async (url) => {
      sessionStorage.setItem('test-worker-registration-url', String(url));
      return null;
    };
  });
  page.on('request', request => {
    if (request.resourceType() === 'document') documentLoads += 1;
  });
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') return route.fulfill({ json: {
      version: applied ? '1.0.23' : '1.0.22',
      commit: applied ? NEW_COMMIT : OLD_COMMIT,
      release: applied ? '1.0.23-22222222' : '1.0.22-11111111',
      latest_version: applied ? '1.0.23' : '1.0.22',
      update_available: false, update_status: 'current', compatible: true, can_update: false,
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'current', message: null },
    } });
    if (path === '/api/update/check') return route.fulfill({ json: {
      version: '1.0.22', commit: OLD_COMMIT, release: '1.0.22-11111111',
      latest_version: '1.0.23', latest_commit: NEW_COMMIT,
      update_available: true, update_status: 'available', compatible: true, can_update: true,
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'available', message: null },
    } });
    if (path === '/api/update/apply') {
      applyCalls += 1;
      applied = true;
      return route.fulfill({ json: {
        status: 'queued', phase: 'queued', progress: 0, message: 'Update queued',
        rollback_available: false,
      } });
    }
    if (path === '/api/update/status') {
      statusPolls += 1;
      if (!applied) return route.fulfill({ json: { status: 'idle' } });
      if (statusPolls <= 4) {
        return route.fulfill({ status: [408, 425, 429][statusPolls - 2], json: {} });
      }
      return route.fulfill({ json: {
        status: 'succeeded', phase: 'complete', progress: 100,
        message: 'Updated to v1.0.23', rollback_available: true,
      } });
    }
    return null;
  });

  await page.goto('/static/index.html?pandamonium-update-reconcile=pandamonium-v388');
  await page.locator('#updater-check').click();
  await page.locator('#updater-apply').click();
  await page.locator('#styled-confirm-ok').click();
  await expect.poll(() => documentLoads).toBe(2);
  await expect(page.locator('#updater-progress-title')).toHaveText('Update installed');
  expect(applyCalls).toBe(1);
  expect(statusPolls).toBe(6);
  expect(await page.evaluate(() => sessionStorage.getItem('test-worker-registration-url')))
    .toBe('http://127.0.0.1:4173/static/');
});

for (const [status, title, pill] of [
  [401, 'Sign in to continue', 'Sign in required'],
  [403, 'Administrator access required', 'Admin required'],
]) test(`updater reports HTTP ${status} authorization accurately`, async ({ page }) => {
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') return route.fulfill({ json: {
      version: '1.0.22', commit: OLD_COMMIT, release: '1.0.22-11111111',
      latest_version: '1.0.22', update_available: false, update_status: 'current',
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'current', message: null },
    } });
    if (path === '/api/update/status') return route.fulfill({ status, json: {} });
    return null;
  });

  await page.goto('/static/index.html');
  if (status === 401) {
    await expect(page).toHaveURL('/login');
    return;
  }
  await expect(page.locator('#updater-progress-title')).toHaveText(title);
  await expect(page.locator('#updater-status-pill')).toHaveText(pill);
});

test('page polling stops after a nonretryable 400 response', async ({ page }) => {
  let applied = false;
  let statusPolls = 0;
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') return route.fulfill({ json: {
      version: '1.0.22', commit: OLD_COMMIT, release: '1.0.22-11111111',
      latest_version: '1.0.22', update_available: false, update_status: 'current',
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'current', message: null },
    } });
    if (path === '/api/update/check') return route.fulfill({ json: {
      version: '1.0.22', commit: OLD_COMMIT, release: '1.0.22-11111111',
      latest_version: '1.0.23', latest_commit: NEW_COMMIT,
      update_available: true, update_status: 'available', compatible: true, can_update: true,
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'available', message: null },
    } });
    if (path === '/api/update/apply') {
      applied = true;
      return route.fulfill({ json: {
        status: 'queued', phase: 'queued', progress: 0, message: 'Update queued',
        rollback_available: false,
      } });
    }
    if (path === '/api/update/status') {
      statusPolls += 1;
      return applied
        ? route.fulfill({ status: 400, json: { detail: 'invalid request' } })
        : route.fulfill({ json: { status: 'idle' } });
    }
    return null;
  });

  await page.goto('/static/index.html');
  await page.locator('#sidebar-update-check').click();
  await page.locator('#updater-apply').click();
  await page.locator('#styled-confirm-ok').click();
  await expect(page.locator('#updater-progress-title')).toHaveText('Update queued');
  await page.waitForTimeout(1200);
  expect(statusPolls).toBe(2);
});

test('successful update still reloads when session storage is unavailable', async ({ page }) => {
  let applied = false;
  let applyCalls = 0;
  let documentLoads = 0;
  await page.addInitScript(() => {
    const originalGetItem = Storage.prototype.getItem;
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.getItem = function getItem(key) {
      if (this === sessionStorage && String(key).startsWith('pandamonium:update-')) {
        throw new DOMException('storage blocked', 'SecurityError');
      }
      return originalGetItem.call(this, key);
    };
    Storage.prototype.setItem = function setItem(key, value) {
      if (this === sessionStorage && String(key).startsWith('pandamonium:update-')) {
        throw new DOMException('storage blocked', 'SecurityError');
      }
      return originalSetItem.call(this, key, value);
    };
    navigator.serviceWorker.getRegistration = async () => null;
  });
  page.on('request', request => {
    if (request.resourceType() === 'document') documentLoads += 1;
  });
  await shellRoutes(page, (route, path) => {
    if (path === '/api/version') return route.fulfill({ json: {
      version: applied ? '1.0.23' : '1.0.22',
      commit: applied ? NEW_COMMIT : OLD_COMMIT,
      release: applied ? '1.0.23-22222222' : '1.0.22-11111111',
      latest_version: applied ? '1.0.23' : '1.0.22',
      update_available: false, update_status: 'current', compatible: true, can_update: false,
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'current', message: null },
    } });
    if (path === '/api/update/check') return route.fulfill({ json: {
      version: '1.0.22', commit: OLD_COMMIT, release: '1.0.22-11111111',
      latest_version: '1.0.23', latest_commit: NEW_COMMIT,
      update_available: true, update_status: 'available', compatible: true, can_update: true,
      installation: { supported: true, kind: 'managed-native', trigger: 'systemd-path' },
      release_check: { status: 'available', message: null },
    } });
    if (path === '/api/update/apply') {
      applyCalls += 1;
      applied = true;
      return route.fulfill({ json: {
        status: 'queued', phase: 'queued', progress: 0, message: 'Update queued',
        rollback_available: false,
      } });
    }
    if (path === '/api/update/status') return route.fulfill({ json: applied ? {
      status: 'succeeded', phase: 'complete', progress: 100,
      message: 'Updated to v1.0.23', rollback_available: true,
    } : { status: 'idle' } });
    return null;
  });

  await page.goto('/static/index.html');
  await page.locator('#sidebar-update-check').click();
  await page.locator('#updater-apply').click();
  await page.locator('#styled-confirm-ok').click();
  await expect.poll(() => documentLoads).toBe(2);
  await expect(page.locator('#updater-progress-title')).toHaveText('Update installed');
  expect(applyCalls).toBe(1);
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
