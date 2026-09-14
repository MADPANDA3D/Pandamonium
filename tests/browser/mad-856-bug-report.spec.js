import { expect, test } from '@playwright/test';

/**
 * MAD-856 — guided in-app bug reports.
 *
 * The static test server has no backend, so /api/** is mocked. Covers the
 * right-dock desktop surface and the mobile full-width sheet, capture +
 * screenshots (reorder/remove/label), review with duplicates + public warning,
 * security diversion, exact-once retry, navigation persistence, and the
 * accessibility contract.
 */

const PNG_1PX = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
);

const PREPARED_TITLE = '[Bug] Send button does nothing';
const PREPARED_BODY = '### Summary\nSend button does nothing\n\n### Steps to reproduce\n1. Open chat\n2. Click send';

function sessionFixture() {
  return {
    id: 'session-one',
    name: 'Existing chat',
    model: 'test/model',
    endpoint_url: 'http://model.test/v1/chat/completions',
    message_count: 1,
    archived: false,
    created_at: '2026-09-04T20:00:00Z',
    updated_at: '2026-09-04T20:01:00Z',
    last_message_at: '2026-09-04T20:01:00Z',
    workspace: '',
  };
}

async function mockApi(page, options = {}) {
  const state = {
    uploads: [],
    submits: [],
    submitStatus: options.submitStatus || 200,
    security: !!options.security,
    duplicates: options.duplicates || [],
    baseUrl: options.baseUrl || '',
  };
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/api/sessions') return route.fulfill({ json: [sessionFixture()] });
    if (path === '/api/auth/status') return route.fulfill({ json: { username: 'tester', is_admin: true, privileges: {} } });
    if (path === '/api/default-chat') {
      return route.fulfill({
        json: { endpoint_id: 'endpoint-one', endpoint_url: 'http://model.test/v1/chat/completions', model: 'test/model' },
      });
    }
    if (path === '/api/history/session-one') {
      return route.fulfill({
        json: { history: [{ role: 'user', content: 'Hello' }], model: 'test/model', name: 'Existing chat', offset: 0, limit: 100, total: 1, has_more_before: false },
      });
    }
    if (path === '/api/model-endpoints' || path === '/api/models') return route.fulfill({ json: [] });
    if (path === '/api/feedback/config') {
      return route.fulfill({
        json: {
          enabled: true,
          public_submission: options.publicSubmission !== false,
          configured: options.publicSubmission !== false,
          repo: 'MADPANDA3D/Pandamonium',
          reason: '',
          github_reason: options.publicSubmission === false
            ? 'GitHub submission is not configured on this installation. Set PANDAMONIUM_GITHUB_APP_ID.'
            : '',
          security_url: 'https://github.com/MADPANDA3D/Pandamonium/security/advisories/new',
          limits: { max_attachments: 8, max_attachment_bytes: 8388608 },
        },
      });
    }
    if (path === '/api/feedback/diagnostics') {
      return route.fulfill({
        json: {
          ok: true,
          diagnostics: {
            included: true,
            generated_at: '2026-09-04T20:00:00+00:00',
            app: { version: '1.0.62', revision: 'abc123', installation_method: 'container', runtime: 'python 3.12', platform_class: 'linux-container', platform_release: '6.1' },
            route: '/static/index.html',
            client: { user_agent_class: 'Chrome 140', viewport_class: 'desktop', locale: 'en-US' },
            session: { id: 'session-one' },
            correlation: {},
            latest_error: { category: 'provider_error', detail: 'timeout', component: 'agent_loop', status: 'failed', at: '2026-09-04T19:59:00+00:00' },
            service_health: { api: { status: 'healthy', checked_at: '2026-09-04T20:00:00+00:00' }, last_reported: [] },
          },
        },
      });
    }
    if (path === '/api/feedback/upload') {
      const index = state.uploads.length + 1;
      state.uploads.push(index);
      return route.fulfill({
        json: {
          ok: true,
          attachment: {
            id: `att-${String(index).padStart(8, '0')}`,
            name: `screenshot-${index}.png`,
            label: '',
            route: '/static/index.html',
            bytes: 68,
            sha256: 'a'.repeat(64),
            content_type: 'image/png',
          },
        },
      });
    }
    if (path === '/api/feedback/prepare') {
      const payload = route.request().postDataJSON() || {};
      return route.fulfill({
        json: {
          ok: true,
          title: PREPARED_TITLE,
          body: PREPARED_BODY,
          fingerprint: 'deadbeefdeadbeef',
          diagnostics: { included: true, app: { version: '1.0.62' } },
          attachments: (payload.attachment_ids || []).map((id, index) => ({ id, name: `screenshot-${index + 1}.png`, label: '', route: '/static/index.html', bytes: 68, sha256: 'a'.repeat(64) })),
          duplicates: state.duplicates,
          duplicates_error: '',
          security_private_only: state.security,
          security_url: 'https://github.com/MADPANDA3D/Pandamonium/security/advisories/new',
          public_submission: true,
        },
      });
    }
    if (path === '/api/feedback/submit' && route.request().method() === 'POST') {
      const payload = route.request().postDataJSON() || {};
      state.submits.push(payload);
      if (state.submitStatus !== 200) {
        const status = state.submitStatus;
        state.submitStatus = 200;
        return route.fulfill({ status, json: { detail: status === 502 ? 'GitHub is unavailable right now. The report is saved locally; retry when it recovers.' : 'Rejected.' } });
      }
      return route.fulfill({
        json: {
          ok: true,
          already_submitted: false,
          issue_url: 'https://github.com/MADPANDA3D/Pandamonium/issues/77',
          issue_number: 77,
          attached_to_existing: false,
          redacted_report: { title: PREPARED_TITLE, body: PREPARED_BODY },
        },
      });
    }
    return route.fulfill({ json: {} });
  });
  return state;
}

