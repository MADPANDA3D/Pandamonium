import { expect, test } from '@playwright/test';

const model = {
  endpoint_id: 'endpoint-one',
  endpoint_url: 'http://model.test/v1/chat/completions',
  model: 'test/model',
};

async function mockApp(page, { mailboxes = null } = {}) {
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/auth/status') {
      return route.fulfill({
        json: {
          username: 'tester',
          is_admin: true,
          privileges: {},
          agent_identity: { source: 'configured', display_name: 'Jarvis' },
        },
      });
    }
    if (url.pathname === '/api/default-chat') return route.fulfill({ json: model });
    if (url.pathname === '/api/models') {
      return route.fulfill({
        json: {
          items: [{ ...model, id: model.endpoint_id, url: model.endpoint_url, models: [model.model], offline: false }],
        },
      });
    }
    if (url.pathname === '/api/model-endpoints') return route.fulfill({ json: [] });
    if (url.pathname === '/api/sessions') return route.fulfill({ json: [] });
    if (url.pathname === '/api/mcp/portal/mailboxes' && mailboxes) {
      return route.fulfill({ json: mailboxes });
    }
    if (url.pathname.includes('/api/mcp/portal/mailboxes/google/')) {
      return route.fulfill({ json: { status: 'ready', emails: [], total: 0 } });
    }
    if (url.pathname === '/api/email/accounts') return route.fulfill({ json: { accounts: [] } });
    return route.fulfill({ json: {} });
  });
}

test('mailbox account cards keep readable two-column, zoom, and mobile layouts', async ({ page }) => {
  const accounts = Array.from({ length: 4 }, (_, index) => ({
    id: `google-${index + 1}`,
    label: `Mad Panda Operations Account ${index + 1}`,
    email: `very-long-operations-address-${index + 1}@madpanda3d.example.com`,
    default: index === 0,
    verification: index === 1 ? 'pending verification' : 'verified',
  }));
  await mockApp(page, {
    mailboxes: {
      configured: true,
      status: 'connected',
      my_email: { configured: true, status: 'ready', accounts },
      agent_mail: { configured: true, status: 'ready', inboxes: [] },
    },
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/static/index.html');
  await page.evaluate(async () => (await import('/static/js/emailLibrary.js')).openEmailLibrary());

  const cards = page.locator('#email-my-mailboxes .portal-mailbox-card');
  await expect(cards).toHaveCount(4);
  await expect.poll(() => cards.evaluateAll(nodes => nodes.every(node => node.getBoundingClientRect().height >= 87.5))).toBe(true);
  const desktop = await cards.evaluateAll(nodes => nodes.map(node => {
    const rect = node.getBoundingClientRect();
    return { left: rect.left, top: rect.top, height: rect.height };
  }));
  expect(Math.abs(desktop[0].top - desktop[1].top)).toBeLessThan(2);
  expect(desktop[2].top).toBeGreaterThan(desktop[0].top + 70);
  expect(desktop.every(card => card.height >= 87.5)).toBe(true);
  await expect(cards.first().locator('.portal-mailbox-action')).toHaveText('Open');
  await expect(cards.first().locator('.portal-mailbox-badges')).toContainText('Default');

  const copyFlow = await cards.first().evaluate(card => {
    const title = card.querySelector('.portal-mailbox-identity strong').getBoundingClientRect();
    const address = card.querySelector('.portal-mailbox-identity > span').getBoundingClientRect();
    const badges = card.querySelector('.portal-mailbox-badges').getBoundingClientRect();
    return { titleBottom: title.bottom, addressTop: address.top, addressBottom: address.bottom, badgesTop: badges.top };
  });
  expect(copyFlow.addressTop).toBeGreaterThanOrEqual(copyFlow.titleBottom - 1);
  expect(copyFlow.badgesTop).toBeGreaterThanOrEqual(copyFlow.addressBottom - 1);

  await cards.first().focus();
  await page.keyboard.press('Enter');
  await expect(cards.first()).toHaveAttribute('aria-pressed', 'true');
  await expect(cards.first().locator('.portal-mailbox-action')).toHaveText('Selected');

  // 640 CSS pixels is the effective layout viewport of a 1280px browser at 200% zoom.
  await page.setViewportSize({ width: 640, height: 800 });
  const zoomed = await cards.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect()));
  expect(zoomed[1].top).toBeGreaterThan(zoomed[0].top + 70);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobile = await cards.evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect()));
  const mobileGrid = await page.locator('#email-my-mailboxes .portal-mailbox-grid').evaluate(node => node.getBoundingClientRect());
  expect(mobile[1].top).toBeGreaterThan(mobile[0].top + 70);
  expect(mobile.every(card => card.right <= mobileGrid.right + 0.5 && card.left >= mobileGrid.left - 0.5)).toBe(true);
});

test('setup guide can be skipped, closed, reopened, continued, and restarted without changing settings', async ({ page }) => {
  const savedToggles = JSON.stringify({ web: true, web_chat: true, web_agent: true, mcp: true });
  await page.addInitScript(value => localStorage.setItem('odysseus-toggles', value), savedToggles);
  await mockApp(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/static/index.html');

  const guideButton = page.locator('#user-bar-guide');
  const modal = page.locator('#guide-modal');
  await expect(guideButton).toBeVisible();
  const initializedToggles = await page.evaluate(() => localStorage.getItem('odysseus-toggles'));
  await guideButton.click();
  await expect(modal).toBeVisible();
  await expect(modal.getByRole('button', { name: 'Continue setup' })).toBeVisible();
  await expect(modal.getByRole('button', { name: 'Restart product tour' })).toBeVisible();
  await expect(modal.getByRole('button', { name: 'Skip for now' })).toBeVisible();
  const stepCards = modal.locator('.first-run-step');
  await expect(stepCards).toHaveCount(3);
  await modal.locator('.guide-modal-content').evaluate(async node => {
    await Promise.all(node.getAnimations().map(animation => animation.finished));
  });
  const guideWidth = await modal.locator('.guide-modal-content').evaluate(node => node.getBoundingClientRect().width);
  const stepLayout = await stepCards.evaluateAll(nodes => nodes.map(node => {
    const rect = node.getBoundingClientRect();
    const index = node.querySelector('.first-run-step-index').getBoundingClientRect();
    const label = node.querySelector('.first-run-step-label').getBoundingClientRect();
    const state = node.querySelector('.first-run-step-state').getBoundingClientRect();
    return {
      top: rect.top,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
      indexTop: index.top,
      indexBottom: index.bottom,
      labelBottom: label.bottom,
      stateTop: state.top,
      stateBottom: state.bottom,
    };
  }));
  expect(guideWidth).toBeGreaterThanOrEqual(500);
  expect(guideWidth).toBeLessThanOrEqual(530);
  expect(stepLayout[1].top).toBeGreaterThanOrEqual(stepLayout[0].bottom + 7);
  expect(stepLayout[2].top).toBeGreaterThanOrEqual(stepLayout[1].bottom + 7);
  expect(Math.max(...stepLayout.map(card => card.width)) - Math.min(...stepLayout.map(card => card.width))).toBeLessThan(2);
  expect(stepLayout.every(card => card.height >= 52)).toBe(true);
  expect(stepLayout.every(card => card.indexTop > card.top && card.indexBottom < card.bottom)).toBe(true);
  expect(stepLayout.every(card => card.labelBottom <= card.stateTop + 1)).toBe(true);
  expect(stepLayout.every(card => card.stateBottom <= card.bottom - 8)).toBe(true);

  await modal.getByRole('button', { name: 'Skip for now' }).click();
  await expect(modal).toBeHidden();
  await guideButton.click();
  await modal.getByRole('button', { name: 'Close setup guide' }).click();
  await expect(modal).toBeHidden();
  await expect(guideButton).toBeFocused();

  await guideButton.click();
  await modal.getByRole('button', { name: 'Continue setup' }).click();
  await expect(page.locator('#chat-history')).toContainText('Set up Pandamonium');
  expect(await page.evaluate(() => localStorage.getItem('odysseus-toggles'))).toBe(initializedToggles);

  await guideButton.click();
  await modal.getByRole('button', { name: 'Restart product tour' }).click();
  await expect(page.locator('body')).toHaveClass(/tour-active/, { timeout: 8_000 });
  await expect(page.locator('#tour-tooltip')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('odysseus-toggles'))).toBe(initializedToggles);
  await page.locator('#tour-tooltip .tour-btn-skip').click();
  await expect(page.locator('body')).not.toHaveClass(/tour-active/);
});