async function boot(page) {
  await page.goto('/static/index.html#session-one');
  await expect.poll(() => page.evaluate(async () => {
    const directImport = await import('/static/js/sessions.js');
    return window.sessionModule === directImport.default;
  }), { timeout: 15_000 }).toBe(true);
}

async function openPanel(page) {
  await page.evaluate(async () => {
    const module = await import('/static/js/bugReport.js');
    await (module.default || module).openBugReport();
  });
  await expect(page.locator('#bug-report-modal')).toBeVisible();
}

async function fillSummary(page, summary = 'Send button does nothing') {
  await page.locator('#bug-report-summary').fill(summary);
  await page.locator('#bug-report-goal').fill('Send a message');
  await page.locator('#bug-report-expected').fill('The message sends');
  await page.locator('#bug-report-actual').fill('Nothing happens');
  await page.locator('.bug-report-step-input').first().fill('Open a chat');
}

async function goToEvidence(page) {
  await page.locator('#bug-report-next').click();
  await expect(page.locator('#bug-report-panel-evidence')).toBeVisible();
}

async function addScreenshot(page, name = 'shot.png') {
  await page.locator('#bug-report-file-input').setInputFiles({ name, mimeType: 'image/png', buffer: PNG_1PX });
}

async function goToReview(page) {
  await page.locator('#bug-report-next').click();
  await expect(page.locator('#bug-report-panel-review')).toBeVisible();
  await expect(page.locator('#bug-report-review-title')).toContainText('[Bug]');
}

test('opens as a right-docked panel and keeps the draft across navigation', async ({ page }) => {
  await mockApi(page);
  await boot(page);
  await openPanel(page);
  await expect.poll(() => page.evaluate(() => document.getElementById('bug-report-modal')?.classList.contains('modal-right-docked'))).toBe(true);
  await fillSummary(page, 'Draft survives navigation');

  await page.reload();
  await boot(page);
  await openPanel(page);
  await expect(page.locator('#bug-report-summary')).toHaveValue('Draft survives navigation');
  await expect(page.locator('.bug-report-step-input').first()).toHaveValue('Open a chat');
});

test('captures screenshots with labels, reorder, and removal', async ({ page }) => {
  const state = await mockApi(page);
  await boot(page);
  await openPanel(page);
  await fillSummary(page);
  await goToEvidence(page);

  await addScreenshot(page, 'first.png');
  await addScreenshot(page, 'second.png');
  await expect(page.locator('.bug-report-attachment')).toHaveCount(2);
  await expect.poll(() => state.uploads.length).toBe(2);

  await page.locator('.bug-report-attachment-label').nth(0).fill('after tapping Send');
  await page.locator('.bug-report-attachment').nth(1).getByLabel('Move screenshot 2 up').click();
  await expect(page.locator('.bug-report-attachment-name').nth(0)).toContainText('second.png');
  await expect(page.locator('.bug-report-attachment-name').nth(1)).toContainText('first.png');

  await page.locator('.bug-report-attachment').nth(1).getByLabel('Remove screenshot 2').click();
  await expect(page.locator('.bug-report-attachment')).toHaveCount(1);

  await page.locator('#bug-report-next').click();
  await expect(page.locator('#bug-report-review-title')).toBeVisible();
  await expect(page.locator('#bug-report-review-attachments')).toContainText('second.png');
});