test('ASK_USER keeps long choices readable and supports keyboard selection, Send, and close', async ({ page }) => {
  await mockApp(page);
  await page.goto('/static/index.html');
  await page.evaluate(() => {
    window.__askUserSent = [];
    document.querySelector('.send-btn').addEventListener('click', event => {
      event.preventDefault();
      event.stopImmediatePropagation();
      window.__askUserSent.push(document.getElementById('message').value);
    }, true);
  });

  const choices = Array.from({ length: 12 }, (_, index) => ({
    label: `Choice ${index + 1} with a deliberately long readable title`,
    description: `Detailed explanation ${index + 1} that wraps independently without colliding with the title or the next row.`,
  }));
  await page.evaluate(async options => {
    const renderer = await import('/static/js/chatRenderer.js');
    renderer.renderAskUserCard({ question: 'Choose the safest next action', options }, { scroll: false });
  }, choices);

  const card = page.locator('.ask-user-card');
  const list = card.locator('.ask-user-options');
  const first = card.locator('.ask-user-option').first();
  await expect(card).toBeVisible();
  expect(await list.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
  expect(await first.evaluate(node => node.getBoundingClientRect().height)).toBeGreaterThanOrEqual(54);
  const optionFlow = await first.evaluate(row => {
    const label = row.querySelector('.ask-user-option-label').getBoundingClientRect();
    const description = row.querySelector('.ask-user-option-desc').getBoundingClientRect();
    return { labelBottom: label.bottom, descriptionTop: description.top };
  });
  expect(optionFlow.descriptionTop).toBeGreaterThanOrEqual(optionFlow.labelBottom - 1);

  await first.focus();
  expect(await first.evaluate(node => getComputedStyle(node).outlineStyle)).not.toBe('none');
  await page.keyboard.press('Enter');
  await expect(card).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => window.__askUserSent.at(-1))).toBe(choices[0].label);

  await page.evaluate(async options => {
    const renderer = await import('/static/js/chatRenderer.js');
    renderer.renderAskUserCard({ question: 'Pick several', options, multi: true }, { scroll: false });
  }, choices.slice(0, 3));
  await page.locator('.ask-user-option input').first().focus();
  await page.keyboard.press('Space');
  await page.locator('.ask-user-other-input').fill('Custom answer');
  await page.locator('.ask-user-other-send').click();
  await expect.poll(() => page.evaluate(() => window.__askUserSent.at(-1))).toBe(`${choices[0].label}, Custom answer`);

  await page.evaluate(async options => {
    const renderer = await import('/static/js/chatRenderer.js');
    renderer.renderAskUserCard({ question: 'Dismiss me', options }, { scroll: false });
  }, choices.slice(0, 2));
  await page.locator('.ask-user-close').click();
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('message');
  await page.evaluate(async options => {
    const renderer = await import('/static/js/chatRenderer.js');
    renderer.renderAskUserCard({ question: 'Escape me', options }, { scroll: false });
  }, choices.slice(0, 2));
  await page.keyboard.press('Escape');
  await expect(page.locator('.ask-user-card')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('message');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(async options => {
    const renderer = await import('/static/js/chatRenderer.js');
    renderer.renderAskUserCard({ question: 'Mobile custom answer', options }, { scroll: false });
  }, choices.slice(0, 3));
  const mobileRow = await page.locator('.ask-user-other').evaluate(node => node.getBoundingClientRect());
  const mobileCard = await page.locator('.ask-user-card').evaluate(node => node.getBoundingClientRect());
  expect(mobileRow.left).toBeGreaterThanOrEqual(mobileCard.left);
  expect(mobileRow.right).toBeLessThanOrEqual(mobileCard.right);
});