test('review shows the exact public report, duplicates, and warning before submit', async ({ page }) => {
  const state = await mockApi(page, {
    duplicates: [{ number: 12, title: 'Send button broken', url: 'https://x/12', state: 'open', updated_at: 'now' }],
  });
  await boot(page);
  await openPanel(page);
  await fillSummary(page);
  await goToEvidence(page);
  await addScreenshot(page);
  await goToReview(page);

  await expect(page.locator('#bug-report-review-body')).toContainText('### Steps to reproduce');
  await expect(page.locator('#bug-report-public-warning')).toContainText('public');
  await expect(page.locator('#bug-report-submit')).toBeDisabled();
  await page.locator('#bug-report-confirm').check();
  await expect(page.locator('#bug-report-submit')).toBeEnabled();

  await expect(page.locator('#bug-report-duplicate-12')).toBeVisible();
  await page.locator('#bug-report-submit').click();
  await expect(page.locator('#bug-report-issue-link')).toHaveAttribute('href', /issues\/77/);
  await expect.poll(() => state.submits.length).toBe(1);
  expect(state.submits[0].confirmation).toBe(true);
  expect(state.submits[0].duplicate_decision).toBe('new');
  expect(state.submits[0].attachment_ids).toHaveLength(1);
});

test('a failed submission keeps the draft and retries with the same idempotency key', async ({ page }) => {
  const state = await mockApi(page, { submitStatus: 502 });
  await boot(page);
  await openPanel(page);
  await fillSummary(page);
  await goToEvidence(page);
  await goToReview(page);
  await page.locator('#bug-report-confirm').check();
  await page.locator('#bug-report-submit').click();
  await expect(page.locator('#bug-report-status')).toContainText('draft is saved');
  await expect(page.locator('#bug-report-submit')).toBeEnabled();

  await page.locator('#bug-report-submit').click();
  await expect(page.locator('#bug-report-issue-link')).toBeVisible();
  expect(state.submits).toHaveLength(2);
  expect(state.submits[0].idempotency_key).toBe(state.submits[1].idempotency_key);
});

test('security reports divert to the private advisory path', async ({ page }) => {
  const state = await mockApi(page, { security: true });
  await boot(page);
  await openPanel(page);
  await fillSummary(page, 'Token leak in logs');
  await page.locator('#bug-report-type').selectOption('security');
  await goToEvidence(page);
  await goToReview(page);
  await expect(page.locator('#bug-report-security-notice')).toBeVisible();
  await expect(page.locator('#bug-report-security-link')).toHaveAttribute('href', /security\/advisories\/new/);
  await page.locator('#bug-report-confirm').check();
  await expect(page.locator('#bug-report-submit')).toBeDisabled();
  expect(state.submits).toHaveLength(0);
});

test('GitHub-unconfigured installations fail closed with honest copy', async ({ page }) => {
  await mockApi(page, { publicSubmission: false });
  await boot(page);
  await openPanel(page);
  await expect(page.locator('#bug-report-status')).toContainText('PANDAMONIUM_GITHUB_APP_ID');
  await fillSummary(page);
  await goToEvidence(page);
  await goToReview(page);
  await page.locator('#bug-report-confirm').check();
  await expect(page.locator('#bug-report-submit')).toBeDisabled();
});

test('mobile renders a full-width sheet', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page);
  await boot(page);
  await openPanel(page);
  const box = await page.locator('#bug-report-content').boundingBox();
  expect(box.width).toBeGreaterThan(370);
  await expect.poll(() => page.evaluate(() => document.getElementById('bug-report-modal')?.classList.contains('modal-right-docked'))).toBe(false);
});

test('the form is keyboard and screen-reader labelled', async ({ page }) => {
  await mockApi(page);
  await boot(page);
  await openPanel(page);
  await expect(page.locator('#bug-report-modal')).toHaveAttribute('role', 'dialog');
  await expect(page.locator('#bug-report-modal')).toHaveAttribute('aria-labelledby', 'bug-report-heading');
  for (const id of ['bug-report-summary', 'bug-report-goal', 'bug-report-expected', 'bug-report-actual', 'bug-report-workaround', 'bug-report-reviewed-text']) {
    await expect(page.locator(`label[for="${id}"]`)).toHaveCount(1);
  }
  await expect(page.locator('#bug-report-tab-capture')).toHaveAttribute('role', 'tab');
  await expect(page.locator('#bug-report-tab-capture')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#bug-report-drop-zone')).toHaveAttribute('role', 'button');
  await expect(page.locator('#bug-report-status')).toHaveAttribute('aria-live', 'polite');
  // The step 1 tab exposes the required summary as a labelled input.
  await page.locator('#bug-report-summary').focus();
  await expect(page.locator('#bug-report-summary')).toBeFocused();
});